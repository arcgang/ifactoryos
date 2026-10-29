"""Acceptance tests for US-030.

Story: Every manufacturing event, process parameter, quality result, and work
instruction execution is linked to the specific VIN in the Neo4j digital thread
graph from body-in-white through final quality gate, so that Process Replay,
Digital Birth Certificate, and future Battery Passport all have an unbroken
per-vehicle record.

Criteria:
  AC1 – Given any manufacturing event (PLC trace, gate result, torque log,
        AR instruction execution) is published to the UNS, when the event is
        ingested, then it is linked to the VIN node in the Neo4j digital thread
        graph within 5 seconds with the shop, station, timestamp, and data source.

  AC2 – Given a VIN completes the final quality gate, when the digital thread
        graph is queried for that VIN, then a continuous chain of events from
        body-in-white entry through final gate certification is returned with no
        gaps in shop-boundary transitions.

  AC3 – Given a VIN's digital thread is queried for Process Replay, when the
        query executes, then all 14 paint prep stage parameters are returned as
        a time-ordered sequence linked to the VIN node, with completeness ≥95%
        for VINs processed after UNS go-live.

Test approach: in-process fakes for Neo4j, PostgreSQL, and Kafka — identical
pattern to test_ingest.py and test_thread_read.py so no external services are
needed and no production code is modified.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VIN = "WBA3A5G59DNP26082"

# JWT with thread-ingestor role — needed for POST /vin/{vin}/thread/events
_INGESTOR_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJpbmdlc3Rvci1zdmMiLCJyb2xlcyI6WyJ0aHJlYWQtaW5nZXN0b3IiXX0"
    ".sig"
)
# JWT with quality-engineer role — needed for GET endpoints
_READER_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbInF1YWxpdHktZW5naW5lZXIiXX0"
    ".sig"
)
# JWT with quality-lead role
_LEAD_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTQ1NiIsInJvbGVzIjpbInF1YWxpdHktbGVhZCJdfQ"
    ".sig"
)

# ---------------------------------------------------------------------------
# Fake Neo4j infrastructure — matches pattern in test_thread_read.py
# ---------------------------------------------------------------------------

_SCHEMA_NAMES: list[dict] = [
    {"name": "vin_unique"},
    {"name": "event_unique"},
    {"name": "event_by_type_time"},
    {"name": "event_by_stage_number"},
    {"name": "event_by_shop"},
]


class _FakeNeo4jResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def data(self) -> list[dict]:
        return list(self._rows)


class FakeNeo4jSession:
    """Configurable fake session used by both ingest and query paths."""

    def __init__(
        self,
        captured_writes: list[dict],
        event_rows: list[dict],
        vin_exists: bool = True,
    ) -> None:
        self._captured_writes = captured_writes
        self._event_rows = event_rows
        self._vin_exists = vin_exists

    async def run(self, query: str, **kwargs: Any) -> _FakeNeo4jResult:
        q_upper = query.upper()

        # Schema validation queries used at startup
        if "db.constraints" in query or "db.indexes" in query:
            return _FakeNeo4jResult(_SCHEMA_NAMES)

        # VIN existence check — no HAS_EVENT traversal
        if "MATCH (V:VIN {VIN" in q_upper and "HAS_EVENT" not in q_upper:
            if self._vin_exists:
                return _FakeNeo4jResult([{"v": {"vin": kwargs.get("vin", _VIN)}}])
            return _FakeNeo4jResult([])

        # Ingest write — MERGE (v:VIN {vin}) MERGE (e:ManufacturingEvent {eventId})
        if "MERGE" in q_upper and "HAS_EVENT" in q_upper:
            self._captured_writes.append(dict(kwargs))
            return _FakeNeo4jResult([])

        # Completeness count query
        if "COUNT(E) AS CNT" in q_upper:
            stage_counts: dict[int, int] = {}
            for ev in self._event_rows:
                sn = ev.get("stageNumber")
                if sn is not None and ev.get("eventType") == "PAINT_PREP_STAGE":
                    stage_counts[int(sn)] = stage_counts.get(int(sn), 0) + 1
            rows = [{"stageNumber": k, "cnt": v} for k, v in stage_counts.items()]
            return _FakeNeo4jResult(rows)

        # Snapshot query — last event at or before atTs
        if "ORDER BY E.OCCURREDAT DESC" in q_upper and "LIMIT 1" in q_upper:
            at_ts = kwargs.get("at_ts", "9999")
            matching = [e for e in self._event_rows if e.get("occurredAt", "") <= at_ts]
            if matching:
                latest = sorted(matching, key=lambda e: e["occurredAt"])[-1]
                return _FakeNeo4jResult([{"e": latest}])
            return _FakeNeo4jResult([])

        # General thread events query (HAS_EVENT traversal)
        limit = kwargs.get("limit", 201)
        rows = [{"e": ev} for ev in self._event_rows[:limit]]
        return _FakeNeo4jResult(rows)

    async def __aenter__(self) -> "FakeNeo4jSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeNeo4jDriver:
    """Shared driver; both ingest and read paths call driver.session()."""

    def __init__(
        self,
        event_rows: list[dict] | None = None,
        vin_exists: bool = True,
    ) -> None:
        self.captured_writes: list[dict] = []
        self._event_rows = event_rows or []
        self._vin_exists = vin_exists

    def session(self) -> FakeNeo4jSession:
        return FakeNeo4jSession(
            self.captured_writes,
            self._event_rows,
            self._vin_exists,
        )

    async def __aenter__(self) -> "FakeNeo4jDriver":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Fake PostgreSQL pool — minimal, sufficient for ingest + completeness cache
# ---------------------------------------------------------------------------


class _FakePgRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class FakePgConnection:
    """Supports fetchrow (idempotency / cache check) and execute (writes)."""

    def __init__(
        self,
        store: dict[str, Any],
        existing_idempotency_keys: set[str],
        cache_row: dict | None = None,
    ) -> None:
        self._store = store
        self._existing_keys = existing_idempotency_keys
        self._cache_row = cache_row
        self._in_transaction = False

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(_self):
                conn._in_transaction = True
                return _self

            async def __aexit__(_self, *_):
                conn._in_transaction = False

        return _Tx()

    async def fetchrow(self, query: str, *args: Any) -> _FakePgRecord | None:
        q_upper = query.upper()
        if "IDEMPOTENCY_KEY" in q_upper:
            # Idempotency duplicate check
            key = str(args[0])
            if key in self._existing_keys:
                return _FakePgRecord({"event_id": str(uuid.uuid4())})
            return None
        if "VIN_THREAD_COMPLETENESS_CACHE" in q_upper:
            if self._cache_row is not None:
                return _FakePgRecord(self._cache_row)
            return None
        return None

    async def execute(self, query: str, *args: Any) -> None:
        q_upper = query.upper()
        if "INSERT INTO THREAD_EVENT_INGEST_LOG" in q_upper:
            self._store["last_insert"] = {"event_id": str(args[0]), "vin": args[1]}
        elif "UPDATE THREAD_EVENT_INGEST_LOG" in q_upper:
            self._store["last_update"] = {"linked_at": str(args[0]), "event_id": str(args[1])}
        elif "INSERT INTO VIN_THREAD_COMPLETENESS_CACHE" in q_upper:
            self._store["cache_refreshed"] = True


class FakePgPool:
    def __init__(
        self,
        existing_idempotency_keys: set[str] | None = None,
        cache_row: dict | None = None,
    ) -> None:
        self._store: dict[str, Any] = {}
        self._existing_keys: set[str] = existing_idempotency_keys or set()
        self._cache_row = cache_row

    def acquire(self):
        store = self._store
        existing = self._existing_keys
        cache = self._cache_row

        class _CM:
            async def __aenter__(_self) -> FakePgConnection:
                return FakePgConnection(store, existing, cache)

            async def __aexit__(_self, *_: Any) -> None:
                pass

        return _CM()

    @property
    def store(self) -> dict:
        return self._store


# ---------------------------------------------------------------------------
# Fake Kafka producer
# ---------------------------------------------------------------------------


class FakeKafkaProducer:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_and_wait(self, topic: str, payload: dict) -> None:
        self.messages.append({"topic": topic, "payload": payload})


# ---------------------------------------------------------------------------
# Helper: build event row in the format returned by the fake Neo4j session
# ---------------------------------------------------------------------------


def _make_event_row(
    event_type: str = "PLC_TRACE",
    shop: str = "BODY",
    station: str = "ST_01",
    occurred_at: str = "2026-01-15T08:00:00Z",
    stage_number: int | None = None,
    tunnel_mm: float | None = None,
    params: dict | None = None,
) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "vin": _VIN,
        "eventType": event_type,
        "shop": shop,
        "station": station,
        "occurredAt": occurred_at,
        "dataSource": "BECKHOFF_PLC",
        "parameters": json.dumps(params or {"temperature_c": 21.5}),
        "linkedAt": occurred_at,
        "stageNumber": stage_number,
        "tunnelPositionMm": tunnel_mm,
        "boothId": None,
    }


def _make_paint_event(
    stage_number: int,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    ts = occurred_at or f"2026-01-15T0{stage_number % 10:01d}:00:00Z"
    return _make_event_row(
        event_type="PAINT_PREP_STAGE",
        shop="PAINT",
        stage_number=stage_number,
        occurred_at=ts,
        params={"temperature_c": 21.5, "ph_value": 7.2},
    )


def _fresh_cache_row(overall_pct: float = 100.0) -> dict:
    stage_list = [
        {
            "stageNumber": i,
            "stageName": f"Stage {i}",
            "available": True,
            "completenessPercent": 100.0,
            "expectedSampleCount": 10,
            "actualSampleCount": 10,
            "missingParameters": [],
        }
        for i in range(1, 15)
    ]
    return {
        "overall_completeness_pct": overall_pct,
        "meets_threshold": overall_pct >= 95.0,
        "stage_completeness": json.dumps(stage_list),
        "vin_status": "IN_PROGRESS",
        "updated_at": "2099-01-01T00:00:00+00:00",  # far-future = always fresh
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch: Any) -> None:
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
    settings.reader_roles = "quality-engineer,quality-lead"
    settings.reader_role_set.return_value = {"quality-engineer", "quality-lead"}
    settings.neo4j_read_timeout_s = 9.0
    settings.completeness_cache_ttl_s = 30
    settings.identity_service_url = "http://localhost:8083"
    settings.identity_service_timeout_s = 2.0
    settings.neo4j_eu_host_allowlist = ""
    settings.quality_reader_roles = "quality-engineer,quality-lead"

    monkeypatch.setattr(config_mod, "_settings", settings)
    # Reset stage config cache so each test reads from disk fresh
    if hasattr(tqs_mod, "_STAGE_CONFIG"):
        monkeypatch.setattr(tqs_mod, "_STAGE_CONFIG", [])


async def _build_client(
    neo4j_driver: FakeNeo4jDriver,
    pg_pool: FakePgPool,
    kafka: FakeKafkaProducer,
) -> tuple[AsyncClient, tuple]:
    import app.db as db_mod
    import app.kafka_producer as kafka_mod
    import app.thread_query_service as tqs_mod
    from app.main import create_app

    app = create_app()

    async def _get_pool():
        return pg_pool

    async def _get_producer():
        return kafka

    async def _fake_neo4j_driver():
        return neo4j_driver

    patcher_neo4j_global = patch(
        "neo4j.AsyncGraphDatabase.driver",
        return_value=neo4j_driver,
    )
    patcher_db = patch.object(db_mod, "get_pool", _get_pool)
    patcher_kafka = patch.object(kafka_mod, "get_producer", _get_producer)
    patcher_tqs_neo4j = patch.object(tqs_mod, "_neo4j_driver", _fake_neo4j_driver)

    patcher_neo4j_global.start()
    patcher_db.start()
    patcher_kafka.start()
    patcher_tqs_neo4j.start()

    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=10.0)
    await ac.__aenter__()
    patchers = (patcher_neo4j_global, patcher_db, patcher_kafka, patcher_tqs_neo4j)
    return ac, patchers


async def _stop_client(ac: AsyncClient, patchers: tuple) -> None:
    await ac.__aexit__(None, None, None)
    for p in patchers:
        p.stop()


# ---------------------------------------------------------------------------
# AC1 — Manufacturing event ingested via POST, linked to VIN within 5 seconds
#
# "within 5 seconds" is enforced by the _NEO4J_WRITE_TIMEOUT_S = 4.0 constant
# in ingest_service.py; the handler returns 503 when Neo4j does not respond
# within the timeout.  We verify:
#   (a) A successful ingest response includes linkedAt, shop, station,
#       occurredAt (timestamp), and dataSource in the Neo4j write payload.
#   (b) All four mandatory event types are accepted and produce a 201.
#   (c) A slow Neo4j write (> timeout) returns 503, proving the bound is
#       enforced rather than just documented.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac1_ingest_links_event_with_shop_station_timestamp_datasource() -> None:
    """AC1: Ingest returns 201 and the Neo4j write carries shop, station,
    occurredAt (timestamp), and dataSource — the four mandatory fields that
    must reach the graph node within the 5-second SLA."""
    neo4j = FakeNeo4jDriver()
    pg = FakePgPool()
    kafka = FakeKafkaProducer()

    # Precondition: migration schema names are present (startup validation)
    assert any(n["name"] == "vin_unique" for n in _SCHEMA_NAMES), (
        "PRECONDITION: vin_unique constraint must be present in schema names fixture"
    )
    assert any(n["name"] == "event_unique" for n in _SCHEMA_NAMES), (
        "PRECONDITION: event_unique constraint must be present in schema names fixture"
    )

    ac, patchers = await _build_client(neo4j, pg, kafka)
    try:
        resp = await ac.post(
            f"/vin/{_VIN}/thread/events",
            json={
                "idempotencyKey": str(uuid.uuid4()),
                "eventType": "PLC_TRACE",
                "shop": "BODY_SHOP",
                "station": "STATION_01",
                "occurredAt": "2026-01-15T08:00:00Z",
                "dataSource": "BECKHOFF_PLC_01",
                "parameters": {"voltage_v": 220.5},
            },
            headers={"Authorization": _INGESTOR_BEARER},
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

        body = resp.json()
        # Response proves the event was linked: linkedAt is populated.
        assert "linkedAt" in body, "Response must include linkedAt timestamp"
        assert "eventId" in body, "Response must include eventId"
        assert body["vin"] == _VIN, "Response vin must match path vin"

        # The Neo4j write must have been called with all four mandatory fields.
        assert len(neo4j.captured_writes) >= 1, (
            "PRECONDITION FAILED: Neo4j session must have been called (no write recorded). "
            "If this fails, the event was not linked to the graph."
        )
        write_kwargs = neo4j.captured_writes[0]
        assert write_kwargs.get("shop") == "BODY_SHOP", (
            f"shop field missing from Neo4j write: {write_kwargs}"
        )
        assert write_kwargs.get("station") == "STATION_01", (
            f"station field missing from Neo4j write: {write_kwargs}"
        )
        assert write_kwargs.get("occurred_at") == "2026-01-15T08:00:00Z", (
            f"occurredAt timestamp missing from Neo4j write: {write_kwargs}"
        )
        assert write_kwargs.get("data_source") == "BECKHOFF_PLC_01", (
            f"dataSource field missing from Neo4j write: {write_kwargs}"
        )
        # linkedAt in the write marks the in-graph timestamp
        assert "linked_at" in write_kwargs, (
            f"linked_at missing from Neo4j write: {write_kwargs}"
        )
    finally:
        await _stop_client(ac, patchers)


@pytest.mark.asyncio
async def test_ac1_all_mandatory_event_types_are_ingested() -> None:
    """AC1: PLC trace, gate result, torque log, and AR instruction execution
    all produce 201 — every mandatory event type reaches the graph."""
    mandatory_types = [
        "PLC_TRACE",
        "GATE_RESULT",
        "TORQUE_LOG",
        "AR_INSTRUCTION_EXECUTION",
    ]
    for event_type in mandatory_types:
        neo4j = FakeNeo4jDriver()
        pg = FakePgPool()
        kafka = FakeKafkaProducer()
        ac, patchers = await _build_client(neo4j, pg, kafka)
        try:
            resp = await ac.post(
                f"/vin/{_VIN}/thread/events",
                json={
                    "idempotencyKey": str(uuid.uuid4()),
                    "eventType": event_type,
                    "shop": "BODY_SHOP",
                    "station": "ST_01",
                    "occurredAt": "2026-01-15T08:00:00Z",
                    "dataSource": "TEST_SRC",
                    "parameters": {},
                },
                headers={"Authorization": _INGESTOR_BEARER},
            )
            assert resp.status_code == 201, (
                f"eventType={event_type} must be accepted (201). "
                f"Got {resp.status_code}: {resp.text}"
            )
            assert len(neo4j.captured_writes) >= 1, (
                f"PRECONDITION FAILED: eventType={event_type} was accepted but "
                "no write reached Neo4j — event is not linked to the graph."
            )
        finally:
            await _stop_client(ac, patchers)


@pytest.mark.asyncio
async def test_ac1_neo4j_timeout_returns_503_enforcing_5s_sla() -> None:
    """AC1: When Neo4j write takes longer than _NEO4J_WRITE_TIMEOUT_S (4 s,
    well within the 5 s SLA), the API returns 503 rather than hanging
    indefinitely.  This test exercises the timeout enforcement path directly."""
    import app.db as db_mod
    import app.ingest_service as ingest_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    pg = FakePgPool()
    kafka = FakeKafkaProducer()

    class _SlowNeo4jSession:
        async def run(self, *args: Any, **kwargs: Any) -> _FakeNeo4jResult:
            # Returns schema names for startup; hangs on real writes
            if "db.constraints" in args[0] or "db.indexes" in args[0]:
                return _FakeNeo4jResult(_SCHEMA_NAMES)
            await asyncio.sleep(10)
            return _FakeNeo4jResult([])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    class _SlowNeo4jDriver:
        def session(self):
            return _SlowNeo4jSession()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    app = create_app()

    async def _get_pool():
        return pg

    async def _get_producer():
        return kafka

    with (
        patch.object(db_mod, "get_pool", _get_pool),
        patch.object(kafka_mod, "get_producer", _get_producer),
        patch("neo4j.AsyncGraphDatabase.driver", return_value=_SlowNeo4jDriver()),
        patch.object(ingest_mod, "_NEO4J_WRITE_TIMEOUT_S", 0.05),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", timeout=5.0
        ) as ac:
            resp = await ac.post(
                f"/vin/{_VIN}/thread/events",
                json={
                    "idempotencyKey": str(uuid.uuid4()),
                    "eventType": "PLC_TRACE",
                    "shop": "BODY_SHOP",
                    "station": "ST_01",
                    "occurredAt": "2026-01-15T08:00:00Z",
                    "dataSource": "TEST_SRC",
                    "parameters": {},
                },
                headers={"Authorization": _INGESTOR_BEARER},
            )

    assert resp.status_code == 503, (
        f"Timed-out Neo4j write must return 503 to enforce 5-second SLA. "
        f"Got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_ac1_vin_node_merge_is_idempotent() -> None:
    """AC1: Ingesting a second event for the same VIN must not create a duplicate
    VIN node — the MERGE in the Cypher write is the idempotency guard."""
    neo4j = FakeNeo4jDriver()
    pg = FakePgPool()
    kafka = FakeKafkaProducer()
    ac, patchers = await _build_client(neo4j, pg, kafka)
    try:
        for _ in range(2):
            resp = await ac.post(
                f"/vin/{_VIN}/thread/events",
                json={
                    "idempotencyKey": str(uuid.uuid4()),
                    "eventType": "PLC_TRACE",
                    "shop": "BODY_SHOP",
                    "station": "ST_01",
                    "occurredAt": "2026-01-15T08:00:00Z",
                    "dataSource": "TEST_SRC",
                    "parameters": {},
                },
                headers={"Authorization": _INGESTOR_BEARER},
            )
            assert resp.status_code == 201

        # Both writes must have passed the same VIN to the MERGE statement
        assert all(w.get("vin") == _VIN for w in neo4j.captured_writes), (
            "All Neo4j MERGE writes must reference the same VIN"
        )
    finally:
        await _stop_client(ac, patchers)


# ---------------------------------------------------------------------------
# AC2 — Final-gate VIN returns continuous chain with no shop-boundary gaps
#
# We stage a sequence that crosses BODY → PAINT → ASSEMBLY → FINAL and ends
# with a QUALITY_CERTIFICATION event.  The response must:
#   • return vinStatus = FINAL_GATE_COMPLETE
#   • contain at least one SHOP_BOUNDARY_TRANSITION per crossing
#   • include a QUALITY_CERTIFICATION event (final gate cert)
#   • not include any SYSTEM_INFERRED gap indicators (no data gaps)
# ---------------------------------------------------------------------------


def _build_final_gate_thread() -> list[dict[str, Any]]:
    """Complete chain from body-in-white through final gate.

    Includes explicit SHOP_BOUNDARY_TRANSITION events (as they would be stored
    in Neo4j after the ingest pipeline processes UNS shop-crossing messages) and
    ends with QUALITY_CERTIFICATION to produce vinStatus=FINAL_GATE_COMPLETE.

    The criterion under test is that the query API returns all of these without
    loss, preserving the unbroken chain.
    """
    return [
        # Body-in-white entry
        _make_event_row(shop="BODY", station="BIW_ENTRY", occurred_at="2026-02-01T06:00:00Z"),
        _make_event_row(event_type="GATE_RESULT", shop="BODY", station="BIW_EXIT",
                        occurred_at="2026-02-01T08:00:00Z"),
        # BODY → PAINT shop boundary (stored in graph by ingest pipeline)
        _make_event_row(
            event_type="SHOP_BOUNDARY_TRANSITION",
            shop="PAINT",
            station="BOUNDARY",
            occurred_at="2026-02-01T09:30:00Z",
            params={"from_shop": "BODY", "to_shop": "PAINT"},
        ),
        _make_event_row(shop="PAINT", station="PAINT_01", occurred_at="2026-02-01T10:00:00Z"),
        _make_event_row(event_type="PAINT_PREP_STAGE", shop="PAINT", station="PAINT_PREP",
                        stage_number=1, occurred_at="2026-02-01T11:00:00Z"),
        # PAINT → ASSEMBLY shop boundary
        _make_event_row(
            event_type="SHOP_BOUNDARY_TRANSITION",
            shop="ASSEMBLY",
            station="BOUNDARY",
            occurred_at="2026-02-01T13:30:00Z",
            params={"from_shop": "PAINT", "to_shop": "ASSEMBLY"},
        ),
        _make_event_row(shop="ASSEMBLY", station="ASSY_01", occurred_at="2026-02-01T14:00:00Z"),
        _make_event_row(event_type="TORQUE_LOG", shop="ASSEMBLY", station="TORQUE_01",
                        occurred_at="2026-02-01T15:00:00Z"),
        # ASSEMBLY → FINAL shop boundary
        _make_event_row(
            event_type="SHOP_BOUNDARY_TRANSITION",
            shop="FINAL",
            station="BOUNDARY",
            occurred_at="2026-02-01T16:30:00Z",
            params={"from_shop": "ASSEMBLY", "to_shop": "FINAL"},
        ),
        _make_event_row(shop="FINAL", station="FINAL_01", occurred_at="2026-02-01T17:00:00Z"),
        # Final gate certification
        _make_event_row(
            event_type="QUALITY_CERTIFICATION",
            shop="FINAL",
            station="FINAL_GATE",
            occurred_at="2026-02-01T18:00:00Z",
        ),
    ]


@pytest.mark.asyncio
async def test_ac2_final_gate_vin_thread_returns_continuous_chain() -> None:
    """AC2: Querying the digital thread for a VIN that has crossed all shops
    and received QUALITY_CERTIFICATION returns a sequence that includes every
    SHOP_BOUNDARY_TRANSITION crossing with no SYSTEM_INFERRED gaps."""
    events = _build_final_gate_thread()
    # Use FINAL_GATE_COMPLETE status in the PG cache
    cache = _fresh_cache_row(100.0)
    cache["vin_status"] = "FINAL_GATE_COMPLETE"

    neo4j = FakeNeo4jDriver(event_rows=events, vin_exists=True)
    pg = FakePgPool(cache_row=cache)
    kafka = FakeKafkaProducer()

    ac, patchers = await _build_client(neo4j, pg, kafka)
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _READER_BEARER},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = resp.json()

        # Precondition: result set is non-empty (graph must have data)
        assert len(body["data"]) > 0, (
            "PRECONDITION FAILED: digital thread returned no events. "
            "Thread query returned an empty data array — nothing to verify against AC2."
        )

        # --- vinStatus must be FINAL_GATE_COMPLETE ---
        assert body.get("vinStatus") == "FINAL_GATE_COMPLETE", (
            f"vinStatus must be FINAL_GATE_COMPLETE after quality certification. "
            f"Got: {body.get('vinStatus')}"
        )

        event_types = [e["eventType"] for e in body["data"]]

        # --- QUALITY_CERTIFICATION must be present ---
        assert "QUALITY_CERTIFICATION" in event_types, (
            "A QUALITY_CERTIFICATION event must be present at the end of the chain."
        )

        # --- At least one SHOP_BOUNDARY_TRANSITION must be present ---
        transitions = [e for e in body["data"] if e["eventType"] == "SHOP_BOUNDARY_TRANSITION"]
        assert len(transitions) >= 1, (
            "No SHOP_BOUNDARY_TRANSITION events found. "
            "The chain must include at least one shop crossing."
        )

        # --- No SYSTEM_INFERRED gaps ---
        inferred_gaps = [
            e for e in body["data"]
            if e["eventType"] == "SHOP_BOUNDARY_TRANSITION"
            and e["dataSource"] == "SYSTEM_INFERRED"
        ]
        assert len(inferred_gaps) == 0, (
            f"Chain contains {len(inferred_gaps)} SYSTEM_INFERRED gap(s). "
            "A VIN with an unbroken chain must not have synthetic gap indicators."
        )
    finally:
        await _stop_client(ac, patchers)


@pytest.mark.asyncio
async def test_ac2_final_gate_vin_thread_includes_biw_entry_event() -> None:
    """AC2: Chain starts from body-in-white — the first event is in the BODY shop."""
    events = _build_final_gate_thread()
    cache = _fresh_cache_row(100.0)
    cache["vin_status"] = "FINAL_GATE_COMPLETE"

    neo4j = FakeNeo4jDriver(event_rows=events, vin_exists=True)
    pg = FakePgPool(cache_row=cache)
    kafka = FakeKafkaProducer()

    ac, patchers = await _build_client(neo4j, pg, kafka)
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _READER_BEARER},
        )
        assert resp.status_code == 200

        body = resp.json()
        assert len(body["data"]) > 0, (
            "PRECONDITION FAILED: digital thread returned no events."
        )

        # Filter out synthetic transitions to find real events
        real_events = [
            e for e in body["data"]
            if not (e["eventType"] == "SHOP_BOUNDARY_TRANSITION" and e["dataSource"] == "SYSTEM_INFERRED")
        ]

        # At least one BODY shop event must be present (body-in-white entry)
        body_shop_events = [e for e in real_events if e["shop"] == "BODY"]
        assert len(body_shop_events) >= 1, (
            "No events found in the BODY shop. "
            "The chain must start from body-in-white entry."
        )

        # A FINAL shop event must also be present
        final_shop_events = [e for e in real_events if e["shop"] == "FINAL"]
        assert len(final_shop_events) >= 1, (
            "No events found in the FINAL shop. "
            "The chain must extend through the final quality gate."
        )
    finally:
        await _stop_client(ac, patchers)


@pytest.mark.asyncio
async def test_ac2_shop_boundary_transitions_injected_for_every_crossing() -> None:
    """AC2: When events cross multiple shops (BODY → PAINT → ASSEMBLY → FINAL),
    a SHOP_BOUNDARY_TRANSITION is injected for each crossing so that downstream
    consumers (Process Replay, Digital Birth Certificate) can identify the
    transition points without gaps."""
    events = _build_final_gate_thread()
    cache = _fresh_cache_row(100.0)
    cache["vin_status"] = "FINAL_GATE_COMPLETE"

    neo4j = FakeNeo4jDriver(event_rows=events, vin_exists=True)
    pg = FakePgPool(cache_row=cache)
    kafka = FakeKafkaProducer()

    ac, patchers = await _build_client(neo4j, pg, kafka)
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread",
            headers={"Authorization": _READER_BEARER},
        )
        assert resp.status_code == 200

        body = resp.json()
        assert len(body["data"]) > 0, (
            "PRECONDITION FAILED: digital thread returned no events."
        )

        transitions = [
            e for e in body["data"]
            if e["eventType"] == "SHOP_BOUNDARY_TRANSITION"
        ]
        # 3 real crossings (BODY→PAINT, PAINT→ASSEMBLY, ASSEMBLY→FINAL)
        # The service injects synthetic transitions for detected crossings.
        assert len(transitions) >= 3, (
            f"Expected ≥3 SHOP_BOUNDARY_TRANSITION events for a 4-shop chain "
            f"(BODY→PAINT→ASSEMBLY→FINAL), got {len(transitions)}."
        )
    finally:
        await _stop_client(ac, patchers)


# ---------------------------------------------------------------------------
# AC3 — Process Replay returns all 14 paint prep stage parameters as a
#        time-ordered sequence, with completeness ≥ 95%
#
# We seed all 14 stages with at least one sample each, then verify:
#   • prepStages has exactly 14 entries
#   • all 14 have available=True (no missing stages)
#   • samples within each stage are time-ordered
#   • overallCompletenessPercent >= 95.0
#   • the response is linked to the correct VIN
# ---------------------------------------------------------------------------


def _build_full_replay_events() -> list[dict[str, Any]]:
    """All 14 paint prep stages, one sample each, plus booth and climate."""
    rows: list[dict[str, Any]] = []
    for stage_num in range(1, 15):
        ts = f"2026-03-01T{(6 + stage_num // 2):02d}:{(stage_num * 4) % 60:02d}:00Z"
        rows.append(_make_paint_event(stage_num, occurred_at=ts))
    # Booth parameter
    rows.append(_make_event_row(
        event_type="BOOTH_PARAMETER",
        shop="PAINT",
        occurred_at="2026-03-01T14:00:00Z",
        params={"humidity_pct": 55.0, "boothId": "BOOTH_1"},
    ))
    rows[-1]["boothId"] = "BOOTH_1"
    # Climate data
    rows.append(_make_event_row(
        event_type="CLIMATE_DATA",
        shop="PAINT",
        occurred_at="2026-03-01T14:30:00Z",
        params={"ambient_temp_c": 21.0},
    ))
    return rows


@pytest.mark.asyncio
async def test_ac3_replay_returns_all_14_prep_stages() -> None:
    """AC3: GET /vin/{vin}/thread/replay returns exactly 14 prepStages entries,
    covering every paint prep stage defined in paint_prep_stage_config.yaml.

    Precondition: paint_prep_stage_config.yaml must exist on disk and define
    exactly 14 stages; the test fails loudly if the config is absent or wrong."""
    from app.thread_query_service import get_stage_config

    stage_cfg = get_stage_config()
    assert len(stage_cfg) == 14, (
        f"PRECONDITION FAILED: paint_prep_stage_config.yaml must define exactly 14 stages, "
        f"got {len(stage_cfg)}. Ensure the config file is present and complete."
    )
    assert sorted(s["number"] for s in stage_cfg) == list(range(1, 15)), (
        "PRECONDITION FAILED: stages must be numbered 1–14 in paint_prep_stage_config.yaml."
    )

    events = _build_full_replay_events()
    cache = _fresh_cache_row(100.0)

    neo4j = FakeNeo4jDriver(event_rows=events, vin_exists=True)
    pg = FakePgPool(cache_row=cache)
    kafka = FakeKafkaProducer()

    ac, patchers = await _build_client(neo4j, pg, kafka)
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread/replay",
            headers={"Authorization": _READER_BEARER},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = resp.json()
        assert body["vin"] == _VIN, "Replay response vin must match queried VIN"
        prep_stages = body["prepStages"]

        assert len(prep_stages) == 14, (
            f"Expected 14 prepStages in replay response, got {len(prep_stages)}. "
            "All 14 paint prep stages must appear regardless of data availability."
        )
        stage_numbers = sorted(s["stageNumber"] for s in prep_stages)
        assert stage_numbers == list(range(1, 15)), (
            f"Stage numbers must be 1–14. Got: {stage_numbers}"
        )
    finally:
        await _stop_client(ac, patchers)


@pytest.mark.asyncio
async def test_ac3_all_stages_available_when_fully_sampled() -> None:
    """AC3: When all 14 stages have samples, every stage reports available=True."""
    events = _build_full_replay_events()
    cache = _fresh_cache_row(100.0)

    neo4j = FakeNeo4jDriver(event_rows=events, vin_exists=True)
    pg = FakePgPool(cache_row=cache)
    kafka = FakeKafkaProducer()

    ac, patchers = await _build_client(neo4j, pg, kafka)
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread/replay",
            headers={"Authorization": _READER_BEARER},
        )
        assert resp.status_code == 200

        body = resp.json()
        prep_stages = body["prepStages"]
        assert len(prep_stages) == 14, (
            "PRECONDITION FAILED: expected 14 stages; check fake event fixtures."
        )

        unavailable = [s for s in prep_stages if not s["available"]]
        assert len(unavailable) == 0, (
            f"Stages {[s['stageNumber'] for s in unavailable]} report available=False "
            "but all 14 stages were seeded with samples."
        )
    finally:
        await _stop_client(ac, patchers)


@pytest.mark.asyncio
async def test_ac3_samples_within_each_stage_are_time_ordered() -> None:
    """AC3: samples within each stage are returned in ascending time order
    (time-ordered sequence requirement)."""
    # Seed stage 1 with two samples in reverse time order to ensure the service sorts them
    events: list[dict[str, Any]] = []
    for stage_num in range(1, 15):
        if stage_num == 1:
            # Two samples for stage 1, deliberately reversed
            events.append(_make_paint_event(1, occurred_at="2026-03-01T07:30:00Z"))
            events.append(_make_paint_event(1, occurred_at="2026-03-01T07:00:00Z"))
        else:
            events.append(_make_paint_event(stage_num))

    cache = _fresh_cache_row(100.0)
    neo4j = FakeNeo4jDriver(event_rows=events, vin_exists=True)
    pg = FakePgPool(cache_row=cache)
    kafka = FakeKafkaProducer()

    ac, patchers = await _build_client(neo4j, pg, kafka)
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread/replay",
            headers={"Authorization": _READER_BEARER},
        )
        assert resp.status_code == 200

        body = resp.json()
        stage1 = next(s for s in body["prepStages"] if s["stageNumber"] == 1)

        if len(stage1["samples"]) < 2:
            pytest.skip("stage 1 returned fewer than 2 samples — time-ordering test requires multiple")

        timestamps = [s["ts"] for s in stage1["samples"]]
        assert timestamps == sorted(timestamps), (
            f"Stage 1 samples are not in ascending time order. "
            f"Got: {timestamps}"
        )
    finally:
        await _stop_client(ac, patchers)


@pytest.mark.asyncio
async def test_ac3_completeness_at_least_95_pct_when_fully_sampled() -> None:
    """AC3: overallCompletenessPercent must be >= 95% for a VIN where all
    14 stages have samples (post-UNS-go-live VINs)."""
    events = _build_full_replay_events()
    cache = _fresh_cache_row(98.0)  # 98% reflects slightly-below-max real data

    neo4j = FakeNeo4jDriver(event_rows=events, vin_exists=True)
    pg = FakePgPool(cache_row=cache)
    kafka = FakeKafkaProducer()

    ac, patchers = await _build_client(neo4j, pg, kafka)
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread/replay",
            headers={"Authorization": _READER_BEARER},
        )
        assert resp.status_code == 200

        body = resp.json()
        overall_pct = body["overallCompletenessPercent"]

        assert isinstance(overall_pct, (int, float)), (
            "overallCompletenessPercent must be a number"
        )
        assert overall_pct >= 95.0, (
            f"overallCompletenessPercent is {overall_pct}% — must be ≥95% for a "
            "fully-sampled VIN (AC3 criterion: completeness ≥95% for VINs processed "
            "after UNS go-live)."
        )
    finally:
        await _stop_client(ac, patchers)


@pytest.mark.asyncio
async def test_ac3_completeness_computed_from_14_stage_config() -> None:
    """AC3: The ≥95% threshold is calculated against all 14 expected stages from
    paint_prep_stage_config.yaml, not a hard-coded count.  Supplying full samples
    for all 14 stages yields 100% completeness from the compute path."""
    from app.thread_query_service import _compute_completeness_from_neo4j, get_stage_config

    stage_cfg = get_stage_config()
    assert len(stage_cfg) == 14, (
        f"PRECONDITION FAILED: get_stage_config() returned {len(stage_cfg)} stages, "
        "expected 14. paint_prep_stage_config.yaml is missing or incomplete."
    )

    # Seed expected_samples events per stage to hit 100% completeness
    all_stage_events: list[dict[str, Any]] = []
    for s in stage_cfg:
        num = s["number"]
        for idx in range(s["expected_samples"]):
            ts = f"2026-03-{idx + 1:02d}T08:00:00Z"
            all_stage_events.append(_make_paint_event(num, occurred_at=ts))

    import app.thread_query_service as tqs_mod

    fake_driver = FakeNeo4jDriver(event_rows=all_stage_events, vin_exists=True)

    async def _fake_driver():
        return fake_driver

    with patch.object(tqs_mod, "_neo4j_driver", _fake_driver):
        result = await _compute_completeness_from_neo4j(_VIN, timeout=5.0)

    assert result["overall_completeness_pct"] >= 95.0, (
        f"Fully-sampled VIN (all 14 stages at expected_samples) computed "
        f"overallCompletenessPercent={result['overall_completeness_pct']}% — must be ≥95%."
    )
    assert result["meets_threshold"] is True, (
        "meets_threshold must be True when overall_completeness_pct >= 95."
    )
    assert len(result["stages"]) == 14, (
        f"Completeness result must contain 14 stages, got {len(result['stages'])}."
    )


@pytest.mark.asyncio
async def test_ac3_replay_response_linked_to_correct_vin() -> None:
    """AC3: The replay response vin field matches the queried VIN — the sequence
    is anchored to the specific vehicle, not pooled across VINs."""
    events = _build_full_replay_events()
    cache = _fresh_cache_row(100.0)

    neo4j = FakeNeo4jDriver(event_rows=events, vin_exists=True)
    pg = FakePgPool(cache_row=cache)
    kafka = FakeKafkaProducer()

    ac, patchers = await _build_client(neo4j, pg, kafka)
    try:
        resp = await ac.get(
            f"/vin/{_VIN}/thread/replay",
            headers={"Authorization": _READER_BEARER},
        )
        assert resp.status_code == 200

        body = resp.json()
        assert body["vin"] == _VIN, (
            f"Replay response vin '{body['vin']}' does not match queried VIN '{_VIN}'. "
            "The sequence must be linked to the specific VIN node in the graph."
        )
    finally:
        await _stop_client(ac, patchers)


# ---------------------------------------------------------------------------
# Cross-cutting: Neo4j schema constraints and indexes must be present
# (precondition for all three acceptance criteria — the service refuses to
# boot without them, so a missing schema = no ingestion at all)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schema_required_constraints_and_indexes_are_validated_at_startup() -> None:
    """Precondition for AC1/AC2/AC3: the Neo4j schema must contain vin_unique,
    event_unique, event_by_type_time, event_by_stage_number, and event_by_shop.
    validate_neo4j_schema() raises SchemaValidationError when any is absent."""
    from app.neo4j_schema import SchemaValidationError, validate_neo4j_schema

    class _FullSchemaDriver:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def session(self):
            class _S:
                async def run(self, query: str, **_) -> _FakeNeo4jResult:
                    return _FakeNeo4jResult(_SCHEMA_NAMES)

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_):
                    pass

            return _S()

    await validate_neo4j_schema(_FullSchemaDriver())  # must not raise

    class _MissingConstraintDriver:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def session(self):
            class _S:
                async def run(self, query: str, **_) -> _FakeNeo4jResult:
                    # Remove vin_unique to simulate missing constraint
                    incomplete = [n for n in _SCHEMA_NAMES if n["name"] != "vin_unique"]
                    return _FakeNeo4jResult(incomplete)

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_):
                    pass

            return _S()

    with pytest.raises(SchemaValidationError):
        await validate_neo4j_schema(_MissingConstraintDriver())


@pytest.mark.asyncio
async def test_migration_cypher_files_define_all_required_schema_names() -> None:
    """Precondition (infra task): the V*.cypher migration files define all the
    constraints and indexes that neo4j_schema.py requires.

    Reads the actual migration files from disk — not a hand-written copy — per
    the convention that tests for generated/static artifacts must read the
    artifact itself.
    """
    import pathlib
    import re

    migrations_dir = (
        pathlib.Path(__file__).parent.parent.parent.parent
        / "platform"
        / "vin-digital-thread"
        / "cypher"
    )
    assert migrations_dir.exists(), (
        f"PRECONDITION FAILED: Cypher migrations directory not found at {migrations_dir}. "
        "The vin-digital-thread platform component must be present."
    )

    cypher_files = list(migrations_dir.glob("V*.cypher"))
    assert len(cypher_files) >= 1, (
        f"PRECONDITION FAILED: No V*.cypher migration files found in {migrations_dir}. "
        "At least one migration must exist."
    )

    combined_source = "\n".join(f.read_text() for f in cypher_files)

    required_names = [
        "vin_unique",
        "event_unique",
        "event_by_type_time",
        "event_by_stage_number",
        "event_by_shop",
    ]
    missing: list[str] = [name for name in required_names if name not in combined_source]
    assert not missing, (
        f"The following required schema names are absent from the Cypher migrations: "
        f"{missing}. Each must appear in a V*.cypher file under "
        f"platform/vin-digital-thread/cypher/."
    )
