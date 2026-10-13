"""Tests for POST /handover.

Covered scenarios:
  - 201 happy path (shift lead role) — correct response shape
  - 409 duplicate idempotency key
  - 409 duplicate (shift_id, line_id) combination
  - 403 caller without shift-lead role
  - 401 missing authorization header
  - 400 invalid payload (missing required field)
  - 400 invalid VIN in heldVins
  - AdoptionEvent emitted with required fields (userId, role, actionType, stationId, timestamp)
  - HandoverSubmittedEvent emitted with required fields
  - FakeConn instance isolation
"""

from __future__ import annotations

import json
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
_SHIFT_ID = str(uuid.UUID("11111111-1111-1111-1111-111111111111"))
_LINE_ID = "WELD-01"
_IDEM_KEY = str(uuid.UUID("22222222-2222-2222-2222-222222222222"))

_BASE_PAYLOAD: dict[str, Any] = {
    "shiftId": _SHIFT_ID,
    "lineId": _LINE_ID,
    "shiftStartTs": "2099-01-01T06:00:00+00:00",
    "shiftEndTs": "2099-01-01T14:00:00+00:00",
    "alertDispositions": [],
    "oeeDeltaPct": 2.5,
    "heldVins": [],
    "idempotencyKey": _IDEM_KEY,
}

# JWT tokens — not verified in tests (verify_signature=False)
# sub=sl-001, roles=[ifos-shiftlead]
_BEARER_SHIFTLEAD = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJzbC0wMDEiLCJyb2xlcyI6WyJpZm9zLXNoaWZ0bGVhZCJdfQ"
    ".sig"
)
# sub=u-002, roles=[ifos-associate]
_BEARER_ASSOCIATE = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1LTAwMiIsInJvbGVzIjpbImlmb3MtYXNzb2NpYXRlIl19"
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

    def __init__(
        self,
        existing_idempotency_key: str | None = None,
        existing_shift_line: tuple[str, str] | None = None,
    ) -> None:
        self._existing_idempotency_key = existing_idempotency_key
        self._existing_shift_line = existing_shift_line
        self.inserted_rows: list[dict] = []

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        q = query.lower()
        if "idempotency_key" in q and self._existing_idempotency_key:
            if str(args[0]) == self._existing_idempotency_key:
                return _FakeRecord({"id": str(uuid.uuid4())})
        if "shift_id" in q and "line_id" in q and self._existing_shift_line:
            shift_id, line_id = self._existing_shift_line
            if str(args[0]) == shift_id and str(args[1]) == line_id:
                return _FakeRecord({"id": str(uuid.uuid4())})
        return None

    async def execute(self, query: str, *args: Any) -> None:
        if "insert into shift_handover" in query.lower():
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
    existing_shift_line: tuple[str, str] | None = None,
) -> tuple[TestClient, FakeConn]:
    conn = FakeConn(
        existing_idempotency_key=existing_idempotency_key,
        existing_shift_line=existing_shift_line,
    )
    pool = FakePool(conn)
    app = create_app()
    from app.db import get_pool
    app.dependency_overrides[get_pool] = lambda: pool
    return TestClient(app, raise_server_exceptions=True), conn


# ---------------------------------------------------------------------------
# POST /handover
# ---------------------------------------------------------------------------

