"""
Acceptance tests for US-013 · Process Replay  (backend — vin-digital-thread-api)

These tests exercise the seam between the replay endpoint and the snapshot
endpoint, covering:

  AC1 — GET /vin/{vin}/thread/replay returns all 14 prep-stage tracks,
          booth parameters, and climate data in a single response within the
          10-second budget.  The replay window start/end is present so the
          frontend can render an aligned timeline.

  AC2 — GET /vin/{vin}/thread/snapshot?atTs=<ts> returns parameter values,
          stage name, and tunnel position at the requested timestamp.

  AC3 — Partial-traceability (OQ-6): missing stages are represented with
          available=False and samples=[]; overallCompletenessPercent is
          present at the top-level of the replay response.

The fake Neo4j / PostgreSQL infrastructure is taken from test_thread_read.py
so the pattern is consistent with the existing test suite.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Reuse helpers from the sibling test module
# ---------------------------------------------------------------------------

_VIN = "WBA3A5C55FK123456"
_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbInF1YWxpdHktZW5naW5lZXIiXX0"
    ".sig"
)


# ---------- Fake Neo4j ----------

def _make_paint_event(
    stage_number: int,
    occurred_at: str,
    tunnel_mm: float | None = None,
    params: dict | None = None,
) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "eventType": "PAINT_PREP_STAGE",
        "occurredAt": occurred_at,
        "shop": "PAINT",
        "station": f"ST_{stage_number:02d}",
        "dataSource": "PLC",
        "parameters": json.dumps(
            params
            or {
                "ecoat_voltage_v": 300 + stage_number * 2,
                "phosphate_conc": 1.1 + stage_number * 0.05,
                "temperature_c": 20 + stage_number,
                "humidity_pct": 55,
            }
        ),
        "linkedAt": occurred_at,
        "stageNumber": stage_number,
        "tunnelPositionMm": tunnel_mm if tunnel_mm is not None else 5000 + stage_number * 3000,
        "boothId": None,
    }


def _make_booth_event(booth_id: str, occurred_at: str) -> dict[str, Any]:
    ev = {
        "eventId": str(uuid.uuid4()),
        "eventType": "BOOTH_PARAMETER",
        "occurredAt": occurred_at,
        "shop": "PAINT",
        "station": booth_id,
        "dataSource": "SCADA",
        "parameters": json.dumps({"pressure_bar": 2.1, "flow_rate_lpm": 12.5}),
        "linkedAt": occurred_at,
        "stageNumber": None,
        "tunnelPositionMm": None,
        "boothId": booth_id,
    }
    return ev


def _make_climate_event(occurred_at: str) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "eventType": "CLIMATE_DATA",
        "occurredAt": occurred_at,
        "shop": "PAINT",
        "station": "CLIMATE_SENSOR_1",
        "dataSource": "HVAC",
        "parameters": json.dumps({"ambient_temp_c": 21.5, "humidity_pct": 45.0}),
        "linkedAt": occurred_at,
        "stageNumber": None,
        "tunnelPositionMm": None,
        "boothId": None,
    }


class FakeNeo4jResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def data(self) -> list[dict]:
        return self._rows


class FakeNeo4jSession:
    def __init__(self, events: list[dict], vin_exists: bool = True) -> None:
        self._events = events
        self._vin_exists = vin_exists

    async def run(self, cypher: str, **kwargs: Any) -> FakeNeo4jResult:
        cypher_upper = cypher.upper()

        # VIN existence check
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
            return FakeNeo4jResult(
                [{"stageNumber": k, "cnt": v} for k, v in stage_counts.items()]
            )

        # Snapshot query — last event at or before atTs
        if "ORDER BY E.OCCURREDAT DESC" in cypher_upper and "LIMIT 1" in cypher_upper:
            at_ts = kwargs.get("at_ts", "9999")
            matching = [e for e in self._events if e["occurredAt"] <= at_ts]
            if matching:
                latest = sorted(matching, key=lambda e: e["occurredAt"])[-1]
                return FakeNeo4jResult([{"e": latest}])
            return FakeNeo4jResult([])

        # General events / replay query
        limit = kwargs.get("limit", 201)
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
    async def _fake_neo4j_driver():
        return fake_driver
    return _fake_neo4j_driver


# ---------- Fake PostgreSQL ----------

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
        if "VIN_THREAD_COMPLETENESS_CACHE" in query.upper() and self._cache_row is not None:
            return FakePgRecord(self._cache_row)
        return None

    async def execute(self, query: str, *args: Any) -> None:
        pass


class FakePgPool:
    def __init__(self, cache_row: dict | None = None) -> None:
        self.conn = FakePgConnection(cache_row)

    def acquire(self):
        conn = self.conn

        class _CM:
            async def __aenter__(_self) -> FakePgConnection:
                return conn

            async def __aexit__(_self, *_: Any) -> None:
                pass

        return _CM()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch: Any) -> None:
    import app.config as config_mod

    from unittest.mock import MagicMock
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


def _full_14_events() -> list[dict]:
    """14 paint prep stages + one booth + one climate event."""
    # Stage i → hour i (01:00 .. 14:00), all valid ISO 8601.
    events = [
        _make_paint_event(i, f"2026-10-12T{i:02d}:00:00+00:00", tunnel_mm=5000 + i * 3000)
        for i in range(1, 15)
    ]
    events.append(_make_booth_event("BOOTH_1", "2026-10-12T15:00:00+00:00"))
    events.append(_make_climate_event("2026-10-12T16:00:00+00:00"))
    return events


def _oq6_partial_events() -> list[dict]:
    """Stages 1-5 present, stages 6-14 missing (OQ-6 partial traceability)."""
    return [
        _make_paint_event(i, f"2026-10-12T{i:02d}:00:00+00:00")
        for i in range(1, 6)
    ]


async def _make_client(
    events: list[dict],
    vin_exists: bool = True,
    cache_row: dict | None = None,
) -> AsyncClient:
    neo4j = FakeNeo4jDriver(events, vin_exists=vin_exists)
    pg = FakePgPool(cache_row=cache_row)

    import app.db as db_mod
    import app.thread_query_service as tqs_mod
    from app.main import create_app

    app = create_app()

    async def _fake_get_pool():
        return pg

    cm = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=5.0
    )
    cm._patchers = [
        patch.object(db_mod, "get_pool", _fake_get_pool),
        patch.object(tqs_mod, "_neo4j_driver", _make_neo4j_patcher(neo4j)),
    ]
    return cm


@pytest_asyncio.fixture()
async def client_full() -> AsyncGenerator[AsyncClient, None]:
    neo4j = FakeNeo4jDriver(_full_14_events(), vin_exists=True)
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
async def client_oq6() -> AsyncGenerator[AsyncClient, None]:
    neo4j = FakeNeo4jDriver(_oq6_partial_events(), vin_exists=True)
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
# AC1 — replay returns 14 prep-stage tracks + booth + climate, within budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac1_replay_responds_within_10_seconds(client_full: AsyncClient) -> None:
    """AC1: the replay endpoint must respond within 10 seconds."""
    t0 = time.monotonic()
    resp = await client_full.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    elapsed = time.monotonic() - t0
    assert resp.status_code == 200
    assert elapsed < 10.0, f"Replay took {elapsed:.2f}s — exceeds 10-second budget"


@pytest.mark.asyncio
async def test_ac1_replay_has_exactly_14_prep_stages(client_full: AsyncClient) -> None:
    """AC1: all 14 prep stages are present in the response."""
    resp = await client_full.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 200
    body = resp.json()

    # Precondition: prepStages field must exist
    assert "prepStages" in body, "replay response must contain 'prepStages'"

    stages = body["prepStages"]
    assert len(stages) == 14, f"Expected 14 prep stages, got {len(stages)}"


@pytest.mark.asyncio
async def test_ac1_replay_stage_numbers_are_1_to_14(client_full: AsyncClient) -> None:
    """AC1: stage numbers cover 1–14 contiguously so the timeline is aligned."""
    resp = await client_full.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    stage_numbers = sorted(s["stageNumber"] for s in body["prepStages"])
    assert stage_numbers == list(range(1, 15)), (
        f"Stage numbers must be 1-14 contiguously; got {stage_numbers}"
    )


@pytest.mark.asyncio
async def test_ac1_replay_includes_booth_parameters(client_full: AsyncClient) -> None:
    """AC1: booth parameters are present in the replay response."""
    resp = await client_full.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    assert "boothParameters" in body, "replay response must contain 'boothParameters'"
    assert isinstance(body["boothParameters"], list)
    assert len(body["boothParameters"]) >= 1, "at least one booth must be in the response"
    booth = body["boothParameters"][0]
    assert booth["available"] is True
    assert len(booth["samples"]) >= 1


@pytest.mark.asyncio
async def test_ac1_replay_includes_climate_data(client_full: AsyncClient) -> None:
    """AC1: climate data is present in the replay response."""
    resp = await client_full.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    assert "climateData" in body, "replay response must contain 'climateData'"
    assert body["climateData"]["available"] is True
    assert len(body["climateData"]["samples"]) >= 1


@pytest.mark.asyncio
async def test_ac1_replay_window_timestamps_present(client_full: AsyncClient) -> None:
    """AC1: replayWindowStart and replayWindowEnd must be non-null for aligned rendering."""
    resp = await client_full.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    assert body.get("replayWindowStart") is not None, (
        "replayWindowStart must be set so the frontend can align the timeline"
    )
    assert body.get("replayWindowEnd") is not None, (
        "replayWindowEnd must be set so the frontend can align the timeline"
    )
    # Sanity: start before end
    assert body["replayWindowStart"] <= body["replayWindowEnd"]


@pytest.mark.asyncio
async def test_ac1_replay_stage_samples_contain_expected_parameters(
    client_full: AsyncClient,
) -> None:
    """AC1: each available stage exposes the key paint parameters in its samples."""
    resp = await client_full.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    for stage in body["prepStages"]:
        if stage["available"]:
            for sample in stage["samples"]:
                params = sample["parameters"]
                # ecoat voltage, phosphate concentration, humidity, temperature
                assert "ecoat_voltage_v" in params, (
                    f"Stage {stage['stageNumber']} sample missing ecoat_voltage_v"
                )
                assert "phosphate_conc" in params, (
                    f"Stage {stage['stageNumber']} sample missing phosphate_conc"
                )
                assert "temperature_c" in params, (
                    f"Stage {stage['stageNumber']} sample missing temperature_c"
                )
                assert "humidity_pct" in params, (
                    f"Stage {stage['stageNumber']} sample missing humidity_pct"
                )


# ---------------------------------------------------------------------------
# AC2 — snapshot returns parameter values, stage name, and tunnel position
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac2_snapshot_returns_stage_name(client_full: AsyncClient) -> None:
    """AC2: snapshot must return the stage name at the requested timestamp."""
    resp = await client_full.get(
        f"/vin/{_VIN}/thread/snapshot?atTs=2026-10-12T07:00:00Z",
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "stageName" in body, "snapshot must contain 'stageName'"
    # With full data loaded, a stage name must be resolved
    assert body["stageName"] is not None, "stageName must be non-null for a known stage"


@pytest.mark.asyncio
async def test_ac2_snapshot_returns_tunnel_position(client_full: AsyncClient) -> None:
    """AC2: snapshot must return the tunnel position at the requested timestamp."""
    resp = await client_full.get(
        f"/vin/{_VIN}/thread/snapshot?atTs=2026-10-12T07:00:00Z",
        headers={"Authorization": _BEARER},
    )
    body = resp.json()
    assert "tunnelPositionMm" in body, "snapshot must contain 'tunnelPositionMm'"
    # The fake event for stage 7 has tunnelPositionMm = 5000 + 7*3000 = 26000
    assert body["tunnelPositionMm"] is not None, (
        "tunnelPositionMm must be non-null when the event carries position data"
    )
    assert isinstance(body["tunnelPositionMm"], (int, float))


@pytest.mark.asyncio
async def test_ac2_snapshot_returns_parameter_values(client_full: AsyncClient) -> None:
    """AC2: snapshot must return parameter values at the requested timestamp."""
    resp = await client_full.get(
        f"/vin/{_VIN}/thread/snapshot?atTs=2026-10-12T07:00:00Z",
        headers={"Authorization": _BEARER},
    )
    body = resp.json()
    assert "parameters" in body, "snapshot must contain 'parameters'"
    assert isinstance(body["parameters"], dict)
    assert len(body["parameters"]) > 0, (
        "parameters dict must be non-empty when data is available"
    )


@pytest.mark.asyncio
async def test_ac2_snapshot_data_available_true_for_known_timestamp(
    client_full: AsyncClient,
) -> None:
    """AC2: dataAvailable is True when there is an event at or before atTs."""
    resp = await client_full.get(
        f"/vin/{_VIN}/thread/snapshot?atTs=2026-10-12T07:00:00Z",
        headers={"Authorization": _BEARER},
    )
    body = resp.json()
    assert body.get("dataAvailable") is True, (
        "dataAvailable must be True when an event exists at or before the requested timestamp"
    )


@pytest.mark.asyncio
async def test_ac2_snapshot_returns_vin_in_response(client_full: AsyncClient) -> None:
    """AC2: snapshot must echo the VIN so the data panel can display it."""
    resp = await client_full.get(
        f"/vin/{_VIN}/thread/snapshot?atTs=2026-10-12T07:00:00Z",
        headers={"Authorization": _BEARER},
    )
    body = resp.json()
    assert body["vin"] == _VIN


@pytest.mark.asyncio
async def test_ac2_snapshot_echoes_requested_timestamp(client_full: AsyncClient) -> None:
    """AC2: snapshot atTs field echoes the requested timestamp for panel display."""
    at_ts = "2026-10-12T07:00:00Z"
    resp = await client_full.get(
        f"/vin/{_VIN}/thread/snapshot?atTs={at_ts}",
        headers={"Authorization": _BEARER},
    )
    body = resp.json()
    assert body["atTs"] == at_ts


# ---------------------------------------------------------------------------
# AC3 — OQ-6 partial traceability: missing stages marked available=False,
#         completeness % present at top of replay response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac3_oq6_missing_stages_are_available_false(client_oq6: AsyncClient) -> None:
    """AC3: stages without data (OQ-6) must have available=False in the replay."""
    resp = await client_oq6.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    assert resp.status_code == 200
    body = resp.json()

    # Precondition: 14 stages present regardless of availability
    assert len(body["prepStages"]) == 14, (
        "All 14 stage slots must be present even when data is missing"
    )

    missing_stages = [s for s in body["prepStages"] if s["stageNumber"] > 5]
    for s in missing_stages:
        assert s["available"] is False, (
            f"Stage {s['stageNumber']} should be available=False (OQ-6 partial data)"
        )
        assert s["samples"] == [], (
            f"Stage {s['stageNumber']} must have empty samples list when unavailable"
        )


@pytest.mark.asyncio
async def test_ac3_oq6_present_stages_are_available_true(client_oq6: AsyncClient) -> None:
    """AC3: stages with data (1-5) must have available=True."""
    resp = await client_oq6.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    present_stages = [s for s in body["prepStages"] if s["stageNumber"] <= 5]
    for s in present_stages:
        assert s["available"] is True, (
            f"Stage {s['stageNumber']} should be available=True (data exists)"
        )
        assert len(s["samples"]) >= 1, (
            f"Stage {s['stageNumber']} must have at least one sample"
        )


@pytest.mark.asyncio
async def test_ac3_overall_completeness_percent_in_replay_response(
    client_oq6: AsyncClient,
) -> None:
    """AC3: overallCompletenessPercent must be present at the top level of the replay."""
    resp = await client_oq6.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    assert "overallCompletenessPercent" in body, (
        "overallCompletenessPercent must appear at the top level of the replay response "
        "so the frontend can display it above the timeline"
    )
    pct = body["overallCompletenessPercent"]
    assert 0.0 <= pct <= 100.0, f"completeness percent out of range: {pct}"


@pytest.mark.asyncio
async def test_ac3_oq6_completeness_below_100(client_oq6: AsyncClient) -> None:
    """AC3: partial data must produce a completeness less than 100%."""
    resp = await client_oq6.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    pct = body["overallCompletenessPercent"]
    assert pct < 100.0, (
        f"With only 5 of 14 stages present, completeness must be < 100%, got {pct}"
    )


@pytest.mark.asyncio
async def test_ac3_completeness_100_when_all_stages_present(
    client_full: AsyncClient,
) -> None:
    """AC3 (inverse): all 14 stages present → completeness equals 100%."""
    resp = await client_full.get(
        f"/vin/{_VIN}/thread/replay", headers={"Authorization": _BEARER}
    )
    body = resp.json()
    pct = body["overallCompletenessPercent"]
    assert pct == 100.0, (
        f"All 14 stages available — expected 100% completeness, got {pct}"
    )


@pytest.mark.asyncio
async def test_ac3_snapshot_data_unavailable_when_no_events(
    client_oq6: AsyncClient,
) -> None:
    """AC3: snapshot at a timestamp before any event returns dataAvailable=False."""
    resp = await client_oq6.get(
        # Request a snapshot before any of the OQ-6 events
        f"/vin/{_VIN}/thread/snapshot?atTs=2026-01-01T00:00:00Z",
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dataAvailable"] is False, (
        "dataAvailable must be False when no event exists at or before the requested timestamp"
    )
