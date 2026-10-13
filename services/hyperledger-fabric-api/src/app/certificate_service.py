"""Certificate business logic — orchestrates DB, Fabric, and Kafka."""

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg

from app.fabric_client import (
    CertificateNotFoundError,
    FabricClient,
    FabricCertificateData,
    FabricHashConflictError,
    FabricTimeoutError,
    FabricUnavailableError,
)
from app.models import (
    CertificateIssueRequest,
    CertificateIssueResponse,
    CertificateListResponse,
    CertificateRecord,
    CertificateVerifyResponse,
    CursorPage,
    GateResultSnapshot,
    SourceSystem,
    StationResultItem,
)

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


# ---------------------------------------------------------------------------
# Re-exported error types so router imports from one place
# ---------------------------------------------------------------------------

__all__ = [
    "CertificateNotFoundError",
    "FabricHashConflictError",
    "FabricTimeoutError",
    "FabricUnavailableError",
    "issue_certificate",
    "list_certificates",
    "get_certificate",
    "verify_certificate",
]


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _encode_cursor(issued_at: str, certificate_id: str) -> str:
    raw = json.dumps({"issued_at": issued_at, "certificate_id": certificate_id})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        data = json.loads(raw)
        return data["issued_at"], data["certificate_id"]
    except (ValueError, KeyError, UnicodeDecodeError) as err:
        raise ValueError(f"Invalid cursor: {cursor}") from err


def _row_to_record(row: asyncpg.Record) -> CertificateRecord:
    snapshot_raw = row["gate_result_snapshot"]
    if isinstance(snapshot_raw, str):
        snapshot_dict = json.loads(snapshot_raw)
    else:
        snapshot_dict = dict(snapshot_raw)

    snapshot = GateResultSnapshot(
        gateId=snapshot_dict["gateId"],
        stationResults=[
            StationResultItem(**sr) for sr in snapshot_dict["stationResults"]
        ],
    )

    return CertificateRecord(
        certificateId=row["certificate_id"],
        vin=row["vin"],
        gateResultSnapshot=snapshot,
        healthScore=float(row["health_score"]),
        validatorId=row["validator_id"],
        issuedAt=row["issued_at"].isoformat() if hasattr(row["issued_at"], "isoformat") else row["issued_at"],
        certificateHash=row["certificate_hash"],
        fabricTransactionId=row["fabric_transaction_id"],
        fabricBlockNumber=int(row["fabric_block_number"]),
        anchoredAt=row["anchored_at"].isoformat() if hasattr(row["anchored_at"], "isoformat") else row["anchored_at"],
        sourceSystem=SourceSystem(row["source_system"]),
        shiftId=row["shift_id"] if row["shift_id"] else None,
    )


