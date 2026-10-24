"""Certificate endpoints:
  POST /certificate/issue
  GET  /certificate/{certificateId}/verify
  GET  /certificates
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.auth import require_certificate_issuer, require_certificate_reader
from app.db import get_pool
from app.fabric_client import FabricPeerError, FabricTimeoutError, submit_transaction, query_transaction_hash
from app.kafka_producer import TOPIC_CERTIFICATE_ISSUED, TOPIC_CERTIFICATE_VERIFY_FAILED, publish

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Certificates"])

_HASH_RE = re.compile(r"^[a-fA-F0-9]{64}$")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class StationResultEnum(str, Enum):
    PASS = "PASS"
    CONDITIONAL = "CONDITIONAL"
    FAIL = "FAIL"


class StationCodeEnum(str, Enum):
    DIM = "DIM"
    LEAK = "LEAK"
    ELEC = "ELEC"
    NVH = "NVH"
    ADAS = "ADAS"


class StationResult(BaseModel):
    stationCode: StationCodeEnum
    result: StationResultEnum
    details: str | None = None


class GateResultSnapshot(BaseModel):
    gateId: str
    stationResults: list[StationResult]


class SourceSystemEnum(str, Enum):
    quality_studio = "quality-studio"
    legacy = "legacy"


class CertificateIssueRequest(BaseModel):
    certificateId: str = Field(..., description="Client-generated UUID idempotency key")
    vin: str = Field(..., min_length=17, max_length=17)
    gateResultSnapshot: GateResultSnapshot
    healthScore: float = Field(..., ge=0.0, le=100.0)
    validatorId: str
    issuedAt: str
    certificateHash: str
    shiftId: str | None = None
    sourceSystem: SourceSystemEnum = SourceSystemEnum.quality_studio

    @field_validator("certificateId")
    @classmethod
    def _must_be_uuid(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError as err:
            raise ValueError("certificateId must be a valid UUID") from err
        return v

    @field_validator("certificateHash")
    @classmethod
    def _must_be_sha256(cls, v: str) -> str:
        if not _HASH_RE.match(v):
            raise ValueError("certificateHash must be a 64-character hex SHA-256 digest")
        return v

    @field_validator("validatorId")
    @classmethod
    def _validator_id_must_be_uuid(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError as err:
            raise ValueError("validatorId must be a valid UUID") from err
        return v

    @field_validator("shiftId")
    @classmethod
    def _shift_id_must_be_uuid(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            uuid.UUID(v)
        except ValueError as err:
            raise ValueError("shiftId must be a valid UUID") from err
        return v


class CertificateIssueResponse(BaseModel):
    certificateId: str
    vin: str
    fabricTransactionId: str
    fabricBlockNumber: int
    anchoredAt: str
    certificateHash: str
    idempotent: bool | None = None


class CertificateVerifyResponse(BaseModel):
    certificateId: str
    vin: str
    fabricTransactionId: str
    certificateHash: str
    verified: bool
    verifiedAt: str


class CursorPage(BaseModel):
    next_cursor: str | None
    limit: int


class CertificateListResponse(BaseModel):
    data: list[CertificateIssueResponse]
    page: CursorPage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


def _encode_cursor(issued_at: str, certificate_id: str) -> str:
    raw = f"{issued_at}|{certificate_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, str] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        parts = raw.split("|", 1)
        if len(parts) != 2:
            return None
        return parts[0], parts[1]
    except (binascii.Error, UnicodeDecodeError):
        return None


def _row_to_response(row: Any) -> CertificateIssueResponse:
    return CertificateIssueResponse(
        certificateId=str(row["certificate_id"]),
        vin=row["vin"],
        fabricTransactionId=row["fabric_transaction_id"],
        fabricBlockNumber=row["fabric_block_number"],
        anchoredAt=row["anchored_at"].isoformat() if hasattr(row["anchored_at"], "isoformat") else str(row["anchored_at"]),
        certificateHash=row["certificate_hash"],
    )


# ---------------------------------------------------------------------------
# POST /certificate/issue
# ---------------------------------------------------------------------------

@router.post(
    "/certificate/issue",
    operation_id="issueCertificate",
    summary="Anchor a Digital Birth Certificate to Hyperledger Fabric",
    status_code=201,
    responses={
        200: {"model": CertificateIssueResponse},
        201: {"model": CertificateIssueResponse},
        400: {},
        401: {},
        403: {},
        409: {},
        502: {},
        504: {},
    },
)
async def issue_certificate(
    body: CertificateIssueRequest,
    jwt_payload: dict[str, Any] = Depends(require_certificate_issuer),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
    from app.config import settings

    async with pool.acquire() as conn:
        # Idempotency check
        existing = await conn.fetchrow(
            """
            SELECT certificate_id, vin, fabric_transaction_id, fabric_block_number,
                   anchored_at, certificate_hash
            FROM fabric_certificate
            WHERE certificate_id = $1
            """,
            body.certificateId,
        )

        if existing is not None:
            if existing["certificate_hash"] != body.certificateHash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=_error(
                        "CERTIFICATE_HASH_MISMATCH",
                        f"certificateId {body.certificateId} is already anchored with a different hash",
                    ),
                )
            resp = CertificateIssueResponse(
                certificateId=str(existing["certificate_id"]),
                vin=existing["vin"],
                fabricTransactionId=existing["fabric_transaction_id"],
                fabricBlockNumber=existing["fabric_block_number"],
                anchoredAt=existing["anchored_at"].isoformat() if hasattr(existing["anchored_at"], "isoformat") else str(existing["anchored_at"]),
                certificateHash=existing["certificate_hash"],
                idempotent=True,
            )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=json.loads(resp.model_dump_json()),
            )

        # INSERT fabric_anchor_attempt with status=pending
        attempt_id = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO fabric_anchor_attempt
                (id, certificate_id, status, created_at, updated_at)
            VALUES ($1, $2, 'pending', NOW(), NOW())
            """,
            attempt_id,
            body.certificateId,
        )

    # Submit to Fabric outside the DB connection (long-running gRPC call)
    gate_snapshot_dict = body.gateResultSnapshot.model_dump()
    try:
        commit = await submit_transaction(
            certificate_id=body.certificateId,
            vin=body.vin,
            certificate_hash=body.certificateHash,
            gate_result_snapshot=gate_snapshot_dict,
            health_score=body.healthScore,
            validator_id=body.validatorId,
            issued_at=body.issuedAt,
            source_system=body.sourceSystem.value,
            shift_id=body.shiftId,
            timeout_seconds=settings.fabric_transaction_timeout_seconds,
        )
    except FabricTimeoutError as err:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE fabric_anchor_attempt
                SET status = 'timeout', updated_at = NOW()
                WHERE id = $1
                """,
                attempt_id,
            )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=_error("FABRIC_TIMEOUT", "Fabric transaction commit timed out"),
        ) from err
    except FabricPeerError as err:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE fabric_anchor_attempt
                SET status = 'failed', error_detail = $2, updated_at = NOW()
                WHERE id = $1
                """,
                attempt_id,
                str(err),
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_error("FABRIC_PEER_ERROR", "Fabric peer unavailable or endorsement failed"),
        ) from err

    anchored_at = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO fabric_certificate (
                certificate_id, vin, fabric_transaction_id, fabric_block_number,
                anchored_at, certificate_hash, validator_id, health_score,
                gate_result_snapshot, source_system, shift_id, issued_at, status,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12,
                'committed', NOW(), NOW()
            )
            """,
            body.certificateId,
            body.vin,
            commit.transaction_id,
            commit.block_number,
            anchored_at,
            body.certificateHash,
            body.validatorId,
            body.healthScore,
            json.dumps(gate_snapshot_dict),
            body.sourceSystem.value,
            body.shiftId,
            body.issuedAt,
        )

        await conn.execute(
            """
            UPDATE fabric_anchor_attempt
            SET status = 'committed', updated_at = NOW()
            WHERE id = $1
            """,
            attempt_id,
        )

    # Publish CertificateIssuedEvent synchronously per kafka-event-within-1s-of-anchor
    try:
        await asyncio.wait_for(
            publish(
                TOPIC_CERTIFICATE_ISSUED,
                {
                    "eventId": str(uuid.uuid4()),
                    "certificateId": body.certificateId,
                    "vin": body.vin,
                    "fabricTransactionId": commit.transaction_id,
                    "fabricBlockNumber": commit.block_number,
                    "validatorId": body.validatorId,
                    "issuedAt": body.issuedAt,
                    "anchoredAt": anchored_at.isoformat(),
                    "sourceSystem": body.sourceSystem.value,
                    "shiftId": body.shiftId,
                },
            ),
            timeout=1.0,
        )
    except (OSError, asyncio.TimeoutError, RuntimeError) as exc:
        logger.warning(
            "CertificateIssuedEvent publish failed for certificateId=%s: %s",
            body.certificateId,
            exc,
        )

    resp = CertificateIssueResponse(
        certificateId=body.certificateId,
        vin=body.vin,
        fabricTransactionId=commit.transaction_id,
        fabricBlockNumber=commit.block_number,
        anchoredAt=anchored_at.isoformat(),
        certificateHash=body.certificateHash,
        idempotent=False,
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=json.loads(resp.model_dump_json()),
    )


# ---------------------------------------------------------------------------
# GET /certificate/{certificateId}/verify
# ---------------------------------------------------------------------------

@router.get(
    "/certificate/{certificateId}/verify",
    operation_id="verifyCertificate",
    summary="Verify certificate hash against the Fabric ledger",
    response_model=CertificateVerifyResponse,
)
async def verify_certificate(
    certificateId: str,
    jwt_payload: dict[str, Any] = Depends(require_certificate_reader),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
    try:
        uuid.UUID(certificateId)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_CERTIFICATE_ID", "certificateId must be a valid UUID"),
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT certificate_id, vin, fabric_transaction_id, certificate_hash
            FROM fabric_certificate
            WHERE certificate_id = $1
            """,
            certificateId,
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("CERTIFICATE_NOT_FOUND", f"Certificate {certificateId} not found"),
        )

    stored_hash: str = row["certificate_hash"]
    fabric_tx_id: str = row["fabric_transaction_id"]
    vin: str = row["vin"]

    ledger_hash = await query_transaction_hash(transaction_id=fabric_tx_id)
    verified_at = datetime.now(timezone.utc).isoformat()

    if ledger_hash is None or ledger_hash != stored_hash:
        # Emit CertificateVerifyFailedEvent
        try:
            await asyncio.wait_for(
                publish(
                    TOPIC_CERTIFICATE_VERIFY_FAILED,
                    {
                        "eventId": str(uuid.uuid4()),
                        "certificateId": certificateId,
                        "vin": vin,
                        "fabricTransactionId": fabric_tx_id,
                        "storedHash": stored_hash,
                        "ledgerHash": ledger_hash,
                        "verifiedAt": verified_at,
                    },
                ),
                timeout=1.0,
            )
        except (OSError, asyncio.TimeoutError, RuntimeError) as exc:
            logger.warning(
                "CertificateVerifyFailedEvent publish failed for certificateId=%s: %s",
                certificateId,
                exc,
            )

        resp = CertificateVerifyResponse(
            certificateId=certificateId,
            vin=vin,
            fabricTransactionId=fabric_tx_id,
            certificateHash=stored_hash,
            verified=False,
            verifiedAt=verified_at,
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=json.loads(resp.model_dump_json()),
        )

    resp = CertificateVerifyResponse(
        certificateId=certificateId,
        vin=vin,
        fabricTransactionId=fabric_tx_id,
        certificateHash=stored_hash,
        verified=True,
        verifiedAt=verified_at,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=json.loads(resp.model_dump_json()),
    )