class TestSubmitHandover:
    def test_201_happy_path_shift_lead(self) -> None:
        client, conn = _make_client()
        with patch("app.routers.handover.publish", new=AsyncMock()):
            resp = client.post(
                "/handover",
                json=_BASE_PAYLOAD,
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert "handoverId" in body
        uuid.UUID(body["handoverId"])
        assert "submittedAt" in body
        datetime.fromisoformat(body["submittedAt"])
        assert body["submittedByUserId"] == "sl-001"

    def test_201_inserts_exactly_1_row(self) -> None:
        client, conn = _make_client()
        with patch("app.routers.handover.publish", new=AsyncMock()):
            client.post(
                "/handover",
                json=_BASE_PAYLOAD,
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert len(conn.inserted_rows) == 1

    def test_409_duplicate_idempotency_key(self) -> None:
        client, _ = _make_client(existing_idempotency_key=_IDEM_KEY)
        with patch("app.routers.handover.publish", new=AsyncMock()):
            resp = client.post(
                "/handover",
                json=_BASE_PAYLOAD,
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "DUPLICATE_HANDOVER"
        assert "correlation_id" in body

    def test_409_duplicate_shift_line(self) -> None:
        client, _ = _make_client(
            existing_shift_line=(_SHIFT_ID, _LINE_ID),
        )
        payload = {**_BASE_PAYLOAD, "idempotencyKey": str(uuid.uuid4())}
        with patch("app.routers.handover.publish", new=AsyncMock()):
            resp = client.post(
                "/handover",
                json=payload,
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "HANDOVER_ALREADY_SUBMITTED"

    def test_403_non_shift_lead_role(self) -> None:
        client, _ = _make_client()
        with patch("app.routers.handover.publish", new=AsyncMock()):
            resp = client.post(
                "/handover",
                json=_BASE_PAYLOAD,
                headers={"Authorization": _BEARER_ASSOCIATE},
            )
        assert resp.status_code == 403
        body = resp.json()
        assert body["code"] == "FORBIDDEN"
        assert "correlation_id" in body

    def test_401_missing_authorization_header(self) -> None:
        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post("/handover", json=_BASE_PAYLOAD)
        assert resp.status_code == 401

    def test_422_missing_required_field_shiftId(self) -> None:
        client, _ = _make_client()
        payload = {k: v for k, v in _BASE_PAYLOAD.items() if k != "shiftId"}
        resp = client.post(
            "/handover",
            json=payload,
            headers={"Authorization": _BEARER_SHIFTLEAD},
        )
        assert resp.status_code == 422

    def test_422_invalid_vin_in_held_vins(self) -> None:
        client, _ = _make_client()
        payload = {**_BASE_PAYLOAD, "heldVins": ["INVALID_VIN_123456"]}
        resp = client.post(
            "/handover",
            json=payload,
            headers={"Authorization": _BEARER_SHIFTLEAD},
        )
        assert resp.status_code == 422

    def test_valid_vin_in_held_vins_accepted(self) -> None:
        client, _ = _make_client()
        payload = {**_BASE_PAYLOAD, "heldVins": [_VIN]}
        with patch("app.routers.handover.publish", new=AsyncMock()):
            resp = client.post(
                "/handover",
                json=payload,
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 201

    def test_alert_dispositions_accepted(self) -> None:
        client, _ = _make_client()
        payload = {
            **_BASE_PAYLOAD,
            "alertDispositions": [
                {
                    "alertId": str(uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
                    "disposition": "resolved",
                    "note": "Fixed by realigning",
                }
            ],
        }
        with patch("app.routers.handover.publish", new=AsyncMock()):
            resp = client.post(
                "/handover",
                json=payload,
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )
        assert resp.status_code == 201

    def test_adoption_event_emitted_with_required_fields(self) -> None:
        """Test adoption event fields by directly invoking the publish helper."""
        import asyncio
        from app.routers.handover import _publish_handover_events

        mock_publish = AsyncMock()
        handover_id = str(uuid.uuid4())

        with patch("app.routers.handover.publish", new=mock_publish):
            asyncio.get_event_loop().run_until_complete(
                _publish_handover_events(
                    handover_id=handover_id,
                    shift_id=str(uuid.uuid4()),
                    line_id=_LINE_ID,
                    shift_start_ts="2099-01-01T06:00:00+00:00",
                    shift_end_ts="2099-01-01T14:00:00+00:00",
                    user_id="sl-001",
                    caller_role="ifos-shiftlead",
                    oee_delta_pct=2.5,
                    submitted_at="2099-01-01T14:00:00+00:00",
                )
            )

        adoption_calls = [
            c for c in mock_publish.call_args_list
            if c[0][0] == "factory.munich.line-command.adoption.v1"
        ]
        assert len(adoption_calls) == 1
        adoption_payload = adoption_calls[0][0][1]
        assert adoption_payload["userId"] == "sl-001"
        assert adoption_payload["role"] == "ifos-shiftlead"
        assert adoption_payload["actionType"] == "HANDOVER_SUBMITTED"
        assert adoption_payload["stationId"] == _LINE_ID
        assert "occurredAt" in adoption_payload
        datetime.fromisoformat(adoption_payload["occurredAt"])

    def test_handover_submitted_event_emitted_with_required_fields(self) -> None:
        """Test HandoverSubmittedEvent fields by directly invoking the publish helper."""
        import asyncio
        from app.routers.handover import _publish_handover_events

        mock_publish = AsyncMock()
        handover_id = str(uuid.uuid4())

        with patch("app.routers.handover.publish", new=mock_publish):
            asyncio.get_event_loop().run_until_complete(
                _publish_handover_events(
                    handover_id=handover_id,
                    shift_id=_SHIFT_ID,
                    line_id=_LINE_ID,
                    shift_start_ts="2099-01-01T06:00:00+00:00",
                    shift_end_ts="2099-01-01T14:00:00+00:00",
                    user_id="sl-001",
                    caller_role="ifos-shiftlead",
                    oee_delta_pct=2.5,
                    submitted_at="2099-01-01T14:00:00+00:00",
                )
            )

        handover_calls = [
            c for c in mock_publish.call_args_list
            if c[0][0] == "factory.munich.line-command.handover.v1"
        ]
        assert len(handover_calls) == 1
        payload = handover_calls[0][0][1]
        assert payload["handoverId"] == handover_id
        assert payload["submittedByUserId"] == "sl-001"
        assert payload["shiftId"] == _SHIFT_ID
        assert payload["lineId"] == _LINE_ID
        assert "submittedAt" in payload


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
                "INSERT INTO shift_handover (id) VALUES ($1::uuid)",
                str(uuid.uuid4()),
            )
        )
        assert len(conn_a.inserted_rows) == 1
        assert len(conn_b.inserted_rows) == 0

    def test_new_instance_starts_empty(self) -> None:
        conn = FakeConn()
        assert len(conn.inserted_rows) == 0
