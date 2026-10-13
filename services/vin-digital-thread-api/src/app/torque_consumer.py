"""Kafka consumer for TorqueSubmittedEvent — links TorqueRecord nodes to VIN in Neo4j.

Consumes bmw.ifos.munich.line-command.torque.submitted.v1 under the
digital-thread-writer-consumer group.  For each event it executes an idempotent
Neo4j MERGE on a TorqueRecord node keyed by torqueLogId, then MERGEs a
TORQUE_LOG relationship from the corresponding VIN node.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

logger = logging.getLogger(__name__)

TOPIC_TORQUE_SUBMITTED = "bmw.ifos.munich.line-command.torque.submitted.v1"
_CONSUMER_GROUP = "digital-thread-writer-consumer"
_NEO4J_WRITE_TIMEOUT_S = 1.5  # leaves margin within the 2-second end-to-end SLA

_consumer: AIOKafkaConsumer | None = None
_consumer_task: asyncio.Task | None = None  # type: ignore[type-arg]


async def _neo4j_link_torque(event: dict[str, Any]) -> None:
    """MERGE TorqueRecord node and TORQUE_LOG edge from VIN node in Neo4j."""
    from neo4j import AsyncGraphDatabase

    from app.config import get_settings

    cfg = get_settings()
    async with AsyncGraphDatabase.driver(
        cfg.neo4j_uri,
        auth=(cfg.neo4j_user, cfg.neo4j_password),
    ) as driver:
        async with driver.session() as session:
            await session.run(
                """
                MERGE (t:TorqueRecord {torqueLogId: $torque_log_id})
                ON CREATE SET
                    t.vin              = $vin,
                    t.stationId        = $station_id,
                    t.fastenerPointId  = $fastener_point_id,
                    t.torqueNm         = $torque_nm,
                    t.anglesDeg        = $angles_deg,
                    t.plcTimestamp     = $plc_timestamp,
                    t.unsPublishedAt   = $uns_published_at
                WITH t
                MERGE (v:VIN {vin: $vin})
                MERGE (v)-[r:TORQUE_LOG {torqueLogId: $torque_log_id}]->(t)
                ON CREATE SET
                    r.fastenerPointId  = $fastener_point_id,
                    r.torqueNm         = $torque_nm,
                    r.anglesDeg        = $angles_deg,
                    r.stationId        = $station_id,
                    r.plcTimestamp     = $plc_timestamp,
                    r.unsPublishedAt   = $uns_published_at
                """,
                torque_log_id=event["torqueLogId"],
                vin=event["vin"],
                station_id=event.get("stationId", ""),
                fastener_point_id=event.get("fastenerPointId", ""),
                torque_nm=event.get("torqueNm", 0.0),
                angles_deg=event.get("anglesDeg", 0.0),
                plc_timestamp=event.get("plcTimestamp", ""),
                uns_published_at=event.get("unsPublishedAt"),
            )


async def _process_message(raw: bytes) -> None:
    try:
        event: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.error("TorqueSubmittedEvent: failed to deserialise message — skipping")
        return

    torque_log_id = event.get("torqueLogId")
    vin = event.get("vin")
    if not torque_log_id or not vin:
        logger.error(
            "TorqueSubmittedEvent: missing required fields torqueLogId/vin — skipping: %s",
            event,
        )
        return

    try:
        await asyncio.wait_for(
            _neo4j_link_torque(event),
            timeout=_NEO4J_WRITE_TIMEOUT_S,
        )
        logger.info(
            "TorqueRecord linked: torqueLogId=%s vin=%s", torque_log_id, vin
        )
    except asyncio.TimeoutError:
        logger.error(
            "Neo4j write timed out for torqueLogId=%s vin=%s", torque_log_id, vin
        )
    except (OSError, RuntimeError) as err:
        logger.exception(
            "Neo4j write failed for torqueLogId=%s vin=%s", torque_log_id, vin, exc_info=err
        )


async def _consume_loop(consumer: AIOKafkaConsumer) -> None:
    try:
        async for msg in consumer:
            if msg.value is None:
                continue
            await _process_message(msg.value)
    except KafkaError:
        logger.exception("TorqueSubmittedEvent consumer encountered a Kafka error")
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await consumer.stop()
        except Exception:  # noqa: BLE001
            pass


async def start_torque_consumer() -> None:
    """Start the background Kafka consumer task; no-op if already running."""
    global _consumer, _consumer_task

    if _consumer_task is not None and not _consumer_task.done():
        return

    from app.config import get_settings

    cfg = get_settings()
    _consumer = AIOKafkaConsumer(
        TOPIC_TORQUE_SUBMITTED,
        bootstrap_servers=cfg.kafka_bootstrap_servers,
        group_id=_CONSUMER_GROUP,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    await _consumer.start()
    _consumer_task = asyncio.create_task(_consume_loop(_consumer))
    logger.info("TorqueSubmittedEvent consumer started on topic=%s", TOPIC_TORQUE_SUBMITTED)


async def stop_torque_consumer() -> None:
    """Cancel the consumer task and await its completion."""
    global _consumer, _consumer_task

    if _consumer_task is not None:
        _consumer_task.cancel()
        try:
            await _consumer_task
        except asyncio.CancelledError:
            pass
        _consumer_task = None

    if _consumer is not None:
        try:
            await _consumer.stop()
        except Exception:  # noqa: BLE001
            pass
        _consumer = None
