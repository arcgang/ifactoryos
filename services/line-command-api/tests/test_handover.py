"""Unit and integration tests for the shift handover endpoints and Andon consumer.

Covers:
  POST /handover → 403 when caller lacks shift-lead role
  POST /handover → 201 with full HandoverResponse on valid payload
  POST /handover → 409 on idempotency_key collision
  POST /handover → publishes HandoverSubmittedEvent and AdoptionEvent to Kafka
  POST /handover → 401 when no token provided
  GET  /handovers → 200 paginated list
  GET  /handovers → cursor advances on second call (has_more)
  GET  /handovers/{handoverId} → 200 with full HandoverRecord
  GET  /handovers/{handoverId} → 404 for unknown UUID
  Andon consumer dedup → same opcenter_event_id produces one row (ON CONFLICT)
  Andon consumer → missing opcenter_event_id is dropped without error
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

_FAR_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)
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


class FakeHandoverConnection:
    """Fake asyncpg connection for handover endpoint tests."""

    def __init__(
        self,
        handover_rows: list[dict] | None = None,
        handover_single: dict | None = None,
        idempotency_conflict: bool = False,
    ) -> None:
        self._handover_rows = handover_rows or []
        self._handover_single = handover_single
        self._idempotency_conflict = idempotency_conflict

        self.inserts: list[tuple[str, tuple]] = []
        self.updates: list[tuple[str, tuple]] = []
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
        if "shift_handover" in q:
            return [_FakeRecord(r) for r in self._handover_rows]
        return []

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        q = query.lower()
        if "idempotency_key" in q:
            if self._idempotency_conflict:
                return _FakeRecord(
                    {"id": uuid.uuid4(), "created_at": _NOW}
                )
            return None
        if "shift_handover" in q:
            if self._handover_single is not None:
                return _FakeRecord(self._handover_single)
            return None
        return None

    async def execute(self, query: str, *args: Any) -> None:
        q = query.lower()
        if "insert" in q:
            self.inserts.append((query, args))
        elif "update" in q:
            self.updates.append((query, args))


class FakePool:
    def __init__(self, conn: FakeHandoverConnection) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        conn = self._conn

        class _Ctx:
            async def __aenter__(self_) -> FakeHandoverConnection:
                return conn

            async def __aexit__(self_, *_: Any) -> None:
                pass

        return _Ctx()


# ---------------------------------------------------------------------------
# App builder
# ---------------------------------------------------------------------------

def _build_app(conn: FakeHandoverConnection) -> Any:
    from app.db import get_pool
    from app.main import create_app

    app = create_app()
    fake_pool = FakePool(conn)
    app.dependency_overrides[get_pool] = lambda: fake_pool
    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_handover_body(
    idempotency_key: str | None = None,
    alert_id: str | None = None,
) -> dict:
    return {
        "shiftId": str(uuid.uuid4()),
        "lineId": "WELD-LINE-1",
        "shiftStartTs": "2026-10-13T06:00:00+00:00",
        "shiftEndTs": "2026-10-13T14:00:00+00:00",
        "alertDispositions": [
            {
                "alertId": alert_id or str(uuid.uuid4()),
                "disposition": "resolved",
                "note": "Fixed at station 3",
            }
        ],
        "oeeDeltaPct": 2.5,
        "heldVins": ["WBA12345678901234"],
        "notes": "All stations running normally at handover.",
        "idempotencyKey": idempotency_key or str(uuid.uuid4()),
    }


def _handover_db_row(
    line_id: str = "WELD-LINE-1",
    shift_end_ts: datetime | None = None,
) -> dict:
    return {
        "id": uuid.uuid4(),
        "shift_id": uuid.uuid4(),
        "line_id": line_id,
        "shift_start_ts": _NOW - timedelta(hours=8),
        "shift_end_ts": shift_end_ts or _NOW,
        "submitted_by_user_id": "sl-001",
        "alert_dispositions": [
            {"alertId": str(uuid.uuid4()), "disposition": "resolved", "note": None}
        ],
        "oee_delta_pct": 1.5,
        "held_vins": ["WBA12345678901234"],
        "notes": None,
        "idempotency_key": uuid.uuid4(),
        "created_at": _NOW,
        "updated_at": _NOW,
    }


# ---------------------------------------------------------------------------
# POST /handover — RBAC
# ---------------------------------------------------------------------------

class TestSubmitHandoverRbac:
    @pytest.mark.asyncio
    async def test_returns_403_for_associate_role(self):
        conn = FakeHandoverConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        with patch("app.routers.handover.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/handover",
                    json=_valid_handover_body(),
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert resp.status_code == 403
        assert resp.json()["code"] == "FORBIDDEN"

    @pytest.mark.asyncio
    async def test_returns_401_without_token(self):
        conn = FakeHandoverConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/handover", json=_valid_handover_body())
        assert resp.status_code == 401
        assert resp.json()["code"] == "MISSING_TOKEN"


# ---------------------------------------------------------------------------
# POST /handover — valid submission
# ---------------------------------------------------------------------------

class TestSubmitHandoverSuccess:
    @pytest.mark.asyncio
    async def test_returns_201_with_handover_id_and_submitted_at(self):
        conn = FakeHandoverConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        published: list[dict] = []

        async def fake_publish(topic: str, payload: dict) -> None:
            published.append({"topic": topic, "payload": payload})

        with patch("app.routers.handover.publish", side_effect=fake_publish):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/handover",
                    json=_valid_handover_body(),
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )

        assert resp.status_code == 201
        body = resp.json()
        assert "handoverId" in body
        assert "submittedAt" in body
        assert body["submittedByUserId"] == "sl-001"

    @pytest.mark.asyncio
    async def test_inserts_handover_row_in_db(self):
        conn = FakeHandoverConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        with patch("app.routers.handover.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    "/handover",
                    json=_valid_handover_body(),
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
        assert len(conn.inserts) == 1
        assert "shift_handover" in conn.inserts[0][0].lower()

    @pytest.mark.asyncio
    async def test_updates_andon_alert_disposition(self):
        alert_id = str(uuid.uuid4())
        conn = FakeHandoverConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        body = _valid_handover_body(alert_id=alert_id)
        body["alertDispositions"][0]["disposition"] = "escalated"
        with patch("app.routers.handover.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    "/handover",
                    json=body,
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
        assert any("UPDATE andon_alert" in u[0] for u in conn.updates)

    @pytest.mark.asyncio
    async def test_publishes_handover_submitted_event(self):
        conn = FakeHandoverConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        published: list[dict] = []

        async def fake_publish(topic: str, payload: dict) -> None:
            published.append({"topic": topic, "payload": payload})

        with patch("app.routers.handover.publish", side_effect=fake_publish):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    "/handover",
                    json=_valid_handover_body(),
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )

        topics = [p["topic"] for p in published]
        assert "factory.munich.line-command.handover.v1" in topics
        handover_event = next(
            p["payload"] for p in published
            if p["topic"] == "factory.munich.line-command.handover.v1"
        )
        assert handover_event["eventType"] == "HANDOVER_SUBMITTED"
        assert "handoverId" in handover_event

    @pytest.mark.asyncio
    async def test_publishes_adoption_event_handover_submitted(self):
        conn = FakeHandoverConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        published: list[dict] = []

        async def fake_publish(topic: str, payload: dict) -> None:
            published.append({"topic": topic, "payload": payload})

        with patch("app.routers.handover.publish", side_effect=fake_publish):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    "/handover",
                    json=_valid_handover_body(),
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )

        adoption_events = [
            p for p in published
            if p["topic"] == "factory.munich.line-command.adoption.v1"
        ]
        assert len(adoption_events) == 1
        assert adoption_events[0]["payload"]["eventType"] == "HANDOVER_SUBMITTED"

    @pytest.mark.asyncio
    async def test_invalid_vin_returns_422(self):
        conn = FakeHandoverConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        body = _valid_handover_body()
        body["heldVins"] = ["INVALID-VIN"]
        with patch("app.routers.handover.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/handover",
                    json=body,
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_audit_write_failure_returns_500(self):
        conn = FakeHandoverConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        class FailingAuditWriter:
            def write_event(self, **_: Any) -> str:
                raise RuntimeError("Azure WORM unavailable")

        with (
            patch("app.routers.handover.publish", new=AsyncMock()),
            patch("app.routers.handover.get_audit_writer", return_value=FailingAuditWriter()),
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/handover",
                    json=_valid_handover_body(),
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
        assert resp.status_code == 500
        assert resp.json()["code"] == "INSERT_FAILED"


# ---------------------------------------------------------------------------
# POST /handover — idempotency collision → 409
# ---------------------------------------------------------------------------

class TestSubmitHandoverIdempotency:
    @pytest.mark.asyncio
    async def test_returns_409_when_idempotency_key_already_used(self):
        idem_key = str(uuid.uuid4())
        conn = FakeHandoverConnection(idempotency_conflict=True)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        with patch("app.routers.handover.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/handover",
                    json=_valid_handover_body(idempotency_key=idem_key),
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
        assert resp.status_code == 409
        assert resp.json()["code"] == "HANDOVER_ALREADY_SUBMITTED"

    @pytest.mark.asyncio
    async def test_409_response_has_required_error_fields(self):
        conn = FakeHandoverConnection(idempotency_conflict=True)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        with patch("app.routers.handover.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/handover",
                    json=_valid_handover_body(),
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
        body = resp.json()
        assert "code" in body
        assert "message" in body
        assert "correlation_id" in body


# ---------------------------------------------------------------------------
# GET /handovers — list pagination
# ---------------------------------------------------------------------------

class TestListHandovers:
    @pytest.mark.asyncio
    async def test_returns_200_with_data_and_page(self):
        rows = [_handover_db_row()]
        conn = FakeHandoverConnection(handover_rows=rows)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/handovers",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "page" in body
        assert len(body["data"]) == 1

    @pytest.mark.asyncio
    async def test_cursor_set_when_more_rows_than_limit(self):
        rows = [
            _handover_db_row(shift_end_ts=_NOW - timedelta(hours=i))
            for i in range(3)
        ]
        conn = FakeHandoverConnection(handover_rows=rows)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/handovers",
                params={"limit": 2},
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["page"]["next_cursor"] is not None

    @pytest.mark.asyncio
    async def test_null_cursor_on_last_page(self):
        rows = [_handover_db_row()]
        conn = FakeHandoverConnection(handover_rows=rows)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/handovers",
                params={"limit": 20},
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        body = resp.json()
        assert body["page"]["next_cursor"] is None

    @pytest.mark.asyncio
    async def test_returns_401_without_token(self):
        conn = FakeHandoverConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/handovers")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_cursor_returns_400(self):
        conn = FakeHandoverConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/handovers",
                params={"cursor": "NOT_VALID!!!"},
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "CURSOR_INVALID"

    @pytest.mark.asyncio
    async def test_record_fields_match_contract(self):
        rows = [_handover_db_row()]
        conn = FakeHandoverConnection(handover_rows=rows)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/handovers",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        item = resp.json()["data"][0]
        assert "handoverId" in item
        assert "shiftId" in item
        assert "lineId" in item
        assert "shiftStartTs" in item
        assert "shiftEndTs" in item
        assert "submittedByUserId" in item
        assert "alertDispositions" in item
        assert "oeeDeltaPct" in item
        assert "heldVins" in item
        assert "createdAt" in item


# ---------------------------------------------------------------------------
# GET /handovers/{handoverId}
# ---------------------------------------------------------------------------

class TestGetHandover:
    @pytest.mark.asyncio
    async def test_returns_200_with_full_record(self):
        row = _handover_db_row()
        handover_id = row["id"]
        conn = FakeHandoverConnection(handover_single=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/handovers/{handover_id}",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["handoverId"] == str(handover_id)
        assert body["lineId"] == "WELD-LINE-1"
        assert isinstance(body["alertDispositions"], list)
        assert isinstance(body["heldVins"], list)

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_uuid(self):
        conn = FakeHandoverConnection(handover_single=None)
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/handovers/{uuid.uuid4()}",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "HANDOVER_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_returns_401_without_token(self):
        conn = FakeHandoverConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/handovers/{uuid.uuid4()}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Andon consumer deduplication (integration-style unit test)
# ---------------------------------------------------------------------------

class TestAndonConsumerDedup:
    @pytest.mark.asyncio
    async def test_same_opcenter_event_id_produces_one_insert(self):
        from app.andon_consumer import process_andon_event

        executed_queries: list[str] = []

        class FakeConn:
            async def execute(self, query: str, *args: Any) -> None:
                executed_queries.append(query)

        event = {
            "alertId": str(uuid.uuid4()),
            "opcenterEventId": "evt-001",
            "stationId": "ST-01",
            "lineId": "WELD-LINE-1",
            "alertType": "predictive",
            "predictedFailureType": "electrode_wear",
            "minutesToPredictedFailure": 15,
            "recommendedAction": "Replace electrode tip",
            "status": "open",
            "detectedAt": _NOW.isoformat(),
        }

        conn = FakeConn()
        await process_andon_event(conn, event)
        await process_andon_event(conn, event)

        assert len(executed_queries) == 2
        for q in executed_queries:
            assert "ON CONFLICT" in q

    @pytest.mark.asyncio
    async def test_event_missing_opcenter_event_id_is_dropped(self):
        from app.andon_consumer import process_andon_event

        executed_queries: list[str] = []

        class FakeConn:
            async def execute(self, query: str, *args: Any) -> None:
                executed_queries.append(query)

        event = {
            "alertId": str(uuid.uuid4()),
            "stationId": "ST-01",
            "lineId": "WELD-LINE-1",
            "alertType": "predictive",
            "predictedFailureType": "electrode_wear",
            "status": "open",
            "detectedAt": _NOW.isoformat(),
        }

        conn = FakeConn()
        await process_andon_event(conn, event)
        assert len(executed_queries) == 0
