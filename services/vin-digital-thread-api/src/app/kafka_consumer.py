"""Kafka consumer for factory.munich.uns.manufacturing.event.v1.

Consumes UnsManufacturingEvent records from the UNS backbone, extracts VIN and
event fields, and passes them through the same ingest pipeline (PII scrubbing,
idempotency, Neo4j write, Kafka publishing) as the HTTP endpoint.  The Kafka
messageId is used as the idempotencyKey to guarantee exactly-once processing.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_consumer_task: asyncio.Task[None] | None = None


async def start_consumer() -> None:
    """Start the background consumer loop; idempotent."""
    global _consumer_task
    if _consumer_task is None or _consumer_task.done():
        _consumer_task = asyncio.create_task(_consume_loop(), name="uns-consumer")


async def stop_consumer() -> None:
    """Cancel and await the background consumer task."""
    global _consumer_task
    if _consumer_task is not None and not _consumer_task.done():
        _consumer_task.cancel()
        try:
            await _consumer_task
        except asyncio.CancelledError:
            pass
    _consumer_task = None


async def _consume_loop() -> None:
    from aiokafka import AIOKafkaConsumer
    from aiokafka.errors import KafkaError

    from app.config import get_settings

    cfg = get_settings()

    consumer = AIOKafkaConsumer(
        cfg.uns_manufacturing_topic,
        bootstrap_servers=cfg.kafka_bootstrap_servers,
        group_id=cfg.uns_consumer_group,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    try:
        await consumer.start()
        logger.info(
            "UNS consumer started on topic=%s group=%s",
            cfg.uns_manufacturing_topic,
            cfg.uns_consumer_group,
        )
        async for message in consumer:
            await _handle_message(message)
    except asyncio.CancelledError:
        logger.info("UNS consumer cancelled")
        raise
    except KafkaError:
        logger.exception("UNS consumer Kafka error; loop exiting")
    finally:
        await consumer.stop()


async def _handle_message(message: Any) -> None:
    import json

    from app.db import get_pool
    from app.ingest_service import (
        DuplicateEventError,
        VinFormatError,
        ingest_event,
    )
    from app.models import EventType, ThreadEventIngestRequest

    try:
        raw: dict[str, Any] = json.loads(message.value)
    except (ValueError, TypeError):
        logger.error(
            "UNS consumer: failed to parse message offset=%s", message.offset
        )
        return

    vin: str | None = raw.get("vin")
    if not vin:
        logger.warning("UNS consumer: message has no 'vin' field, skipping offset=%s", message.offset)
        return

    # Use Kafka messageId as idempotency key; fall back to headers or offset-based UUID.
    message_id: str = raw.get("messageId") or raw.get("message_id") or str(
        uuid.uuid5(uuid.NAMESPACE_OID, f"{message.topic}:{message.partition}:{message.offset}")
    )

    event_type_str: str = raw.get("eventType", "")
    try:
        event_type = EventType(event_type_str)
    except ValueError:
        logger.warning(
            "UNS consumer: unknown eventType=%r at offset=%s, skipping", event_type_str, message.offset
        )
        return

    request = ThreadEventIngestRequest(
        idempotencyKey=uuid.UUID(message_id),
        eventType=event_type,
        shop=raw.get("shop", ""),
        station=raw.get("station", ""),
        occurredAt=raw.get("occurredAt", ""),
        dataSource=raw.get("dataSource", "uns-backbone"),
        parameters=raw.get("parameters", {}),
        stageNumber=raw.get("stageNumber"),
        tunnelPositionMm=raw.get("tunnelPositionMm"),
    )

    ingestor_id: str = raw.get("ingestedBy") or "uns-backbone-consumer"

    try:
        pool = await get_pool()
        await ingest_event(vin=vin, request=request, user_id=ingestor_id, pool=pool)
    except DuplicateEventError:
        logger.debug("UNS consumer: duplicate messageId=%s, skipped", message_id)
    except VinFormatError:
        logger.warning("UNS consumer: invalid VIN=%r at offset=%s, skipping", vin, message.offset)
    except (OSError, RuntimeError, ValueError) as err:
        logger.exception(
            "UNS consumer: unhandled error processing offset=%s vin=%r: %s", message.offset, vin, err
        )
