"""Business logic for POST /vin/{vin}/thread/events.

Write order (per pg-write-ahead-log-before-neo4j decision):
  1. INSERT into thread_event_ingest_log (status=PENDING) — idempotency check
  2. CREATE event node in Neo4j and MERGE link to VIN node
  3. UPDATE thread_event_ingest_log.status = LINKED
  4. Publish VinThreadEventLinked to Kafka
  5. UPSERT completeness cache
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone

import asyncpg

from app.models import ThreadEventIngestRequest, ThreadEventIngestResponse

logger = logging.getLogger(__name__)

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

_NEO4J_WRITE_TIMEOUT_S = 4.0
_PG_WRITE_TIMEOUT_S = 2.0


def _now_utc_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


async def ingest_event(
    vin: str,
    request: ThreadEventIngestRequest,
    user_id: str,
    pool: asyncpg.Pool,
) -> ThreadEventIngestResponse:
    """Ingest one manufacturing event and link it to the VIN digital thread."""
    if not _VIN_RE.match(vin):
        raise _VinFormatError(vin)

    idempotency_key = str(request.idempotencyKey)
    event_id = uuid.uuid4()
    linked_at = _now_utc_iso()

    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT event_id FROM thread_event_ingest_log WHERE idempotency_key = $1",
                idempotency_key,
            )
            if existing is not None:
                raise _DuplicateEventError(idempotency_key)

            await conn.execute(
                """
                INSERT INTO thread_event_ingest_log
                    (event_id, vin, idempotency_key, event_type, shop, station,
                     occurred_at, data_source, parameters, stage_number,
                     tunnel_position_mm, status, ingested_by, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7::timestamptz, $8, $9::jsonb,
                        $10, $11, 'PENDING', $12, now())
                """,
                str(event_id),
                vin,
                idempotency_key,
                request.eventType.value,
                request.shop,
                request.station,
                request.occurredAt,
                request.dataSource,
                _jsonb_params(request.parameters),
                request.stageNumber,
                request.tunnelPositionMm,
                user_id,
            )

    try:
        await asyncio.wait_for(
            _neo4j_link_event(event_id, vin, request, linked_at),
            timeout=_NEO4J_WRITE_TIMEOUT_S,
        )
    except asyncio.TimeoutError as err:
        logger.error(
            "Neo4j write timed out for event_id=%s vin=%s", event_id, vin
        )
        raise _Neo4jUnavailableError() from err
    except (OSError, RuntimeError) as err:
        logger.exception("Neo4j write failed for event_id=%s vin=%s", event_id, vin)
        raise _Neo4jWriteError() from err

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE thread_event_ingest_log SET status = 'LINKED', linked_at = $1::timestamptz WHERE event_id = $2",
            linked_at,
            str(event_id),
        )

    asyncio.create_task(
        _publish_event_linked(str(event_id), vin, request, linked_at, user_id)
    )
    asyncio.create_task(_refresh_completeness_cache(vin, pool))

    return ThreadEventIngestResponse(
        vin=vin,
        eventId=event_id,
        linkedAt=linked_at,
    )


async def _neo4j_link_event(
    event_id: uuid.UUID,
    vin: str,
    request: ThreadEventIngestRequest,
    linked_at: str,
) -> None:
    """Create event node and MERGE VIN→event edge in Neo4j."""
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
                MERGE (v:VIN {vin: $vin})
                CREATE (e:ManufacturingEvent {
                    eventId: $event_id,
                    eventType: $event_type,
                    shop: $shop,
                    station: $station,
                    occurredAt: $occurred_at,
                    dataSource: $data_source,
                    linkedAt: $linked_at
                })
                CREATE (v)-[:HAS_EVENT]->(e)
                """,
                vin=vin,
                event_id=str(event_id),
                event_type=request.eventType.value,
                shop=request.shop,
                station=request.station,
                occurred_at=request.occurredAt,
                data_source=request.dataSource,
                linked_at=linked_at,
            )


async def _publish_event_linked(
    event_id: str,
    vin: str,
    request: ThreadEventIngestRequest,
    linked_at: str,
    user_id: str,
) -> None:
    from app.kafka_producer import TOPIC_EVENT_LINKED, publish

    payload = {
        "eventId": event_id,
        "vin": vin,
        "eventType": request.eventType.value,
        "shop": request.shop,
        "station": request.station,
        "occurredAt": request.occurredAt,
        "linkedAt": linked_at,
        "ingestedBy": user_id,
    }
    await publish(TOPIC_EVENT_LINKED, payload)


async def _refresh_completeness_cache(vin: str, pool: asyncpg.Pool) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO vin_thread_completeness_cache (vin, updated_at)
                VALUES ($1, now())
                ON CONFLICT (vin) DO UPDATE SET updated_at = now()
                """,
                vin,
            )
    except asyncpg.PostgresError as err:
        logger.exception("Failed to refresh completeness cache for vin=%s", vin, exc_info=err)


def _jsonb_params(params: dict) -> str:
    import json

    return json.dumps(params)


class _VinFormatError(ValueError):
    def __init__(self, vin: str) -> None:
        super().__init__(f"VIN format invalid: {vin!r}")
        self.vin = vin


class _DuplicateEventError(Exception):
    def __init__(self, idempotency_key: str) -> None:
        super().__init__(f"Duplicate idempotency key: {idempotency_key}")
        self.idempotency_key = idempotency_key


class _Neo4jWriteError(Exception):
    pass


class _Neo4jUnavailableError(Exception):
    pass


VinFormatError = _VinFormatError
DuplicateEventError = _DuplicateEventError
Neo4jWriteError = _Neo4jWriteError
Neo4jUnavailableError = _Neo4jUnavailableError
