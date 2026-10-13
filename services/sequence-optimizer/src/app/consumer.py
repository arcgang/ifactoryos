"""Kafka consumer for live constraint telemetry + breach-event publisher.

Consumer group: planning-cockpit-constraint-consumer
Inbound topic:  bmw.munich.i4.uns.constraints.live.v1
Outbound topic: sequence.constraint.alert
DLQ topic:      uns.munich.i4.dlq.planning-cockpit-constraint-consumer
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import asyncpg

from app.models import ConstraintAlertEvent, ConstraintType

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_S = 1.0
_PRODUCE_TIMEOUT_S = 5.0


def _now_utc_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def evaluate_breach(
    current_value: float,
    warning_level: float,
) -> bool:
    """Return True when current_value crosses (exceeds) warning_level."""
    return current_value > warning_level


def build_alert_event(
    constraint_type: ConstraintType,
    current_value: float,
    warning_level: float,
    detected_at: str,
) -> ConstraintAlertEvent:
    return ConstraintAlertEvent(
        constraintType=constraint_type,
        currentValue=current_value,
        warningLevel=warning_level,
        detectedAt=detected_at,
    )


async def _resolve_affected_vins(
    pool: asyncpg.Pool,
    constraint_type: str,
) -> list[str]:
    """Return VINs from the current week's optimizer_run that are impacted by this constraint."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT (item->>'vin') AS vin_value
            FROM optimizer_run,
                 jsonb_array_elements(ranked_sequence) AS item
            WHERE created_at >= (date_trunc('week', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
              AND (item->>'vin') IS NOT NULL
            ORDER BY vin_value
            LIMIT 500
            """,
        )
    return [r["vin_value"] for r in rows if r["vin_value"]]


async def _persist_alert(
    pool: asyncpg.Pool,
    alert_id: uuid.UUID,
    constraint_type: str,
    current_value: float,
    warning_level: float,
    affected_vins: list[str],
    detected_at: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO constraint_alert (
                alert_id, constraint_type, current_value, threshold_value,
                affected_vins, status, detected_at
            ) VALUES ($1::uuid, $2, $3, $4, $5::jsonb, 'active', $6::timestamptz)
            ON CONFLICT DO NOTHING
            """,
            str(alert_id),
            constraint_type,
            current_value,
            warning_level,
            json.dumps(affected_vins),
            detected_at,
        )


async def _write_alert_audit(
    alert_id: str,
    constraint_type: str,
    current_value: float,
    affected_vins: list[str],
) -> None:
    """POST breach audit event to immutable audit log — best-effort, 1 s timeout."""
    try:
        import httpx

        from app.config import get_settings

        cfg = get_settings()
        payload = {
            "action": "constraint_alert_created",
            "alertId": alert_id,
            "constraintType": constraint_type,
            "currentValue": current_value,
            "affectedVins": affected_vins,
            "source": "sequence-optimizer",
        }
        async with httpx.AsyncClient(timeout=cfg.audit_log_timeout_s) as client:
            await client.post(cfg.audit_log_url, json=payload)
    except Exception as exc:  # noqa: BLE001 — fire-and-forget audit write
        logger.exception("Failed to write audit event for alert: %s", exc)


async def _send_to_dlq(
    producer: "aiokafka.AIOKafkaProducer",
    dlq_topic: str,
    raw_value: bytes,
    reason: str,
) -> None:
    try:
        await asyncio.wait_for(
            producer.send(
                dlq_topic,
                value=json.dumps({"reason": reason, "raw": raw_value.decode("utf-8", errors="replace")}).encode(),
            ),
            timeout=_PRODUCE_TIMEOUT_S,
        )
    except (asyncio.TimeoutError, OSError) as err:
        logger.exception("Failed to send message to DLQ topic=%s: %s", dlq_topic, err)


async def run_consumer(pool: asyncpg.Pool) -> None:
    """Long-running coroutine: consume telemetry, publish breach alerts."""
    import aiokafka

    from app.config import get_settings
    from app.thresholds import get_warning_level

    cfg = get_settings()

    consumer = aiokafka.AIOKafkaConsumer(
        cfg.constraint_telemetry_topic,
        bootstrap_servers=cfg.kafka_bootstrap_servers,
        group_id=cfg.constraint_consumer_group,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    producer = aiokafka.AIOKafkaProducer(
        bootstrap_servers=cfg.kafka_bootstrap_servers,
        request_timeout_ms=int(_PRODUCE_TIMEOUT_S * 1000),
    )

    await consumer.start()
    await producer.start()
    try:
        async for msg in consumer:
            await _handle_message(
                msg.value,
                pool=pool,
                producer=producer,
                alert_topic=cfg.constraint_alert_topic,
                dlq_topic=cfg.constraint_dlq_topic,
            )
    finally:
        await consumer.stop()
        await producer.stop()


async def _handle_message(
    raw_value: bytes,
    pool: asyncpg.Pool,
    producer: "aiokafka.AIOKafkaProducer",
    alert_topic: str,
    dlq_topic: str,
) -> None:
    from app.thresholds import get_warning_level

    try:
        payload = json.loads(raw_value)
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        logger.error("Failed to decode telemetry message: %s", err)
        await _send_to_dlq(producer, dlq_topic, raw_value, f"decode_error: {err}")
        return

    constraint_type_raw: str | None = payload.get("constraintType")
    current_value: float | None = payload.get("currentValue")

    if constraint_type_raw is None or current_value is None:
        logger.error(
            "Telemetry message missing constraintType or currentValue: %s", payload
        )
        await _send_to_dlq(
            producer, dlq_topic, raw_value, "missing_required_fields"
        )
        return

    try:
        constraint_type = ConstraintType(constraint_type_raw)
    except ValueError:
        logger.error("Unknown constraintType %r in telemetry", constraint_type_raw)
        await _send_to_dlq(
            producer, dlq_topic, raw_value, f"unknown_constraint_type: {constraint_type_raw}"
        )
        return

    try:
        warning_level = await get_warning_level(pool, constraint_type.value)
    except asyncpg.PostgresError as err:
        logger.exception(
            "DB error looking up warningLevel for constraintType=%s", constraint_type.value
        )
        await _send_to_dlq(
            producer, dlq_topic, raw_value, f"db_error: {err}"
        )
        return

    if warning_level is None:
        logger.debug(
            "No threshold configured for constraintType=%s — skipping", constraint_type.value
        )
        return

    if not evaluate_breach(float(current_value), warning_level):
        return

    detected_at = _now_utc_iso()

    try:
        affected_vins = await _resolve_affected_vins(pool, constraint_type.value)
    except asyncpg.PostgresError as err:
        logger.exception(
            "DB error resolving affected VINs for constraintType=%s", constraint_type.value
        )
        affected_vins = []

    alert_id = uuid.uuid4()
    try:
        await _persist_alert(
            pool=pool,
            alert_id=alert_id,
            constraint_type=constraint_type.value,
            current_value=float(current_value),
            warning_level=warning_level,
            affected_vins=affected_vins,
            detected_at=detected_at,
        )
    except asyncpg.PostgresError as err:
        logger.exception(
            "DB error persisting constraint_alert for constraintType=%s", constraint_type.value
        )
        await _send_to_dlq(producer, dlq_topic, raw_value, f"db_persist_error: {err}")
        return

    event = build_alert_event(
        constraint_type=constraint_type,
        current_value=float(current_value),
        warning_level=warning_level,
        detected_at=detected_at,
    )

    event_payload = {
        **event.model_dump(mode="json"),
        "alertId": str(alert_id),
        "affectedVins": affected_vins,
    }
    event_bytes = json.dumps(event_payload).encode()
    try:
        await asyncio.wait_for(
            producer.send(alert_topic, value=event_bytes),
            timeout=_PRODUCE_TIMEOUT_S,
        )
        logger.info(
            "Published constraint breach alert alertId=%s constraintType=%s currentValue=%s warningLevel=%s affectedVins=%d",
            alert_id,
            constraint_type.value,
            current_value,
            warning_level,
            len(affected_vins),
        )
    except asyncio.TimeoutError:
        logger.error(
            "Timed out publishing alert for constraintType=%s", constraint_type.value
        )
        await _send_to_dlq(producer, dlq_topic, raw_value, "alert_publish_timeout")
    except OSError as err:
        logger.exception(
            "Failed to publish alert for constraintType=%s", constraint_type.value
        )
        await _send_to_dlq(producer, dlq_topic, raw_value, f"alert_publish_error: {err}")
        return

    asyncio.create_task(
        _write_alert_audit(
            alert_id=str(alert_id),
            constraint_type=constraint_type.value,
            current_value=float(current_value),
            affected_vins=affected_vins,
        )
    )
