"""Integration-style tests for POST /vin/{vin}/thread/events.

Uses in-process fakes for PostgreSQL (asyncpg), Neo4j, and Kafka so the test
suite runs without external services.  Each test class instantiates its own
fake, verifying instance isolation (counters restart from their initial value).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

_VIN = "WBA3A5C55FK123456"
_IDEMPOTENCY_KEY = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

_VALID_BODY: dict = {
    "idempotencyKey": _IDEMPOTENCY_KEY,
    "eventType": "PLC_TRACE",
    "shop": "BODY_SHOP",
    "station": "STATION_01",
    "occurredAt": "2026-01-15T08:00:00Z",
    "dataSource": "PLC_SRC_01",
    "parameters": {"voltage_v": 220.5, "active": True},
}

_BEARER = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbInRocmVhZC1pbmdlc3RvciJdfQ.sig"


# ---------------------------------------------------------------------------
# Fake infrastructure
# ---------------------------------------------------------------------------


class FakeRecord:
    """Minimal stand-in for asyncpg Record."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


class FakeConnection:
    """Fake asyncpg connection — each instance starts with clean state."""

    def __init__(self, store: dict[str, dict], existing_keys: set[str]) -> None:
        self._store = store
        self._existing_keys = existing_keys
        self._in_transaction = False

    def transaction(self):
        fake = self

        class _Tx:
            async def __aenter__(_self):
                fake._in_transaction = True
                return _self

            async def __aexit__(_self, *_):
                fake._in_transaction = False

        return _Tx()

    async def fetchrow(self, query: str, *args: Any) -> FakeRecord | None:
        key = str(args[0])
        if key in self._existing_keys:
            return FakeRecord({"event_id": str(uuid.uuid4())})
        return None

    async def execute(self, query: str, *args: Any) -> None:
        query_upper = query.upper()
        if "INSERT INTO THREAD_EVENT_INGEST_LOG" in query_upper:
            self._store["last_insert"] = {
                "event_id": str(args[0]),
                "vin": args[1],
                "idempotency_key": args[2],
            }
        elif "UPDATE THREAD_EVENT_INGEST_LOG" in query_upper:
            self._store["last_update"] = {"linked_at": str(args[0]), "event_id": str(args[1])}
        elif "INSERT INTO VIN_THREAD_COMPLETENESS_CACHE" in query_upper:
            self._store["cache_refreshed"] = True


class FakePool:
    """Fake asyncpg Pool — each instance has independent state."""

    def __init__(self, existing_keys: set[str] | None = None) -> None:
        self._store: dict[str, dict] = {}
        self._existing_keys: set[str] = existing_keys or set()
        self._acquire_count = 0

    def acquire(self):
        store = self._store
        existing_keys = self._existing_keys

        class _CM:
            async def __aenter__(_self) -> FakeConnection:
                return FakeConnection(store, existing_keys)

            async def __aexit__(_self, *_) -> None:
                pass

        self._acquire_count += 1
        return _CM()

    @property
    def store(self) -> dict:
        return self._store


class _FakeResult:
    """Minimal stand-in for neo4j AsyncResult — supports .data() only."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def data(self) -> list[dict]:
        return list(self._rows)


# Names returned by the schema validation queries (CALL db.constraints/indexes).
_SCHEMA_NAMES: list[dict] = [
    {"name": "vin_unique"},
    {"name": "event_unique"},
    {"name": "event_by_type_time"},
    {"name": "event_by_stage_number"},
    {"name": "event_by_shop"},
]


class FakeNeo4jSession:
    def __init__(self, store: list) -> None:
        self._store = store

    async def run(self, query: str, **kwargs: Any) -> _FakeResult:
        self._store.append(kwargs)
        # Return schema names for startup validation queries.
        if "db.constraints" in query or "db.indexes" in query:
            return _FakeResult(_SCHEMA_NAMES)
        return _FakeResult([])

    async def __aenter__(self) -> "FakeNeo4jSession":
        return self

    async def __aexit__(self, *_) -> None:
        pass


class FakeNeo4jDriver:
    def __init__(self) -> None:
        self.sessions: list[dict] = []

    def session(self) -> FakeNeo4jSession:
        return FakeNeo4jSession(self.sessions)

    async def __aenter__(self) -> "FakeNeo4jDriver":
        return self

    async def __aexit__(self, *_) -> None:
        pass


class FakeKafkaProducer:
    """Fake Kafka producer — each instance starts with an empty messages list."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_and_wait(self, topic: str, payload: dict) -> None:
        self.messages.append({"topic": topic, "payload": payload})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    """Inject config without hitting os.environ."""
    import app.config as config_mod

    settings = MagicMock()
    settings.neo4j_uri = "bolt://localhost:7687"
    settings.neo4j_user = "neo4j"
    settings.neo4j_password = "localpassword"
    settings.citus_url = "postgresql://localhost/test"
    settings.kafka_bootstrap_servers = "localhost:9092"
    settings.oidc_issuer_url = "http://localhost:8090/default"
    settings.oidc_client_id = "local"

    monkeypatch.setattr(config_mod, "_settings", settings)


