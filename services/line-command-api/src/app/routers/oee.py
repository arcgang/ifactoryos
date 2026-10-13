"""GET /oee/snapshots — latest OEE snapshot per weld line from opcenter-sync-service DB."""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.auth import require_bearer
from app.cursor import decode_cursor, encode_cursor
from app.db import get_pool
from app.models import CursorPage, ErrorResponse, OeeSnapshot, OeeSnapshotListResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["OEE"])

_STALE_THRESHOLD_SECONDS = 30


def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


@router.get(
    "/oee/snapshots",
    operation_id="listOeeSnapshots",
    summary="List latest OEE snapshots for all weld lines",
    responses={
        200: {"model": OeeSnapshotListResponse},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def list_oee_snapshots(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    lineId: str | None = Query(default=None),
    maxAgeSeconds: int | None = Query(default=None, ge=1),
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
        wheres.append(f"s.line_id = ${idx}")
        params.append(lineId)
        idx += 1

    if maxAgeSeconds is not None:
        wheres.append(f"s.received_at >= NOW() - (${idx} * INTERVAL '1 second')")
        params.append(maxAgeSeconds)
        idx += 1

    if cursor_ts is not None and cursor_id is not None:
        wheres.append(
            f"(s.created_at, s.oee_snapshot_id) < (${idx}, ${idx + 1})"
        )
        params.append(cursor_ts)
        params.append(cursor_id)
        idx += 2

    where_clause = "WHERE " + " AND ".join(wheres) if wheres else ""
    fetch_limit = limit + 1

    query = f"""
        SELECT
            s.oee_snapshot_id,
            s.line_id,
            s.oee_percent,
            s.availability,
            s.performance,
            s.quality,
            s.measured_at,
            s.received_at,
            s.created_at
        FROM (
            SELECT DISTINCT ON (line_id)
                oee_snapshot_id, line_id, oee_percent, availability,
                performance, quality, measured_at, received_at, created_at
            FROM oee_snapshot
            ORDER BY line_id, measured_at DESC
        ) s
        {where_clause}
        ORDER BY s.created_at DESC, s.oee_snapshot_id DESC
        LIMIT ${idx}
    """
    params.append(fetch_limit)

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
    except Exception as exc:
        logger.exception("Failed to query oee_snapshot: %s", exc)
        return JSONResponse(
            status_code=500,
            content=_error("QUERY_FAILED", "Failed to query OEE snapshots"),
        )

    has_more = len(rows) > limit
    page_rows = rows[:limit]

    now = datetime.now(timezone.utc)
    items: list[OeeSnapshot] = []
    for row in page_rows:
        measured_at: datetime = row["measured_at"]
        age = math.floor((now - measured_at.astimezone(timezone.utc)).total_seconds())
        age = max(age, 0)
        items.append(
            OeeSnapshot(
                lineId=row["line_id"],
                oeePercent=float(row["oee_percent"]),
                availability=float(row["availability"]),
                performance=float(row["performance"]),
                quality=float(row["quality"]),
                measuredAt=measured_at.astimezone(timezone.utc).isoformat(),
                dataAgeSeconds=age,
                stale=age > _STALE_THRESHOLD_SECONDS,
            )
        )

    next_cursor: str | None = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor(last["created_at"], str(last["oee_snapshot_id"]))

    response = OeeSnapshotListResponse(
        data=items,
        page=CursorPage(next_cursor=next_cursor, limit=limit),
    )
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
