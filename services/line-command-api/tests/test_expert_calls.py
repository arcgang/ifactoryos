"""Unit tests for POST /ar/expert-calls and POST /ar/expert-calls/{callId}/end.

Uses in-process fakes for PostgreSQL (asyncpg), VDT HTTP client, and Kafka.
Each test class instantiates its own fakes so state never bleeds between tests.

Covered scenarios:
  POST /ar/expert-calls — video path (Works Council approved)
  POST /ar/expert-calls — voice-only path (Works Council NOT approved)
  POST /ar/expert-calls — idempotent repeat POST returns existing row unchanged
  POST /ar/expert-calls/{callId}/end — writes audit entry, publishes Kafka event
  POST /ar/expert-calls/{callId}/end — blocked when call status already 'ended' (404)
  POST /ar/expert-calls/{callId}/end — 502 when VDT call fails, DB not updated
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Test constants — no absolute timestamps; far-future anchor used instead
# ---------------------------------------------------------------------------

_VIN = "WBA3A5C55FK123456"
_STATION = "MARRIAGE-01"
_SESSION_ID = str(uuid.uuid4())
_CALL_ID = str(uuid.uuid4())
_AUDIT_ENTRY_ID = str(uuid.uuid4())
_EXPERT_USER = "expert-007"

# JWT: sub=user-123, roles=[ifos-associate]
_BEARER_ASSOCIATE = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbImlmb3MtYXNzb2NpYXRlIl19"
    ".sig"
)

_FAR_FUTURE = datetime(2099, 1, 1, 6, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fake DB infrastructure — mutable state in instance fields, never module-level
# ---------------------------------------------------------------------------

class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class FakeConnection:
    """Fake asyncpg connection — each instantiation starts with clean state."""

    def __init__(
        self,
        video_approved: bool = False,
        existing_call: dict | None = None,
    ) -> None:
        self._video_approved = video_approved
        self._existing_call = existing_call
        self.initiated_calls: list[dict] = []
        self.ended_calls: list[dict] = []
        self._executes: list[dict] = []
        self._in_transaction = False

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        q = query.lower()
        if "works_council_config" in q:
            return _FakeRecord({"video_streaming_approved": self._video_approved})
        if "insert into expert_call" in q:
            call_id = uuid.uuid4()
            row: dict = {
                "id": call_id,
                "call_mode": args[3] if len(args) > 3 else "voice",
                "created_at": _FAR_FUTURE,
            }
            self.initiated_calls.append({"args": list(args), "row": row})
            return _FakeRecord(row)
        if "update expert_call" in q and "returning" in q:
            # First UPDATE: SET status='ended' RETURNING id, ended_at
            if self._existing_call is None:
                return None
            call_id_arg = args[0]
            if str(call_id_arg) != str(self._existing_call.get("id", "")):
                return None
            ended_at = args[1]
            updated: dict = {
                "id": call_id_arg,
                "ended_at": ended_at,
            }
            self.ended_calls.append({"args": list(args), "row": updated})
            return _FakeRecord(updated)
        if "select" in q and "expert_call" in q and "status" in q:
            if self._existing_call is None:
                return None
            return _FakeRecord(self._existing_call)
        return None

    async def execute(self, query: str, *args: Any) -> None:
        self._executes.append({"query": query, "args": list(args)})

    async def fetchval(self, query: str, *args: Any) -> Any:
        return None

    @asynccontextmanager
    async def _transaction_ctx(self):
        self._in_transaction = True
        try:
            yield
        except Exception:
            self._in_transaction = False
            raise
        self._in_transaction = False

    def transaction(self) -> Any:
        return self._transaction_ctx()


class FakePool:
    """Fake asyncpg pool — each instantiation has its own FakeConnection."""

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
    from app.audit import AuditStore
    from app.main import create_app
    from app.routers.expert_calls import get_audit_writer, get_pool

    app = create_app()
    fake_pool = FakePool(conn)
    fake_writer = AuditStore()

    app.dependency_overrides[get_pool] = lambda: fake_pool
    app.dependency_overrides[get_audit_writer] = lambda: fake_writer

    return app


# ---------------------------------------------------------------------------
# Helper: build mock VDT HTTP response
# ---------------------------------------------------------------------------

def _mock_vdt_success(audit_entry_id: str) -> Any:
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"eventId": audit_entry_id}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _mock_vdt_failure() -> Any:
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ---------------------------------------------------------------------------
# POST /ar/expert-calls — video path (Works Council approved)
# ---------------------------------------------------------------------------

class TestInitiateExpertCallVideoPath:
    @pytest.mark.asyncio
    async def test_video_approved_returns_201_with_callMode_video(self):
        conn = FakeConnection(video_approved=True)
        app = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/ar/expert-calls",
                    json={"vin": _VIN, "stationId": _STATION, "sessionId": _SESSION_ID},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert resp.status_code == 201
        body = resp.json()
        assert body["callMode"] == "video"
        assert "callId" in body
        assert "createdAt" in body

    @pytest.mark.asyncio
    async def test_video_approved_inserts_exactly_1_expert_call_row(self):
        conn = FakeConnection(video_approved=True)
        app = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    "/ar/expert-calls",
                    json={"vin": _VIN, "stationId": _STATION, "sessionId": _SESSION_ID},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert len(conn.initiated_calls) == 1


# ---------------------------------------------------------------------------
# POST /ar/expert-calls — voice-only path (Works Council NOT approved)
# ---------------------------------------------------------------------------

class TestInitiateExpertCallVoicePath:
    @pytest.mark.asyncio
    async def test_video_not_approved_returns_201_with_callMode_voice(self):
        conn = FakeConnection(video_approved=False)
        app = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/ar/expert-calls",
                    json={"vin": _VIN, "stationId": _STATION, "sessionId": _SESSION_ID},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert resp.status_code == 201
        body = resp.json()
        assert body["callMode"] == "voice"

    @pytest.mark.asyncio
    async def test_voice_only_passes_call_mode_voice_to_db(self):
        conn = FakeConnection(video_approved=False)
        app = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    "/ar/expert-calls",
                    json={"vin": _VIN, "stationId": _STATION, "sessionId": _SESSION_ID},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert len(conn.initiated_calls) == 1
        # call_mode is the 4th positional arg (index 3) in INSERT
        assert conn.initiated_calls[0]["args"][3] == "voice"


# ---------------------------------------------------------------------------
# POST /ar/expert-calls — idempotent repeat POST
# ---------------------------------------------------------------------------

class TestInitiateExpertCallIdempotent:
    @pytest.mark.asyncio
    async def test_repeat_post_with_same_idempotency_key_returns_201_both_times(self):
        idempotency_key = str(uuid.uuid4())
        conn = FakeConnection(video_approved=False)
        app = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r1 = await client.post(
                    "/ar/expert-calls",
                    json={
                        "vin": _VIN,
                        "stationId": _STATION,
                        "sessionId": _SESSION_ID,
                        "idempotencyKey": idempotency_key,
                    },
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
                r2 = await client.post(
                    "/ar/expert-calls",
                    json={
                        "vin": _VIN,
                        "stationId": _STATION,
                        "sessionId": _SESSION_ID,
                        "idempotencyKey": idempotency_key,
                    },
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert r1.status_code == 201
        assert r2.status_code == 201

    @pytest.mark.asyncio
    async def test_repeat_post_passes_same_idempotency_key_to_db(self):
        idempotency_key = str(uuid.uuid4())
        conn = FakeConnection(video_approved=False)
        app = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for _ in range(2):
                    await client.post(
                        "/ar/expert-calls",
                        json={
                            "vin": _VIN,
                            "stationId": _STATION,
                            "sessionId": _SESSION_ID,
                            "idempotencyKey": idempotency_key,
                        },
                        headers={"Authorization": _BEARER_ASSOCIATE},
                    )
        # Both calls should pass the same idempotency_key (args[4]) to the INSERT
        key_first = str(conn.initiated_calls[0]["args"][4])
        key_second = str(conn.initiated_calls[1]["args"][4])
        assert key_first == key_second == idempotency_key


# ---------------------------------------------------------------------------
# POST /ar/expert-calls/{callId}/end — happy path
# ---------------------------------------------------------------------------

class TestEndExpertCall:
    @pytest.mark.asyncio
    async def test_end_active_call_returns_200_with_callId_auditEntryId_endedAt(self):
        existing = {
            "id": _CALL_ID,
            "vin": _VIN,
            "station_id": _STATION,
            "call_mode": "video",
            "status": "active",
        }
        conn = FakeConnection(existing_call=existing)
        app = _build_app(conn)
        with (
            patch(
                "app.expert_call_service.httpx.AsyncClient",
                return_value=_mock_vdt_success(_AUDIT_ENTRY_ID),
            ),
            patch("app.routers.expert_calls.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/ar/expert-calls/{_CALL_ID}/end",
                    json={"expertUserId": _EXPERT_USER, "durationSeconds": 120},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["callId"] == _CALL_ID
        assert body["auditEntryId"] == _AUDIT_ENTRY_ID
        assert "endedAt" in body

    @pytest.mark.asyncio
    async def test_end_call_issues_update_returning_query(self):
        existing = {
            "id": _CALL_ID,
            "vin": _VIN,
            "station_id": _STATION,
            "call_mode": "voice",
            "status": "active",
        }
        conn = FakeConnection(existing_call=existing)
        app = _build_app(conn)
        with (
            patch(
                "app.expert_call_service.httpx.AsyncClient",
                return_value=_mock_vdt_success(_AUDIT_ENTRY_ID),
            ),
            patch("app.routers.expert_calls.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/ar/expert-calls/{_CALL_ID}/end",
                    json={"expertUserId": _EXPERT_USER, "durationSeconds": 60},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert resp.status_code == 200
        assert len(conn.ended_calls) == 1


# ---------------------------------------------------------------------------
# POST /ar/expert-calls/{callId}/end — blocked when already ended (404)
# ---------------------------------------------------------------------------

class TestEndExpertCallAlreadyEnded:
    @pytest.mark.asyncio
    async def test_end_call_with_status_ended_returns_404_CALL_ALREADY_ENDED(self):
        existing = {
            "id": _CALL_ID,
            "vin": _VIN,
            "station_id": _STATION,
            "call_mode": "video",
            "status": "ended",
        }
        conn = FakeConnection(existing_call=existing)
        app = _build_app(conn)
        with patch("app.routers.expert_calls.publish", new=AsyncMock()):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/ar/expert-calls/{_CALL_ID}/end",
                    json={"expertUserId": _EXPERT_USER, "durationSeconds": 10},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "CALL_ALREADY_ENDED"

    @pytest.mark.asyncio
    async def test_end_nonexistent_call_returns_404_CALL_NOT_FOUND(self):
        conn = FakeConnection(existing_call=None)
        app = _build_app(conn)
        with patch("app.routers.expert_calls.publish", new=AsyncMock()):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/ar/expert-calls/{_CALL_ID}/end",
                    json={"expertUserId": _EXPERT_USER, "durationSeconds": 10},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "CALL_NOT_FOUND"


# ---------------------------------------------------------------------------
# POST /ar/expert-calls/{callId}/end — VDT failure → 502, DB not updated
# ---------------------------------------------------------------------------

class TestEndExpertCallVdtFailure:
    @pytest.mark.asyncio
    async def test_vdt_failure_returns_502_VDT_AUDIT_FAILED(self):
        existing = {
            "id": _CALL_ID,
            "vin": _VIN,
            "station_id": _STATION,
            "call_mode": "video",
            "status": "active",
        }
        conn = FakeConnection(existing_call=existing)
        app = _build_app(conn)
        with (
            patch(
                "app.expert_call_service.httpx.AsyncClient",
                return_value=_mock_vdt_failure(),
            ),
            patch("app.routers.expert_calls.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/ar/expert-calls/{_CALL_ID}/end",
                    json={"expertUserId": _EXPERT_USER, "durationSeconds": 30},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert resp.status_code == 502
        body = resp.json()
        assert body["code"] == "VDT_AUDIT_FAILED"

    @pytest.mark.asyncio
    async def test_vdt_failure_rolls_back_so_no_audit_entry_id_written(self):
        """VDT failure triggers transaction rollback — no audit_entry_id UPDATE is issued."""
        existing = {
            "id": _CALL_ID,
            "vin": _VIN,
            "station_id": _STATION,
            "call_mode": "video",
            "status": "active",
        }
        conn = FakeConnection(existing_call=existing)
        app = _build_app(conn)
        with (
            patch(
                "app.expert_call_service.httpx.AsyncClient",
                return_value=_mock_vdt_failure(),
            ),
            patch("app.routers.expert_calls.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    f"/ar/expert-calls/{_CALL_ID}/end",
                    json={"expertUserId": _EXPERT_USER, "durationSeconds": 30},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        # The second UPDATE (set audit_entry_id) must NOT have been issued
        audit_executes = [
            e for e in conn._executes if "audit_entry_id" in e["query"].lower()
        ]
        assert len(audit_executes) == 0


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestExpertCallAuth:
    @pytest.mark.asyncio
    async def test_missing_bearer_on_initiate_returns_401(self):
        conn = FakeConnection()
        app = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/ar/expert-calls",
                    json={"vin": _VIN, "stationId": _STATION, "sessionId": _SESSION_ID},
                )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_bearer_on_end_returns_401(self):
        conn = FakeConnection()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/ar/expert-calls/{_CALL_ID}/end",
                json={"expertUserId": _EXPERT_USER, "durationSeconds": 10},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# FakeConnection instance-isolation test
# ---------------------------------------------------------------------------

class TestFakeConnectionInstanceIsolation:
    def test_two_instances_have_independent_initiated_calls_lists(self):
        conn_a = FakeConnection()
        conn_b = FakeConnection()
        conn_a.initiated_calls.append({"args": []})
        assert len(conn_a.initiated_calls) == 1
        assert len(conn_b.initiated_calls) == 0

    def test_new_instance_initiated_calls_starts_at_0(self):
        conn = FakeConnection()
        assert len(conn.initiated_calls) == 0
        assert len(conn.ended_calls) == 0
