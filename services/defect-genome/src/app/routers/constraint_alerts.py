"""GET /alerts, GET /alerts/{alertId}, POST /alerts/{alertId}/acknowledge.

Implements operationIds: listAlerts, getClusterAlert (via /alerts path), acknowledgeAlert.
The /alerts prefix serves constraint-style alerts returning ConstraintAlert on acknowledge.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.alerts_service import build_alert_list_response, decode_cursor, _row_to_alert
from app.auth import require_bearer
from app.routers.alerts import get_alert_store

router = APIRouter(tags=["Alerts"])


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------

def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


# ---------------------------------------------------------------------------
# GET /alerts
# ---------------------------------------------------------------------------

@router.get(
    "/alerts",
    operation_id="listAlerts",
    summary="List active and recent constraint breach alerts (cursor-paginated)",
    status_code=200,
    responses={
        200: {},
        401: {},
    },
)
async def list_alerts(
    status_filter: str = Query(
        default="active",
        alias="status",
        pattern="^(active|acknowledged|resolved|all)$",
    ),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    jwt_payload: dict[str, Any] = Depends(require_bearer),
    alert_store: list[dict[str, Any]] = Depends(get_alert_store),
) -> JSONResponse:
    cursor_id: str | None = None
    if cursor is not None:
        cursor_id = decode_cursor(cursor)
        if cursor_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_error("INVALID_CURSOR", "cursor parameter is malformed"),
            )

    # Map query param enum values to internal alert status values.
    # "active" => "new", "all" => no filter, others pass through.
    if status_filter == "all":
        effective_filter: str | None = None
    elif status_filter == "active":
        effective_filter = "new"
    else:
        effective_filter = status_filter
    rows = _filter_alerts_by_status(alert_store, status_filter=effective_filter, cursor_id=cursor_id)
    body = build_alert_list_response(rows, limit=limit)
    return JSONResponse(status_code=200, content=body)


def _filter_alerts_by_status(
    store: list[dict[str, Any]],
    *,
    status_filter: str | None,
    cursor_id: str | None,
) -> list[dict[str, Any]]:
    result = list(store)
    if status_filter is not None:
        result = [r for r in result if r["status"] == status_filter]
    if cursor_id is not None:
        ids = [str(r["alert_id"]) for r in result]
        try:
            idx = ids.index(cursor_id)
            result = result[idx + 1 :]
        except ValueError:
            result = []
    return result


# ---------------------------------------------------------------------------
# GET /alerts/{alertId}
# ---------------------------------------------------------------------------

@router.get(
    "/alerts/{alertId}",
    operation_id="getAlert",
    summary="Get a specific cluster alert by ID",
    status_code=200,
    responses={
        200: {},
        401: {},
        404: {},
        500: {},
    },
)
async def get_alert(
    alertId: str,
    jwt_payload: dict[str, Any] = Depends(require_bearer),
    alert_store: list[dict[str, Any]] = Depends(get_alert_store),
) -> JSONResponse:
    try:
        alert_uuid = str(uuid.UUID(alertId))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_ALERT_ID", "alertId must be a valid UUID"),
        )

    alert = _find_alert(alert_store, alert_uuid)
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("ALERT_NOT_FOUND", f"No alert found with id {alertId}"),
        )

    return JSONResponse(status_code=200, content=_row_to_alert(alert))


# ---------------------------------------------------------------------------
# POST /alerts/{alertId}/acknowledge
# ---------------------------------------------------------------------------

@router.post(
    "/alerts/{alertId}/acknowledge",
    operation_id="acknowledgeAlert",
    summary="Acknowledge a constraint breach alert",
    status_code=200,
    responses={
        200: {},
        401: {},
        404: {},
    },
)
async def acknowledge_alert(
    alertId: str,
    jwt_payload: dict[str, Any] = Depends(require_bearer),
    alert_store: list[dict[str, Any]] = Depends(get_alert_store),
) -> JSONResponse:
    try:
        alert_uuid = str(uuid.UUID(alertId))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_ALERT_ID", "alertId must be a valid UUID"),
        )

    alert = _find_alert(alert_store, alert_uuid)
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("ALERT_NOT_FOUND", f"No alert found with id {alertId}"),
        )

    now = datetime.now(timezone.utc).isoformat()
    if alert["status"] not in ("acknowledged", "in_progress", "resolved"):
        alert["status"] = "acknowledged"
        alert["acknowledged_at"] = now
        alert["updated_at"] = now

    # Return ConstraintAlert shape (camelCase, sequence-optimizer style)
    return JSONResponse(status_code=200, content=_row_to_constraint_alert(alert))


def _row_to_constraint_alert(row: dict[str, Any]) -> dict[str, Any]:
    def _iso(v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            return v.isoformat()
        return str(v)

    status_val = row.get("status", "active")
    constraint_status = "acknowledged" if status_val in ("acknowledged", "in_progress") else (
        "resolved" if status_val == "resolved" else "active"
    )

    return {
        "alertId": str(row["alert_id"]),
        "constraintType": row.get("constraint_type", "paint_capacity"),
        "currentValue": float(row.get("current_value", 0.0)),
        "thresholdValue": float(row.get("threshold_value", 0.0)),
        "affectedVins": row.get("affected_vins", []),
        "status": constraint_status,
        "detectedAt": _iso(row.get("triggered_at") or row.get("detected_at")),
        "acknowledgedAt": _iso(row.get("acknowledged_at")),
        "acknowledgedBy": row.get("acknowledged_by_user_id"),
    }


def _find_alert(
    store: list[dict[str, Any]], alert_id: str
) -> dict[str, Any] | None:
    for row in store:
        if str(row["alert_id"]) == alert_id:
            return row
    return None