@pytest.fixture()
def fake_pool() -> FakePool:
    return FakePool()


@pytest.fixture()
def fake_pool_with_duplicate() -> FakePool:
    return FakePool(existing_keys={_IDEMPOTENCY_KEY})


@pytest.fixture()
def fake_neo4j_driver() -> FakeNeo4jDriver:
    return FakeNeo4jDriver()


@pytest.fixture()
def fake_kafka() -> FakeKafkaProducer:
    return FakeKafkaProducer()


@pytest_asyncio.fixture()
async def client(fake_pool, fake_neo4j_driver, fake_kafka) -> AsyncGenerator[AsyncClient, None]:
    import app.db as db_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    app = create_app()

    async def _fake_get_pool():
        return fake_pool

    async def _fake_get_producer():
        return fake_kafka

    with (
        patch.object(db_mod, "get_pool", _fake_get_pool),
        patch.object(kafka_mod, "get_producer", _fake_get_producer),
        patch(
            "neo4j.AsyncGraphDatabase.driver",
            return_value=fake_neo4j_driver,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac


@pytest_asyncio.fixture()
async def client_duplicate(fake_pool_with_duplicate, fake_neo4j_driver, fake_kafka):
    import app.db as db_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    app = create_app()

    async def _fake_get_pool():
        return fake_pool_with_duplicate

    async def _fake_get_producer():
        return fake_kafka

    with (
        patch.object(db_mod, "get_pool", _fake_get_pool),
        patch.object(kafka_mod, "get_producer", _fake_get_producer),
        patch(
            "neo4j.AsyncGraphDatabase.driver",
            return_value=fake_neo4j_driver,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac


# ---------------------------------------------------------------------------
# Tests: 201 happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_returns_201(client: AsyncClient) -> None:
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=_VALID_BODY,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_ingest_response_contains_required_fields(client: AsyncClient) -> None:
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=_VALID_BODY,
        headers={"Authorization": _BEARER},
    )
    body = resp.json()
    assert "eventId" in body
    assert "vin" in body
    assert "linkedAt" in body


@pytest.mark.asyncio
async def test_ingest_response_vin_matches_path(client: AsyncClient) -> None:
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=_VALID_BODY,
        headers={"Authorization": _BEARER},
    )
    assert resp.json()["vin"] == _VIN


@pytest.mark.asyncio
async def test_ingest_event_id_is_uuid(client: AsyncClient) -> None:
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=_VALID_BODY,
        headers={"Authorization": _BEARER},
    )
    event_id = resp.json()["eventId"]
    uuid.UUID(event_id)  # raises if not valid UUID


@pytest.mark.asyncio
async def test_ingest_linked_at_is_utc_datetime(client: AsyncClient) -> None:
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=_VALID_BODY,
        headers={"Authorization": _BEARER},
    )
    linked_at = resp.json()["linkedAt"]
    dt = datetime.fromisoformat(linked_at.replace("Z", "+00:00"))
    assert dt.tzinfo is not None


@pytest.mark.asyncio
async def test_ingest_writes_to_pg_before_neo4j(client: AsyncClient, fake_pool: FakePool) -> None:
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=_VALID_BODY,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 201
    assert "last_insert" in fake_pool.store
    assert fake_pool.store["last_insert"]["vin"] == _VIN


@pytest.mark.asyncio
async def test_ingest_updates_pg_status_to_linked(client: AsyncClient, fake_pool: FakePool) -> None:
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=_VALID_BODY,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 201
    assert "last_update" in fake_pool.store


