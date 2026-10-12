"""GET /ar/instructions/{vin} — VIN-specific AR holographic work instruction overlay."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.ar_service import (
    fetch_from_cache,
    fetch_from_vdt,
    insert_ar_session,
    log_failure,
    store_in_cache,
)
from app.audit import NULL_HASH, AuditStore, AuditWriterProtocol, fire_audit_write, sha256_hex
from app.auth import caller_subject, require_ar_role
from app.config import settings
from app.db import get_pool
from app.kafka_producer import TOPIC_ADOPTION_EVENT, TOPIC_AR_SESSION_STARTED, publish
from app.models import (
    ArInstructionFallbackResponse,
    ArInstructionResponse,
    DeviceType,
    ErrorResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AR Instructions"])

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

# Process-level audit writer; tests may swap via get_audit_writer dependency.
_process_audit_writer: AuditWriterProtocol = AuditStore()


def get_audit_writer() -> AuditWriterProtocol:
    return _process_audit_writer


def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


@router.get(
    "/ar/instructions/{vin}",
    operation_id="getArInstructions",
    summary="Retrieve VIN-specific AR holographic work instruction overlay",
    responses={
        200: {"model": ArInstructionResponse},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        504: {"model": ArInstructionFallbackResponse},
    },
)
async def get_ar_instructions(
    vin: str,
    stationId: str = Query(..., description="Station identifier"),
    deviceType: DeviceType = Query(..., description="AR device type"),
    jwt_payload: dict[str, Any] = Depends(require_ar_role),
    pool: Any = Depends(get_pool),
    audit_writer: AuditWriterProtocol = Depends(get_audit_writer),
) -> JSONResponse:
    correlation_id = str(uuid.uuid4())
    user_id = caller_subject(jwt_payload)

    if not _VIN_RE.match(vin):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("VIN_FORMAT_INVALID", "VIN must match ^[A-HJ-NPR-Z0-9]{17}$"),
        )

    async with pool.acquire() as conn:
        # --- Cache lookup ---
        cached = await fetch_from_cache(conn, vin, stationId)
        if cached is not None:
            logger.info(
                "ar_instructions cache hit vin=%s station=%s corr=%s",
                vin, stationId, correlation_id,
            )
            idempotency_key = str(uuid.uuid4())
            event_id = await insert_ar_session(
                conn, vin, stationId, deviceType, user_id, idempotency_key
            )
            await _publish_session_events(vin, stationId, deviceType, user_id, event_id)
            return JSONResponse(
                status_code=200,
                content=json.loads(cached.model_dump_json()),
            )

        # --- Cache miss: call VDT ---
        try:
            response = await fetch_from_vdt(
                vin=vin,
                station_id=stationId,
                vdt_base_url=settings.vdt_base_url,
                timeout_seconds=settings.vdt_timeout_seconds,
                fallback_pdf_qr_url_template=settings.fallback_pdf_qr_url_template,
            )
        except httpx.TimeoutException:
            return await _handle_timeout(
                conn=conn,
                vin=vin,
                station_id=stationId,
                device_type=deviceType,
                correlation_id=correlation_id,
                user_id=user_id,
                audit_writer=audit_writer,
            )
        except HTTPException:
            raise

        # Store in cache (best-effort; do not fail the request on cache write error)
        try:
            await store_in_cache(conn, response, settings.cache_ttl_seconds)
        except Exception as cache_exc:
            logger.warning(
                "Cache write failed for vin=%s corr=%s: %s", vin, correlation_id, cache_exc
            )

        idempotency_key = str(uuid.uuid4())
        event_id = await insert_ar_session(
            conn, vin, stationId, deviceType, user_id, idempotency_key
        )
        await _publish_session_events(vin, stationId, deviceType, user_id, event_id)

        return JSONResponse(
            status_code=200,
            content=json.loads(response.model_dump_json()),
        )


async def _handle_timeout(
    conn: Any,
    vin: str,
    station_id: str,
    device_type: DeviceType,
    correlation_id: str,
    user_id: str,
    audit_writer: AuditWriterProtocol,
) -> JSONResponse:
    """Insert failure log row, fire audit write, return 504 fallback response."""
    failed_at = await log_failure(
        conn=conn,
        vin=vin,
        station_id=station_id,
        device_type=device_type,
        failure_reason="upstream_timeout",
        correlation_id=correlation_id,
    )

    after_hash = sha256_hex(
        json.dumps(
            {"vin": vin, "station_id": station_id, "failure_reason": "upstream_timeout"},
            sort_keys=True,
        )
    )
    fire_audit_write(
        writer=audit_writer,
        user_id=user_id,
        action_type="AR_OVERLAY_LOAD_FAILURE",
        entity_id=vin,
        before_hash=NULL_HASH,
        after_hash=after_hash,
    )

    fallback_url = settings.fallback_pdf_qr_url_template.format(vin=vin)
    body = ArInstructionFallbackResponse(
        vin=vin,
        stationId=station_id,
        fallbackPdfQrUrl=fallback_url,
        failureLoggedAt=failed_at,
        correlationId=correlation_id,
    )
    return JSONResponse(
        status_code=504,
        content=json.loads(body.model_dump_json()),
    )


async def _publish_session_events(
    vin: str,
    station_id: str,
    device_type: DeviceType,
    user_id: str,
    event_id: str,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    ar_session_payload = {
        "eventId": event_id,
        "vin": vin,
        "stationId": station_id,
        "deviceType": device_type.value,
        "userId": user_id,
        "startedAt": now_iso,
    }
    adoption_payload = {
        "eventId": event_id,
        "eventType": "AR_SESSION_STARTED",
        "vin": vin,
        "stationId": station_id,
        "deviceType": device_type.value,
        "occurredAt": now_iso,
    }
    try:
        await publish(TOPIC_AR_SESSION_STARTED, ar_session_payload)
        await publish(TOPIC_ADOPTION_EVENT, adoption_payload)
    except Exception as kafka_exc:
        logger.warning("Kafka publish failed for vin=%s: %s", vin, kafka_exc)
