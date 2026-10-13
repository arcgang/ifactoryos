"""Andon alert endpoints: list, get, acknowledge, and line-command alias."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse

from app.auth import caller_subject, require_bearer, require_shift_lead
from app.cursor import decode_cursor, encode_cursor
from app.db import get_pool
from app.kafka_producer import TOPIC_ADOPTION_EVENT, publish
from app.models import (
    AlertAcknowledgeRequest,
    AlertAcknowledgeResponse,
    AndonAlert,
    AndonAlertListResponse,
    CursorPage,
    ErrorResponse,
    ShiftHandover,
    ShiftHandoverListResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Andon Alerts"])

_VALID_ALERT_STATUSES = {"open", "acknowledged", "resolved", "false_positive"}
_VALID_LC_STATUSES = {"open", "resolved", "escalated", "monitoring"}


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
            resolved_at
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
    alertId: UUID = Path(...),
    _jwt: dict[str, Any] = Depends(require_bearer),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    alert_id, opcenter_event_id, station_id, line_id, alert_type,
                    predicted_failure_type, minutes_to_predicted_failure,
                    recommended_action, status, detected_at, acknowledged_at,
                    resolved_at
                FROM andon_alert
                WHERE alert_id = $1
                """,
                alertId,
            )
    except Exception as exc:
        logger.exception("Failed to fetch andon_alert %s: %s", alertId, exc)
        return JSONResponse(
            status_code=500,
            content=_error("QUERY_FAILED", "Failed to fetch Andon alert"),
        )

    if row is None:
        return JSONResponse(
            status_code=404,
            content=_error("ALERT_NOT_FOUND", f"Andon alert {alertId} not found"),
        )

    alert = _row_to_alert(row)
    return JSONResponse(status_code=200, content=alert.model_dump(mode="json"))


@router.post(
    "/andon/alerts/{alertId}/acknowledge",
    operation_id="acknowledgeAndonAlert",
    summary="Acknowledge a predictive Andon alert and record disposition",
    responses={
        200: {"model": AlertAcknowledgeResponse},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def acknowledge_andon_alert(
    alertId: UUID = Path(...),
    body: AlertAcknowledgeRequest = ...,
    jwt_payload: dict[str, Any] = Depends(require_shift_lead),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
    now = datetime.now(timezone.utc)
    caller = caller_subject(jwt_payload)

    early_response: JSONResponse | None = None
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT alert_id, status
                    FROM andon_alert
                    WHERE alert_id = $1
                    FOR UPDATE
                    """,
                    alertId,
                )

                if row is None:
                    early_response = JSONResponse(
                        status_code=404,
                        content=_error("ALERT_NOT_FOUND", f"Andon alert {alertId} not found"),
                    )
                else:
                    current_status: str = row["status"]
                    terminal_statuses = {"acknowledged", "resolved", "false_positive"}
                    if current_status in terminal_statuses:
                        early_response = JSONResponse(
                            status_code=409,
                            content=_error(
                                "ALERT_ALREADY_ACKNOWLEDGED",
                                f"Alert is already in terminal state: {current_status}",
                            ),
                        )
                    else:
                        await conn.execute(
                            """
                            UPDATE andon_alert
                            SET status = 'acknowledged', acknowledged_at = $1, updated_at = $1
                            WHERE alert_id = $2
                            """,
                            now,
                            alertId,
                        )
    except Exception as exc:
        logger.exception("Failed to acknowledge andon_alert %s: %s", alertId, exc)
        return JSONResponse(
            status_code=500,
            content=_error("UPDATE_FAILED", "Failed to acknowledge Andon alert"),
        )

    if early_response is not None:
        return early_response

    event_id = uuid.uuid4()
    adoption_payload = {
        "eventId": str(event_id),
        "eventType": "ALERT_ACKNOWLEDGED",
        "alertId": str(alertId),
        "disposition": body.disposition.value,
        "note": body.note,
        "acknowledgedBy": caller,
        "occurredAt": now.isoformat(),
    }
    try:
        await publish(TOPIC_ADOPTION_EVENT, adoption_payload)
    except Exception as kafka_exc:
        logger.warning("Kafka publish failed for ALERT_ACKNOWLEDGED alertId=%s: %s", alertId, kafka_exc)

    response = AlertAcknowledgeResponse(
        alertId=alertId,
        status="acknowledged",
        acknowledgedAt=now.isoformat(),
        eventId=event_id,
    )
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


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
    # line-command uses 'escalated'/'monitoring' but DB only stores 'acknowledged'.
    # For the alias endpoint we pass status through as-is; if status is one of the
    # line-command-specific values (escalated/monitoring) we translate to the DB column.
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


@router.get(
    "/api/v1/handovers",
    operation_id="listHandovers",
    summary="List recent shift handovers for Command Center",
    responses={
        200: {"model": ShiftHandoverListResponse},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def list_handovers(
    lineId: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    _jwt: dict[str, Any] = Depends(require_bearer),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
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

    if lineId is not None:
        wheres.append(f"line_id = ${idx}")
        params.append(lineId)
        idx += 1

    if cursor_ts is not None and cursor_id is not None:
        wheres.append(f"(shift_end_ts, id) < (${idx}, ${idx + 1})")
        params.append(cursor_ts)
        params.append(cursor_id)
        idx += 2

    where_clause = "WHERE " + " AND ".join(wheres) if wheres else ""
    fetch_limit = limit + 1
    params.append(fetch_limit)

    query = f"""
        SELECT
            id, shift_id, line_id, shift_start_ts, shift_end_ts,
            submitted_by_user_id, alert_dispositions, oee_delta_pct,
            held_vins, notes, idempotency_key, created_at, updated_at
        FROM shift_handover
        {where_clause}
        ORDER BY shift_end_ts DESC, id DESC
        LIMIT ${idx}
    """

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
    except Exception as exc:
        logger.exception("Failed to query shift_handover: %s", exc)
        return JSONResponse(
            status_code=500,
            content=_error("QUERY_FAILED", "Failed to query shift handovers"),
        )

    has_more = len(rows) > limit
    page_rows = rows[:limit]

    items: list[ShiftHandover] = []
    for row in page_rows:
        items.append(
            ShiftHandover(
                id=row["id"],
                shift_id=row["shift_id"],
                line_id=row["line_id"],
                shift_start_ts=row["shift_start_ts"].astimezone(timezone.utc).isoformat(),
                shift_end_ts=row["shift_end_ts"].astimezone(timezone.utc).isoformat(),
                submitted_by_user_id=row["submitted_by_user_id"],
                alert_dispositions=list(row["alert_dispositions"]),
                oee_delta_pct=float(row["oee_delta_pct"]),
                held_vins=list(row["held_vins"]),
                notes=row["notes"],
                idempotency_key=row["idempotency_key"],
                created_at=row["created_at"].astimezone(timezone.utc).isoformat(),
                updated_at=row["updated_at"].astimezone(timezone.utc).isoformat(),
            )
        )

    next_cursor: str | None = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor(last["shift_end_ts"], str(last["id"]))

    response = ShiftHandoverListResponse(
        data=items,
        page=CursorPage(next_cursor=next_cursor, limit=limit),
    )
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
