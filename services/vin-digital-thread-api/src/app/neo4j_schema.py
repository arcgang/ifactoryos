"""Boot-time Neo4j schema validation.

Called from the lifespan hook. Raises SchemaValidationError if any
required constraint or index is absent, so the pod fails its readiness
probe rather than silently operating without idempotency guards.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Every name listed here must be present in db.constraints()/db.indexes()
# after the Cypher migrations have been applied.
_REQUIRED_CONSTRAINTS: frozenset[str] = frozenset(
    {
        "vin_unique",
        "event_unique",
    }
)

_REQUIRED_INDEXES: frozenset[str] = frozenset(
    {
        "event_by_type_time",
        "event_by_stage_number",
        "event_by_shop",
    }
)


class SchemaValidationError(RuntimeError):
    """Raised when required Neo4j constraints or indexes are missing."""


async def validate_neo4j_schema(driver) -> None:  # type: ignore[type-arg]
    """Assert that all required constraints and indexes exist.

    Executes the canonical startup validation queries against the supplied
    async Neo4j driver and raises SchemaValidationError on any gap.

    The driver is passed in (not re-created here) so tests can inject a
    testcontainers-backed driver without touching the global config.
    """
    async with driver.session() as session:
        constraint_result = await session.run(
            "CALL db.constraints() YIELD name RETURN name"
        )
        present_constraints: set[str] = {
            record["name"] for record in await constraint_result.data()
        }

    async with driver.session() as session:
        index_result = await session.run(
            "CALL db.indexes() YIELD name RETURN name"
        )
        present_indexes: set[str] = {
            record["name"] for record in await index_result.data()
        }

    missing_constraints = _REQUIRED_CONSTRAINTS - present_constraints
    missing_indexes = _REQUIRED_INDEXES - present_indexes

    if missing_constraints or missing_indexes:
        msg_parts: list[str] = []
        if missing_constraints:
            msg_parts.append(f"missing constraints: {sorted(missing_constraints)}")
        if missing_indexes:
            msg_parts.append(f"missing indexes: {sorted(missing_indexes)}")
        raise SchemaValidationError(
            "Neo4j schema incomplete — " + "; ".join(msg_parts)
        )

    logger.info(
        "Neo4j schema validation passed: %d constraints, %d indexes confirmed",
        len(_REQUIRED_CONSTRAINTS),
        len(_REQUIRED_INDEXES),
    )
