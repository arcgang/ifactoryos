"""Torque log processing service.

Handles idempotency, tolerance validation, and Kafka event publishing
for PlcTorqueTraceEvent messages consumed from the UNS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

import httpx
from opentelemetry import trace

from app.kafka_producer import TOPIC_ADOPTION_EVENT

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("line-command-api.torque_service")

TOPIC_TORQUE_SUBMITTED = "bmw.ifos.munich.line-command.torque.submitted.v1"
TOPIC_TORQUE_OUT_OF_TOLERANCE = "bmw.ifos.munich.line-command.torque.out-of-tolerance.v1"


class DBConnection(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Any | None: ...
    async def execute(self, query: str, *args: Any) -> None: ...
    async def fetchval(self, query: str, *args: Any) -> Any | None: ...


async def is_duplicate(conn: DBConnection, idempotency_key: str) -> bool:
    """Return True if a torque_log row already exists for this idempotency_key."""
    row = await conn.fetchrow(
        "SELECT id FROM torque_log WHERE idempotency_key = $1::uuid",
        idempotency_key,
    )
    return row is not None


async def insert_torque_log(
    conn: DBConnection,
    *,
    vin: str,
    station_id: str,
    fastener_point_id: str,
    torque_nm: Decimal,
    angles_deg: Decimal,
    plc_timestamp: datetime,
    idempotency_key: str,
    trace_data: list[dict] | None,
    edge_buffered: bool,
) -> str:
    """Insert a new torque_log row with uns_publish_status='queued'. Returns the new UUID."""
    row_id = str(uuid.uuid4())
    trace_json = json.dumps(trace_data) if trace_data is not None else None
    with _tracer.start_as_current_span("db.torque_log.insert") as span:
        span.set_attribute("db.table", "torque_log")
        span.set_attribute("vin", vin)
        span.set_attribute("station_id", station_id)
        await conn.execute(
            """
            INSERT INTO torque_log
                (id, vin, station_id, fastener_point_id, torque_nm, angles_deg,
                 plc_timestamp, uns_publish_status, edge_buffered, idempotency_key,
                 trace_data)
            VALUES
                ($1::uuid, $2, $3, $4, $5, $6, $7, 'queued', $8, $9::uuid,
                 $10::jsonb)
            """,
            row_id,
            vin,
            station_id,
            fastener_point_id,
            torque_nm,
            angles_deg,
            plc_timestamp,
            edge_buffered,
            idempotency_key,
            trace_json,
        )
    return row_id


async def lookup_tolerance(
    conn: DBConnection,
    vin: str,
    station_id: str,
    fastener_point_id: str,
    vdt_base_url: str,
    vdt_timeout_seconds: float,
) -> tuple[Decimal | None, Decimal | None]:
    """Return (spec_nm, tolerance_nm) for the fastener, or (None, None) if unavailable."""
    row = await conn.fetchrow(
        """
        SELECT payload_jsonb
        FROM ar_instruction_cache
        WHERE vin = $1
          AND station_id = $2
          AND expires_at > NOW()
        ORDER BY fetched_at DESC
        LIMIT 1
        """,
        vin,
        station_id,
    )

    if row is not None:
        payload: dict = dict(row["payload_jsonb"])
        return _extract_tolerance_from_payload(payload, fastener_point_id)

    # Cache miss — fetch from vin-digital-thread-api
    try:
        url = f"{vdt_base_url}/api/v1/ar/instructions/{vin}"
        async with httpx.AsyncClient(timeout=vdt_timeout_seconds) as client:
            resp = await client.get(url, params={"stationId": station_id})
        if resp.status_code == 200:
            return _extract_tolerance_from_payload(resp.json(), fastener_point_id)
        logger.warning(
            "VDT returned %d for vin=%s station=%s — skipping tolerance",
            resp.status_code, vin, station_id,
        )
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning(
            "Tolerance lookup failed for vin=%s station=%s: %s — skipping",
            vin, station_id, exc,
        )
    return None, None


def _extract_tolerance_from_payload(
    payload: dict, fastener_point_id: str
) -> tuple[Decimal | None, Decimal | None]:
    for step in payload.get("steps", []):
        if step.get("fastenerPointId") == fastener_point_id:
            spec = step.get("torqueSpecNm")
            tol = step.get("torqueToleranceNm")
            if spec is not None and tol is not None:
                return Decimal(str(spec)), Decimal(str(tol))
    return None, None


async def apply_tolerance(
    conn: DBConnection,
    row_id: str,
    torque_nm: Decimal,
    spec_nm: Decimal,
    tolerance_nm: Decimal,
) -> bool:
    in_tolerance = abs(torque_nm - spec_nm) <= tolerance_nm
    await conn.execute(
        """
        UPDATE torque_log
        SET spec_nm = $1, tolerance_nm = $2, in_tolerance = $3, updated_at = NOW()
        WHERE id = $4::uuid
        """,
        spec_nm,
        tolerance_nm,
        in_tolerance,
        row_id,
    )
    return in_tolerance


async def mark_published(conn: DBConnection, row_id: str, published_at: datetime) -> None:
    await conn.execute(
        """
        UPDATE torque_log
        SET uns_publish_status = 'published', uns_published_at = $1, updated_at = NOW()
        WHERE id = $2::uuid
        """,
        published_at,
        row_id,
    )


async def mark_publish_failed(conn: DBConnection, row_id: str) -> None:
    await conn.execute(
        """
        UPDATE torque_log
        SET uns_publish_status = 'failed', updated_at = NOW()
        WHERE id = $1::uuid
        """,
        row_id,
    )


async def set_alert_id(conn: DBConnection, row_id: str, alert_id: str) -> None:
    await conn.execute(
        """
        UPDATE torque_log
        SET alert_id = $1::uuid, updated_at = NOW()
        WHERE id = $2::uuid
        """,
        alert_id,
        row_id,
    )


async def process_torque_event(
    conn: DBConnection,
    event: dict,
    publish_fn: Any,
    vdt_base_url: str,
    vdt_timeout_seconds: float,
    pool: Any = None,
) -> None:
    """Process one PlcTorqueTraceEvent.

    Steps:
    1. Idempotency check — skip if already processed.
    2. INSERT torque_log with uns_publish_status='queued'.
    3. Tolerance lookup and UPDATE.
    4. Publish TorqueSubmittedEvent asynchronously; update status on ack/failure.
    5. If out-of-tolerance, publish TorqueOutOfToleranceEvent.
    6. Publish AdoptionEvent (acks=1, within 1s).

    When pool is supplied the background publish task acquires its own
    connection so conn can be safely released by the caller after this
    coroutine returns.  When pool is None (unit-test path) the background
    task reuses conn directly.
    """
    idempotency_key = str(event["idempotencyKey"])
    vin = event["vin"]
    station_id = event["stationId"]
    fastener_point_id = event["fastenerPointId"]
    torque_nm = Decimal(str(event["torqueNm"]))
    angles_deg = Decimal(str(event["anglesDeg"]))
    plc_timestamp = datetime.fromisoformat(event["plcTimestamp"])
    if plc_timestamp.tzinfo is None:
        plc_timestamp = plc_timestamp.replace(tzinfo=timezone.utc)
    trace_data = event.get("traceData")
    edge_buffered = bool(event.get("edgeBuffered", False))

    if await is_duplicate(conn, idempotency_key):
        logger.info(
            "Duplicate torque event skipped: idempotency_key=%s vin=%s",
            idempotency_key, vin,
        )
        return

    row_id = await insert_torque_log(
        conn,
        vin=vin,
        station_id=station_id,
        fastener_point_id=fastener_point_id,
        torque_nm=torque_nm,
        angles_deg=angles_deg,
        plc_timestamp=plc_timestamp,
        idempotency_key=idempotency_key,
        trace_data=trace_data,
        edge_buffered=edge_buffered,
    )

    spec_nm, tolerance_nm = await lookup_tolerance(
        conn, vin, station_id, fastener_point_id, vdt_base_url, vdt_timeout_seconds
    )

    in_tolerance: bool | None = None
    if spec_nm is not None and tolerance_nm is not None:
        in_tolerance = await apply_tolerance(conn, row_id, torque_nm, spec_nm, tolerance_nm)

    # Publish TorqueSubmittedEvent asynchronously.
    # When a pool is available the background task acquires a fresh connection
    # so the caller can safely release conn after this coroutine returns.
    if pool is not None:
        asyncio.create_task(
            _publish_torque_submitted_with_pool(
                pool=pool,
                publish_fn=publish_fn,
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
            )
        )
    else:
        asyncio.create_task(
            _publish_torque_submitted(
                conn=conn,
                publish_fn=publish_fn,
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
            )
        )

    # Publish AdoptionEvent synchronously within 1s (acks=1 per settled decision)
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        await asyncio.wait_for(
            publish_fn(
                TOPIC_ADOPTION_EVENT,
                {
                    "eventId": str(uuid.uuid4()),
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


async def _publish_torque_submitted_with_pool(
    pool: Any,
    publish_fn: Any,
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
    alert_id: str | None = None,
) -> None:
    """Acquire a fresh pool connection and delegate to _publish_torque_submitted.

    Used by the Kafka consumer path so the caller's connection can be safely
    released after process_torque_event returns.  alert_id may be pre-supplied
    by the HTTP handler so the response body and the Kafka event carry the same
    UUID; when None a new one is generated by _publish_out_of_tolerance.
    """
    async with pool.acquire() as conn:
        await _publish_torque_submitted(
            conn=conn,
            publish_fn=publish_fn,
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


async def _publish_torque_submitted(
    conn: DBConnection,
    publish_fn: Any,
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
    alert_id: str | None = None,
) -> None:
    """Publish TorqueSubmittedEvent and update uns_publish_status."""
    published_at = datetime.now(timezone.utc)
    payload: dict = {
        "torqueLogId": row_id,
        "vin": vin,
        "stationId": station_id,
        "fastenerPointId": fastener_point_id,
        "torqueNm": float(torque_nm),
        "anglesDeg": float(angles_deg),
        "plcTimestamp": plc_timestamp.isoformat(),
        "publishedAt": published_at.isoformat(),
    }
    if in_tolerance is not None:
        payload["inTolerance"] = in_tolerance

    try:
        with _tracer.start_as_current_span("kafka.produce.torque_submitted") as span:
            span.set_attribute("messaging.destination", TOPIC_TORQUE_SUBMITTED)
            span.set_attribute("vin", vin)
            await publish_fn(TOPIC_TORQUE_SUBMITTED, payload)
        with _tracer.start_as_current_span("db.torque_log.mark_published") as span:
            span.set_attribute("db.table", "torque_log")
            await mark_published(conn, row_id, published_at)
        logger.info(
            "TorqueSubmittedEvent published: torqueLogId=%s vin=%s in_tolerance=%s",
            row_id, vin, in_tolerance,
        )
    except (OSError, asyncio.TimeoutError, RuntimeError) as exc:
        logger.error(
            "TorqueSubmittedEvent publish failed for torqueLogId=%s: %s",
            row_id, exc,
        )
        try:
            await mark_publish_failed(conn, row_id)
        except OSError as db_exc:
            logger.error(
                "Failed to mark uns_publish_status=failed for torqueLogId=%s: %s",
                row_id, db_exc,
            )
        return

    if in_tolerance is False and spec_nm is not None and tolerance_nm is not None:
        await _publish_out_of_tolerance(
            conn=conn,
            publish_fn=publish_fn,
            row_id=row_id,
            vin=vin,
            station_id=station_id,
            fastener_point_id=fastener_point_id,
            torque_nm=torque_nm,
            spec_nm=spec_nm,
            tolerance_nm=tolerance_nm,
            alert_id=alert_id,
        )


async def _publish_out_of_tolerance(
    conn: DBConnection,
    publish_fn: Any,
    row_id: str,
    vin: str,
    station_id: str,
    fastener_point_id: str,
    torque_nm: Decimal,
    spec_nm: Decimal,
    tolerance_nm: Decimal,
    alert_id: str | None = None,
) -> None:
    alert_id = alert_id or str(uuid.uuid4())
    occurred_at = datetime.now(timezone.utc).isoformat()
    try:
        with _tracer.start_as_current_span("kafka.produce.torque_out_of_tolerance") as span:
            span.set_attribute("messaging.destination", TOPIC_TORQUE_OUT_OF_TOLERANCE)
            span.set_attribute("vin", vin)
            span.set_attribute("alert_id", alert_id)
            await publish_fn(
                TOPIC_TORQUE_OUT_OF_TOLERANCE,
                {
                    "alertId": alert_id,
                    "torqueLogId": row_id,
                    "vin": vin,
                    "stationId": station_id,
                    "fastenerPointId": fastener_point_id,
                    "torqueNm": float(torque_nm),
                    "specNm": float(spec_nm),
                    "toleranceNm": float(tolerance_nm),
                    "occurredAt": occurred_at,
                },
            )
        with _tracer.start_as_current_span("db.torque_log.set_alert_id") as span:
            span.set_attribute("db.table", "torque_log")
            await set_alert_id(conn, row_id, alert_id)
        logger.warning(
            "TorqueOutOfToleranceEvent published: alertId=%s vin=%s fastener=%s",
            alert_id, vin, fastener_point_id,
        )
    except (OSError, asyncio.TimeoutError, RuntimeError) as exc:
        logger.error(
            "TorqueOutOfToleranceEvent publish failed for torqueLogId=%s: %s",
            row_id, exc,
        )
