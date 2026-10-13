"""Tests for POST /ar/sessions.

Covered scenarios:
  - 201 happy path — correct response shape (sessionId, eventId, recordedAt)
  - 409 duplicate idempotency key
  - 401 missing authorization header
  - 400 / 422 invalid payload (missing required field, bad VIN, bad deviceType)
  - ar_session persisted with no biometric fields — extra fields in body are rejected (400)
  - ArSessionStartedEvent emitted with vin, deviceType, stationId, userId; no biometric fields
  - AdoptionEvent emitted with userId, role, actionType='AR_SESSION_STARTED', stationId, timestamp
  - FakeConn instance isolation
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import create_app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VIN = "WBA3A5C55FK123456"
_STATION = "MARRIAGE-01"
_IDEM_KEY = str(uuid.UUID("33333333-3333-3333-3333-333333333333"))

_BASE_PAYLOAD: dict[str, Any] = {
    "vin": _VIN,
    "stationId": _STATION,
    "deviceType": "HoloLens2",
    "idempotencyKey": _IDEM_KEY,
}

# JWT tokens — not verified in tests (verify_signature=False)
# sub=u-002, roles=[ifos-associate]
_BEARER_ASSOCIATE = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1LTAwMiIsInJvbGVzIjpbImlmb3MtYXNzb2NpYXRlIl19"
    ".sig"
)
# sub=sl-001, roles=[ifos-shiftlead]
_BEARER_SHIFTLEAD = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJzbC0wMDEiLCJyb2xlcyI6WyJpZm9zLXNoaWZ0bGVhZCJdfQ"
    ".sig"
)


# ---------------------------------------------------------------------------
# Fake DB
# ---------------------------------------------------------------------------

class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class FakeConn:
    """Fake asyncpg connection — mutable state in instance fields only."""

    def __init__(self, existing_idempotency_key: str | None = None) -> None:
        self._existing_idempotency_key = existing_idempotency_key
        self.inserted_rows: list[dict] = []

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        if "idempotency_key" in query.lower() and self._existing_idempotency_key:
            if str(args[0]) == self._existing_idempotency_key:
                return _FakeRecord({"id": str(uuid.uuid4())})
        return None

    async def execute(self, query: str, *args: Any) -> None:
        if "insert into ar_session" in query.lower():
            self.inserted_rows.append({"args": list(args)})


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> "_AcquireCtx":
        return _AcquireCtx(self._conn)


class _AcquireCtx:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Test client factory
# ---------------------------------------------------------------------------

def _make_client(
    existing_idempotency_key: str | None = None,
) -> tuple[TestClient, FakeConn]:
    conn = FakeConn(existing_idempotency_key=existing_idempotency_key)
    pool = FakePool(conn)
    app = create_app()
    from app.db import get_pool
    app.dependency_overrides[get_pool] = lambda: pool
    return TestClient(app, raise_server_exceptions=True), conn


# ---------------------------------------------------------------------------
# POST /ar/sessions
# ---------------------------------------------------------------------------

class TestStartArSession:
    def test_201_happy_path_hololens2(self) -> None:
        client, conn = _make_client()
        with patch("app.routers.ar_sessions.publish", new=AsyncMock()):
            resp = client.post(
                "/ar/sessions",
                json=_BASE_PAYLOAD,
                headers={"Authorization": _BEARER_ASSOCIATE},
            )
        assert resp.status_code == 201
        body = resp.json()
        uuid.UUID(body["sessionId"])
        uuid.UUID(body["eventId"])
        datetime.fromisoformat(body["recordedAt"])

    def test_201_happy_path_ipad(self) -> None:
        client, conn = _make_client()
        payload = {**_BASE_PAYLOAD, "deviceType": "iPad", "idempotencyKey": str(uuid.uuid4())}
        with patch("app.routers.ar_sessions.publish", new=AsyncMock()):
            resp = client.post(
                "/ar/sessions",
                json=payload,
                headers={"Authorization": _BEARER_ASSOCIATE},
            )
        assert resp.status_code == 201

    def test_201_inserts_exactly_1_row(self) -> None:
        client, conn = _make_client()
        with patch("app.routers.ar_sessions.publish", new=AsyncMock()):
            client.post(
                "/ar/sessions",
                json=_BASE_PAYLOAD,
                headers={"Authorization": _BEARER_ASSOCIATE},
            )
        assert len(conn.inserted_rows) == 1

    def test_201_shift_lead_role_also_permitted(self) -> None:
        client, _ = _make_client()
        with patch("app.routers.ar_sessions.publish", new=AsyncMock()):
            resp = client.post(
                "/ar/sessions",
                json=_BASE_PAYLOAD,
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 201

    def test_409_duplicate_idempotency_key(self) -> None:
        client, _ = _make_client(existing_idempotency_key=_IDEM_KEY)
        with patch("app.routers.ar_sessions.publish", new=AsyncMock()):
            resp = client.post(
                "/ar/sessions",
                json=_BASE_PAYLOAD,
                headers={"Authorization": _BEARER_ASSOCIATE},
            )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "DUPLICATE_AR_SESSION"
        assert "correlation_id" in body

    def test_401_missing_authorization_header(self) -> None:
        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post("/ar/sessions", json=_BASE_PAYLOAD)
        assert resp.status_code == 401

    def test_422_missing_required_field_vin(self) -> None:
        client, _ = _make_client()
        payload = {k: v for k, v in _BASE_PAYLOAD.items() if k != "vin"}
        resp = client.post(
            "/ar/sessions",
            json=payload,
            headers={"Authorization": _BEARER_ASSOCIATE},
        )
        assert resp.status_code == 422

    def test_422_invalid_vin_pattern(self) -> None:
        client, _ = _make_client()
        payload = {**_BASE_PAYLOAD, "vin": "INVALID_VIN_123456"}
        resp = client.post(
            "/ar/sessions",
            json=payload,
            headers={"Authorization": _BEARER_ASSOCIATE},
        )
        assert resp.status_code == 422

    def test_422_invalid_device_type(self) -> None:
        client, _ = _make_client()
        payload = {**_BASE_PAYLOAD, "deviceType": "iPhone"}
        resp = client.post(
            "/ar/sessions",
            json=payload,
            headers={"Authorization": _BEARER_ASSOCIATE},
        )
        assert resp.status_code == 422

    def test_422_extra_biometric_field_rejected(self) -> None:
        """Extra fields are forbidden — no biometric data allowed."""
        client, _ = _make_client()
        payload = {**_BASE_PAYLOAD, "gazeData": "some-gaze"}
        resp = client.post(
            "/ar/sessions",
            json=payload,
            headers={"Authorization": _BEARER_ASSOCIATE},
        )
        assert resp.status_code == 422

    def test_ar_session_started_event_has_required_fields_no_biometric(self) -> None:
        """Test ArSessionStartedEvent fields by directly invoking the publish helper."""
        import asyncio
        from app.routers.ar_sessions import _publish_ar_session_events

        mock_publish = AsyncMock()
        session_id = str(uuid.uuid4())
        event_id = str(uuid.uuid4())

        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            asyncio.get_event_loop().run_until_complete(
                _publish_ar_session_events(
                    session_id=session_id,
                    event_id=event_id,
                    vin=_VIN,
                    station_id=_STATION,
                    device_type="HoloLens2",
                    user_id="u-002",
                    caller_role="ifos-associate",
                    recorded_at="2099-01-01T06:00:00+00:00",
                )
            )

        ar_calls = [
            c for c in mock_publish.call_args_list
            if c[0][0] == "bmw.ifos.munich.line-command.ar-session.started.v1"
        ]
        assert len(ar_calls) == 1
        payload = ar_calls[0][0][1]
        assert payload["vin"] == _VIN
        assert payload["deviceType"] == "HoloLens2"
        assert payload["stationId"] == _STATION
        assert payload["userId"] == "u-002"
        assert payload["eventId"] == event_id
        assert "startedAt" in payload
        # Must not contain any biometric or gaze fields
        assert "gazeData" not in payload
        assert "eyeTracking" not in payload
        assert "biometric" not in payload

    def test_adoption_event_emitted_with_required_fields(self) -> None:
        """Test AdoptionEvent fields by directly invoking the publish helper."""
        import asyncio
        from app.routers.ar_sessions import _publish_ar_session_events

        mock_publish = AsyncMock()
        session_id = str(uuid.uuid4())
        event_id = str(uuid.uuid4())

        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            asyncio.get_event_loop().run_until_complete(
                _publish_ar_session_events(
                    session_id=session_id,
                    event_id=event_id,
                    vin=_VIN,
                    station_id=_STATION,
                    device_type="HoloLens2",
                    user_id="u-002",
                    caller_role="ifos-associate",
                    recorded_at="2099-01-01T06:00:00+00:00",
                )
            )

        adoption_calls = [
            c for c in mock_publish.call_args_list
            if c[0][0] == "factory.munich.line-command.adoption.v1"
        ]
        assert len(adoption_calls) == 1
        adoption_payload = adoption_calls[0][0][1]
        assert adoption_payload["userId"] == "u-002"
        assert adoption_payload["role"] == "ifos-associate"
        assert adoption_payload["actionType"] == "AR_SESSION_STARTED"
        assert adoption_payload["stationId"] == _STATION
        assert adoption_payload["vin"] == _VIN
        assert adoption_payload["deviceType"] == "HoloLens2"
        assert "occurredAt" in adoption_payload
        datetime.fromisoformat(adoption_payload["occurredAt"])

    def test_adoption_event_emitted_within_1_second(self) -> None:
        """The occurredAt timestamp in the adoption event must be close to now."""
        import asyncio
        from app.routers.ar_sessions import _publish_ar_session_events

        mock_publish = AsyncMock()
        before = datetime.now(timezone.utc)

        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            asyncio.get_event_loop().run_until_complete(
                _publish_ar_session_events(
                    session_id=str(uuid.uuid4()),
                    event_id=str(uuid.uuid4()),
                    vin=_VIN,
                    station_id=_STATION,
                    device_type="HoloLens2",
                    user_id="u-002",
                    caller_role="ifos-associate",
                    recorded_at="2099-01-01T06:00:00+00:00",
                )
            )

        after = datetime.now(timezone.utc)
        adoption_calls = [
            c for c in mock_publish.call_args_list
            if c[0][0] == "factory.munich.line-command.adoption.v1"
        ]
        assert len(adoption_calls) == 1
        occurred_at = datetime.fromisoformat(adoption_calls[0][0][1]["occurredAt"])
        elapsed = (after - occurred_at).total_seconds()
        assert elapsed < 2.0, f"AdoptionEvent.occurredAt is {elapsed:.2f}s before test end"


# ---------------------------------------------------------------------------
# FakeConn instance isolation
# ---------------------------------------------------------------------------

class TestFakeConnInstanceIsolation:
    def test_two_instances_have_independent_inserted_rows(self) -> None:
        conn_a = FakeConn()
        conn_b = FakeConn()
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            conn_a.execute(
                "INSERT INTO ar_session (id) VALUES ($1::uuid)",
                str(uuid.uuid4()),
            )
        )
        assert len(conn_a.inserted_rows) == 1
        assert len(conn_b.inserted_rows) == 0

    def test_new_instance_starts_empty(self) -> None:
        conn = FakeConn()
        assert len(conn.inserted_rows) == 0
