"""Kafka consumer for PaintDefectDetected events.

Consumer group: defect-genome-paint-consumer
Inbound topic:  bmw.munich.paint.defect.events.v1
Outbound:       bmw.munich.defectgenome.cluster.alerts.v1
DLQ:            uns.munich.i4.dlq.defect-genome-paint-consumer
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import asyncpg

from app.cluster_state import get_recent_events, record_event, run_dbscan, score_cluster_xgboost
from app.models import DefectClass

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_S = 1.0
_PRODUCE_TIMEOUT_S = 5.0
_MODEL_VERSION = "1.0.0"


def _now_utc_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


async def _get_threshold_config(pool: asyncpg.Pool) -> dict:
    """Read current threshold config from the database."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT low_confidence_threshold, cluster_alert_threshold,
                   cluster_alert_min_vins, cluster_alert_window_hours
            FROM confidence_threshold_config
            WHERE singleton_guard = 'singleton'
            """
        )
    if row is None:
        return {
            "low_confidence_threshold": 0.60,
            "cluster_alert_threshold": 0.80,
            "cluster_alert_min_vins": 3,
            "cluster_alert_window_hours": 4,
        }
    return dict(row)


async def _persist_cluster_alert(
    pool: asyncpg.Pool,
    cluster_id: uuid.UUID,
    alert_id: uuid.UUID,
    defect_class: str,
    confidence_score: float,
    affected_vin_count: int,
    root_cause_signature: str,
    process_replay_url: str,
    detected_at: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO cluster_alert (
                alert_id, cluster_id, defect_class, status, confidence_score,
                affected_vin_count, root_cause_signature, process_replay_url,
                detected_at, created_at, updated_at, data_region
            ) VALUES (
                $1::uuid, $2::uuid, $3, 'new', $4, $5, $6, $7,
                $8::timestamptz, now(), now(), 'eu-de-west-central'
            )
            ON CONFLICT DO NOTHING
            """,
            str(alert_id),
            str(cluster_id),
            defect_class,
            confidence_score,
            affected_vin_count,
            root_cause_signature,
            process_replay_url,
            detected_at,
        )


