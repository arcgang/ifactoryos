"""GET /andon/alerts/{alertId} and POST /andon/alerts/{alertId}/acknowledge."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.andon_service import AlreadyAcknowledgedError, acknowledge_alert, fetch_alert, fetch_weld_trace
from app.audit import AuditStore, AuditWriterProtocol, fire_audit_write, sha256_hex
from app.auth import caller_subject, require_bearer
from app.config import settings
from app.db import get_pool, get_ts_pool
from app.kafka_producer import TOPIC_ADOPTION_EVENT, TOPIC_MAINTENANCE_DISPATCH, publish
from app.models import (
    AlertAcknowledgeRequest,
    AlertAcknowledgeResponse,
    AndonAlert,
    ErrorResponse,
    WeldTrace,
    WeldTracePoint,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Andon Alerts"])

_process_audit_writer: AuditWriterProtocol = AuditStore()


def get_audit_writer() -> AuditWriterProtocol:
    return _process_audit_writer


def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


def _require_shift_lead(payload: dict[str, Any]) -> dict[str, Any]:
    """Raise 403 unless the JWT carries the shift-lead role."""
    roles: list[str] = payload.get("roles", []) or []
    scopes: list[str] = payload.get("scp", "").split() if payload.get("scp") else []
    all_grants = set(roles) | set(scopes)
    if settings.shift_lead_role not in all_grants:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_error(
                "FORBIDDEN",
                f"Caller requires {settings.shift_lead_role}",
            ),
        )
    return payload


# ---------------------------------------------------------------------------
# GET /andon/alerts/{alertId}
# ---------------------------------------------------------------------------

@router.get(
    "/andon/alerts/{alertId}",
    operation_id="getAndonAlert",
    summary="Get a specific Andon alert by ID",
    responses={
        200: {"model": AndonAlert},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def get_andon_alert(
    alertId: str,
    jwt_payload: dict[str, Any] = Depends(require_bearer),
    pool: Any = Depends(get_pool),
    ts_pool: Any = Depends(get_ts_pool),
) -> JSONResponse:
    async with pool.acquire() as conn:
        row = await fetch_alert(conn, alertId)

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("ALERT_NOT_FOUND", f"Andon alert {alertId!r} not found"),
        )

    async with ts_pool.acquire() as ts_conn:
        raw_trace = await fetch_weld_trace(ts_conn, row["station_id"])

    weld_trace = WeldTrace(
        weld_current_a=[WeldTracePoint(**p) for p in raw_trace["weld_current_a"]],
        weld_voltage_v=[WeldTracePoint(**p) for p in raw_trace["weld_voltage_v"]],
    )

    alert = AndonAlert(
        alertId=row["alert_id"],
        opcenterEventId=row["opcenter_event_id"],
        stationId=row["station_id"],
        lineId=row["line_id"],
        alertType=row["alert_type"],
        predictedFailureType=row["predicted_failure_type"],
        minutesToPredictedFailure=row["minutes_to_predicted_failure"],
        recommendedAction=row["recommended_action"],
        status=row["status"],
        detectedAt=row["detected_at"],
        receivedAt=row["received_at"],
        acknowledgedAt=row["acknowledged_at"],
        resolvedAt=row["resolved_at"],
        correlationId=row["correlation_id"],
        weldTrace=weld_trace,
        thresholdCrossingAt=row["detected_at"],
    )
    return JSONResponse(
        status_code=200,
        content=json.loads(alert.model_dump_json()),
    )


# ---------------------------------------------------------------------------
# POST /andon/alerts/{alertId}/acknowledge
# ---------------------------------------------------------------------------

@router.post(
    "/andon/alerts/{alertId}/acknowledge",
    operation_id="acknowledgeAndonAlert",
    summary="Acknowledge an Andon alert (shift-lead only)",
    responses={
        200: {"model": AlertAcknowledgeResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def acknowledge_andon_alert(
    alertId: str,
    body: AlertAcknowledgeRequest,
    jwt_payload: dict[str, Any] = Depends(require_bearer),
    pool: Any = Depends(get_pool),
    audit_writer: AuditWriterProtocol = Depends(get_audit_writer),
) -> JSONResponse:
    _require_shift_lead(jwt_payload)
    user_id = caller_subject(jwt_payload)

    # Fetch alert to confirm existence and capture station_id + pre-state
    async with pool.acquire() as conn:
        row = await fetch_alert(conn, alertId)

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("ALERT_NOT_FOUND", f"Andon alert {alertId!r} not found"),
        )

    pre_state = json.dumps(
        {"alert_id": alertId, "status": row["status"]}, sort_keys=True
    )

    async with pool.acquire() as conn:
        try:
            acknowledged_at = await acknowledge_alert(conn, alertId)
        except AlreadyAcknowledgedError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_error(
                    "ALREADY_ACKNOWLEDGED",
                    "Alert is already in a terminal state",
                ),
            ) from exc

    post_state = json.dumps(
        {"alert_id": alertId, "status": "acknowledged"}, sort_keys=True
    )

    # Fire immutable audit write within 1 second
    fire_audit_write(
        writer=audit_writer,
        user_id=user_id,
        action_type="MAINTENANCE_DISPATCH",
        entity_id=alertId,
        before_hash=sha256_hex(pre_state),
        after_hash=sha256_hex(post_state),
    )

    event_id = str(uuid.uuid4())
    now_iso = acknowledged_at.isoformat()

    # Publish MaintenanceDispatchEvent
    dispatch_payload = {
        "eventId": event_id,
        "eventType": "maintenance_dispatch",
        "userId": user_id,
        "stationId": row["station_id"],
        "alertId": alertId,
        "lineId": row["line_id"],
        "timestamp": now_iso,
        "disposition": body.disposition,
        "source": "line-command",
    }
    # Publish AdoptionEvent — sessionId is the JWT token ID (jti claim) per design decision
    session_id = str(jwt_payload.get("jti") or uuid.uuid4())
    adoption_payload = {
        "eventId": event_id,
        "eventType": "maintenance_dispatch",
        "actionType": "maintenance_dispatch",
        "userId": user_id,
        "sessionId": session_id,
        "stationId": row["station_id"],
        "alertId": alertId,
        "lineId": row["line_id"],
        "timestamp": now_iso,
        "source": "line-command",
    }

    try:
        await publish(TOPIC_MAINTENANCE_DISPATCH, dispatch_payload)
        await publish(TOPIC_ADOPTION_EVENT, adoption_payload)
    except Exception as kafka_exc:
        logger.warning(
            "Kafka publish failed for alertId=%s corr=%s: %s",
            alertId, event_id, kafka_exc,
        )

    response = AlertAcknowledgeResponse(
        alertId=row["alert_id"],
        status="acknowledged",
        acknowledgedAt=acknowledged_at,
        eventId=uuid.UUID(event_id),
    )
    return JSONResponse(
        status_code=200,
        content=json.loads(response.model_dump_json()),
    )
