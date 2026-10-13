"""Gate result ingestion and query service.

Handles OpCenter event deduplication, persistence, and Kafka publishing.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from opentelemetry import trace

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("opcenter-sync-service.gate_result_service")

VALID_GATE_TYPES = frozenset({"dimensional", "leak", "electrical", "nvh", "adas"})
VALID_RESULTS = frozenset({"pass", "conditional_pass", "fail"})


class DBConnection(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Any | None: ...
    async def execute(self, query: str, *args: Any) -> None: ...
    async def fetch(self, query: str, *args: Any) -> list[Any]: ...
    async def fetchval(self, query: str, *args: Any) -> Any | None: ...


async def ingest_gate_result(
    conn: DBConnection,
    *,
    vin: str,
    gate_id: str,
    gate_type: str,
    result: str,
    measurements: list[dict],
    measured_at: datetime,
    opcenter_event_id: str | None,
    publish_fn: Any,
) -> str | None:
    """Persist a gate result row and publish to Kafka.

    Returns the gate_result_id on success, or None when the opcenter_event_id
    is a duplicate (silently dropped per dedup decision).
    """
    if measured_at.tzinfo is None:
        measured_at = measured_at.replace(tzinfo=timezone.utc)

    gate_result_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    measurements_json = json.dumps(measurements)

    with _tracer.start_as_current_span("db.gate_result.insert") as span:
        span.set_attribute("db.table", "gate_result")
        span.set_attribute("vin", vin)
        span.set_attribute("gate_type", gate_type)

        # INSERT … ON CONFLICT DO NOTHING implements dedup on opcenter_event_id
        result_id = await conn.fetchval(
            """
            INSERT INTO gate_result
                (gate_result_id, vin, gate_id, gate_type, result,
                 measurements, measured_at, opcenter_event_id, correlation_id)
            VALUES
                ($1::uuid, $2, $3, $4, $5,
                 $6::jsonb, $7, $8, $9::uuid)
            ON CONFLICT (opcenter_event_id) WHERE opcenter_event_id IS NOT NULL
            DO NOTHING
            RETURNING gate_result_id::text
            """,
            gate_result_id,
            vin,
            gate_id,
            gate_type,
            result,
            measurements_json,
            measured_at,
            opcenter_event_id,
            correlation_id,
        )

    if result_id is None:
        # Duplicate — opcenter_event_id already present
        logger.info(
            "Duplicate gate result dropped: opcenter_event_id=%s vin=%s",
            opcenter_event_id,
            vin,
        )
        return None

    # Publish GateResultEvent to Kafka
    from app.kafka_producer import TOPIC_GATE_RESULT

    event_payload: dict[str, Any] = {
        "gateResultId": gate_result_id,
        "vin": vin,
        "gateId": gate_id,
        "gateType": gate_type,
        "result": result,
        "measurements": measurements,
        "measuredAt": measured_at.isoformat(),
        "receivedFromOpcenterAt": datetime.now(timezone.utc).isoformat(),
        "correlationId": correlation_id,
    }
    if opcenter_event_id is not None:
        event_payload["opcenterEventId"] = opcenter_event_id

    try:
        with _tracer.start_as_current_span("kafka.produce.gate_result_received") as span:
            span.set_attribute("messaging.destination", TOPIC_GATE_RESULT)
            span.set_attribute("vin", vin)
            span.set_attribute("gate_type", gate_type)
            await publish_fn(TOPIC_GATE_RESULT, event_payload)
        logger.info(
            "GateResultEvent published: gate_result_id=%s vin=%s gate_type=%s",
            gate_result_id,
            vin,
            gate_type,
        )
    except (OSError, RuntimeError) as exc:
        logger.error(
            "GateResultEvent publish failed for gate_result_id=%s: %s",
            gate_result_id,
            exc,
        )

    return gate_result_id


def validate_gate_result_payload(
    vin: str,
    gate_type: str,
    result: str,
) -> list[str]:
    """Return a list of validation error messages; empty list means valid."""
    errors: list[str] = []
    import re

    if not re.match(r"^[A-HJ-NPR-Z0-9]{17}$", vin):
        errors.append(f"Invalid VIN format: {vin!r}")
    if gate_type not in VALID_GATE_TYPES:
        errors.append(f"Invalid gateType: {gate_type!r}; must be one of {sorted(VALID_GATE_TYPES)}")
    if result not in VALID_RESULTS:
        errors.append(f"Invalid result: {result!r}; must be one of {sorted(VALID_RESULTS)}")
    return errors
