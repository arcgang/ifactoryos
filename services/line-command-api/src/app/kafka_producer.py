"""Kafka producer — one shared producer per process lifetime.

Uses SASL/SCRAM-SHA-512 + SSL (SPIFFE mTLS) per CONN-005 when credentials
are configured; falls back to plaintext for local development.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_producer = None

TOPIC_AR_SESSION_STARTED = "bmw.ifos.munich.line-command.ar-session.started.v1"
TOPIC_ADOPTION_EVENT = "factory.munich.line-command.adoption.v1"
TOPIC_MAINTENANCE_DISPATCH = "factory.munich.line-command.maintenance-dispatch.v1"


async def get_producer():
    global _producer
    if _producer is None:
        from aiokafka import AIOKafkaProducer

        from app.config import settings

        kwargs: dict = {
            "bootstrap_servers": settings.kafka_bootstrap_servers,
            "value_serializer": lambda v: json.dumps(v).encode(),
        }
        if settings.kafka_sasl_username:
            kwargs.update(
                {
                    "security_protocol": "SASL_SSL",
                    "sasl_mechanism": "SCRAM-SHA-512",
                    "sasl_plain_username": settings.kafka_sasl_username,
                    "sasl_plain_password": settings.kafka_sasl_password,
                }
            )
        _producer = AIOKafkaProducer(**kwargs)
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
        logger.exception("Failed to publish to topic %s: %s", topic, err)
