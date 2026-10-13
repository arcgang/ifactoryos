"""Kafka consumer for PlcTorqueTraceEvent messages from the UNS.

Consumes from bmw.ifos.munich.uns.torque.plc-trace.v1 and delegates
processing to torque_service.process_torque_event.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

TOPIC_TORQUE_PLC_TRACE = "bmw.ifos.munich.uns.torque.plc-trace.v1"
CONSUMER_GROUP = "line-command-torque-consumer"


async def run_torque_consumer(
    bootstrap_servers: str,
    sasl_username: str,
    sasl_password: str,
    vdt_base_url: str,
    vdt_timeout_seconds: float,
    pool: Any,
) -> None:
    """Run the Kafka consumer loop until cancelled.

    Designed to be started as a background asyncio task from the lifespan hook.
    """
    from aiokafka import AIOKafkaConsumer
    from aiokafka.errors import KafkaError

    from app.kafka_producer import get_producer
    from app.torque_service import process_torque_event

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

    consumer = AIOKafkaConsumer(TOPIC_TORQUE_PLC_TRACE, **kwargs)
    await consumer.start()
    logger.info(
        "Torque consumer started: topic=%s group=%s",
        TOPIC_TORQUE_PLC_TRACE,
        CONSUMER_GROUP,
    )
    producer = await get_producer()

    async def _publish(topic: str, payload: dict) -> None:
        await producer.send_and_wait(topic, payload)

    try:
        async for msg in consumer:
            try:
                event: dict = msg.value
                async with pool.acquire() as conn:
                    await process_torque_event(
                        conn=conn,
                        event=event,
                        publish_fn=_publish,
                        vdt_base_url=vdt_base_url,
                        vdt_timeout_seconds=vdt_timeout_seconds,
                        pool=pool,
                    )
            except asyncio.CancelledError:
                raise
            except KafkaError as exc:
                logger.error("Kafka error processing torque event: %s", exc)
            except (KeyError, ValueError, TypeError) as exc:
                logger.exception("Malformed torque event dropped: %s", exc)
    except asyncio.CancelledError:
        logger.info("Torque consumer cancelled — shutting down")
    finally:
        await consumer.stop()
        logger.info("Torque consumer stopped")
