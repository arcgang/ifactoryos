"""Testcontainers integration test: apply all Cypher migrations and assert schema.

Boots a real Neo4j 5.x container, executes every V*.cypher migration in order,
then asserts that all required constraints and indexes are present.
The test also exercises validate_neo4j_schema() to confirm the validation path
used at API boot time passes against a fully-migrated database.

Skip when SKIP_CONTAINER_TESTS=1 is set (CI environments without Docker).
"""

from __future__ import annotations

import os
import pathlib
import re
import pytest
import pytest_asyncio

SKIP_CONTAINERS = os.getenv("SKIP_CONTAINER_TESTS", "0") == "1"
pytestmark = pytest.mark.skipif(SKIP_CONTAINERS, reason="SKIP_CONTAINER_TESTS=1")

# tests/ → vin-digital-thread-api/ → services/ → repo root
_MIGRATIONS_DIR = (
    pathlib.Path(__file__).parent.parent.parent.parent
    / "platform"
    / "vin-digital-thread"
    / "cypher"
)

# Matches V<digits>__<name>.cypher — applied in numeric order.
_MIGRATION_RE = re.compile(r"^V(\d+)__.*\.cypher$")


def _load_migrations() -> list[str]:
    """Return complete Cypher statements from each V*.cypher file, sorted by version.

    Strips -- comments, then splits on semicolons so each multi-line DDL
    statement is passed to session.run() as a single string.
    """
    files = sorted(
        (f for f in _MIGRATIONS_DIR.iterdir() if _MIGRATION_RE.match(f.name)),
        key=lambda f: int(_MIGRATION_RE.match(f.name).group(1)),  # type: ignore[union-attr]
    )
    statements: list[str] = []
    for path in files:
        raw = path.read_text()
        stripped_lines = [
            line for line in raw.splitlines()
            if not line.strip().startswith("--")
        ]
        source = "\n".join(stripped_lines)
        for stmt in source.split(";"):
            stmt = stmt.strip()
            if stmt:
                statements.append(stmt)
    return statements


# ---------------------------------------------------------------------------
# Module-scoped container — started once, shared across all tests.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def neo4j_container():
    from testcontainers.neo4j import Neo4jContainer

    with Neo4jContainer(image="neo4j:5") as container:
        yield container


@pytest_asyncio.fixture(scope="module")
async def neo4j_driver(neo4j_container):
    from neo4j import AsyncGraphDatabase

    bolt_url = neo4j_container.get_connection_url()
    # testcontainers[neo4j] ≥4.x exposes NEO4J_PASSWORD; default is "password".
    password = getattr(neo4j_container, "NEO4J_PASSWORD", "password")

    driver = AsyncGraphDatabase.driver(bolt_url, auth=("neo4j", password))
    # Apply all migrations before any test in this module runs.
    await _run_migrations(driver)
    yield driver
    await driver.close()


async def _run_migrations(driver) -> None:
    statements = _load_migrations()
    for stmt in statements:
        # Open a fresh session per statement so each DDL auto-commits.
        async with driver.session() as session:
            result = await session.run(stmt)
            await result.consume()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_constraint_names(driver) -> set[str]:
    async with driver.session() as session:
        result = await session.run("CALL db.constraints() YIELD name RETURN name")
        return {record["name"] for record in await result.data()}


async def _get_index_names(driver) -> set[str]:
    async with driver.session() as session:
        result = await session.run("CALL db.indexes() YIELD name RETURN name")
        return {record["name"] for record in await result.data()}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vin_unique_constraint_exists(neo4j_driver) -> None:
    names = await _get_constraint_names(neo4j_driver)
    assert "vin_unique" in names, f"vin_unique not found in {names}"


@pytest.mark.asyncio
async def test_event_unique_constraint_exists(neo4j_driver) -> None:
    names = await _get_constraint_names(neo4j_driver)
    assert "event_unique" in names, f"event_unique not found in {names}"


@pytest.mark.asyncio
async def test_event_by_type_time_index_exists(neo4j_driver) -> None:
    names = await _get_index_names(neo4j_driver)
    assert "event_by_type_time" in names, f"event_by_type_time not found in {names}"


@pytest.mark.asyncio
async def test_event_by_stage_number_index_exists(neo4j_driver) -> None:
    names = await _get_index_names(neo4j_driver)
    assert "event_by_stage_number" in names, f"event_by_stage_number not found in {names}"


@pytest.mark.asyncio
async def test_event_by_shop_index_exists(neo4j_driver) -> None:
    names = await _get_index_names(neo4j_driver)
    assert "event_by_shop" in names, f"event_by_shop not found in {names}"


@pytest.mark.asyncio
async def test_all_schema_objects_pass_validation(neo4j_driver) -> None:
    """validate_neo4j_schema() must not raise after migrations are applied."""
    from app.neo4j_schema import validate_neo4j_schema

    await validate_neo4j_schema(neo4j_driver)


@pytest.mark.asyncio
async def test_validate_schema_raises_on_missing_constraint(neo4j_driver) -> None:
    """validate_neo4j_schema() raises SchemaValidationError when a name is absent."""
    import app.neo4j_schema as schema_mod
    from app.neo4j_schema import SchemaValidationError, validate_neo4j_schema

    original = schema_mod._REQUIRED_CONSTRAINTS
    try:
        schema_mod._REQUIRED_CONSTRAINTS = frozenset(
            {"vin_unique", "nonexistent_constraint_xyz"}
        )
        with pytest.raises(SchemaValidationError, match="missing constraints"):
            await validate_neo4j_schema(neo4j_driver)
    finally:
        schema_mod._REQUIRED_CONSTRAINTS = original
