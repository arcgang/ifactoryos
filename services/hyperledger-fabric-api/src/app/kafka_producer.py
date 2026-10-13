"""Kafka producer — one shared producer per process lifetime."""

from __future__ import annotations

import json
import logging

from aiokafka import AIOKafkaProducer

logger = logging.getLogger(__name__)

_producer: AIOKafkaProducer | None = None

TOPIC_CERTIFICATE_ISSUED = "bmw.ifos.munich.fabric.certificate.issued.v1"
TOPIC_CERTIFICATE_VERIFY_FAILED = "bmw.ifos.munich.fabric.certificate.verify-failed.v1"


async def get_producer() -> AIOKafkaProducer:
    global _producer
    if _producer is None:
        from app.config import get_settings

        cfg = get_settings()
        _producer = AIOKafkaProducer(
            bootstrap_servers=cfg.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode(),
        )
        await _producer.start()
    return _producer


async def stop_producer() -> None:
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None


async def publish(topic: str, payload: dict) -> None:
    from aiokafka.errors import KafkaError

    try:
        producer = await get_producer()
        await producer.send_and_wait(topic, payload)
    except KafkaError as err:
        logger.exception("Failed to publish to topic %s", topic, exc_info=err)