async def issue_certificate(
    *,
    request: CertificateIssueRequest,
    pool: asyncpg.Pool,
    fabric: FabricClient,
    kafka_publish: Any,  # callable(topic, payload) -> Awaitable
) -> CertificateIssueResponse:
    """Anchor a certificate to Fabric, mirror in DB, emit Kafka event.

    Returns CertificateIssueResponse.
    Raises: FabricHashConflictError, FabricUnavailableError, FabricTimeoutError.
    """
    cert_id = str(request.certificateId)

    # --- Idempotency check: consult local DB first to avoid Fabric round-trip ---
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT certificate_id, vin, fabric_transaction_id, fabric_block_number,"
            " anchored_at, certificate_hash"
            " FROM fabric_certificate"
            " WHERE certificate_id = $1",
            cert_id,
        )

    if existing is not None:
        stored_hash: str = existing["certificate_hash"]
        if stored_hash != request.certificateHash:
            raise FabricHashConflictError(
                f"certificateId {cert_id} already anchored with a different hash"
            )
        return CertificateIssueResponse(
            certificateId=request.certificateId,
            vin=existing["vin"],
            fabricTransactionId=existing["fabric_transaction_id"],
            fabricBlockNumber=int(existing["fabric_block_number"]),
            anchoredAt=existing["anchored_at"].isoformat()
            if hasattr(existing["anchored_at"], "isoformat")
            else existing["anchored_at"],
            certificateHash=stored_hash,
            idempotent=True,
        )

    # --- Submit to Fabric (may raise FabricUnavailableError / FabricTimeoutError) ---
    anchor, idempotent_fabric = await fabric.issue_certificate(
        certificate_id=cert_id,
        vin=request.vin,
        certificate_hash=request.certificateHash,
        validator_id=str(request.validatorId),
        issued_at=request.issuedAt,
    )

    # --- Mirror in local DB (within a transaction) ---
    attempt_id = str(uuid.uuid4())
    snapshot_json = request.gateResultSnapshot.model_dump_json()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Record the anchor attempt
            await conn.execute(
                "INSERT INTO fabric_anchor_attempt"
                " (attempt_id, certificate_id, status, fabric_transaction_id,"
                "  fabric_block_number, latency_ms, created_at)"
                " VALUES ($1, $2, 'committed', $3, $4, 0, NOW())"
                " ON CONFLICT DO NOTHING",
                attempt_id,
                cert_id,
                anchor.transaction_id,
                anchor.block_number,
            )
            # Mirror the certificate
            await conn.execute(
                "INSERT INTO fabric_certificate"
                " (certificate_id, vin, shift_id, gate_result_snapshot,"
                "  health_score, validator_id, issued_at, certificate_hash,"
                "  fabric_transaction_id, fabric_block_number, anchored_at, source_system)"
                " VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7::timestamptz, $8,"
                "         $9, $10, $11::timestamptz, $12)"
                " ON CONFLICT (certificate_id) DO NOTHING",
                cert_id,
                request.vin,
                str(request.shiftId) if request.shiftId else None,
                snapshot_json,
                request.healthScore,
                str(request.validatorId),
                request.issuedAt,
                request.certificateHash,
                anchor.transaction_id,
                anchor.block_number,
                anchor.anchored_at,
                request.sourceSystem.value,
            )

    # --- Emit CertificateIssuedEvent to Kafka ---
    from app.kafka_producer import TOPIC_CERTIFICATE_ISSUED

    event_payload = {
        "certificateId": cert_id,
        "vin": request.vin,
        "fabricTransactionId": anchor.transaction_id,
        "fabricBlockNumber": anchor.block_number,
        "anchoredAt": anchor.anchored_at,
        "certificateHash": request.certificateHash,
        "validatorId": str(request.validatorId),
        "issuedAt": request.issuedAt,
        "sourceSystem": request.sourceSystem.value,
        "shiftId": str(request.shiftId) if request.shiftId else None,
    }
    await kafka_publish(TOPIC_CERTIFICATE_ISSUED, event_payload)

    return CertificateIssueResponse(
        certificateId=request.certificateId,
        vin=request.vin,
        fabricTransactionId=anchor.transaction_id,
        fabricBlockNumber=anchor.block_number,
        anchoredAt=anchor.anchored_at,
        certificateHash=request.certificateHash,
        idempotent=False,
    )