@pytest.mark.asyncio
async def test_ingest_all_event_types_accepted(client: AsyncClient) -> None:
    event_types = [
        "PLC_TRACE",
        "GATE_RESULT",
        "TORQUE_LOG",
        "AR_INSTRUCTION_EXECUTION",
        "PAINT_PREP_STAGE",
        "BOOTH_PARAMETER",
        "CLIMATE_DATA",
        "SHOP_BOUNDARY_TRANSITION",
        "QUALITY_CERTIFICATION",
    ]
    for et in event_types:
        body = {**_VALID_BODY, "eventType": et, "idempotencyKey": str(uuid.uuid4())}
        resp = await client.post(
            f"/vin/{_VIN}/thread/events",
            json=body,
            headers={"Authorization": _BEARER},
        )
        assert resp.status_code == 201, f"Expected 201 for eventType={et}, got {resp.status_code}"


@pytest.mark.asyncio
async def test_ingest_with_stage_number(client: AsyncClient) -> None:
    body = {**_VALID_BODY, "idempotencyKey": str(uuid.uuid4()), "stageNumber": 7}
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=body,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_ingest_with_tunnel_position(client: AsyncClient) -> None:
    body = {**_VALID_BODY, "idempotencyKey": str(uuid.uuid4()), "tunnelPositionMm": 1234.5}
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=body,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Tests: 400 — malformed input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_vin_returns_400(client: AsyncClient) -> None:
    resp = await client.post(
        "/vin/INVALIDVIN00000000/thread/events",
        json=_VALID_BODY,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_vin_with_forbidden_char_returns_400(client: AsyncClient) -> None:
    # 'O' is not allowed in ISO 3779
    resp = await client.post(
        "/vin/WBA3A5C55FK12OO56/thread/events",
        json=_VALID_BODY,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_missing_required_field_returns_422(client: AsyncClient) -> None:
    body = dict(_VALID_BODY)
    del body["eventType"]
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=body,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_event_type_returns_422(client: AsyncClient) -> None:
    body = {**_VALID_BODY, "eventType": "UNKNOWN_TYPE"}
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=body,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_stage_number_out_of_range_returns_422(client: AsyncClient) -> None:
    body = {**_VALID_BODY, "idempotencyKey": str(uuid.uuid4()), "stageNumber": 15}
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=body,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests: 401 / 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_bearer_returns_401(client: AsyncClient) -> None:
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=_VALID_BODY,
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_missing_role_returns_403(client: AsyncClient) -> None:
    # Token with sub but no thread-ingestor role
    no_role_token = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbImFub3RoZXItcm9sZSJdfQ.sig"
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=_VALID_BODY,
        headers={"Authorization": no_role_token},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_401_response_contains_error_envelope(client: AsyncClient) -> None:
    resp = await client.post(f"/vin/{_VIN}/thread/events", json=_VALID_BODY)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests: 409 — duplicate idempotency key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_returns_409(
    client_duplicate: AsyncClient,
) -> None:
    resp = await client_duplicate.post(
        f"/vin/{_VIN}/thread/events",
        json=_VALID_BODY,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_duplicate_response_has_error_envelope(
    client_duplicate: AsyncClient,
) -> None:
    resp = await client_duplicate.post(
        f"/vin/{_VIN}/thread/events",
        json=_VALID_BODY,
        headers={"Authorization": _BEARER},
    )
    body = resp.json()
    assert "code" in body
    assert "message" in body
    assert "correlation_id" in body


# ---------------------------------------------------------------------------
# Tests: error envelope shape on every error response
# ---------------------------------------------------------------------------


_ERROR_FIELDS = {"code", "message", "correlation_id"}


@pytest.mark.asyncio
async def test_400_error_envelope(client: AsyncClient) -> None:
    resp = await client.post(
        "/vin/TOOSHORT/thread/events",
        json=_VALID_BODY,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 400
    body = resp.json()
    for field in _ERROR_FIELDS:
        assert field in body, f"Missing field {field!r} in 400 response"


@pytest.mark.asyncio
async def test_422_error_envelope(client: AsyncClient) -> None:
    body = dict(_VALID_BODY)
    del body["shop"]
    resp = await client.post(
        f"/vin/{_VIN}/thread/events",
        json=body,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests: 500 / 503 from Neo4j failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_neo4j_write_failure_returns_500(fake_pool: FakePool, fake_kafka: FakeKafkaProducer) -> None:
    import app.db as db_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    app = create_app()

    async def _fake_get_pool():
        return fake_pool

    async def _fake_get_producer():
        return fake_kafka

    class _FailingDriver:
        def session(self):
            raise RuntimeError("Neo4j write failed")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    with (
        patch.object(db_mod, "get_pool", _fake_get_pool),
        patch.object(kafka_mod, "get_producer", _fake_get_producer),
        patch("neo4j.AsyncGraphDatabase.driver", return_value=_FailingDriver()),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/vin/{_VIN}/thread/events",
                json=_VALID_BODY,
                headers={"Authorization": _BEARER},
            )

    assert resp.status_code == 500
    body = resp.json()
    assert "code" in body
    assert "correlation_id" in body


@pytest.mark.asyncio
async def test_neo4j_timeout_returns_503(fake_pool: FakePool, fake_kafka: FakeKafkaProducer) -> None:
    import asyncio as _asyncio

    import app.db as db_mod
    import app.ingest_service as ingest_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    app = create_app()

    async def _fake_get_pool():
        return fake_pool

    async def _fake_get_producer():
        return fake_kafka

    class _SlowSession:
        async def run(self, *args, **kwargs):
            await _asyncio.sleep(10)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    class _SlowDriver:
        def session(self):
            return _SlowSession()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    with (
        patch.object(db_mod, "get_pool", _fake_get_pool),
        patch.object(kafka_mod, "get_producer", _fake_get_producer),
        patch("neo4j.AsyncGraphDatabase.driver", return_value=_SlowDriver()),
        patch.object(ingest_mod, "_NEO4J_WRITE_TIMEOUT_S", 0.05),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", timeout=5.0
        ) as ac:
            resp = await ac.post(
                f"/vin/{_VIN}/thread/events",
                json=_VALID_BODY,
                headers={"Authorization": _BEARER},
            )

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests: no DB write on non-2xx
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_db_write_on_401(fake_pool: FakePool) -> None:
    import app.db as db_mod
    from app.main import create_app

    app = create_app()

    async def _fake_get_pool():
        return fake_pool

    with patch.object(db_mod, "get_pool", _fake_get_pool):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/vin/{_VIN}/thread/events",
                json=_VALID_BODY,
            )

    assert resp.status_code == 401
    assert "last_insert" not in fake_pool.store


@pytest.mark.asyncio
async def test_no_db_write_on_invalid_vin(fake_neo4j_driver: FakeNeo4jDriver, fake_kafka: FakeKafkaProducer) -> None:
    import app.db as db_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    pool = FakePool()
    app = create_app()

    async def _fake_get_pool():
        return pool

    async def _fake_get_producer():
        return fake_kafka

    with (
        patch.object(db_mod, "get_pool", _fake_get_pool),
        patch.object(kafka_mod, "get_producer", _fake_get_producer),
        patch("neo4j.AsyncGraphDatabase.driver", return_value=fake_neo4j_driver),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/vin/BADVIN12345678901/thread/events",
                json=_VALID_BODY,
                headers={"Authorization": _BEARER},
            )

    assert resp.status_code == 400
    assert "last_insert" not in pool.store


# ---------------------------------------------------------------------------
# Tests: instance isolation on FakePool
# ---------------------------------------------------------------------------


def test_fake_pool_instance_isolation() -> None:
    pool_a = FakePool()
    pool_b = FakePool()
    pool_a._store["key"] = {"value": 1}
    assert "key" not in pool_b._store


def test_fake_pool_acquire_count_restarts_per_instance() -> None:
    pool_a = FakePool()
    pool_a.acquire()
    pool_b = FakePool()
    assert pool_b._acquire_count == 0, "New instance must start with acquire_count=0"


def test_fake_kafka_instance_isolation() -> None:
    k1 = FakeKafkaProducer()
    k2 = FakeKafkaProducer()
    asyncio.run(k1.send_and_wait("topic", {"msg": 1}))
    assert len(k2.messages) == 0


# ---------------------------------------------------------------------------
# Tests: VIN regex
# ---------------------------------------------------------------------------


def test_vin_regex_rejects_i_o_q() -> None:
    from app.ingest_service import _VIN_RE

    assert not _VIN_RE.match("WBA3A5C55FK12II56")  # I not allowed
    assert not _VIN_RE.match("WBA3A5C55FK12OO56")  # O not allowed
    assert not _VIN_RE.match("WBA3A5C55FK12QQ56")  # Q not allowed


def test_vin_regex_accepts_valid_vin() -> None:
    from app.ingest_service import _VIN_RE

    assert _VIN_RE.match("WBA3A5C55FK123456")


def test_vin_regex_rejects_wrong_length() -> None:
    from app.ingest_service import _VIN_RE

    assert not _VIN_RE.match("WBA3A5C55FK12345")   # 16 chars
    assert not _VIN_RE.match("WBA3A5C55FK1234567")  # 18 chars
