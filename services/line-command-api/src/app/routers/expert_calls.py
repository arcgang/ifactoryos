"""POST /ar/expert-calls and POST /ar/expert-calls/{callId}/end."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.audit import NULL_HASH, AuditStore, AuditWriterProtocol, fire_audit_write, sha256_hex
from app.auth import caller_subject, require_ar_role
from app.config import settings
from app.db import get_pool
from app.expert_call_service import (
    end_expert_call,
    get_video_approved,
    initiate_expert_call,
)
from app.kafka_producer import TOPIC_EXPERT_CALL_ENDED, publish
from app.models import (
    ErrorResponse,
    ExpertCallEndRequest,
    ExpertCallEndResponse,
    ExpertCallRequest,
    ExpertCallResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AR Instructions"])

_process_audit_writer: AuditWriterProtocol = AuditStore()


def get_audit_writer() -> AuditWriterProtocol:
    return _process_audit_writer


def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


@router.post(
    "/ar/expert-calls",
    operation_id="initiateExpertCall",
    summary="Initiate a remote expert call from the AR Workbench",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"model": ExpertCallResponse},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
    },
)
async def initiate_expert_call_endpoint(
    body: ExpertCallRequest,
    jwt_payload: dict[str, Any] = Depends(require_ar_role),
    pool: Any = Depends(get_pool),
    audit_writer: AuditWriterProtocol = Depends(get_audit_writer),
) -> JSONResponse:
    correlation_id = str(uuid.uuid4())
    user_id = caller_subject(jwt_payload)

    idempotency_key = str(body.idempotencyKey) if body.idempotencyKey else str(uuid.uuid4())

    async with pool.acquire() as conn:
        video_approved = await get_video_approved(conn, settings.expert_call_plant_id)
        call_mode = "video" if video_approved else "voice"

        row = await initiate_expert_call(
            conn=conn,
            vin=body.vin,
            station_id=body.stationId,
            session_id=str(body.sessionId),
            idempotency_key=idempotency_key,
            initiated_by_user_id=user_id,
            call_mode=call_mode,
        )

    after_hash = sha256_hex(f"{body.vin}{body.stationId}{call_mode}{user_id}")
    fire_audit_write(
        writer=audit_writer,
        user_id=user_id,
        action_type="EXPERT_CALL_INITIATED",
        entity_id=body.vin,
        before_hash=NULL_HASH,
        after_hash=after_hash,
    )

    logger.info(
        "expert_call initiated call_id=%s vin=%s mode=%s corr=%s",
        row["id"],
        body.vin,
        call_mode,
        correlation_id,
        extra={"call_id": str(row["id"]), "vin": body.vin, "correlation_id": correlation_id},
    )

    response = ExpertCallResponse(
        callId=row["id"],
        callMode=row["call_mode"],
        createdAt=row["created_at"],
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=response.model_dump(mode="json"),
    )


@router.post(
    "/ar/expert-calls/{callId}/end",
    operation_id="endExpertCall",
    summary="End a remote expert call and log the session to the VIN digital thread audit trail",
    responses={
        200: {"model": ExpertCallEndResponse},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def end_expert_call_endpoint(
    callId: str,
    body: ExpertCallEndRequest,
    jwt_payload: dict[str, Any] = Depends(require_ar_role),
    pool: Any = Depends(get_pool),
    audit_writer: AuditWriterProtocol = Depends(get_audit_writer),
) -> JSONResponse:
    correlation_id = str(uuid.uuid4())
    user_id = caller_subject(jwt_payload)

    try:
        uuid.UUID(callId)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_CALL_ID", "callId must be a valid UUID"),
        )

    async with pool.acquire() as conn:
        result = await end_expert_call(
            conn=conn,
            call_id=callId,
            expert_user_id=body.expertUserId,
            duration_seconds=body.durationSeconds,
            vdt_base_url=settings.vdt_base_url,
            vdt_timeout_seconds=settings.vdt_timeout_seconds,
        )

    after_hash = sha256_hex(
        f"{result['call_mode']}{body.expertUserId}{body.durationSeconds}"
    )
    fire_audit_write(
        writer=audit_writer,
        user_id=user_id,
        action_type="EXPERT_CALL_END",
        entity_id=result["vin"],
        before_hash=NULL_HASH,
        after_hash=after_hash,
    )

    # Publish ExpertCallEndedEvent to Kafka
    now_iso = datetime.now(timezone.utc).isoformat()
    event_payload = {
        "eventId": str(uuid.uuid4()),
        "callId": callId,
        "vin": result["vin"],
        "stationId": result["station_id"],
        "callMode": result["call_mode"],
        "expertUserId": result["expert_user_id"],
        "callDurationSeconds": result["duration_seconds"],
        "auditEntryId": result["audit_entry_id"],
        "endedAt": result["ended_at"].isoformat() if isinstance(result["ended_at"], datetime) else str(result["ended_at"]),
        "biometricDataIncluded": False,
        "gazeDataIncluded": False,
        "occurredAt": now_iso,
    }
    try:
        await publish(TOPIC_EXPERT_CALL_ENDED, event_payload)
    except Exception as kafka_exc:
        logger.warning(
            "Kafka publish failed for expert_call_ended call_id=%s: %s",
            callId,
            kafka_exc,
            extra={"call_id": callId, "correlation_id": correlation_id},
        )

    logger.info(
        "expert_call ended call_id=%s vin=%s audit_entry_id=%s corr=%s",
        callId,
        result["vin"],
        result["audit_entry_id"],
        correlation_id,
        extra={"call_id": callId, "vin": result["vin"], "correlation_id": correlation_id},
    )

    ended_at_dt = result["ended_at"]
    if not isinstance(ended_at_dt, datetime):
        ended_at_dt = datetime.now(timezone.utc)

    response = ExpertCallEndResponse(
        callId=uuid.UUID(callId),
        auditEntryId=uuid.UUID(result["audit_entry_id"]),
        endedAt=ended_at_dt,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=response.model_dump(mode="json"),
    )
