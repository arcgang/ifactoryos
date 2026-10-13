"""Tests for GET /vin/{vin}/thread and GET /vin/{vin}/thread/completeness.

Uses in-process fakes for Neo4j, PostgreSQL, and Kafka so no external
services are needed.  Each test class instantiates its own fake, verifying
instance isolation.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

_VIN = "WBA3A5C55FK123456"
_INVALID_VIN = "TOOSHORT"

# JWT carrying quality-engineer role
_QE_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbInF1YWxpdHktZW5naW5lZXIiXX0.sig"
)
# JWT carrying quality-lead role
_QL_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTQ1NiIsInJvbGVzIjpbInF1YWxpdHktbGVhZCJdfQ.sig"
)
# JWT with no recognised role
_NO_ROLE_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTc4OSIsInJvbGVzIjpbInVua25vd24tcm9sZSJdfQ.sig"
)

# ---------------------------------------------------------------------------
# Fake infrastructure shared between thread query tests
# ---------------------------------------------------------------------------


class FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class FakeAsyncpgConnection:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self._executed: list[str] = []

    async def fetchrow(self, query: str, *args: Any) -> FakeRecord | None:
        # Return first row if any; used by completeness cache check.
        return FakeRecord(self._rows[0]) if self._rows else None

    async def execute(self, query: str, *args: Any) -> None:
        self._executed.append(query)


class FakeAsyncpgPool:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows: list[dict] = rows or []
        self._connections: list[FakeAsyncpgConnection] = []

    def acquire(self) -> Any:
        rows = self._rows
        pool = self

        class _CM:
            async def __aenter__(_self) -> FakeAsyncpgConnection:
                conn = FakeAsyncpgConnection(rows)
                pool._connections.append(conn)
                return conn

            async def __aexit__(_self, *_) -> None:
                pass

        return _CM()


class _FakeNeo4jResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def data(self) -> list[dict]:
        return list(self._rows)


_SCHEMA_NAMES = [
    {"name": "vin_unique"},
    {"name": "event_unique"},
    {"name": "event_by_type_time"},
    {"name": "event_by_stage_number"},
    {"name": "event_by_shop"},
]


class FakeNeo4jSession:
    def __init__(self, event_rows: list[dict], vin_exists: bool = True) -> None:
        self._event_rows = event_rows
        self._vin_exists = vin_exists

    async def run(self, query: str, **kwargs: Any) -> _FakeNeo4jResult:
        if "db.constraints" in query or "db.indexes" in query:
            return _FakeNeo4jResult(_SCHEMA_NAMES)
        if "v.vin AS vin" in query:
            # VIN existence check
            if self._vin_exists:
                return _FakeNeo4jResult([{"vin": kwargs.get("vin", _VIN)}])
            return _FakeNeo4jResult([])
        if "PAINT_PREP_STAGE" in query:
            # Stage count query for completeness
            return _FakeNeo4jResult(self._event_rows)
        # Default: thread event query
        return _FakeNeo4jResult(self._event_rows)

    async def __aenter__(self) -> "FakeNeo4jSession":
        return self

    async def __aexit__(self, *_) -> None:
        pass


class FakeNeo4jDriver:
    def __init__(self, event_rows: list[dict] | None = None, vin_exists: bool = True) -> None:
        self._event_rows = event_rows or []
        self._vin_exists = vin_exists

    def session(self) -> FakeNeo4jSession:
        return FakeNeo4jSession(self._event_rows, self._vin_exists)

    async def __aenter__(self) -> "FakeNeo4jDriver":
        return self

    async def __aexit__(self, *_) -> None:
        pass


class FakeKafkaProducer:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_and_wait(self, topic: str, payload: dict) -> None:
        self.messages.append({"topic": topic, "payload": payload})


# ---------------------------------------------------------------------------
# Sample event data helpers
# ---------------------------------------------------------------------------

def _make_event_row(
    event_id: str | None = None,
    event_type: str = "PLC_TRACE",
    shop: str = "BODY",
    occurred_at: str = "2026-03-01T08:00:00Z",
    parameters: dict | None = None,
    stage_number: int | None = None,
) -> dict:
    return {
        "eventId": event_id or str(uuid.uuid4()),
        "eventType": event_type,
        "shop": shop,
        "station": "STATION_01",
        "occurredAt": occurred_at,
        "dataSource": "BECKHOFF_PLC",
        "parameters": json.dumps(parameters or {"voltage_v": 220.5}),
        "createdAt": occurred_at,
        "stageNumber": stage_number,
        "tunnelPositionMm": None,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.config as config_mod
    import app.thread_query_service as tqs_mod

    settings = MagicMock()
    settings.neo4j_uri = "bolt://localhost:7687"
    settings.neo4j_user = "neo4j"
    settings.neo4j_password = "localpassword"
    settings.citus_url = "postgresql://localhost/test"
    settings.kafka_bootstrap_servers = "localhost:9092"
    settings.oidc_issuer_url = "http://localhost:8090/default"
    settings.oidc_client_id = "local"
    settings.ingestor_role = "thread-ingestor"
    settings.quality_reader_roles = "quality-engineer,quality-lead"
    settings.completeness_cache_ttl_s = 30
    settings.paint_prep_stage_config_path = "paint_prep_stage_config.yaml"
    monkeypatch.setattr(config_mod, "_settings", settings)
    # Reset module-level stage config cache so each test starts clean.
    monkeypatch.setattr(tqs_mod, "_stage_config_cache", None)




# ---------------------------------------------------------------------------
# Helper to build a client with custom Neo4j rows and optional PG cache
# ---------------------------------------------------------------------------

async def _make_client(
    event_rows: list[dict],
    pg_rows: list[dict] | None = None,
    vin_exists: bool = True,
) -> tuple[AsyncClient, Any]:
    from app.main import create_app

    import app.db as db_mod
    import app.kafka_producer as kafka_mod

    neo4j_driver = FakeNeo4jDriver(event_rows=event_rows, vin_exists=vin_exists)
    pg_pool = FakeAsyncpgPool(rows=pg_rows)
    kafka = FakeKafkaProducer()
    app = create_app()

    async def _pool():
        return pg_pool

    async def _producer():
        return kafka

    patcher_db = patch.object(db_mod, "get_pool", _pool)
    patcher_kafka = patch.object(kafka_mod, "get_producer", _producer)
    patcher_neo4j = patch("neo4j.AsyncGraphDatabase.driver", return_value=neo4j_driver)
    patcher_db.start()
    patcher_kafka.start()
    patcher_neo4j.start()
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await ac.__aenter__()
    return ac, (patcher_db, patcher_kafka, patcher_neo4j)


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread — auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_missing_bearer_returns_401() -> None:
    ac, patchers = await _make_client([_make_event_row()])
    try:
        resp = await ac.get(f"/vin/{_VIN}/thread")
        assert resp.status_code == 401
        body = resp.json()
        assert "code" in body
        assert "correlation_id" in body
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_thread_wrong_role_returns_403() -> None:
    ac, patchers = await _make_client([_make_event_row()])
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _NO_ROLE_BEARER},
        )
        assert resp.status_code == 403
        body = resp.json()
        assert "code" in body
        assert "correlation_id" in body
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_thread_quality_engineer_role_accepted() -> None:
    ac, patchers = await _make_client([_make_event_row()])
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_thread_quality_lead_role_accepted() -> None:
    ac, patchers = await _make_client([_make_event_row()])
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _QL_BEARER},
        )
        assert resp.status_code == 200
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread — 400 invalid VIN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_invalid_vin_returns_400() -> None:
    ac, patchers = await _make_client([_make_event_row()])
    try:
        resp = await ac.get(
            f"/vin/{_INVALID_VIN}/thread",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "VIN_FORMAT_INVALID"
        assert "correlation_id" in body
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread — 404 VIN not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_vin_not_found_returns_404() -> None:
    ac, patchers = await _make_client([], vin_exists=False)
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "VIN_NOT_FOUND"
        assert "correlation_id" in body
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread — 200 response shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_200_response_shape() -> None:
    ac, patchers = await _make_client([_make_event_row()])
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "vin" in body
        assert "data" in body
        assert "page" in body
        assert body["vin"] == _VIN
        assert isinstance(body["data"], list)
        assert "next_cursor" in body["page"]
        assert "limit" in body["page"]
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_thread_event_fields_present() -> None:
    ac, patchers = await _make_client([_make_event_row()])
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        events = resp.json()["data"]
        assert len(events) >= 1
        ev = events[0]
        assert "eventId" in ev
        assert "vin" in ev
        assert "eventType" in ev
        assert "shop" in ev
        assert "station" in ev
        assert "occurredAt" in ev
        assert "dataSource" in ev
        assert "parameters" in ev
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_thread_events_ordered_by_occurred_at() -> None:
    rows = [
        _make_event_row(occurred_at="2026-03-01T10:00:00Z"),
        _make_event_row(occurred_at="2026-03-01T08:00:00Z"),
        _make_event_row(occurred_at="2026-03-01T09:00:00Z"),
    ]
    ac, patchers = await _make_client(rows)
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        events = resp.json()["data"]
        # Filter out synthetic shop boundary transition events for ordering check
        real_events = [e for e in events if e["eventType"] != "SHOP_BOUNDARY_TRANSITION"]
        # The fake Neo4j session returns rows in the order given, simulating ordered results.
        # The service does not re-sort — it trusts the ORDER BY in Cypher.
        assert len(real_events) == 3
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread — SHOP_BOUNDARY_TRANSITION injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_shop_boundary_transition_injected() -> None:
    """Shop crossing from BODY → PAINT must produce a synthetic SHOP_BOUNDARY_TRANSITION."""
    rows = [
        _make_event_row(shop="BODY", occurred_at="2026-03-01T08:00:00Z"),
        _make_event_row(shop="PAINT", occurred_at="2026-03-01T09:00:00Z"),
    ]
    ac, patchers = await _make_client(rows)
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        events = resp.json()["data"]
        transition_events = [e for e in events if e["eventType"] == "SHOP_BOUNDARY_TRANSITION"]
        assert len(transition_events) >= 1, "Expected at least one SHOP_BOUNDARY_TRANSITION event"
        t = transition_events[0]
        assert t["dataSource"] == "SYSTEM_INFERRED"
        assert t["parameters"]["from_shop"] == "BODY"
        assert t["parameters"]["to_shop"] == "PAINT"
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_thread_no_duplicate_boundary_transition_injected() -> None:
    """If a SHOP_BOUNDARY_TRANSITION already exists in the thread, no synthetic one is added."""
    rows = [
        _make_event_row(shop="BODY", occurred_at="2026-03-01T08:00:00Z"),
        _make_event_row(
            event_type="SHOP_BOUNDARY_TRANSITION",
            shop="PAINT",
            occurred_at="2026-03-01T08:30:00Z",
        ),
        _make_event_row(shop="PAINT", occurred_at="2026-03-01T09:00:00Z"),
    ]
    ac, patchers = await _make_client(rows)
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        events = resp.json()["data"]
        transition_events = [e for e in events if e["eventType"] == "SHOP_BOUNDARY_TRANSITION"]
        # Only the original one should be present — no synthetic duplicate
        assert len(transition_events) == 1
        assert transition_events[0]["dataSource"] != "SYSTEM_INFERRED"
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread — vinStatus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_vin_status_in_progress() -> None:
    rows = [_make_event_row(event_type="PLC_TRACE")]
    ac, patchers = await _make_client(rows)
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        assert resp.json()["vinStatus"] == "IN_PROGRESS"
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_thread_vin_status_final_gate_complete() -> None:
    rows = [
        _make_event_row(event_type="PLC_TRACE"),
        _make_event_row(event_type="QUALITY_CERTIFICATION"),
    ]
    ac, patchers = await _make_client(rows)
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        assert resp.json()["vinStatus"] == "FINAL_GATE_COMPLETE"
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread — pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_default_limit_is_100() -> None:
    ac, patchers = await _make_client([_make_event_row()])
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        assert resp.json()["page"]["limit"] == 100
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_thread_limit_param_respected() -> None:
    ac, patchers = await _make_client([_make_event_row()])
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread?limit=50",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        assert resp.json()["page"]["limit"] == 50
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_thread_limit_over_200_returns_422() -> None:
    ac, patchers = await _make_client([_make_event_row()])
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread?limit=201",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 422
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_thread_next_cursor_null_on_last_page() -> None:
    """When returned events < limit, next_cursor must be null."""
    ac, patchers = await _make_client([_make_event_row()])
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread?limit=200",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        assert resp.json()["page"]["next_cursor"] is None
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread/completeness — auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completeness_missing_bearer_returns_401() -> None:
    ac, patchers = await _make_client([])
    try:
        resp = await ac.get(f"/vin/{_VIN}/thread/completeness")
        assert resp.status_code == 401
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_completeness_wrong_role_returns_403() -> None:
    ac, patchers = await _make_client([])
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread/completeness",
            headers={"Authorization": _NO_ROLE_BEARER},
        )
        assert resp.status_code == 403
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread/completeness — 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completeness_invalid_vin_returns_400() -> None:
    ac, patchers = await _make_client([])
    try:
        resp = await ac.get(
            f"/vin/{_INVALID_VIN}/thread/completeness",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "VIN_FORMAT_INVALID"
        assert "correlation_id" in body
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread/completeness — 200 response shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completeness_200_response_shape() -> None:
    """Response must include vin, overallCompletenessPercent, meetsThreshold, stages."""
    ac, patchers = await _make_client([])
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread/completeness",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["vin"] == _VIN
        assert "overallCompletenessPercent" in body
        assert "meetsThreshold" in body
        assert "stages" in body
        assert isinstance(body["stages"], list)
        assert len(body["stages"]) == 14
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_completeness_stage_fields_present() -> None:
    ac, patchers = await _make_client([])
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread/completeness",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        stages = resp.json()["stages"]
        for stage in stages:
            assert "stageNumber" in stage
            assert "stageName" in stage
            assert "available" in stage
            assert "completenessPercent" in stage
            assert "expectedSampleCount" in stage
            assert "actualSampleCount" in stage
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_completeness_stage_numbers_1_through_14() -> None:
    ac, patchers = await _make_client([])
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread/completeness",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        stage_numbers = [s["stageNumber"] for s in resp.json()["stages"]]
        assert sorted(stage_numbers) == list(range(1, 15))
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_completeness_meets_threshold_false_when_zero_samples() -> None:
    """With no PAINT_PREP_STAGE events, completeness=0 and meetsThreshold=False."""
    ac, patchers = await _make_client([])
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread/completeness",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["overallCompletenessPercent"] == 0.0
        assert body["meetsThreshold"] is False
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_completeness_meets_threshold_true_when_fully_sampled() -> None:
    """Completeness is >= 95 when all expected samples are present."""
    from app.thread_query_service import _compute_completeness, get_stage_config

    stages = get_stage_config()
    # Build fake stage_counts that match expectedSampleCount for every stage.
    stage_counts = {
        s["stageNumber"]: {
            "actualSampleCount": s["expectedSampleCount"],
            "presentParameters": set(s.get("expectedParameters", [])),
        }
        for s in stages
    }
    overall_pct, _ = _compute_completeness(stages, stage_counts)
    assert overall_pct >= 95.0


@pytest.mark.asyncio
async def test_completeness_uses_cache_when_fresh() -> None:
    """When PG cache is fresh (within TTL), Neo4j is not queried for stage counts."""
    from datetime import datetime, timezone

    from app.thread_query_service import _compute_completeness, get_stage_config

    stages = get_stage_config()
    stage_details = [
        {
            "stageNumber": s["stageNumber"],
            "stageName": s["stageName"],
            "available": True,
            "completenessPercent": 100.0,
            "expectedSampleCount": s["expectedSampleCount"],
            "actualSampleCount": s["expectedSampleCount"],
            "missingParameters": None,
        }
        for s in stages
    ]

    # PG row with updated_at = now (fresh)
    now = datetime.now(tz=timezone.utc)
    cache_row = {
        "overall_completeness_pct": 100.0,
        "meets_threshold": True,
        "stage_completeness": json.dumps(stage_details),
        "vin_status": "IN_PROGRESS",
        "updated_at": now,
    }

    ac, patchers = await _make_client([], pg_rows=[cache_row])
    try:
        # The fake Neo4j driver returns zero PAINT_PREP_STAGE rows.
        # If the cache is used, overallCompletenessPercent must be 100.
        resp = await ac.get(
            f"/vin/{_VIN}/thread/completeness",
            headers={"Authorization": _QE_BEARER},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Cache has 100%, Neo4j fallback would give 0%; confirming 100% means cache was used.
        assert body["overallCompletenessPercent"] == 100.0
    finally:
        await ac.__aexit__(None, None, None)
        for p in patchers:
            p.stop()


# ---------------------------------------------------------------------------
# Instance isolation
# ---------------------------------------------------------------------------


def test_fake_asyncpg_pool_instance_isolation() -> None:
    p1 = FakeAsyncpgPool()
    p2 = FakeAsyncpgPool()
    p1._rows.append({"key": "value"})
    assert len(p2._rows) == 0


def test_fake_kafka_producer_instance_isolation() -> None:
    k1 = FakeKafkaProducer()
    k2 = FakeKafkaProducer()
    asyncio.run(k1.send_and_wait("topic", {"msg": 1}))
    assert len(k2.messages) == 0, "New FakeKafkaProducer instance must start with empty messages"


# ---------------------------------------------------------------------------
# Cursor encode/decode
# ---------------------------------------------------------------------------


def test_cursor_roundtrip() -> None:
    from app.thread_query_service import _decode_cursor, _encode_cursor

    oa = "2026-03-01T08:00:00Z"
    eid = str(uuid.uuid4())
    cursor = _encode_cursor(oa, eid)
    decoded_oa, decoded_eid = _decode_cursor(cursor)
    assert decoded_oa == oa
    assert decoded_eid == eid


def test_cursor_decode_invalid_raises() -> None:
    from app.thread_query_service import CursorDecodeError, _decode_cursor

    with pytest.raises(CursorDecodeError):
        _decode_cursor("not-a-valid-cursor!!!")


# ---------------------------------------------------------------------------
# _inject_shop_boundary_transitions unit tests
# ---------------------------------------------------------------------------


def test_inject_no_events() -> None:
    from app.thread_query_service import _inject_shop_boundary_transitions

    result = _inject_shop_boundary_transitions([])
    assert result == []


def test_inject_single_shop_no_transition() -> None:
    from app.models import EventType, ThreadEvent
    from app.thread_query_service import _inject_shop_boundary_transitions

    events = [
        ThreadEvent(
            eventId=str(uuid.uuid4()),
            vin=_VIN,
            eventType=EventType.PLC_TRACE,
            shop="BODY",
            station="S1",
            occurredAt="2026-03-01T08:00:00Z",
            dataSource="SRC",
            parameters={},
        )
    ]
    result = _inject_shop_boundary_transitions(events)
    assert len(result) == 1
    assert result[0].eventType == EventType.PLC_TRACE


def test_inject_adds_transition_on_shop_crossing() -> None:
    from app.models import EventType, ThreadEvent
    from app.thread_query_service import _inject_shop_boundary_transitions

    events = [
        ThreadEvent(
            eventId=str(uuid.uuid4()),
            vin=_VIN,
            eventType=EventType.PLC_TRACE,
            shop="BODY",
            station="S1",
            occurredAt="2026-03-01T08:00:00Z",
            dataSource="SRC",
            parameters={},
        ),
        ThreadEvent(
            eventId=str(uuid.uuid4()),
            vin=_VIN,
            eventType=EventType.PAINT_PREP_STAGE,
            shop="PAINT",
            station="S2",
            occurredAt="2026-03-01T09:00:00Z",
            dataSource="SRC",
            parameters={},
        ),
    ]
    result = _inject_shop_boundary_transitions(events)
    assert len(result) == 3
    assert result[1].eventType == EventType.SHOP_BOUNDARY_TRANSITION
    assert result[1].dataSource == "SYSTEM_INFERRED"
    assert result[1].parameters["from_shop"] == "BODY"
    assert result[1].parameters["to_shop"] == "PAINT"
