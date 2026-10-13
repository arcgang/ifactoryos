"""POST /torque/log and GET /torque/logs — torque tightening record endpoints.

Implements AC-13685a24, AC-69c77dfd, AC-b734032a, AC-fd1f00c6.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from opentelemetry import trace
from pydantic import BaseModel, Field, field_validator

from app.auth import require_bearer
from app.db import get_pool
from app.kafka_producer import publish
from app.torque_service import (
    _publish_torque_submitted_with_pool,
    apply_tolerance,
    insert_torque_log,
    is_duplicate,
    lookup_tolerance,
)

_tracer = trace.get_tracer("line-command-api.torque_router")

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Torque Log"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class _TracePoint(BaseModel):
    offsetMs: int
    torqueNm: float
    angleDeg: float


class TorqueLogRequest(BaseModel):
    vin: str = Field(pattern=r"^[A-HJ-NPR-Z0-9]{17}$")
    stationId: str
    fastenerPointId: str
    torqueNm: float
    anglesDeg: float
    plcTimestamp: datetime
    idempotencyKey: uuid.UUID
    traceData: list[_TracePoint] | None = None
    edgeBuffered: bool = False

    @field_validator("torqueNm", "anglesDeg")
    @classmethod
    def _must_be_finite(cls, v: float) -> float:
        import math
        if not math.isfinite(v):
            raise ValueError("must be a finite number")
        return v


class TorqueLogResponse(BaseModel):
    torqueLogId: str
    vin: str
    status: str
    unsPublishStatus: str
    recordedAt: datetime


class TorqueOutOfToleranceResponse(BaseModel):
    torqueLogId: str
    vin: str
    fastenerPointId: str
    torqueNm: float
    specNm: float
    toleranceNm: float
    alertId: str
    recordedAt: datetime


class _TorqueLogRecord(BaseModel):
    torqueLogId: str
    vin: str
    stationId: str
    fastenerPointId: str
    torqueNm: float
    anglesDeg: float
    status: str
    plcTimestamp: datetime
    createdAt: datetime
    unsPublishStatus: str | None = None


class _CursorPage(BaseModel):
    next_cursor: str | None
    limit: int


class TorqueLogListResponse(BaseModel):
    data: list[_TorqueLogRecord]
    page: _CursorPage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


def _encode_cursor(row_id: str) -> str:
    return base64.urlsafe_b64encode(row_id.encode()).decode()


def _decode_cursor(cursor: str) -> str | None:
    try:
        return base64.urlsafe_b64decode(cursor.encode()).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None



# ---------------------------------------------------------------------------
# POST /torque/log
# ---------------------------------------------------------------------------

@router.post(
    "/torque/log",
    operation_id="submitTorqueLog",
    summary="Submit a torque tightening record from the Beckhoff PLC via edge gateway",
    status_code=201,
    responses={
        201: {"model": TorqueLogResponse},
        202: {"model": TorqueLogResponse},
        400: {},
        401: {},
        409: {},
        422: {"model": TorqueOutOfToleranceResponse},
    },
)
async def submit_torque_log(
    body: TorqueLogRequest,
    jwt_payload: dict[str, Any] = Depends(require_bearer),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
    from app.auth import caller_subject
    from app.config import settings

    idempotency_key = str(body.idempotencyKey)
    user_id = caller_subject(jwt_payload)
    roles: list[str] = jwt_payload.get("roles", []) or []
    caller_role = roles[0] if roles else "unknown"

    async with pool.acquire() as conn:
        if await is_duplicate(conn, idempotency_key):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_error(
                    "DUPLICATE_TORQUE_LOG",
                    f"Idempotency key {idempotency_key} already processed",
                ),
            )

        plc_timestamp = body.plcTimestamp
        if plc_timestamp.tzinfo is None:
            plc_timestamp = plc_timestamp.replace(tzinfo=timezone.utc)

        trace_data = (
            [p.model_dump() for p in body.traceData] if body.traceData else None
        )

        row_id = await insert_torque_log(
            conn,
            vin=body.vin,
            station_id=body.stationId,
            fastener_point_id=body.fastenerPointId,
            torque_nm=Decimal(str(body.torqueNm)),
            angles_deg=Decimal(str(body.anglesDeg)),
            plc_timestamp=plc_timestamp,
            idempotency_key=idempotency_key,
            trace_data=trace_data,
            edge_buffered=body.edgeBuffered,
        )

        spec_nm, tolerance_nm = await lookup_tolerance(
            conn,
            body.vin,
            body.stationId,
            body.fastenerPointId,
            settings.vdt_base_url,
            settings.vdt_timeout_seconds,
        )

        in_tolerance: bool | None = None

        if spec_nm is not None and tolerance_nm is not None:
            in_tolerance = await apply_tolerance(
                conn, row_id, Decimal(str(body.torqueNm)), spec_nm, tolerance_nm
            )

        recorded_at = datetime.now(timezone.utc)

        # Pre-generate alert_id so the HTTP 422 response and the Kafka event carry the same UUID.
        # The background task will persist it to the DB via _publish_out_of_tolerance.
        alert_id: str | None = None
        if in_tolerance is False and spec_nm is not None and tolerance_nm is not None:
            alert_id = str(uuid.uuid4())

        # Publish TorqueSubmittedEvent asynchronously (does not block HTTP response).
        # Acquires its own pool connection so the handler's conn can close normally.
        asyncio.create_task(
            _async_publish_submitted(
                pool=pool,
                row_id=row_id,
                vin=body.vin,
                station_id=body.stationId,
                fastener_point_id=body.fastenerPointId,
                torque_nm=Decimal(str(body.torqueNm)),
                angles_deg=Decimal(str(body.anglesDeg)),
                plc_timestamp=plc_timestamp,
                in_tolerance=in_tolerance,
                spec_nm=spec_nm,
                tolerance_nm=tolerance_nm,
                alert_id=alert_id,
                user_id=user_id,
                caller_role=caller_role,
            )
        )

        if in_tolerance is False and spec_nm is not None and tolerance_nm is not None:
            oot_body = TorqueOutOfToleranceResponse(
                torqueLogId=row_id,
                vin=body.vin,
                fastenerPointId=body.fastenerPointId,
                torqueNm=body.torqueNm,
                specNm=float(spec_nm),
                toleranceNm=float(tolerance_nm),
                alertId=alert_id,  # type: ignore[arg-type]
                recordedAt=recorded_at,
            )
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=json.loads(oot_body.model_dump_json()),
            )

        uns_publish_status = "queued"
        http_status = status.HTTP_202_ACCEPTED if body.edgeBuffered else status.HTTP_201_CREATED
        resp_status = "pending_sync" if body.edgeBuffered else "accepted"

        resp = TorqueLogResponse(
            torqueLogId=row_id,
            vin=body.vin,
            status=resp_status,
            unsPublishStatus=uns_publish_status,
            recordedAt=recorded_at,
        )
        return JSONResponse(
            status_code=http_status,
            content=json.loads(resp.model_dump_json()),
        )


async def _async_publish_submitted(
    pool: Any,
    row_id: str,
    vin: str,
    station_id: str,
    fastener_point_id: str,
    torque_nm: Decimal,
    angles_deg: Decimal,
    plc_timestamp: datetime,
    in_tolerance: bool | None,
    spec_nm: Decimal | None,
    tolerance_nm: Decimal | None,
    alert_id: str | None,
    user_id: str = "unknown",
    caller_role: str = "unknown",
) -> None:
    """Background task: publish TorqueSubmittedEvent, OoT event, AdoptionEvent.

    Delegates Kafka publish and DB status update to torque_service, then
    publishes AdoptionEvent.  Acquires its own pool connections.
    """
    from app.kafka_producer import TOPIC_ADOPTION_EVENT

    # TorqueSubmittedEvent (+ OoT if applicable) via shared service function.
    # _publish_torque_submitted_with_pool acquires its own connection internally.
    await _publish_torque_submitted_with_pool(
        pool=pool,
        publish_fn=publish,
        row_id=row_id,
        vin=vin,
        station_id=station_id,
        fastener_point_id=fastener_point_id,
        torque_nm=torque_nm,
        angles_deg=angles_deg,
        plc_timestamp=plc_timestamp,
        in_tolerance=in_tolerance,
        spec_nm=spec_nm,
        tolerance_nm=tolerance_nm,
        alert_id=alert_id,
    )

    # Publish AdoptionEvent — best-effort, 1s timeout
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with _tracer.start_as_current_span("kafka.produce.adoption_event") as span:
            span.set_attribute("messaging.destination", TOPIC_ADOPTION_EVENT)
            span.set_attribute("vin", vin)
            await asyncio.wait_for(
                publish(
                    TOPIC_ADOPTION_EVENT,
                    {
                        "eventId": str(uuid.uuid4()),
                        "userId": user_id,
                        "role": caller_role,
                        "actionType": "TORQUE_LOG_SUBMITTED",
                        "vin": vin,
                        "stationId": station_id,
                        "torqueLogId": row_id,
                        "occurredAt": now_iso,
                    },
                ),
                timeout=1.0,
            )
    except (OSError, asyncio.TimeoutError, RuntimeError) as exc:
        logger.warning("AdoptionEvent publish failed for torqueLogId=%s: %s", row_id, exc)


# ---------------------------------------------------------------------------
# GET /torque/logs
# ---------------------------------------------------------------------------

@router.get(
    "/torque/logs",
    operation_id="listTorqueLogs",
    summary="List torque log records for a VIN or station (paginated)",
    response_model=TorqueLogListResponse,
    responses={
        200: {"model": TorqueLogListResponse},
        400: {},
        401: {},
    },
)
async def list_torque_logs(
    vin: str | None = Query(default=None, pattern=r"^[A-HJ-NPR-Z0-9]{17}$"),
    stationId: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    jwt_payload: dict[str, Any] = Depends(require_bearer),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
    if vin is None and stationId is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error(
                "MISSING_FILTER",
                "At least one of vin or stationId must be provided",
            ),
        )

    cursor_id: str | None = None
    if cursor is not None:
        cursor_id = _decode_cursor(cursor)
        if cursor_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_error("INVALID_CURSOR", "cursor parameter is malformed"),
            )

    async with pool.acquire() as conn:
        rows = await _query_torque_logs(
            conn, vin=vin, station_id=stationId, cursor_id=cursor_id, limit=limit
        )

    records = [_row_to_record(r) for r in rows[:limit]]
    next_cursor: str | None = None
    if len(rows) > limit:
        next_cursor = _encode_cursor(str(rows[limit - 1]["id"]))

    response = TorqueLogListResponse(
        data=records,
        page=_CursorPage(next_cursor=next_cursor, limit=limit),
    )
    return JSONResponse(
        status_code=200,
        content=json.loads(response.model_dump_json()),
    )


async def _query_torque_logs(
    conn: Any,
    *,
    vin: str | None,
    station_id: str | None,
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

    if station_id is not None:
        conditions.append(f"station_id = ${idx}")
        params.append(station_id)
        idx += 1

    if cursor_id is not None:
        conditions.append(f"id < ${idx}::uuid")
        params.append(cursor_id)
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit + 1)

    query = f"""
        SELECT id, vin, station_id, fastener_point_id, torque_nm, angles_deg,
               in_tolerance, uns_publish_status, plc_timestamp, created_at
        FROM torque_log
        {where}
        ORDER BY id DESC
        LIMIT ${idx}
    """
    return await conn.fetch(query, *params)


def _row_to_record(row: Any) -> _TorqueLogRecord:
    in_tolerance = row["in_tolerance"]
    uns_status = row["uns_publish_status"]

    if in_tolerance is None:
        row_status = "pending_sync"
    elif in_tolerance:
        row_status = "in_tolerance"
    else:
        row_status = "out_of_tolerance"

    return _TorqueLogRecord(
        torqueLogId=str(row["id"]),
        vin=row["vin"],
        stationId=row["station_id"],
        fastenerPointId=row["fastener_point_id"],
        torqueNm=float(row["torque_nm"]),
        anglesDeg=float(row["angles_deg"]),
        status=row_status,
        plcTimestamp=row["plc_timestamp"],
        createdAt=row["created_at"],
        unsPublishStatus=uns_status,
    )
