"""Shift handover submission and retrieval endpoints.

POST /handover — submit a structured digital shift handover (US-009)
GET  /handovers — list paginated handover records
GET  /handovers/{handoverId} — retrieve a single handover record

Implements AC-e37bcf34, AC-5f4b73cb, AC-04cd3711, AC-fd1f00c6.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse

from app.audit import NULL_HASH, get_audit_writer, sha256_hex, write_audit_sync
from app.auth import caller_subject, require_bearer, require_shift_lead
from app.cursor import decode_cursor, encode_cursor
from app.db import get_pool
from app.kafka_producer import TOPIC_ADOPTION_EVENT, TOPIC_HANDOVER_SUBMITTED, publish
from app.models import (
    AlertDispositionItem,
    CursorPage,
    ErrorResponse,
    HandoverListResponse,
    HandoverRecord,
    HandoverRequest,
    HandoverResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Shift Handover"])


def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


def _row_to_record(row: Any) -> HandoverRecord:
    dispositions_raw = row["alert_dispositions"]
    if isinstance(dispositions_raw, str):
        dispositions_raw = json.loads(dispositions_raw)
    dispositions = [AlertDispositionItem(**d) for d in (dispositions_raw or [])]

    held_vins = list(row["held_vins"]) if row["held_vins"] else []
    created_at: datetime = row["created_at"]
    updated_at: datetime | None = row["updated_at"]

    return HandoverRecord(
        handoverId=row["id"],
        shiftId=row["shift_id"],
        lineId=row["line_id"],
        shiftStartTs=row["shift_start_ts"].astimezone(timezone.utc).isoformat(),
        shiftEndTs=row["shift_end_ts"].astimezone(timezone.utc).isoformat(),
        submittedByUserId=row["submitted_by_user_id"],
        alertDispositions=dispositions,
        oeeDeltaPct=float(row["oee_delta_pct"]),
        heldVins=held_vins,
        notes=row["notes"],
        createdAt=created_at.astimezone(timezone.utc).isoformat(),
        updatedAt=updated_at.astimezone(timezone.utc).isoformat() if updated_at else None,
    )


@router.post(
    "/handover",
    operation_id="submitHandover",
    summary="Submit a structured digital shift handover",
    status_code=201,
    responses={
        201: {"model": HandoverResponse},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def submit_handover(
    body: HandoverRequest,
    jwt_payload: dict[str, Any] = Depends(require_shift_lead),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
    caller = caller_subject(jwt_payload)
    roles: list[str] = jwt_payload.get("roles", []) or []
    caller_role = roles[0] if roles else "unknown"
    now = datetime.now(timezone.utc)
    handover_id = uuid.uuid4()
    idempotency_key = body.idempotencyKey or uuid.uuid4()

    try:
        shift_start = datetime.fromisoformat(body.shiftStartTs)
        shift_end = datetime.fromisoformat(body.shiftEndTs)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content=_error("INVALID_TIMESTAMP", f"Invalid timestamp format: {exc}"),
        )

    if shift_start.tzinfo is None:
        shift_start = shift_start.replace(tzinfo=timezone.utc)
    if shift_end.tzinfo is None:
        shift_end = shift_end.replace(tzinfo=timezone.utc)

    dispositions_json = json.dumps(
        [
            {
                "alertId": str(d.alertId),
                "disposition": d.disposition.value,
                "note": d.note,
            }
            for d in body.alertDispositions
        ]
    )

    after_payload = {
        "handoverId": str(handover_id),
        "shiftId": str(body.shiftId),
        "lineId": body.lineId,
        "submittedByUserId": caller,
        "submittedAt": now.isoformat(),
        "oeeDeltaPct": body.oeeDeltaPct,
        "heldVins": body.heldVins,
        "alertDispositions": [
            {"alertId": str(d.alertId), "disposition": d.disposition.value, "note": d.note}
            for d in body.alertDispositions
        ],
        "notes": body.notes,
    }
    after_hash = sha256_hex(json.dumps(after_payload, sort_keys=True))

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    """
                    SELECT id
                    FROM shift_handover
                    WHERE idempotency_key = $1
                    """,
                    idempotency_key,
                )
                if existing is not None:
                    return JSONResponse(
                        status_code=409,
                        content=_error(
                            "HANDOVER_ALREADY_SUBMITTED",
                            "Handover already submitted for this shift (idempotency key conflict)",
                        ),
                    )

                await conn.execute(
                    """
                    INSERT INTO shift_handover (
                        id, shift_id, line_id, shift_start_ts, shift_end_ts,
                        submitted_by_user_id, alert_dispositions, oee_delta_pct,
                        held_vins, notes, idempotency_key, created_at, updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7::jsonb, $8,
                        $9, $10, $11, $12, $12
                    )
                    """,
                    handover_id,
                    body.shiftId,
                    body.lineId,
                    shift_start,
                    shift_end,
                    caller,
                    dispositions_json,
                    body.oeeDeltaPct,
                    body.heldVins,
                    body.notes,
                    idempotency_key,
                    now,
                )

                for disp in body.alertDispositions:
                    db_status = "resolved" if disp.disposition.value == "resolved" else "acknowledged"
                    await conn.execute(
                        """
                        UPDATE andon_alert
                        SET status = $1,
                            acknowledged_at = CASE
                                WHEN acknowledged_at IS NULL THEN $2
                                ELSE acknowledged_at
                            END,
                            resolved_at = CASE
                                WHEN $1 = 'resolved' THEN $2
                                ELSE resolved_at
                            END,
                            updated_at = $2
                        WHERE alert_id = $3
                        """,
                        db_status,
                        now,
                        disp.alertId,
                    )

                write_audit_sync(
                    writer=get_audit_writer(),
                    user_id=caller,
                    action_type="HANDOVER_SUBMITTED",
                    entity_id=str(handover_id),
                    before_hash=NULL_HASH,
                    after_hash=after_hash,
                )

    except Exception as exc:
        logger.exception("Failed to submit handover %s: %s", handover_id, exc)
        return JSONResponse(
            status_code=500,
            content=_error("INSERT_FAILED", "Failed to submit handover"),
        )

    asyncio.create_task(
        _publish_handover_events(
            handover_id=str(handover_id),
            shift_id=str(body.shiftId),
            line_id=body.lineId,
            shift_start_ts=shift_start.isoformat(),
            shift_end_ts=shift_end.isoformat(),
            user_id=caller,
            caller_role=caller_role,
            oee_delta_pct=body.oeeDeltaPct,
            submitted_at=now.isoformat(),
        )
    )

    response = HandoverResponse(
        handoverId=handover_id,
        submittedAt=now.isoformat(),
        submittedByUserId=caller,
    )
    return JSONResponse(status_code=201, content=response.model_dump(mode="json"))


async def _publish_handover_events(
    handover_id: str,
    shift_id: str,
    line_id: str,
    shift_start_ts: str,
    shift_end_ts: str,
    user_id: str,
    caller_role: str,
    oee_delta_pct: float,
    submitted_at: str,
) -> None:
    try:
        await publish(
            TOPIC_HANDOVER_SUBMITTED,
            {
                "eventId": str(uuid.uuid4()),
                "eventType": "HANDOVER_SUBMITTED",
                "handoverId": handover_id,
                "shiftId": shift_id,
                "lineId": line_id,
                "submittedByUserId": user_id,
                "oeeDeltaPct": oee_delta_pct,
                "shiftStartTs": shift_start_ts,
                "shiftEndTs": shift_end_ts,
                "submittedAt": submitted_at,
            },
        )
    except Exception as exc:
        logger.warning("HandoverSubmittedEvent publish failed for handoverId=%s: %s", handover_id, exc)

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        await asyncio.wait_for(
            publish(
                TOPIC_ADOPTION_EVENT,
                {
                    "eventId": str(uuid.uuid4()),
                    "eventType": "HANDOVER_SUBMITTED",
                    "userId": user_id,
                    "role": caller_role,
                    "actionType": "HANDOVER_SUBMITTED",
                    "stationId": line_id,
                    "handoverId": handover_id,
                    "occurredAt": now_iso,
                },
            ),
            timeout=1.0,
        )
    except (OSError, asyncio.TimeoutError, RuntimeError) as exc:
        logger.warning("AdoptionEvent publish failed for handoverId=%s: %s", handover_id, exc)


@router.get(
    "/handovers",
    operation_id="listHandovers",
    summary="List shift handover records (paginated)",
    responses={
        200: {"model": HandoverListResponse},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def list_handovers(
    lineId: str | None = Query(default=None),
    fromTs: str | None = Query(default=None),
    toTs: str | None = Query(default=None),
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

    from_dt: datetime | None = None
    to_dt: datetime | None = None
    if fromTs is not None:
        try:
            from_dt = datetime.fromisoformat(fromTs)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content=_error("INVALID_TIMESTAMP", f"Invalid fromTs: {fromTs!r}"),
            )
    if toTs is not None:
        try:
            to_dt = datetime.fromisoformat(toTs)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content=_error("INVALID_TIMESTAMP", f"Invalid toTs: {toTs!r}"),
            )

    params: list[Any] = []
    wheres: list[str] = []
    idx = 1

    if lineId is not None:
        wheres.append(f"line_id = ${idx}")
        params.append(lineId)
        idx += 1

    if from_dt is not None:
        wheres.append(f"shift_end_ts >= ${idx}")
        params.append(from_dt)
        idx += 1

    if to_dt is not None:
        wheres.append(f"shift_end_ts <= ${idx}")
        params.append(to_dt)
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

    items = [_row_to_record(row) for row in page_rows]

    next_cursor: str | None = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor(last["shift_end_ts"], str(last["id"]))

    response = HandoverListResponse(
        data=items,
        page=CursorPage(next_cursor=next_cursor, limit=limit),
    )
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


@router.get(
    "/handovers/{handoverId}",
    operation_id="getHandover",
    summary="Retrieve a single shift handover record by ID",
    responses={
        200: {"model": HandoverRecord},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def get_handover(
    handoverId: UUID = Path(...),
    _jwt: dict[str, Any] = Depends(require_bearer),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    id, shift_id, line_id, shift_start_ts, shift_end_ts,
                    submitted_by_user_id, alert_dispositions, oee_delta_pct,
                    held_vins, notes, idempotency_key, created_at, updated_at
                FROM shift_handover
                WHERE id = $1
                """,
                handoverId,
            )
    except Exception as exc:
        logger.exception("Failed to fetch shift_handover %s: %s", handoverId, exc)
        return JSONResponse(
            status_code=500,
            content=_error("QUERY_FAILED", "Failed to fetch handover"),
        )

    if row is None:
        return JSONResponse(
            status_code=404,
            content=_error("HANDOVER_NOT_FOUND", f"Handover {handoverId} not found"),
        )

    record = _row_to_record(row)
    return JSONResponse(status_code=200, content=record.model_dump(mode="json"))
