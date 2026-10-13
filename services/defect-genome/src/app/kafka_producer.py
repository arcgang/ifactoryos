"""Shared Kafka producer — one producer per process lifetime."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_producer: Any = None
_PRODUCE_TIMEOUT_S = 5.0


async def get_producer() -> Any:
    global _producer
    if _producer is None:
        import aiokafka

        from app.config import get_settings

        cfg = get_settings()
        _producer = aiokafka.AIOKafkaProducer(
            bootstrap_servers=cfg.kafka_bootstrap_servers,
            request_timeout_ms=int(_PRODUCE_TIMEOUT_S * 1000),
        )
        await _producer.start()
    return _producer


async def stop_producer() -> None:
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None


async def publish(topic: str, payload: dict[str, Any]) -> None:
    """Publish a JSON payload to a Kafka topic — best-effort, logs failures."""
    try:
        producer = await get_producer()
        data = json.dumps(payload).encode()
        await asyncio.wait_for(
            producer.send(topic, value=data),
            timeout=_PRODUCE_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to publish to topic=%s: %s", topic, exc)