async def list_certificates(
    *,
    pool: asyncpg.Pool,
    vin: str | None = None,
    shift_id: str | None = None,
    issued_after: str | None = None,
    issued_before: str | None = None,
    cursor: str | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> CertificateListResponse:
    limit = max(1, min(limit, _MAX_LIMIT))

    params: list[Any] = []
    conditions: list[str] = []
    p = 1

    if vin is not None:
        conditions.append(f"vin = ${p}")
        params.append(vin)
        p += 1
    if shift_id is not None:
        conditions.append(f"shift_id = ${p}")
        params.append(shift_id)
        p += 1
    if issued_after is not None:
        conditions.append(f"issued_at >= ${p}::timestamptz")
        params.append(issued_after)
        p += 1
    if issued_before is not None:
        conditions.append(f"issued_at < ${p}::timestamptz")
        params.append(issued_before)
        p += 1

    # Cursor: keyset on (issued_at DESC, certificate_id ASC)
    if cursor is not None:
        cur_issued_at, cur_cert_id = _decode_cursor(cursor)
        conditions.append(
            f"(issued_at, certificate_id) < (${p}::timestamptz, ${p + 1})"
        )
        params.append(cur_issued_at)
        params.append(cur_cert_id)
        p += 2

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # Fetch limit+1 to detect whether a next page exists
    fetch_limit = limit + 1
    params.append(fetch_limit)
    query = (
        "SELECT certificate_id, vin, shift_id, gate_result_snapshot,"
        " health_score, validator_id, issued_at, certificate_hash,"
        " fabric_transaction_id, fabric_block_number, anchored_at, source_system"
        " FROM fabric_certificate"
        f" {where_clause}"
        f" ORDER BY issued_at DESC, certificate_id ASC"
        f" LIMIT ${p}"
    )

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    has_next = len(rows) > limit
    page_rows = rows[:limit]
    records = [_row_to_record(r) for r in page_rows]

    next_cursor: str | None = None
    if has_next and page_rows:
        last = page_rows[-1]
        last_issued_at = last["issued_at"].isoformat() if hasattr(last["issued_at"], "isoformat") else last["issued_at"]
        next_cursor = _encode_cursor(last_issued_at, str(last["certificate_id"]))

    return CertificateListResponse(
        data=records,
        page=CursorPage(next_cursor=next_cursor, limit=limit),
    )


async def get_certificate(
    *,
    certificate_id: str,
    pool: asyncpg.Pool,
    fabric: FabricClient,
) -> CertificateRecord:
    """Return the local DB record after confirming the Fabric ledger entry is reachable.

    Raises CertificateNotFoundError if absent locally.
    Raises FabricUnavailableError if the Fabric peer query fails.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT certificate_id, vin, shift_id, gate_result_snapshot,"
            " health_score, validator_id, issued_at, certificate_hash,"
            " fabric_transaction_id, fabric_block_number, anchored_at, source_system"
            " FROM fabric_certificate"
            " WHERE certificate_id = $1",
            certificate_id,
        )

    if row is None:
        raise CertificateNotFoundError(f"Certificate {certificate_id} not found")

    # Confirm the Fabric ledger entry is reachable — raises FabricUnavailableError on failure
    await fabric.get_certificate(certificate_id)

    return _row_to_record(row)


async def _fetch_certificate_row(
    *,
    certificate_id: str,
    pool: asyncpg.Pool,
) -> CertificateRecord:
    """Fetch from local DB only — does not call Fabric."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT certificate_id, vin, shift_id, gate_result_snapshot,"
            " health_score, validator_id, issued_at, certificate_hash,"
            " fabric_transaction_id, fabric_block_number, anchored_at, source_system"
            " FROM fabric_certificate"
            " WHERE certificate_id = $1",
            certificate_id,
        )

    if row is None:
        raise CertificateNotFoundError(f"Certificate {certificate_id} not found")

    return _row_to_record(row)


async def verify_certificate(
    *,
    certificate_id: str,
    pool: asyncpg.Pool,
    fabric: FabricClient,
) -> CertificateVerifyResponse:
    """Compare stored hash against the Fabric ledger entry.

    Raises CertificateNotFoundError if absent locally.
    Raises FabricUnavailableError on peer failure.
    Emits CertificateVerifyFailedEvent on mismatch.
    """
    record = await _fetch_certificate_row(certificate_id=certificate_id, pool=pool)

    fabric_data: FabricCertificateData = await fabric.get_certificate(certificate_id)
    verified_at = _utcnow_iso()

    verified = fabric_data.certificate_hash == record.certificateHash
    mismatch_reason: str | None = None

    if not verified:
        mismatch_reason = (
            f"Stored hash {record.certificateHash[:8]}… does not match "
            f"Fabric ledger hash {fabric_data.certificate_hash[:8]}…"
        )
        from app.kafka_producer import TOPIC_CERTIFICATE_VERIFY_FAILED, publish

        await publish(
            TOPIC_CERTIFICATE_VERIFY_FAILED,
            {
                "certificateId": certificate_id,
                "storedHash": record.certificateHash,
                "fabricHash": fabric_data.certificate_hash,
                "verifiedAt": verified_at,
                "mismatchReason": mismatch_reason,
            },
        )

    return CertificateVerifyResponse(
        certificateId=record.certificateId,
        verified=verified,
        storedHash=record.certificateHash,
        fabricTransactionId=record.fabricTransactionId,
        fabricHash=fabric_data.certificate_hash,
        verifiedAt=verified_at,
        mismatchReason=mismatch_reason,
    )
