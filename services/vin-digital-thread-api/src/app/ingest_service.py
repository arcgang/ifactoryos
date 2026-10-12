"""Business logic for POST /vin/{vin}/thread/events.

Write order (per pg-write-ahead-log-before-neo4j decision):
  1. PII scrub parameters map (replace raw employee IDs with pseudonymous tokens)
  2. INSERT into thread_event_ingest_log (status=PENDING) — idempotency check
  3. CREATE event node in Neo4j (scrubbed parameters only) and MERGE link to VIN node
  4. UPDATE thread_event_ingest_log.status = LINKED
  5. Publish VinThreadEventLinked to Kafka (scrubbed payload)
  6. UPSERT completeness cache; publish completeness alert if < 95% threshold
  7. Publish VinThreadFinalGateComplete if VIN reaches FINAL_GATE_COMPLETE status
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
_COMPLETENESS_THRESHOLD = 95.0


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

    from app.config import get_settings
    from app.pii_scrubber import scrub_parameters

    cfg = get_settings()

    # --- PII scrub: replace raw employee IDs with pseudonymous tokens ---
    scrubbed_parameters = await scrub_parameters(
        request.parameters,
        identity_service_url=cfg.identity_service_url,
        timeout_s=cfg.identity_service_timeout_s,
    )

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
                _jsonb_params(scrubbed_parameters),
                request.stageNumber,
                request.tunnelPositionMm,
                user_id,
            )

    try:
        await asyncio.wait_for(
            _neo4j_link_event(event_id, vin, request, linked_at, scrubbed_parameters),
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

    # Fire-and-forget downstream tasks — must not block the HTTP response.
    asyncio.create_task(
        _publish_event_linked(str(event_id), vin, request, linked_at, user_id, scrubbed_parameters)
    )
    asyncio.create_task(_refresh_completeness_and_alert(vin, pool))

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
    scrubbed_parameters: dict,
) -> None:
    """Create event node and MERGE VIN→event edge in Neo4j.

    Only scrubbed_parameters are written — raw employee IDs never reach Neo4j.
    """
    import json

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
                MERGE (e:ManufacturingEvent {eventId: $event_id})
                SET e += {
                    eventType: $event_type,
                    shop: $shop,
                    station: $station,
                    occurredAt: $occurred_at,
                    dataSource: $data_source,
                    parameters: $parameters,
                    linkedAt: $linked_at
                }
                MERGE (v)-[:HAS_EVENT]->(e)
                """,
                vin=vin,
                event_id=str(event_id),
                event_type=request.eventType.value,
                shop=request.shop,
                station=request.station,
                occurred_at=request.occurredAt,
                data_source=request.dataSource,
                parameters=json.dumps(scrubbed_parameters),
                linked_at=linked_at,
            )


async def _publish_event_linked(
    event_id: str,
    vin: str,
    request: ThreadEventIngestRequest,
    linked_at: str,
    user_id: str,
    scrubbed_parameters: dict,
) -> None:
    from app.kafka_producer import TOPIC_EVENT_LINKED, publish

    # Never include raw PII in Kafka payloads — use scrubbed parameters.
    payload = {
        "eventId": event_id,
        "vin": vin,
        "eventType": request.eventType.value,
        "shop": request.shop,
        "station": request.station,
        "occurredAt": request.occurredAt,
        "linkedAt": linked_at,
        "ingestedBy": user_id,
        "parameters": scrubbed_parameters,
    }
    await publish(TOPIC_EVENT_LINKED, payload)


async def _refresh_completeness_and_alert(vin: str, pool: asyncpg.Pool) -> None:
    """Upsert completeness cache; publish alerts when threshold is breached or VIN completes."""
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
            row = await conn.fetchrow(
                """
                SELECT overall_completeness_pct, meets_threshold, alert_published, vin_status
                FROM vin_thread_completeness_cache
                WHERE vin = $1
                """,
                vin,
            )
    except asyncpg.PostgresError as err:
        logger.exception("Failed to refresh completeness cache for vin=%s", vin, exc_info=err)
        return

    if row is None:
        return

    overall_pct = float(row["overall_completeness_pct"])
    alert_published = bool(row["alert_published"])
    vin_status = str(row["vin_status"])

    # Publish completeness alert when below threshold and not yet published.
    if overall_pct < _COMPLETENESS_THRESHOLD and not alert_published:
        asyncio.create_task(_publish_completeness_alert(vin, overall_pct, pool))

    # Publish final gate complete when VIN reaches terminal FINAL_GATE_COMPLETE status.
    if vin_status == "FINAL_GATE_COMPLETE":
        asyncio.create_task(_publish_final_gate_complete(vin))


async def _publish_completeness_alert(
    vin: str,
    completeness_pct: float,
    pool: asyncpg.Pool,
) -> None:
    from app.kafka_producer import TOPIC_COMPLETENESS_ALERT, publish

    payload = {
        "vin": vin,
        "completeness_pct": completeness_pct,
        "threshold_pct": _COMPLETENESS_THRESHOLD,
    }
    try:
        await publish(TOPIC_COMPLETENESS_ALERT, payload)
    except (OSError, RuntimeError) as err:
        logger.exception("Failed to publish completeness alert for vin=%s: %s", vin, err)
        return

    # Mark alert as published so we don't re-publish on every subsequent event.
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE vin_thread_completeness_cache
                SET alert_published = TRUE, alert_published_at = now()
                WHERE vin = $1 AND alert_published = FALSE
                """,
                vin,
            )
    except asyncpg.PostgresError:
        logger.exception("Failed to mark alert_published for vin=%s", vin)


async def _publish_final_gate_complete(vin: str) -> None:
    from app.kafka_producer import TOPIC_FINAL_GATE_COMPLETE, publish

    payload = {
        "vin": vin,
        "status": "FINAL_GATE_COMPLETE",
    }
    try:
        await publish(TOPIC_FINAL_GATE_COMPLETE, payload)
    except (OSError, RuntimeError) as err:
        logger.exception("Failed to publish final gate complete for vin=%s: %s", vin, err)


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