# ---------------------------------------------------------------------------
# GET /certificates
# ---------------------------------------------------------------------------

@router.get(
    "/certificates",
    operation_id="listCertificates",
    summary="List certificates (cursor-paginated), optionally filtered by shiftId",
    response_model=CertificateListResponse,
)
async def list_certificates(
    shiftId: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    jwt_payload: dict[str, Any] = Depends(require_certificate_reader),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
    if shiftId is not None:
        try:
            uuid.UUID(shiftId)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_error("INVALID_SHIFT_ID", "shiftId must be a valid UUID"),
            )

    cursor_issued_at: str | None = None
    cursor_cert_id: str | None = None
    if cursor is not None:
        decoded = _decode_cursor(cursor)
        if decoded is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_error("INVALID_CURSOR", "cursor parameter is malformed"),
            )
        cursor_issued_at, cursor_cert_id = decoded

    async with pool.acquire() as conn:
        rows = await _query_certificates(
            conn,
            shift_id=shiftId,
            cursor_issued_at=cursor_issued_at,
            cursor_cert_id=cursor_cert_id,
            limit=limit,
        )

    records = [_row_to_response(r) for r in rows[:limit]]
    next_cursor: str | None = None
    if len(rows) > limit:
        last = rows[limit - 1]
        anchored_at = last["anchored_at"]
        anchored_at_str = anchored_at.isoformat() if hasattr(anchored_at, "isoformat") else str(anchored_at)
        next_cursor = _encode_cursor(anchored_at_str, str(last["certificate_id"]))

    response = CertificateListResponse(
        data=records,
        page=CursorPage(next_cursor=next_cursor, limit=limit),
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=json.loads(response.model_dump_json()),
    )


async def _query_certificates(
    conn: Any,
    *,
    shift_id: str | None,
    cursor_issued_at: str | None,
    cursor_cert_id: str | None,
    limit: int,
) -> list[Any]:
    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if shift_id is not None:
        conditions.append(f"shift_id = ${idx}::uuid")
        params.append(shift_id)
        idx += 1

    if cursor_issued_at is not None and cursor_cert_id is not None:
        conditions.append(f"(anchored_at, certificate_id) < (${idx}::timestamptz, ${idx + 1}::uuid)")
        params.append(cursor_issued_at)
        params.append(cursor_cert_id)
        idx += 2

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit + 1)

    query = f"""
        SELECT certificate_id, vin, fabric_transaction_id, fabric_block_number,
               anchored_at, certificate_hash
        FROM fabric_certificate
        {where}
        ORDER BY anchored_at DESC, certificate_id DESC
        LIMIT ${idx}
    """
    return await conn.fetch(query, *params)
