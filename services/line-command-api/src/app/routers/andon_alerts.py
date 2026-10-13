"""Andon alert endpoints: list, get, acknowledge, and line-command alias."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse

from app.andon_service import AlreadyAcknowledgedError, acknowledge_alert, fetch_alert, fetch_weld_trace
from app.audit import AuditStore, AuditWriterProtocol, fire_audit_write, sha256_hex
from app.auth import caller_subject, require_bearer, require_shift_lead
from app.config import settings
from app.cursor import decode_cursor, encode_cursor
from app.db import get_pool, get_ts_pool
from app.kafka_producer import TOPIC_ADOPTION_EVENT, TOPIC_MAINTENANCE_DISPATCH, publish
from app.models import (
    AlertAcknowledgeRequest,
    AlertAcknowledgeResponse,
    AndonAlert,
    AndonAlertListResponse,
    CursorPage,
    ErrorResponse,
    WeldTrace,
    WeldTracePoint,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Andon Alerts"])

_process_audit_writer: AuditWriterProtocol = AuditStore()

_VALID_ALERT_STATUSES = {"open", "acknowledged", "resolved", "false_positive"}
_VALID_LC_STATUSES = {"open", "resolved", "escalated", "monitoring"}


def get_audit_writer() -> AuditWriterProtocol:
    return _process_audit_writer


def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


def _row_to_alert(row: Any) -> AndonAlert:
    detected_at: datetime = row["detected_at"]
    resolved_at: datetime | None = row["resolved_at"]
    acknowledged_at: datetime | None = row["acknowledged_at"]
    return AndonAlert(
        alertId=row["alert_id"],
        stationId=row["station_id"],
        lineId=row["line_id"],
        alertType=row["alert_type"],
        predictedFailureType=row["predicted_failure_type"],
        minutesToPredictedFailure=row["minutes_to_predicted_failure"],
        recommendedAction=row["recommended_action"],
        status=row["status"],
        detectedAt=detected_at.astimezone(timezone.utc).isoformat(),
        resolvedAt=resolved_at.astimezone(timezone.utc).isoformat() if resolved_at else None,
        acknowledgedAt=acknowledged_at.astimezone(timezone.utc).isoformat() if acknowledged_at else None,
        opcenterEventId=row["opcenter_event_id"],
        # list endpoint does not include weld trace; these are populated by get_andon_alert
        receivedAt=detected_at.astimezone(timezone.utc).isoformat(),
        correlationId=row.get("correlation_id") or uuid.uuid4(),
        weldTrace=WeldTrace(weld_current_a=[], weld_voltage_v=[]),
        thresholdCrossingAt=detected_at.astimezone(timezone.utc).isoformat(),
    )


async def _query_alerts(
    pool: Any,
    *,
    status_filter: str | None,
    line_id: str | None,
    station_id: str | None,
    cursor: str | None,
    limit: int,
) -> JSONResponse | list[Any]:
    cursor_ts: datetime | None = None
    cursor_id: str | None = None
    if cursor is not None:
        try:
            cursor_ts, cursor_id = decode_cursor(cursor)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content=_error("CURSOR_INVALID", "Invalid pagination cursor"),
            )

    params: list[Any] = []
    wheres: list[str] = []
    idx = 1

    if status_filter is not None:
        wheres.append(f"status = ${idx}")
        params.append(status_filter)
        idx += 1

    if line_id is not None:
        wheres.append(f"line_id = ${idx}")
        params.append(line_id)
        idx += 1

    if station_id is not None:
        wheres.append(f"station_id = ${idx}")
        params.append(station_id)
        idx += 1

    if cursor_ts is not None and cursor_id is not None:
        wheres.append(f"(detected_at, alert_id) < (${idx}, ${idx + 1})")
        params.append(cursor_ts)
        params.append(cursor_id)
        idx += 2

    where_clause = "WHERE " + " AND ".join(wheres) if wheres else ""
    fetch_limit = limit + 1
    params.append(fetch_limit)

    query = f"""
        SELECT
            alert_id, opcenter_event_id, station_id, line_id, alert_type,
            predicted_failure_type, minutes_to_predicted_failure,
            recommended_action, status, detected_at, acknowledged_at,
            resolved_at, correlation_id
        FROM andon_alert
        {where_clause}
        ORDER BY minutes_to_predicted_failure ASC NULLS LAST, detected_at DESC, alert_id DESC
        LIMIT ${idx}
    """

    try:
        async with pool.acquire() as conn:
            return await conn.fetch(query, *params)
    except Exception as exc:
        logger.exception("Failed to query andon_alert: %s", exc)
        return JSONResponse(
            status_code=500,
            content=_error("QUERY_FAILED", "Failed to query Andon alerts"),
        )


@router.get(
    "/andon/alerts",
    operation_id="listAndonAlerts",
    summary="List Andon alerts received from OpCenter",
    responses={
        200: {"model": AndonAlertListResponse},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def list_andon_alerts(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None),
    lineId: str | None = Query(default=None),
    stationId: str | None = Query(default=None),
    _jwt: dict[str, Any] = Depends(require_bearer),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
    if status is not None and status not in _VALID_ALERT_STATUSES:
        return JSONResponse(
            status_code=400,
            content=_error("STATUS_INVALID", f"Invalid status value: {status!r}"),
        )

    result = await _query_alerts(
        pool,
        status_filter=status,
        line_id=lineId,
        station_id=stationId,
        cursor=cursor,
        limit=limit,
    )
    if isinstance(result, JSONResponse):
        return result

    rows = result
    has_more = len(rows) > limit
    page_rows = rows[:limit]

    items = [_row_to_alert(row) for row in page_rows]

    next_cursor: str | None = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor(last["detected_at"], str(last["alert_id"]))

    response = AndonAlertListResponse(
        data=items,
        page=CursorPage(next_cursor=next_cursor, limit=limit),
    )
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


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


@router.post(
    "/andon/alerts/{alertId}/acknowledge",
    operation_id="acknowledgeAndonAlert",
    summary="Acknowledge an Andon alert (shift-lead only)",
    responses={
        200: {"model": AlertAcknowledgeResponse},
        400: {"model": ErrorResponse},
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
    jwt_payload: dict[str, Any] = Depends(require_shift_lead),
    pool: Any = Depends(get_pool),
    audit_writer: AuditWriterProtocol = Depends(get_audit_writer),
) -> JSONResponse:
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


@router.get(
    "/line-command/andon/alerts",
    operation_id="listLineCommandAndonAlerts",
    summary="List open predictive Andon alerts for the current shift (paginated)",
    responses={
        200: {"model": AndonAlertListResponse},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def list_line_command_andon_alerts(
    shiftId: str | None = Query(default=None),
    status: str | None = Query(default=None),
    lineId: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _jwt: dict[str, Any] = Depends(require_bearer),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
    if status is not None and status not in _VALID_LC_STATUSES:
        return JSONResponse(
            status_code=400,
            content=_error("STATUS_INVALID", f"Invalid status value: {status!r}"),
        )

    # Map line-command status values back to andon_alert DB values where they differ.
    db_status = status
    if status in ("escalated", "monitoring"):
        db_status = "acknowledged"

    result = await _query_alerts(
        pool,
        status_filter=db_status,
        line_id=lineId,
        station_id=None,
        cursor=cursor,
        limit=limit,
    )
    if isinstance(result, JSONResponse):
        return result

    rows = result
    has_more = len(rows) > limit
    page_rows = rows[:limit]

    items = [_row_to_alert(row) for row in page_rows]

    next_cursor: str | None = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor(last["detected_at"], str(last["alert_id"]))

    response = AndonAlertListResponse(
        data=items,
        page=CursorPage(next_cursor=next_cursor, limit=limit),
    )
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
