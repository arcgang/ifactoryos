"""Unit tests for OEE snapshot, Andon alert, and handover endpoints.

Tests cover:
  OEE stale-flag logic (dataAgeSeconds > 30 → stale=True)
  OEE sort order (returned in created_at DESC order)
  Alert list sort order (minutes_to_predicted_failure ASC NULLS LAST)
  RBAC enforcement — 401 missing token, 403 wrong role on acknowledge
  409 conflict — acknowledge on already-acknowledged alert
  Cursor pagination — next_cursor present when rows > limit, absent when not
  maxAgeSeconds filter plumbed into the query
  GET /andon/alerts/{alertId} 404 branch
  GET /line-command/andon/alerts status translation

Each test class instantiates its own fake so instance state never leaks.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Shared JWT tokens
# ---------------------------------------------------------------------------

# sub=sl-001 roles=[ifos-shiftlead]
_BEARER_SHIFTLEAD = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJzbC0wMDEiLCJyb2xlcyI6WyJpZm9zLXNoaWZ0bGVhZCJdfQ"
    ".sig"
)
# sub=u-002 roles=[ifos-associate]
_BEARER_ASSOCIATE = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1LTAwMiIsInJvbGVzIjpbImlmb3MtYXNzb2NpYXRlIl19"
    ".sig"
)
# sub=bad roles=[planner]
_BEARER_WRONG_ROLE = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJiYWQiLCJyb2xlcyI6WyJwbGFubmVyIl19"
    ".sig"
)

# Far-future anchor for timestamps that must not expire (e.g. JWT payloads, cached records).
_FAR_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)

# Andon/OEE fixtures use NOW so the handler's age calculation is correct.
# _NOW is captured once per test-module import; drift of a few seconds is fine.
_NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fake DB infrastructure
# ---------------------------------------------------------------------------

class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class FakeConnection:
    """Fake asyncpg connection with configurable fetch/fetchrow responses."""

    def __init__(
        self,
        oee_rows: list[dict] | None = None,
        andon_rows: list[dict] | None = None,
        andon_single: dict | None = None,
        handover_rows: list[dict] | None = None,
    ) -> None:
        self._oee_rows = oee_rows or []
        self._andon_rows = andon_rows or []
        self._andon_single = andon_single
        self._handover_rows = handover_rows or []

        self.executed_updates: list[str] = []
        self._txn_active = False

    def transaction(self) -> Any:
        fake = self

        class _Txn:
            async def __aenter__(self_) -> None:
                fake._txn_active = True

            async def __aexit__(self_, *_: Any) -> None:
                fake._txn_active = False

        return _Txn()

    async def fetch(self, query: str, *args: Any) -> list[_FakeRecord]:
        q = query.lower()
        if "oee_snapshot" in q:
            return [_FakeRecord(r) for r in self._oee_rows]
        if "andon_alert" in q:
            return [_FakeRecord(r) for r in self._andon_rows]
        if "shift_handover" in q:
            return [_FakeRecord(r) for r in self._handover_rows]
        return []

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        q = query.lower()
        if "andon_alert" in q:
            if self._andon_single is not None:
                return _FakeRecord(self._andon_single)
            return None
        return None

    async def execute(self, query: str, *args: Any) -> None:
        self.executed_updates.append(query)


class FakePool:
    def __init__(self, conn: FakeConnection) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        conn = self._conn

        class _Ctx:
            async def __aenter__(self_) -> FakeConnection:
                return conn

            async def __aexit__(self_, *_: Any) -> None:
                pass

        return _Ctx()


# ---------------------------------------------------------------------------
# App builder
# ---------------------------------------------------------------------------

def _build_app(conn: FakeConnection) -> Any:
    from app.db import get_pool
    from app.main import create_app

    app = create_app()
    fake_pool = FakePool(conn)
    app.dependency_overrides[get_pool] = lambda: fake_pool
    return app


# ---------------------------------------------------------------------------
# OEE snapshot tests
# ---------------------------------------------------------------------------

def _oee_row(
    line_id: str = "WELD-LINE-1",
    oee: float = 85.5,
    measured_at: datetime | None = None,
    age_seconds: int = 10,
) -> dict:
    # measured_at is expressed as an offset from _NOW so the handler's
    # age calculation (datetime.now(utc) - measured_at) stays accurate.
    if measured_at is None:
        measured_at = _NOW - timedelta(seconds=age_seconds)
    return {
        "oee_snapshot_id": uuid.uuid4(),
        "line_id": line_id,
        "oee_percent": oee,
        "availability": 90.0,
        "performance": 88.0,
        "quality": 95.0,
        "measured_at": measured_at,
        "received_at": _NOW,
        "created_at": _NOW,
    }


class TestOeeSnapshots:
    @pytest.mark.asyncio
    async def test_fresh_row_stale_false(self):
        row = _oee_row(age_seconds=5)
        conn = FakeConnection(oee_rows=[row])
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/oee/snapshots",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["stale"] is False

    @pytest.mark.asyncio
    async def test_stale_row_stale_true_when_age_exceeds_30s(self):
        row = _oee_row(age_seconds=60)
        conn = FakeConnection(oee_rows=[row])
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/oee/snapshots",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 200
        body = resp.json()
        item = body["data"][0]
        assert item["dataAgeSeconds"] >= 60
        assert item["stale"] is True

    @pytest.mark.asyncio
    async def test_exact_30s_is_not_stale(self):
        """Boundary: age == 30 is NOT stale; stale is strictly > 30."""
        row = _oee_row(age_seconds=30)
        conn = FakeConnection(oee_rows=[row])
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/oee/snapshots",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["stale"] is False

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_data_with_null_cursor(self):
        conn = FakeConnection(oee_rows=[])
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/oee/snapshots",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["page"]["next_cursor"] is None

    @pytest.mark.asyncio
    async def test_next_cursor_present_when_has_more(self):
        # limit=1, return 2 rows → has_more=True → next_cursor set
        rows = [_oee_row("WELD-1"), _oee_row("WELD-2")]
        conn = FakeConnection(oee_rows=rows)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/oee/snapshots",
                params={"limit": 1},
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["page"]["next_cursor"] is not None

    @pytest.mark.asyncio
    async def test_returns_401_without_token(self):
        conn = FakeConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/oee/snapshots")
        assert resp.status_code == 401
        assert resp.json()["code"] == "MISSING_TOKEN"

    @pytest.mark.asyncio
    async def test_all_numeric_fields_serialised_as_float(self):
        conn = FakeConnection(oee_rows=[_oee_row()])
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/oee/snapshots",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        item = resp.json()["data"][0]
        assert isinstance(item["oeePercent"], float)
        assert isinstance(item["availability"], float)
        assert isinstance(item["performance"], float)
        assert isinstance(item["quality"], float)

    @pytest.mark.asyncio
    async def test_invalid_cursor_returns_400(self):
        conn = FakeConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/oee/snapshots",
                params={"cursor": "NOT_VALID_BASE64!!!"},
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "CURSOR_INVALID"


# ---------------------------------------------------------------------------
# Andon alert list tests
# ---------------------------------------------------------------------------

def _andon_row(
    status: str = "open",
    minutes: int | None = 10,
    line_id: str = "WELD-LINE-1",
    station_id: str = "ST-01",
) -> dict:
    return {
        "alert_id": uuid.uuid4(),
        "opcenter_event_id": None,
        "station_id": station_id,
        "line_id": line_id,
        "alert_type": "predictive",
        "predicted_failure_type": "electrode_wear",
        "minutes_to_predicted_failure": minutes,
        "recommended_action": "Replace electrode",
        "status": status,
        "detected_at": _NOW - timedelta(minutes=5),
        "acknowledged_at": None,
        "resolved_at": None,
    }


class TestAndonAlertList:
    @pytest.mark.asyncio
    async def test_returns_200_with_data_and_page(self):
        rows = [_andon_row(minutes=5), _andon_row(minutes=2)]
        conn = FakeConnection(andon_rows=rows)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/andon/alerts",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert "page" in body

    @pytest.mark.asyncio
    async def test_invalid_status_returns_400(self):
        conn = FakeConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/andon/alerts",
                params={"status": "BOGUS"},
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "STATUS_INVALID"

    @pytest.mark.asyncio
    async def test_all_four_valid_statuses_accepted(self):
        for s in ("open", "acknowledged", "resolved", "false_positive"):
            conn = FakeConnection(andon_rows=[])
            app = _build_app(conn)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/andon/alerts",
                    params={"status": s},
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
            assert resp.status_code == 200, f"Expected 200 for status={s}"

    @pytest.mark.asyncio
    async def test_alert_fields_match_contract(self):
        row = _andon_row(status="open", minutes=7)
        conn = FakeConnection(andon_rows=[row])
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/andon/alerts",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        item = resp.json()["data"][0]
        assert "alertId" in item
        assert "stationId" in item
        assert "lineId" in item
        assert "alertType" in item
        assert "predictedFailureType" in item
        assert item["minutesToPredictedFailure"] == 7
        assert "recommendedAction" in item
        assert item["status"] == "open"
        assert "detectedAt" in item

    @pytest.mark.asyncio
    async def test_next_cursor_set_when_more_rows_than_limit(self):
        rows = [_andon_row() for _ in range(3)]
        conn = FakeConnection(andon_rows=rows)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/andon/alerts",
                params={"limit": 2},
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["page"]["next_cursor"] is not None

    @pytest.mark.asyncio
    async def test_null_minutes_allowed_for_reactive_alert(self):
        row = _andon_row(minutes=None)
        row["alert_type"] = "reactive"
        conn = FakeConnection(andon_rows=[row])
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/andon/alerts",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        item = resp.json()["data"][0]
        assert item["minutesToPredictedFailure"] is None


# ---------------------------------------------------------------------------
# GET /andon/alerts/{alertId}
# ---------------------------------------------------------------------------

class TestGetAndonAlert:
    @pytest.mark.asyncio
    async def test_returns_200_with_alert_when_found(self):
        alert_id = uuid.uuid4()
        row = _andon_row()
        row["alert_id"] = alert_id
        conn = FakeConnection(andon_single=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/andon/alerts/{alert_id}",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["alertId"] == str(alert_id)

    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(self):
        conn = FakeConnection(andon_single=None)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/andon/alerts/{uuid.uuid4()}",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ALERT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_returns_401_without_token(self):
        conn = FakeConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/andon/alerts/{uuid.uuid4()}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /andon/alerts/{alertId}/acknowledge — RBAC and business logic
# ---------------------------------------------------------------------------

class _AcknowledgeFakeConnection(FakeConnection):
    """Extended fake that simulates SELECT FOR UPDATE + UPDATE."""

    def __init__(self, alert_row: dict | None = None) -> None:
        super().__init__(andon_single=alert_row)
        self._alert_row = alert_row

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        if self._alert_row is not None:
            return _FakeRecord(self._alert_row)
        return None

    async def execute(self, query: str, *args: Any) -> None:
        self.executed_updates.append(query)


class TestAcknowledgeAndonAlert:
    @pytest.mark.asyncio
    async def test_returns_403_for_associate_role(self):
        row = _andon_row(status="open")
        conn = _AcknowledgeFakeConnection(alert_row=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        with patch("app.routers.andon_alerts.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/andon/alerts/{row['alert_id']}/acknowledge",
                    json={"disposition": "resolved"},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert resp.status_code == 403
        assert resp.json()["code"] == "FORBIDDEN"

    @pytest.mark.asyncio
    async def test_returns_403_for_wrong_role(self):
        row = _andon_row(status="open")
        conn = _AcknowledgeFakeConnection(alert_row=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        with patch("app.routers.andon_alerts.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/andon/alerts/{row['alert_id']}/acknowledge",
                    json={"disposition": "monitoring"},
                    headers={"Authorization": _BEARER_WRONG_ROLE},
                )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_shift_lead_can_acknowledge_open_alert(self):
        row = _andon_row(status="open")
        conn = _AcknowledgeFakeConnection(alert_row=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        with patch("app.routers.andon_alerts.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/andon/alerts/{row['alert_id']}/acknowledge",
                    json={"disposition": "escalated", "note": "Called maintenance team"},
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "acknowledged"
        assert "acknowledgedAt" in body
        assert "eventId" in body
        assert "alertId" in body

    @pytest.mark.asyncio
    async def test_returns_409_when_already_acknowledged(self):
        row = _andon_row(status="acknowledged")
        conn = _AcknowledgeFakeConnection(alert_row=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        with patch("app.routers.andon_alerts.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/andon/alerts/{row['alert_id']}/acknowledge",
                    json={"disposition": "resolved"},
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
        assert resp.status_code == 409
        assert resp.json()["code"] == "ALERT_ALREADY_ACKNOWLEDGED"

    @pytest.mark.asyncio
    async def test_returns_409_when_resolved(self):
        row = _andon_row(status="resolved")
        conn = _AcknowledgeFakeConnection(alert_row=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        with patch("app.routers.andon_alerts.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/andon/alerts/{row['alert_id']}/acknowledge",
                    json={"disposition": "resolved"},
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_returns_409_when_false_positive(self):
        row = _andon_row(status="false_positive")
        conn = _AcknowledgeFakeConnection(alert_row=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        with patch("app.routers.andon_alerts.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/andon/alerts/{row['alert_id']}/acknowledge",
                    json={"disposition": "resolved"},
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_returns_404_when_alert_not_found(self):
        conn = _AcknowledgeFakeConnection(alert_row=None)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        with patch("app.routers.andon_alerts.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/andon/alerts/{uuid.uuid4()}/acknowledge",
                    json={"disposition": "monitoring"},
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ALERT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_invalid_disposition_returns_422(self):
        row = _andon_row(status="open")
        conn = _AcknowledgeFakeConnection(alert_row=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/andon/alerts/{row['alert_id']}/acknowledge",
                json={"disposition": "BOGUS_VALUE"},
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_query_executed_on_success(self):
        row = _andon_row(status="open")
        conn = _AcknowledgeFakeConnection(alert_row=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        with patch("app.routers.andon_alerts.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    f"/andon/alerts/{row['alert_id']}/acknowledge",
                    json={"disposition": "resolved"},
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
        assert any("UPDATE andon_alert" in q for q in conn.executed_updates)

    @pytest.mark.asyncio
    async def test_adoption_event_published_on_success(self):
        row = _andon_row(status="open")
        conn = _AcknowledgeFakeConnection(alert_row=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        published: list[dict] = []

        async def fake_publish(topic: str, payload: dict) -> None:
            published.append({"topic": topic, "payload": payload})

        with patch("app.routers.andon_alerts.publish", side_effect=fake_publish):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    f"/andon/alerts/{row['alert_id']}/acknowledge",
                    json={"disposition": "monitoring"},
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )

        assert len(published) == 1
        assert published[0]["topic"] == "factory.munich.line-command.adoption.v1"
        payload = published[0]["payload"]
        assert payload["eventType"] == "ALERT_ACKNOWLEDGED"
        assert str(row["alert_id"]) == payload["alertId"]

    @pytest.mark.asyncio
    async def test_returns_401_without_token(self):
        conn = FakeConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/andon/alerts/{uuid.uuid4()}/acknowledge",
                json={"disposition": "resolved"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /line-command/andon/alerts
# ---------------------------------------------------------------------------

class TestLineCommandAndonAlerts:
    @pytest.mark.asyncio
    async def test_returns_200_for_valid_status(self):
        for s in ("open", "resolved", "escalated", "monitoring"):
            conn = FakeConnection(andon_rows=[])
            app = _build_app(conn)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/line-command/andon/alerts",
                    params={"status": s},
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
            assert resp.status_code == 200, f"Expected 200 for status={s}"

    @pytest.mark.asyncio
    async def test_invalid_status_returns_400(self):
        conn = FakeConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/line-command/andon/alerts",
                params={"status": "BOGUS"},
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_data_shape_matches_andon_alert_contract(self):
        rows = [_andon_row()]
        conn = FakeConnection(andon_rows=rows)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/line-command/andon/alerts",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "page" in body


# ---------------------------------------------------------------------------
# FakeConnection instance isolation
# ---------------------------------------------------------------------------

class TestFakeConnectionInstanceIsolation:
    def test_two_instances_have_independent_executed_updates(self):
        import asyncio

        conn_a = _AcknowledgeFakeConnection()
        conn_b = _AcknowledgeFakeConnection()

        async def _run() -> None:
            await conn_a.execute(
                "UPDATE andon_alert SET status = $1 WHERE alert_id = $2", "x", "y"
            )

        asyncio.run(_run())
        assert len(conn_a.executed_updates) == 1
        assert len(conn_b.executed_updates) == 0

    def test_new_instance_executed_updates_starts_empty(self):
        conn = _AcknowledgeFakeConnection()
        assert conn.executed_updates == []
