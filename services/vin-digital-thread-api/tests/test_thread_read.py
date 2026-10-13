"""Unit and integration-style tests for thread read endpoints.

Uses in-process fakes for Neo4j and PostgreSQL — no external services required.
Covers: full-data, OQ-6 partial (some stages missing), zero-data, cache-hit,
cache-miss, cursor pagination, auth, and VIN-not-found scenarios.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

_VIN = "WBA3A5C55FK123456"
_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbInF1YWxpdHktZW5naW5lZXIiXX0"
    ".sig"
)
_BEARER_LEAD = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTQ1NiIsInJvbGVzIjpbInF1YWxpdHktbGVhZCJdfQ"
    ".sig"
)
_BEARER_WRONG_ROLE = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTc4OSIsInJvbGVzIjpbInRocmVhZC1pbmdlc3RvciJdfQ"
    ".sig"
)

# ---------------------------------------------------------------------------
# Fake Neo4j infrastructure
# ---------------------------------------------------------------------------


def _make_event(
    event_id: str | None = None,
    event_type: str = "PLC_TRACE",
    occurred_at: str = "2026-01-15T08:00:00+00:00",
    shop: str = "PAINT",
    station: str = "ST_01",
    stage_number: int | None = None,
    tunnel_mm: float | None = None,
    params: dict | None = None,
) -> dict[str, Any]:
    return {
        "eventId": event_id or str(uuid.uuid4()),
        "eventType": event_type,
        "occurredAt": occurred_at,
        "shop": shop,
        "station": station,
        "dataSource": "TEST_SRC",
        "parameters": json.dumps(params or {"temperature_c": 21.5}),
        "linkedAt": "2026-01-15T08:01:00+00:00",
        "stageNumber": stage_number,
        "tunnelPositionMm": tunnel_mm,
        "boothId": None,
    }


def _paint_event(stage_number: int, occurred_at: str = "2026-01-15T08:00:00+00:00") -> dict:
    return _make_event(
        event_type="PAINT_PREP_STAGE",
        stage_number=stage_number,
        occurred_at=occurred_at,
        params={"temperature_c": 21.5, "ph_value": 7.2},
    )


def _booth_event(booth_id: str = "BOOTH_1", occurred_at: str = "2026-01-15T08:05:00+00:00") -> dict:
    ev = _make_event(
        event_type="BOOTH_PARAMETER",
        occurred_at=occurred_at,
        params={"humidity_pct": 55.0, "boothId": booth_id},
    )
    ev["boothId"] = booth_id
    return ev


def _climate_event(occurred_at: str = "2026-01-15T08:10:00+00:00") -> dict:
    return _make_event(
        event_type="CLIMATE_DATA",
        occurred_at=occurred_at,
        params={"temperature_c": 22.0, "humidity_pct": 45.0},
    )


class FakeNeo4jResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def data(self) -> list[dict]:
        return self._rows


class FakeNeo4jSession:
    """Configurable fake Neo4j session."""

    def __init__(
        self,
        events: list[dict[str, Any]],
        vin_exists: bool = True,
    ) -> None:
        self._events = events
        self._vin_exists = vin_exists
        self.queries: list[str] = []

    async def run(self, cypher: str, **kwargs: Any) -> FakeNeo4jResult:
        self.queries.append(cypher)
        cypher_upper = cypher.upper()

        # VIN existence check — MATCH (v:VIN {vin:...}) without HAS_EVENT
        if "MATCH (V:VIN {VIN" in cypher_upper and "HAS_EVENT" not in cypher_upper:
            if self._vin_exists:
                return FakeNeo4jResult([{"v": {"vin": kwargs.get("vin", _VIN)}}])
            return FakeNeo4jResult([])

        # Completeness count query
        if "COUNT(E) AS CNT" in cypher_upper:
            stage_counts: dict[int, int] = {}
            for ev in self._events:
                sn = ev.get("stageNumber")
                if sn is not None and ev.get("eventType") == "PAINT_PREP_STAGE":
                    stage_counts[int(sn)] = stage_counts.get(int(sn), 0) + 1
            rows = [{"stageNumber": k, "cnt": v} for k, v in stage_counts.items()]
            return FakeNeo4jResult(rows)

        # Snapshot query — last event at or before atTs
        if "ORDER BY E.OCCURREDAT DESC" in cypher_upper and "LIMIT 1" in cypher_upper:
            at_ts = kwargs.get("at_ts", "9999")
            matching = [e for e in self._events if e["occurredAt"] <= at_ts]
            if matching:
                latest = sorted(matching, key=lambda e: e["occurredAt"])[-1]
                return FakeNeo4jResult([{"e": latest}])
            return FakeNeo4jResult([])

        # General events query
        limit = kwargs.get("limit", 101)
        rows = [{"e": ev} for ev in self._events[:limit]]
        return FakeNeo4jResult(rows)

    async def __aenter__(self) -> "FakeNeo4jSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeNeo4jDriver:
    def __init__(self, events: list[dict], vin_exists: bool = True) -> None:
        self._events = events
        self._vin_exists = vin_exists
        self.session_obj = FakeNeo4jSession(events, vin_exists)

    def session(self) -> FakeNeo4jSession:
        return self.session_obj

    async def __aenter__(self) -> "FakeNeo4jDriver":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


def _make_neo4j_patcher(fake_driver: FakeNeo4jDriver):
    """Return an async function suitable for patching _neo4j_driver."""
    async def _fake_neo4j_driver():
        return fake_driver
    return _fake_neo4j_driver


# ---------------------------------------------------------------------------
# Fake PostgreSQL infrastructure
# ---------------------------------------------------------------------------


class FakePgRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


class FakePgConnection:
    def __init__(self, cache_row: dict | None = None) -> None:
        self._cache_row = cache_row
        self.upsert_calls: list[dict] = []

    async def fetchrow(self, query: str, *args: Any) -> FakePgRecord | None:
        q_upper = query.upper()
        if "VIN_THREAD_COMPLETENESS_CACHE" in q_upper:
            if self._cache_row is not None:
                return FakePgRecord(self._cache_row)
            return None
        return None

    async def execute(self, query: str, *args: Any) -> None:
        q_upper = query.upper()
        if "VIN_THREAD_COMPLETENESS_CACHE" in q_upper:
            self.upsert_calls.append({"query": query, "args": args})


class FakePgPool:
    def __init__(self, cache_row: dict | None = None) -> None:
        self._cache_row = cache_row
        self.conn = FakePgConnection(cache_row)

    def acquire(self):
        conn = self.conn

        class _CM:
            async def __aenter__(_self) -> FakePgConnection:
                return conn

            async def __aexit__(_self, *_: Any) -> None:
                pass

        return _CM()


def _fresh_cache_row(overall_pct: float = 100.0) -> dict:
    stages_cfg = _fake_stage_list()
    return {
        "overall_completeness_pct": overall_pct,
        "meets_threshold": overall_pct >= 95.0,
        "stage_completeness": json.dumps(stages_cfg),
        "vin_status": "IN_PROGRESS",
        "updated_at": "2099-01-01T00:00:00+00:00",  # far future = always fresh
    }


def _fake_stage_list() -> list[dict]:
    return [
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch: Any) -> None:
    import app.config as config_mod

    settings = MagicMock()
    settings.neo4j_uri = "bolt://localhost:7687"
    settings.neo4j_user = "neo4j"
    settings.neo4j_password = "localpassword"
    settings.citus_url = "postgresql://localhost/test"
    settings.kafka_bootstrap_servers = "localhost:9092"
    settings.oidc_issuer_url = "http://localhost:8090/default"
    settings.oidc_client_id = "local"
    settings.reader_roles = "quality-engineer,quality-lead"
    settings.neo4j_read_timeout_s = 9.0
    settings.completeness_cache_ttl_s = 30
    settings.reader_role_set.return_value = {"quality-engineer", "quality-lead"}
    monkeypatch.setattr(config_mod, "_settings", settings)


@pytest_asyncio.fixture()
async def client_full_data() -> AsyncGenerator[AsyncClient, None]:
    events = [_paint_event(i) for i in range(1, 15)] + [_booth_event(), _climate_event()]
    neo4j = FakeNeo4jDriver(events, vin_exists=True)
    pg = FakePgPool(cache_row=_fresh_cache_row(98.0))

    import app.db as db_mod
    import app.thread_query_service as tqs_mod
    from app.main import create_app

    app = create_app()

    async def _fake_get_pool():
        return pg

    with (
        patch.object(db_mod, "get_pool", _fake_get_pool),
        patch.object(tqs_mod, "_neo4j_driver", _make_neo4j_patcher(neo4j)),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", timeout=5.0
        ) as ac:
            yield ac


@pytest_asyncio.fixture()
async def client_partial_data() -> AsyncGenerator[AsyncClient, None]:
    # OQ-6: only stages 1-5 present, stages 6-14 missing
    events = [_paint_event(i) for i in range(1, 6)]
    neo4j = FakeNeo4jDriver(events, vin_exists=True)
    pg = FakePgPool(cache_row=None)  # cache miss — will compute

    import app.db as db_mod
    import app.thread_query_service as tqs_mod
    from app.main import create_app

    app = create_app()

    async def _fake_get_pool():
        return pg

    with (
        patch.object(db_mod, "get_pool", _fake_get_pool),
        patch.object(tqs_mod, "_neo4j_driver", _make_neo4j_patcher(neo4j)),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", timeout=5.0
        ) as ac:
            yield ac


@pytest_asyncio.fixture()
async def client_no_data() -> AsyncGenerator[AsyncClient, None]:
    # VIN exists but no events
    neo4j = FakeNeo4jDriver([], vin_exists=True)
    pg = FakePgPool(cache_row=None)

    import app.db as db_mod
    import app.thread_query_service as tqs_mod
    from app.main import create_app

    app = create_app()

    async def _fake_get_pool():
        return pg

    with (
        patch.object(db_mod, "get_pool", _fake_get_pool),
        patch.object(tqs_mod, "_neo4j_driver", _make_neo4j_patcher(neo4j)),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", timeout=5.0
        ) as ac:
            yield ac


@pytest_asyncio.fixture()
async def client_vin_not_found() -> AsyncGenerator[AsyncClient, None]:
    neo4j = FakeNeo4jDriver([], vin_exists=False)
    pg = FakePgPool(cache_row=None)

    import app.db as db_mod
    import app.thread_query_service as tqs_mod
    from app.main import create_app

    app = create_app()

    async def _fake_get_pool():
        return pg

    with (
        patch.object(db_mod, "get_pool", _fake_get_pool),
        patch.object(tqs_mod, "_neo4j_driver", _make_neo4j_patcher(neo4j)),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", timeout=5.0
        ) as ac:
            yield ac


# ---------------------------------------------------------------------------
# Tests: GET /vin/{vin}/thread
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_thread_full_data_200(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(f"/vin/{_VIN}/thread", headers={"Authorization": _BEARER})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_thread_response_shape(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(f"/vin/{_VIN}/thread", headers={"Authorization": _BEARER})
    body = resp.json()
    assert "vin" in body
    assert "data" in body
    assert "page" in body
    assert body["vin"] == _VIN
    assert isinstance(body["data"], list)
    assert "next_cursor" in body["page"]
    assert "limit" in body["page"]


@pytest.mark.asyncio
async def test_get_thread_quality_lead_role_allowed(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread", headers={"Authorization": _BEARER_LEAD}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_thread_wrong_role_returns_403(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread", headers={"Authorization": _BEARER_WRONG_ROLE}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_thread_no_token_returns_401(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(f"/vin/{_VIN}/thread")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_thread_invalid_vin_returns_400(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        "/vin/TOOSHORT/thread", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "code" in body
    assert "message" in body
    assert "correlation_id" in body


@pytest.mark.asyncio
async def test_get_thread_vin_not_found_returns_404(client_vin_not_found: AsyncClient) -> None:
    resp = await client_vin_not_found.get(
        f"/vin/{_VIN}/thread", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "VIN_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_thread_limit_respected(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread?limit=5", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"]["limit"] == 5


@pytest.mark.asyncio
async def test_get_thread_shop_filter_param_accepted(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread?shopFilter=PAINT,BODY", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_thread_error_envelope_on_401(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(f"/vin/{_VIN}/thread")
    body = resp.json()
    assert "code" in body
    assert "message" in body
    assert "correlation_id" in body


# ---------------------------------------------------------------------------
# Tests: GET /vin/{vin}/thread/replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_replay_200_full_data(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_replay_has_14_prep_stages(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    assert len(body["prepStages"]) == 14


@pytest.mark.asyncio
async def test_get_replay_has_booth_and_climate(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    assert "boothParameters" in body
    assert "climateData" in body
    assert isinstance(body["boothParameters"], list)
    assert "available" in body["climateData"]
    assert "samples" in body["climateData"]


@pytest.mark.asyncio
async def test_get_replay_completeness_percent_present(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    assert "overallCompletenessPercent" in body
    pct = body["overallCompletenessPercent"]
    assert 0.0 <= pct <= 100.0


@pytest.mark.asyncio
async def test_get_replay_oq6_partial_stages_available_false(
    client_partial_data: AsyncClient,
) -> None:
    resp = await client_partial_data.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["prepStages"]) == 14
    # stages 1-5 have data, stages 6-14 do not
    for s in body["prepStages"]:
        if s["stageNumber"] <= 5:
            assert s["available"] is True, f"Stage {s['stageNumber']} should be available"
        else:
            assert s["available"] is False, f"Stage {s['stageNumber']} should be unavailable (OQ-6)"
            assert s["samples"] == [], f"Stage {s['stageNumber']} missing samples must be []"


@pytest.mark.asyncio
async def test_get_replay_no_data_all_stages_unavailable(client_no_data: AsyncClient) -> None:
    resp = await client_no_data.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["prepStages"]) == 14
    for s in body["prepStages"]:
        assert s["available"] is False
        assert s["samples"] == []


@pytest.mark.asyncio
async def test_get_replay_vin_not_found_404(client_vin_not_found: AsyncClient) -> None:
    resp = await client_vin_not_found.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_replay_no_token_401(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(f"/vin/{_VIN}/thread/replay")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_replay_invalid_vin_400(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        "/vin/INVALID/thread/replay", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: GET /vin/{vin}/thread/snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_snapshot_200(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread/snapshot?atTs=2026-01-15T09:00:00Z",
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_snapshot_response_shape(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread/snapshot?atTs=2026-01-15T09:00:00Z",
        headers={"Authorization": _BEARER},
    )
    body = resp.json()
    assert "vin" in body
    assert "atTs" in body
    assert "parameters" in body
    assert "dataAvailable" in body
    assert body["vin"] == _VIN
    assert body["atTs"] == "2026-01-15T09:00:00Z"


@pytest.mark.asyncio
async def test_get_snapshot_data_unavailable_for_empty_vin(
    client_no_data: AsyncClient,
) -> None:
    resp = await client_no_data.get(
        f"/vin/{_VIN}/thread/snapshot?atTs=2026-01-15T09:00:00Z",
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dataAvailable"] is False


@pytest.mark.asyncio
async def test_get_snapshot_missing_at_ts_returns_422(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread/snapshot",
        headers={"Authorization": _BEARER},
    )
    # atTs is required — FastAPI returns 422 for missing required query param
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_snapshot_invalid_timestamp_returns_400(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread/snapshot?atTs=not-a-date",
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_snapshot_vin_not_found_returns_404(
    client_vin_not_found: AsyncClient,
) -> None:
    resp = await client_vin_not_found.get(
        f"/vin/{_VIN}/thread/snapshot?atTs=2026-01-15T09:00:00Z",
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_snapshot_no_token_401(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread/snapshot?atTs=2026-01-15T09:00:00Z"
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests: GET /vin/{vin}/thread/completeness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_completeness_200_cache_hit(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread/completeness", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_completeness_response_shape(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread/completeness", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    assert "vin" in body
    assert "overallCompletenessPercent" in body
    assert "stages" in body
    assert body["vin"] == _VIN
    assert 0.0 <= body["overallCompletenessPercent"] <= 100.0
    assert isinstance(body["stages"], list)


@pytest.mark.asyncio
async def test_get_completeness_meets_threshold_field_present(
    client_full_data: AsyncClient,
) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread/completeness", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    assert "meetsThreshold" in body
    assert isinstance(body["meetsThreshold"], bool)


@pytest.mark.asyncio
async def test_get_completeness_cache_miss_computes_14_stages(
    client_partial_data: AsyncClient,
) -> None:
    resp = await client_partial_data.get(
        f"/vin/{_VIN}/thread/completeness", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["stages"]) == 14


@pytest.mark.asyncio
async def test_get_completeness_partial_data_below_threshold(
    client_partial_data: AsyncClient,
) -> None:
    resp = await client_partial_data.get(
        f"/vin/{_VIN}/thread/completeness", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    # Only 5 of 14 stages have data — overall should be < 95%
    assert body["overallCompletenessPercent"] < 95.0
    assert body["meetsThreshold"] is False


@pytest.mark.asyncio
async def test_get_completeness_missing_stages_have_available_false(
    client_partial_data: AsyncClient,
) -> None:
    resp = await client_partial_data.get(
        f"/vin/{_VIN}/thread/completeness", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    for stage in body["stages"]:
        if stage["stageNumber"] > 5:
            assert stage["available"] is False
            assert stage["actualSampleCount"] == 0


@pytest.mark.asyncio
async def test_get_completeness_no_data_zero_overall(client_no_data: AsyncClient) -> None:
    resp = await client_no_data.get(
        f"/vin/{_VIN}/thread/completeness", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overallCompletenessPercent"] == 0.0


@pytest.mark.asyncio
async def test_get_completeness_vin_not_found_returns_404(
    client_vin_not_found: AsyncClient,
) -> None:
    resp = await client_vin_not_found.get(
        f"/vin/{_VIN}/thread/completeness", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_completeness_no_token_401(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(f"/vin/{_VIN}/thread/completeness")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_completeness_wrong_role_403(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        f"/vin/{_VIN}/thread/completeness",
        headers={"Authorization": _BEARER_WRONG_ROLE},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_completeness_invalid_vin_400(client_full_data: AsyncClient) -> None:
    resp = await client_full_data.get(
        "/vin/TOOLONGVIN12345678/thread/completeness",
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Unit tests: cursor encode/decode
# ---------------------------------------------------------------------------


def test_cursor_encode_decode_roundtrip() -> None:
    from app.thread_query_service import _decode_cursor, _encode_cursor

    ts = "2026-01-15T08:00:00+00:00"
    eid = str(uuid.uuid4())
    encoded = _encode_cursor(ts, eid)
    decoded_ts, decoded_id = _decode_cursor(encoded)
    assert decoded_ts == ts
    assert decoded_id == eid


def test_cursor_decode_invalid_raises_value_error() -> None:
    from app.thread_query_service import _decode_cursor

    with pytest.raises(ValueError, match="Invalid cursor"):
        _decode_cursor("not-base64-at-all!!")


def test_cursor_decode_missing_key_raises_value_error() -> None:
    import base64

    from app.thread_query_service import _decode_cursor

    # Valid base64 but missing required keys
    raw = base64.urlsafe_b64encode(b'{"foo": "bar"}').decode()
    with pytest.raises(ValueError, match="Invalid cursor"):
        _decode_cursor(raw)


# ---------------------------------------------------------------------------
# Unit tests: completeness calculation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completeness_all_14_stages_full_returns_100() -> None:
    import app.thread_query_service as tqs_mod
    from app.thread_query_service import _compute_completeness_from_neo4j, get_stage_config

    stages_cfg = get_stage_config()
    stage_events = []
    for s in stages_cfg:
        for _ in range(s["expected_samples"]):
            stage_events.append({
                "eventType": "PAINT_PREP_STAGE",
                "stageNumber": s["number"],
                "occurredAt": "2026-01-15T08:00:00+00:00",
                "parameters": "{}",
            })

    fake_neo4j = FakeNeo4jDriver(stage_events, vin_exists=True)

    with patch.object(tqs_mod, "_neo4j_driver", _make_neo4j_patcher(fake_neo4j)):
        result = await _compute_completeness_from_neo4j(_VIN, timeout=5.0)

    assert result["overall_completeness_pct"] == 100.0
    assert result["meets_threshold"] is True


@pytest.mark.asyncio
async def test_completeness_zero_events_returns_0() -> None:
    import app.thread_query_service as tqs_mod
    from app.thread_query_service import _compute_completeness_from_neo4j

    fake_neo4j = FakeNeo4jDriver([], vin_exists=True)

    with patch.object(tqs_mod, "_neo4j_driver", _make_neo4j_patcher(fake_neo4j)):
        result = await _compute_completeness_from_neo4j(_VIN, timeout=5.0)

    assert result["overall_completeness_pct"] == 0.0
    assert result["meets_threshold"] is False
    assert len(result["stages"]) == 14
    for s in result["stages"]:
        assert s["available"] is False
        assert s["actualSampleCount"] == 0


# ---------------------------------------------------------------------------
# Unit tests: stage config loading
# ---------------------------------------------------------------------------


def test_stage_config_loads_14_stages() -> None:
    from app.thread_query_service import get_stage_config

    stages = get_stage_config()
    assert len(stages) == 14


def test_stage_config_numbers_are_1_to_14() -> None:
    from app.thread_query_service import get_stage_config

    stages = get_stage_config()
    numbers = [s["number"] for s in stages]
    assert sorted(numbers) == list(range(1, 15))


def test_stage_config_each_has_expected_parameters() -> None:
    from app.thread_query_service import get_stage_config

    for s in get_stage_config():
        assert "expected_parameters" in s
        assert len(s["expected_parameters"]) > 0


def test_stage_config_each_has_positive_expected_samples() -> None:
    from app.thread_query_service import get_stage_config

    for s in get_stage_config():
        assert s["expected_samples"] > 0


# ---------------------------------------------------------------------------
# Unit tests: FakeNeo4jDriver instance isolation
# ---------------------------------------------------------------------------


def test_fake_neo4j_driver_instances_are_independent() -> None:
    d1 = FakeNeo4jDriver([_paint_event(1)], vin_exists=True)
    d2 = FakeNeo4jDriver([], vin_exists=False)
    assert d1.session_obj._vin_exists is True
    assert d2.session_obj._vin_exists is False
    assert d1._events is not d2._events


def test_fake_pg_pool_instances_are_independent() -> None:
    p1 = FakePgPool(cache_row={"vin_status": "IN_PROGRESS"})
    p2 = FakePgPool(cache_row=None)
    assert p1.conn._cache_row is not None
    assert p2.conn._cache_row is None