async def _upsert_defect_cluster(
    pool: asyncpg.Pool,
    cluster_id: uuid.UUID,
    defect_class: str,
    confidence_score: float,
    affected_vin_count: int,
    root_cause_signature: str,
    most_recent_vin: str,
    process_replay_url: str,
    top_factors_json: str,
    detected_at: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO defect_cluster (
                cluster_id, defect_class, status, root_cause_signature,
                confidence_score, affected_vin_count, most_recent_vin,
                detected_at, process_replay_url, top_factors, model_version,
                created_at, updated_at, data_region
            ) VALUES (
                $1::uuid, $2, 'open', $3, $4, $5, $6,
                $7::timestamptz, $8, $9::jsonb, $10,
                now(), now(), 'eu-de-west-central'
            )
            ON CONFLICT (cluster_id) DO UPDATE SET
                confidence_score = EXCLUDED.confidence_score,
                affected_vin_count = EXCLUDED.affected_vin_count,
                most_recent_vin = EXCLUDED.most_recent_vin,
                top_factors = EXCLUDED.top_factors,
                updated_at = now()
            """,
            str(cluster_id),
            defect_class,
            root_cause_signature,
            confidence_score,
            affected_vin_count,
            most_recent_vin,
            detected_at,
            process_replay_url,
            top_factors_json,
            _MODEL_VERSION,
        )


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
                value=json.dumps({
                    "reason": reason,
                    "raw": raw_value.decode("utf-8", errors="replace"),
                }).encode(),
            ),
            timeout=_PRODUCE_TIMEOUT_S,
        )
    except (asyncio.TimeoutError, OSError) as err:
        logger.exception("Failed to send message to DLQ topic=%s: %s", dlq_topic, err)


async def _handle_message(
    raw_value: bytes,
    pool: asyncpg.Pool,
    producer: "aiokafka.AIOKafkaProducer",
    cfg: object,
) -> None:
    from app.config import Settings

    assert isinstance(cfg, Settings)

    try:
        event = json.loads(raw_value)
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        logger.error("Failed to decode paint defect event: %s", err)
        await _send_to_dlq(producer, cfg.defect_events_dlq_topic, raw_value, f"decode_error: {err}")
        return

    defect_class_raw: str | None = event.get("defectClass")
    vin: str | None = event.get("vin")

    if not defect_class_raw or not vin:
        logger.error("Paint defect event missing defectClass or vin: %s", event)
        await _send_to_dlq(producer, cfg.defect_events_dlq_topic, raw_value, "missing_required_fields")
        return

    try:
        DefectClass(defect_class_raw)
    except ValueError:
        logger.error("Unknown defectClass %r in paint defect event", defect_class_raw)
        await _send_to_dlq(producer, cfg.defect_events_dlq_topic, raw_value, f"unknown_defect_class: {defect_class_raw}")
        return

    # Record into the rolling window
    record_event(event)

    # Load current thresholds
    try:
        thresholds = await _get_threshold_config(pool)
    except asyncpg.PostgresError:
        logger.exception("DB error loading threshold config — skipping cluster evaluation")
        return

    alert_min_vins: int = thresholds["cluster_alert_min_vins"]
    alert_threshold: float = thresholds["cluster_alert_threshold"]
    window_hours: int = thresholds["cluster_alert_window_hours"]

    # Evaluate rolling window
    recent = get_recent_events(defect_class_raw, window_hours)
    clusters = run_dbscan(recent)

    for signature, cluster_events in clusters.items():
        vins_in_cluster = list({e.get("vin", "") for e in cluster_events if e.get("vin")})
        if len(vins_in_cluster) < alert_min_vins:
            continue

        top_factors, model_confidence = score_cluster_xgboost(cluster_events, defect_class_raw, _MODEL_VERSION)
        if model_confidence < alert_threshold:
            continue

        most_recent_vin = vins_in_cluster[-1]
        process_replay_url = f"{cfg.quality_studio_base_url}/process-replay/{most_recent_vin}"

        cluster_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"{defect_class_raw}:{signature}")
        alert_id = uuid.uuid4()
        detected_at = event.get("detectedAt", _now_utc_iso())

        try:
            await _upsert_defect_cluster(
                pool=pool,
                cluster_id=cluster_id,
                defect_class=defect_class_raw,
                confidence_score=model_confidence,
                affected_vin_count=len(vins_in_cluster),
                root_cause_signature=signature,
                most_recent_vin=most_recent_vin,
                process_replay_url=process_replay_url,
                top_factors_json=json.dumps(top_factors),
                detected_at=detected_at,
            )
        except asyncpg.PostgresError:
            logger.exception("DB error upserting defect_cluster for signature=%s", signature)
            continue

        try:
            await _persist_cluster_alert(
                pool=pool,
                cluster_id=cluster_id,
                alert_id=alert_id,
                defect_class=defect_class_raw,
                confidence_score=model_confidence,
                affected_vin_count=len(vins_in_cluster),
                root_cause_signature=signature,
                process_replay_url=process_replay_url,
                detected_at=detected_at,
            )
        except asyncpg.PostgresError:
            logger.exception("DB error persisting cluster_alert alertId=%s", alert_id)
            continue

        alert_payload = {
            "alertId": str(alert_id),
            "clusterId": str(cluster_id),
            "defectClass": defect_class_raw,
            "rootCauseSignature": signature,
            "confidenceScore": model_confidence,
            "affectedVinCount": len(vins_in_cluster),
            "processReplayUrl": process_replay_url,
            "detectedAt": detected_at,
            "modelVersion": _MODEL_VERSION,
        }
        try:
            await asyncio.wait_for(
                producer.send(cfg.cluster_alert_topic, value=json.dumps(alert_payload).encode()),
                timeout=_PRODUCE_TIMEOUT_S,
            )
            logger.info(
                "Published cluster alert alertId=%s clusterId=%s defectClass=%s confidence=%s vins=%d",
                alert_id, cluster_id, defect_class_raw, model_confidence, len(vins_in_cluster),
            )
        except (asyncio.TimeoutError, OSError):
            logger.exception("Failed to publish cluster alert alertId=%s", alert_id)
            await _send_to_dlq(producer, cfg.defect_events_dlq_topic, raw_value, "alert_publish_error")


async def run_consumer(pool: asyncpg.Pool) -> None:
    """Long-running coroutine: consume PaintDefectDetected events and publish cluster alerts."""
    import aiokafka

    from app.config import get_settings

    cfg = get_settings()

    consumer = aiokafka.AIOKafkaConsumer(
        cfg.defect_events_topic,
        bootstrap_servers=cfg.kafka_bootstrap_servers,
        group_id=cfg.defect_consumer_group,
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
            await _handle_message(msg.value, pool=pool, producer=producer, cfg=cfg)
    finally:
        await consumer.stop()
        await producer.stop()
