"""GET /defect-genome/alerts, GET /defect-genome/alerts/{alertId}, and POST /defect-genome/alerts/{alertId}/acknowledge.

Implements operationIds: listClusterAlerts, getClusterAlert, acknowledgeClusterAlert.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.alerts_service import build_alert_list_response, decode_cursor, _row_to_alert
from app.auth import require_bearer

router = APIRouter(tags=["Defect Genome Alerts"])

# ---------------------------------------------------------------------------
# Shared in-memory alert store — replaced by real DB in production.
# Mutable state is encapsulated in a module-level list seeded at import time;
# tests replace _ALERTS via the dependency-override pattern.
# ---------------------------------------------------------------------------

_ALERTS: list[dict[str, Any]] = []


def get_alert_store() -> list[dict[str, Any]]:
    return _ALERTS


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class AcknowledgeAlertRequest(BaseModel):
    acknowledged_by_user_id: str
    notes: str | None = None
    corrective_action_jira_id: str | None = None


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------

def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


# ---------------------------------------------------------------------------
# GET /defect-genome/alerts
# ---------------------------------------------------------------------------

@router.get(
    "/defect-genome/alerts",
    operation_id="listClusterAlerts",
    summary="List cluster alerts for the current user's Quality Studio session",
    status_code=200,
    responses={
        200: {},
        401: {},
        500: {},
    },
)
async def list_cluster_alerts(
    status_filter: str | None = Query(
        default=None,
        alias="status",
        pattern="^(new|acknowledged|in_progress|resolved)$",
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

    rows = _filter_alerts(alert_store, status_filter=status_filter, cursor_id=cursor_id)
    body = build_alert_list_response(rows, limit=limit)
    return JSONResponse(status_code=200, content=body)


def _filter_alerts(
    store: list[dict[str, Any]],
    *,
    status_filter: str | None,
    cursor_id: str | None,
) -> list[dict[str, Any]]:
    result = store
    if status_filter is not None:
        result = [r for r in result if r["status"] == status_filter]
    if cursor_id is not None:
        # Cursor is the alert_id of the last seen row — return everything after it
        # in insertion order (stable sort: newest first by triggered_at).
        ids = [str(r["alert_id"]) for r in result]
        try:
            idx = ids.index(cursor_id)
            result = result[idx + 1 :]
        except ValueError:
            result = []
    return result


# ---------------------------------------------------------------------------
# GET /defect-genome/alerts/{alertId}
# ---------------------------------------------------------------------------

@router.get(
    "/defect-genome/alerts/{alertId}",
    operation_id="getClusterAlert",
    summary="Get a specific cluster alert by ID",
    status_code=200,
    responses={
        200: {},
        401: {},
        404: {},
        500: {},
    },
)
async def get_cluster_alert(
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
# POST /defect-genome/alerts/{alertId}/acknowledge
# ---------------------------------------------------------------------------

@router.post(
    "/defect-genome/alerts/{alertId}/acknowledge",
    operation_id="acknowledgeClusterAlert",
    summary="Acknowledge a cluster alert and optionally assign a corrective action",
    status_code=200,
    responses={
        200: {},
        400: {},
        401: {},
        404: {},
        409: {},
        500: {},
    },
)
async def acknowledge_cluster_alert(
    alertId: str,
    body: AcknowledgeAlertRequest,
    jwt_payload: dict[str, Any] = Depends(require_bearer),
    alert_store: list[dict[str, Any]] = Depends(get_alert_store),
) -> JSONResponse:
    # Validate alertId is a UUID
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

    if alert["status"] in ("acknowledged", "in_progress", "resolved"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_error(
                "ALREADY_ACKNOWLEDGED",
                "Alert has already been acknowledged",
            ),
        )

    now = datetime.now(timezone.utc).isoformat()
    alert["status"] = "in_progress" if body.corrective_action_jira_id else "acknowledged"
    alert["acknowledged_at"] = now
    alert["updated_at"] = now
    alert["acknowledged_by_user_id"] = body.acknowledged_by_user_id
    if body.corrective_action_jira_id:
        alert["corrective_action_jira_id"] = body.corrective_action_jira_id

    return JSONResponse(status_code=200, content=_row_to_alert(alert))


def _find_alert(
    store: list[dict[str, Any]], alert_id: str
) -> dict[str, Any] | None:
    for row in store:
        if str(row["alert_id"]) == alert_id:
            return row
    return None
