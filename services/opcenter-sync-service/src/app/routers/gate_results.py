"""GET /gate-results and GET /gate-results/{gateResultId} — gate result read API.

Implements operationId listGateResults and getGateResult per the LLD contract.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from opentelemetry import trace

from app.auth import require_validator_role
from app.config import settings
from app.db import get_pool
from app.models import (
    CursorPage,
    ErrorResponse,
    GateMeasurement,
    GateResult,
    GateResultListMetadata,
    GateResultListResponse,
)

_tracer = trace.get_tracer("opcenter-sync-service.gate_results_router")

router = APIRouter(tags=["Gate Results"])

_VIN_PATTERN = r"^[A-HJ-NPR-Z0-9]{17}$"
_GATE_TYPES = {"dimensional", "leak", "electrical", "nvh", "adas"}
_RESULTS = {"pass", "conditional_pass", "fail"}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


def _encode_cursor(received_at: datetime, gate_result_id: str) -> str:
    token = json.dumps({"ts": received_at.isoformat(), "id": gate_result_id})
    return base64.urlsafe_b64encode(token.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        parsed = json.loads(raw)
        ts = datetime.fromisoformat(parsed["ts"])
        gate_result_id = parsed["id"]
        return ts, gate_result_id
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, KeyError, ValueError):
        return None


def _row_to_gate_result(row: Any) -> GateResult:
    raw_measurements = row["measurements"]
    if isinstance(raw_measurements, str):
        measurements_data = json.loads(raw_measurements)
    else:
        measurements_data = raw_measurements if raw_measurements is not None else []

    measurements = [GateMeasurement(**m) for m in measurements_data]

    return GateResult(
        gateResultId=str(row["gate_result_id"]),
        vin=row["vin"],
        gateId=row["gate_id"],
        gateType=row["gate_type"],
        result=row["result"],
        measurements=measurements,
        measuredAt=row["measured_at"],
        receivedFromOpcenterAt=row["received_from_opcenter_at"],
        opcenterEventId=row["opcenter_event_id"],
    )


@router.get(
    "/gate-results",
    operation_id="listGateResults",
    summary="List gate results received from OpCenter for quality gate processing",
    response_model=GateResultListResponse,
    responses={
        200: {"model": GateResultListResponse},
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def list_gate_results(
    vin: str | None = Query(default=None, pattern=_VIN_PATTERN),
    gateType: str | None = Query(default=None),
    result: str | None = Query(default=None),
    receivedAfter: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _jwt: dict[str, Any] = Depends(require_validator_role),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
    with _tracer.start_as_current_span("http.get.gate_results") as span:
        span.set_attribute("http.method", "GET")
        span.set_attribute("http.route", "/gate-results")

        if gateType is not None and gateType not in _GATE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_error("INVALID_GATE_TYPE", f"gateType must be one of {sorted(_GATE_TYPES)}"),
            )
        if result is not None and result not in _RESULTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_error("INVALID_RESULT", f"result must be one of {sorted(_RESULTS)}"),
            )

        received_after_dt: datetime | None = None
        if receivedAfter is not None:
            try:
                received_after_dt = datetime.fromisoformat(receivedAfter)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=_error("INVALID_RECEIVED_AFTER", "receivedAfter must be ISO-8601"),
                ) from exc

        cursor_ts: datetime | None = None
        cursor_id: str | None = None
        if cursor is not None:
            decoded = _decode_cursor(cursor)
            if decoded is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=_error("INVALID_CURSOR", "cursor parameter is malformed"),
                )
            cursor_ts, cursor_id = decoded

        async with pool.acquire() as conn:
            rows = await _query_gate_results(
                conn,
                vin=vin,
                gate_type=gateType,
                result=result,
                received_after=received_after_dt,
                cursor_ts=cursor_ts,
                cursor_id=cursor_id,
                limit=limit,
            )

        records = [_row_to_gate_result(r) for r in rows[:limit]]
        next_cursor: str | None = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = _encode_cursor(last["received_from_opcenter_at"], str(last["gate_result_id"]))

        response = GateResultListResponse(
            data=records,
            page=CursorPage(next_cursor=next_cursor, limit=limit),
            metadata=GateResultListMetadata(timeoutSeconds=settings.gate_result_timeout_seconds),
        )
        return JSONResponse(
            status_code=200,
            content=json.loads(response.model_dump_json()),
        )


async def _query_gate_results(
    conn: Any,
    *,
    vin: str | None,
    gate_type: str | None,
    result: str | None,
    received_after: datetime | None,
    cursor_ts: datetime | None,
    cursor_id: str | None,
    limit: int,
) -> list[Any]:
    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if vin is not None:
        conditions.append(f"vin = ${idx}")
        params.append(vin)
        idx += 1

    if gate_type is not None:
        conditions.append(f"gate_type = ${idx}")
        params.append(gate_type)
        idx += 1

    if result is not None:
        conditions.append(f"result = ${idx}")
        params.append(result)
        idx += 1

    if received_after is not None:
        conditions.append(f"received_from_opcenter_at > ${idx}")
        params.append(received_after)
        idx += 1

    # Keyset pagination: (received_from_opcenter_at, gate_result_id) tie-break
    if cursor_ts is not None and cursor_id is not None:
        conditions.append(
            f"(received_from_opcenter_at, gate_result_id) < (${idx}, ${idx + 1}::uuid)"
        )
        params.append(cursor_ts)
        params.append(cursor_id)
        idx += 2

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit + 1)

    query = f"""
        SELECT gate_result_id, vin, gate_id, gate_type, result,
               measurements, measured_at, received_from_opcenter_at,
               opcenter_event_id
        FROM gate_result
        {where}
        ORDER BY received_from_opcenter_at DESC, gate_result_id DESC
        LIMIT ${idx}
    """
    return await conn.fetch(query, *params)


@router.get(
    "/gate-results/{gateResultId}",
    operation_id="getGateResult",
    summary="Get a specific gate result by ID",
    response_model=GateResult,
    responses={
        200: {"model": GateResult},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def get_gate_result(
    gateResultId: str,
    _jwt: dict[str, Any] = Depends(require_validator_role),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
    # Validate UUID format
    try:
        uuid.UUID(gateResultId)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_ID", "gateResultId must be a valid UUID"),
        ) from exc

    with _tracer.start_as_current_span("http.get.gate_result") as span:
        span.set_attribute("http.method", "GET")
        span.set_attribute("http.route", "/gate-results/{gateResultId}")
        span.set_attribute("gate_result_id", gateResultId)

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT gate_result_id, vin, gate_id, gate_type, result,
                       measurements, measured_at, received_from_opcenter_at,
                       opcenter_event_id
                FROM gate_result
                WHERE gate_result_id = $1::uuid
                """,
                gateResultId,
            )

        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_error("GATE_RESULT_NOT_FOUND", f"Gate result {gateResultId!r} not found"),
            )

        gate_result = _row_to_gate_result(row)
        return JSONResponse(
            status_code=200,
            content=json.loads(gate_result.model_dump_json()),
        )
