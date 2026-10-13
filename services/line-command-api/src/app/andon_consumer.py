"""Kafka consumer for AndonAlertEvent messages from the UNS.

Consumes from uns.munich.i4.opcenter.andon (the topic published by
opcenter-sync-service) and upserts into the local andon_alert table using
opcenter_event_id for deduplication (INSERT … ON CONFLICT DO UPDATE).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

TOPIC_ANDON = "uns.munich.i4.opcenter.andon"
CONSUMER_GROUP = "line-command-andon-consumer"


async def process_andon_event(conn: Any, event: dict[str, Any]) -> None:
    """Upsert one AndonAlertEvent into andon_alert.

    The opcenter_event_id is used as the deduplication key: a second message
    with the same id updates the mutable fields (status, minutes_to_predicted_failure,
    recommended_action) but keeps the original detected_at.
    """
    alert_id = event.get("alertId") or event.get("alert_id")
    opcenter_event_id = event.get("opcenterEventId") or event.get("opcenter_event_id")
    station_id = event.get("stationId") or event.get("station_id") or ""
    line_id = event.get("lineId") or event.get("line_id") or ""
    alert_type = event.get("alertType") or event.get("alert_type") or "predictive"
    predicted_failure_type = (
        event.get("predictedFailureType") or event.get("predicted_failure_type") or ""
    )
    minutes = event.get("minutesToPredictedFailure") or event.get("minutes_to_predicted_failure")
    recommended_action = (
        event.get("recommendedAction") or event.get("recommended_action") or ""
    )
    status = event.get("status") or "open"
    detected_at = event.get("detectedAt") or event.get("detected_at") or event.get("timestamp")

    if not opcenter_event_id:
        logger.warning("Andon event missing opcenterEventId — dropped: %s", event)
        return

    await conn.execute(
        """
        INSERT INTO andon_alert (
            alert_id, opcenter_event_id, station_id, line_id, alert_type,
            predicted_failure_type, minutes_to_predicted_failure,
            recommended_action, status, detected_at, received_at,
            created_at, updated_at
        ) VALUES (
            COALESCE($1::uuid, gen_random_uuid()),
            $2, $3, $4, $5,
            $6, $7,
            $8, $9,
            COALESCE($10::timestamptz, NOW()),
            NOW(), NOW(), NOW()
        )
        ON CONFLICT (opcenter_event_id) WHERE opcenter_event_id IS NOT NULL
        DO UPDATE SET
            minutes_to_predicted_failure = EXCLUDED.minutes_to_predicted_failure,
            recommended_action = EXCLUDED.recommended_action,
            status = CASE
                WHEN andon_alert.status IN ('resolved', 'false_positive') THEN andon_alert.status
                ELSE EXCLUDED.status
            END,
            updated_at = NOW()
        """,
        alert_id,
        opcenter_event_id,
        station_id,
        line_id,
        alert_type,
        predicted_failure_type,
        minutes,
        recommended_action,
        status,
        detected_at,
    )


async def run_andon_consumer(
    bootstrap_servers: str,
    sasl_username: str,
    sasl_password: str,
    pool: Any,
) -> None:
    """Run the Andon Kafka consumer loop until cancelled."""
    from aiokafka import AIOKafkaConsumer
    from aiokafka.errors import KafkaError

    kwargs: dict = {
        "bootstrap_servers": bootstrap_servers,
        "group_id": CONSUMER_GROUP,
        "value_deserializer": lambda v: json.loads(v.decode()),
        "auto_offset_reset": "earliest",
        "enable_auto_commit": True,
    }
    if sasl_username:
        kwargs.update(
            {
                "security_protocol": "SASL_SSL",
                "sasl_mechanism": "SCRAM-SHA-512",
                "sasl_plain_username": sasl_username,
                "sasl_plain_password": sasl_password,
            }
        )

    consumer = AIOKafkaConsumer(TOPIC_ANDON, **kwargs)
    await consumer.start()
    logger.info("Andon consumer started: topic=%s group=%s", TOPIC_ANDON, CONSUMER_GROUP)

    try:
        async for msg in consumer:
            try:
                event: dict = msg.value
                async with pool.acquire() as conn:
                    await process_andon_event(conn, event)
            except asyncio.CancelledError:
                raise
            except KafkaError as exc:
                logger.error("Kafka error processing Andon event: %s", exc)
            except (KeyError, ValueError, TypeError) as exc:
                logger.exception("Malformed Andon event dropped: %s", exc)
    except asyncio.CancelledError:
        logger.info("Andon consumer cancelled — shutting down")
    finally:
        await consumer.stop()
        logger.info("Andon consumer stopped")
