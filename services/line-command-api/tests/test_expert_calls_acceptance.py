"""Acceptance tests for US-012 — Trigger a remote expert call from the AR Workbench.

These tests exercise the seam between the two tasks:
  • line-command-api: Works Council mode selection, call initiation, session end,
                      and VIN digital thread audit logging
  • line-command (frontend): covered by the companion frontend acceptance test

Criterion coverage:
  AC1 — video streaming Works-Council-approved: callMode=video returned by POST
  AC2 — voice-only fallback (not approved): callMode=voice, media constraints audio-only
  AC3 — on end: duration, VIN, stationId, callMode, expertUserId logged to VDT with
                biometricDataIncluded=False and gazeDataIncluded=False
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VIN = "WBA3A5C55FK999001"
_STATION = "MARRIAGE-03"
_SESSION_ID = str(uuid.uuid4())
_CALL_ID = str(uuid.uuid4())
_AUDIT_ENTRY_ID = str(uuid.uuid4())
_EXPERT_USER = "expert-acceptance-001"

_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbImlmb3MtYXNzb2NpYXRlIl19"
    ".sig"
)

_FAR_FUTURE = datetime(2099, 1, 1, 6, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fake DB infrastructure — all mutable state in instance fields, never module-level
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

    def __init__(self, video_approved: bool = False, existing_call: dict | None = None) -> None:
        self._video_approved = video_approved
        self._existing_call = existing_call
        self.initiated_calls: list[dict] = []
        self.ended_calls: list[dict] = []
        self._executes: list[dict] = []

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        q = query.lower()
        if "works_council_config" in q:
            return _FakeRecord({"video_streaming_approved": self._video_approved})
        if "insert into expert_call" in q:
            row: dict = {
                "id": uuid.UUID(_CALL_ID),
                "call_mode": args[3] if len(args) > 3 else "voice",
                "created_at": _FAR_FUTURE,
            }
            self.initiated_calls.append({"args": list(args), "row": row})
            return _FakeRecord(row)
        if "update expert_call" in q and "returning" in q:
            if self._existing_call is None:
                return None
            if str(args[0]) != str(self._existing_call.get("id", "")):
                return None
            ended_at = args[1]
            updated: dict = {"id": args[0], "ended_at": ended_at}
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
    async def _tx_ctx(self):
        yield

    def transaction(self) -> Any:
        return self._tx_ctx()


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
    from app.audit import AuditStore
    from app.main import create_app
    from app.routers.expert_calls import get_audit_writer, get_pool

    app = create_app()
    app.dependency_overrides[get_pool] = lambda: FakePool(conn)
    app.dependency_overrides[get_audit_writer] = lambda: AuditStore()
    return app


# ---------------------------------------------------------------------------
# VDT mock helpers — captures the payload sent to VDT
# ---------------------------------------------------------------------------

def _mock_vdt_capturing(audit_entry_id: str) -> tuple[Any, list[dict]]:
    """Return (mock_client, captured_payloads).

    Each call to mock_client.post records the JSON payload in captured_payloads.
    """
    captured: list[dict] = []

    async def _post(url: str, *, json: dict | None = None, **_kwargs: Any) -> Any:
        if json is not None:
            captured.append(json)
        resp = MagicMock()
        resp.status_code = 201
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"eventId": audit_entry_id}
        return resp

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=_post)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client, captured


# ---------------------------------------------------------------------------
# AC1: video streaming Works-Council-approved → callMode=video returned
# ---------------------------------------------------------------------------

class TestAC1VideoApproved:
    """AC1: Given video streaming is Works-Council-approved, call connects with callMode=video."""

    @pytest.mark.asyncio
    async def test_ac1_video_approved_response_has_callMode_video(self):
        """Precondition: works_council_config row has video_streaming_approved=True.
        Expected: POST /ar/expert-calls returns 201 with callMode=video."""
        conn = FakeConnection(video_approved=True)
        # Assert precondition — our fake is configured video_approved=True
        assert conn._video_approved is True, (
            "Precondition failed: FakeConnection must be configured with video_approved=True"
        )

        app = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/ar/expert-calls",
                    json={"vin": _VIN, "stationId": _STATION, "sessionId": _SESSION_ID},
                    headers={"Authorization": _BEARER},
                )

        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        body = resp.json()
        # AC1: callMode must be video when Works Council has approved video streaming
        assert body["callMode"] == "video", (
            f"Expected callMode='video' when video is WC-approved, got '{body['callMode']}'"
        )
        # callId and createdAt must be present so the AR client can wire up the stream
        assert "callId" in body
        assert "createdAt" in body

    @pytest.mark.asyncio
    async def test_ac1_video_approved_stores_call_mode_video_in_db(self):
        """AC1: video call_mode is persisted to expert_call row (not just echoed in response)."""
        conn = FakeConnection(video_approved=True)
        app = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    "/ar/expert-calls",
                    json={"vin": _VIN, "stationId": _STATION, "sessionId": _SESSION_ID},
                    headers={"Authorization": _BEARER},
                )

        assert len(conn.initiated_calls) == 1, "Expected exactly one INSERT into expert_call"
        # call_mode is the 4th positional arg (index 3) in the INSERT
        assert conn.initiated_calls[0]["args"][3] == "video"

    @pytest.mark.asyncio
    async def test_ac1_vin_and_station_id_propagated_to_db(self):
        """AC1: VIN and stationId from the request are persisted (required for the audit trail)."""
        conn = FakeConnection(video_approved=True)
        app = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    "/ar/expert-calls",
                    json={"vin": _VIN, "stationId": _STATION, "sessionId": _SESSION_ID},
                    headers={"Authorization": _BEARER},
                )

        args = conn.initiated_calls[0]["args"]
        assert args[0] == _VIN, f"Expected VIN={_VIN!r} in DB INSERT args, got {args[0]!r}"
        assert args[1] == _STATION, f"Expected stationId={_STATION!r} in DB INSERT args, got {args[1]!r}"


# ---------------------------------------------------------------------------
# AC2: voice-only fallback — no video, audio only
# ---------------------------------------------------------------------------

class TestAC2VoiceOnlyFallback:
    """AC2: Given Works Council has NOT approved video streaming, voice-only call is established."""

    @pytest.mark.asyncio
    async def test_ac2_voice_only_response_has_callMode_voice(self):
        """Precondition: works_council_config row has video_streaming_approved=False.
        Expected: POST /ar/expert-calls returns 201 with callMode=voice."""
        conn = FakeConnection(video_approved=False)
        assert conn._video_approved is False, (
            "Precondition failed: FakeConnection must be configured with video_approved=False"
        )

        app = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/ar/expert-calls",
                    json={"vin": _VIN, "stationId": _STATION, "sessionId": _SESSION_ID},
                    headers={"Authorization": _BEARER},
                )

        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        body = resp.json()
        # AC2: callMode must be voice when Works Council has NOT approved video
        assert body["callMode"] == "voice", (
            f"Expected callMode='voice' when video is not WC-approved, got '{body['callMode']}'"
        )

    @pytest.mark.asyncio
    async def test_ac2_voice_only_stores_call_mode_voice_in_db(self):
        """AC2: voice call_mode is persisted — ensures DB matches the response contract."""
        conn = FakeConnection(video_approved=False)
        app = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    "/ar/expert-calls",
                    json={"vin": _VIN, "stationId": _STATION, "sessionId": _SESSION_ID},
                    headers={"Authorization": _BEARER},
                )

        assert len(conn.initiated_calls) == 1
        # call_mode is the 4th positional arg (index 3) in the INSERT
        assert conn.initiated_calls[0]["args"][3] == "voice", (
            "Expected call_mode='voice' passed to INSERT when video is not WC-approved"
        )

    @pytest.mark.asyncio
    async def test_ac2_absent_works_council_config_defaults_to_voice(self):
        """AC2: missing works_council_config row (returns None) defaults to voice — no crash."""
        class _FakeConnNoWCConfig(FakeConnection):
            async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
                if "works_council_config" in query.lower():
                    return None  # plant not configured → voice by default
                return await super().fetchrow(query, *args)

        conn = _FakeConnNoWCConfig(video_approved=False)
        app = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/ar/expert-calls",
                    json={"vin": _VIN, "stationId": _STATION, "sessionId": _SESSION_ID},
                    headers={"Authorization": _BEARER},
                )

        assert resp.status_code == 201
        assert resp.json()["callMode"] == "voice"


# ---------------------------------------------------------------------------
# AC3: on end — VDT audit trail contains all required fields, no biometric/gaze data
# ---------------------------------------------------------------------------

class TestAC3AuditTrailOnEnd:
    """AC3: When the session closes, VDT receives a payload with duration, VIN, stationId,
    callMode, expertUserId, biometricDataIncluded=False, gazeDataIncluded=False."""

    def _make_active_call(self, call_mode: str = "video") -> dict:
        return {
            "id": _CALL_ID,
            "vin": _VIN,
            "station_id": _STATION,
            "call_mode": call_mode,
            "status": "active",
        }

    @pytest.mark.asyncio
    async def test_ac3_vdt_audit_payload_contains_vin_station_callmode_expert_duration(self):
        """AC3: VDT POST body contains VIN, stationId, callMode, expertUserId, duration."""
        conn = FakeConnection(existing_call=self._make_active_call("video"))
        mock_vdt, captured = _mock_vdt_capturing(_AUDIT_ENTRY_ID)
        app = _build_app(conn)

        with (
            patch("app.expert_call_service.httpx.AsyncClient", return_value=mock_vdt),
            patch("app.routers.expert_calls.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/ar/expert-calls/{_CALL_ID}/end",
                    json={"expertUserId": _EXPERT_USER, "durationSeconds": 120},
                    headers={"Authorization": _BEARER},
                )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert len(captured) == 1, "Expected exactly one VDT POST"
        payload = captured[0]

        # AC3: required audit fields
        assert payload["vin"] == _VIN, f"VIN mismatch in VDT payload: {payload.get('vin')!r}"
        assert payload["stationId"] == _STATION, f"stationId mismatch: {payload.get('stationId')!r}"
        assert payload["callMode"] == "video", f"callMode mismatch: {payload.get('callMode')!r}"
        assert payload["expertUserId"] == _EXPERT_USER, (
            f"expertUserId mismatch: {payload.get('expertUserId')!r}"
        )
        assert payload["callDurationSeconds"] == 120, (
            f"callDurationSeconds mismatch: {payload.get('callDurationSeconds')!r}"
        )
        assert "callId" in payload

    @pytest.mark.asyncio
    async def test_ac3_vdt_audit_payload_has_no_biometric_or_gaze_data(self):
        """AC3: VDT payload must have biometricDataIncluded=False and gazeDataIncluded=False."""
        conn = FakeConnection(existing_call=self._make_active_call("video"))
        mock_vdt, captured = _mock_vdt_capturing(_AUDIT_ENTRY_ID)
        app = _build_app(conn)

        with (
            patch("app.expert_call_service.httpx.AsyncClient", return_value=mock_vdt),
            patch("app.routers.expert_calls.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    f"/ar/expert-calls/{_CALL_ID}/end",
                    json={"expertUserId": _EXPERT_USER, "durationSeconds": 60},
                    headers={"Authorization": _BEARER},
                )

        assert len(captured) == 1
        payload = captured[0]
        # AC3: §87 BetrVG compliance — no biometric or gaze data ever transmitted
        assert payload.get("biometricDataIncluded") is False, (
            f"biometricDataIncluded must be False, got {payload.get('biometricDataIncluded')!r}"
        )
        assert payload.get("gazeDataIncluded") is False, (
            f"gazeDataIncluded must be False, got {payload.get('gazeDataIncluded')!r}"
        )

    @pytest.mark.asyncio
    async def test_ac3_voice_only_call_end_also_logs_to_vdt_with_correct_callmode(self):
        """AC3: voice-only calls also produce a VDT audit entry with callMode=voice."""
        conn = FakeConnection(existing_call=self._make_active_call("voice"))
        mock_vdt, captured = _mock_vdt_capturing(_AUDIT_ENTRY_ID)
        app = _build_app(conn)

        with (
            patch("app.expert_call_service.httpx.AsyncClient", return_value=mock_vdt),
            patch("app.routers.expert_calls.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/ar/expert-calls/{_CALL_ID}/end",
                    json={"expertUserId": _EXPERT_USER, "durationSeconds": 45},
                    headers={"Authorization": _BEARER},
                )

        assert resp.status_code == 200
        assert len(captured) == 1
        payload = captured[0]
        assert payload["callMode"] == "voice"
        assert payload.get("biometricDataIncluded") is False
        assert payload.get("gazeDataIncluded") is False

    @pytest.mark.asyncio
    async def test_ac3_end_response_contains_audit_entry_id(self):
        """AC3: the end-call response returns auditEntryId so the frontend can display confirmation."""
        conn = FakeConnection(existing_call=self._make_active_call("video"))
        mock_vdt, _ = _mock_vdt_capturing(_AUDIT_ENTRY_ID)
        app = _build_app(conn)

        with (
            patch("app.expert_call_service.httpx.AsyncClient", return_value=mock_vdt),
            patch("app.routers.expert_calls.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/ar/expert-calls/{_CALL_ID}/end",
                    json={"expertUserId": _EXPERT_USER, "durationSeconds": 90},
                    headers={"Authorization": _BEARER},
                )

        assert resp.status_code == 200
        body = resp.json()
        # auditEntryId must match what VDT returned — frontend displays this as confirmation
        assert body["auditEntryId"] == _AUDIT_ENTRY_ID, (
            f"Expected auditEntryId={_AUDIT_ENTRY_ID!r}, got {body.get('auditEntryId')!r}"
        )
        assert body["callId"] == _CALL_ID
        assert "endedAt" in body

    @pytest.mark.asyncio
    async def test_ac3_audit_entry_id_written_to_db_row(self):
        """AC3: audit_entry_id is persisted in the expert_call row (not only echoed in response)."""
        conn = FakeConnection(existing_call=self._make_active_call("video"))
        mock_vdt, _ = _mock_vdt_capturing(_AUDIT_ENTRY_ID)
        app = _build_app(conn)

        with (
            patch("app.expert_call_service.httpx.AsyncClient", return_value=mock_vdt),
            patch("app.routers.expert_calls.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    f"/ar/expert-calls/{_CALL_ID}/end",
                    json={"expertUserId": _EXPERT_USER, "durationSeconds": 30},
                    headers={"Authorization": _BEARER},
                )

        # The execute that writes audit_entry_id must have been issued
        audit_writes = [
            e for e in conn._executes if "audit_entry_id" in e["query"].lower()
        ]
        assert len(audit_writes) >= 1, (
            "Expected at least one UPDATE to set audit_entry_id on the expert_call row"
        )

    @pytest.mark.asyncio
    async def test_ac3_vdt_payload_event_type_is_expert_call_end(self):
        """AC3: VDT event type is EXPERT_CALL_END — ensures correct routing in VDT."""
        conn = FakeConnection(existing_call=self._make_active_call("video"))
        mock_vdt, captured = _mock_vdt_capturing(_AUDIT_ENTRY_ID)
        app = _build_app(conn)

        with (
            patch("app.expert_call_service.httpx.AsyncClient", return_value=mock_vdt),
            patch("app.routers.expert_calls.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    f"/ar/expert-calls/{_CALL_ID}/end",
                    json={"expertUserId": _EXPERT_USER, "durationSeconds": 15},
                    headers={"Authorization": _BEARER},
                )

        assert len(captured) == 1
        assert captured[0].get("eventType") == "EXPERT_CALL_END", (
            f"Expected eventType='EXPERT_CALL_END', got {captured[0].get('eventType')!r}"
        )

    @pytest.mark.asyncio
    async def test_ac3_full_flow_video_approved_then_end(self):
        """AC3 + AC1 seam: initiate (video approved) → end → VDT receives correct callMode=video."""
        initiate_conn = FakeConnection(video_approved=True)
        app = _build_app(initiate_conn)

        # Step 1: initiate call
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r1 = await client.post(
                    "/ar/expert-calls",
                    json={"vin": _VIN, "stationId": _STATION, "sessionId": _SESSION_ID},
                    headers={"Authorization": _BEARER},
                )
        assert r1.status_code == 201
        call_id = r1.json()["callId"]
        assert r1.json()["callMode"] == "video"

        # Step 2: end call — build a new connection referencing the call we just created
        end_conn = FakeConnection(
            existing_call={
                "id": call_id,
                "vin": _VIN,
                "station_id": _STATION,
                "call_mode": "video",
                "status": "active",
            }
        )
        end_app = _build_app(end_conn)
        mock_vdt, captured = _mock_vdt_capturing(_AUDIT_ENTRY_ID)

        with (
            patch("app.expert_call_service.httpx.AsyncClient", return_value=mock_vdt),
            patch("app.routers.expert_calls.publish", new=AsyncMock()),
        ):
            transport2 = ASGITransport(app=end_app)
            async with AsyncClient(transport=transport2, base_url="http://test") as client:
                r2 = await client.post(
                    f"/ar/expert-calls/{call_id}/end",
                    json={"expertUserId": _EXPERT_USER, "durationSeconds": 77},
                    headers={"Authorization": _BEARER},
                )

        assert r2.status_code == 200
        assert len(captured) == 1
        payload = captured[0]
        assert payload["vin"] == _VIN
        assert payload["stationId"] == _STATION
        assert payload["callMode"] == "video"
        assert payload["expertUserId"] == _EXPERT_USER
        assert payload["callDurationSeconds"] == 77
        assert payload["biometricDataIncluded"] is False
        assert payload["gazeDataIncluded"] is False

    @pytest.mark.asyncio
    async def test_ac3_full_flow_voice_only_then_end(self):
        """AC3 + AC2 seam: initiate (voice-only) → end → VDT receives callMode=voice."""
        initiate_conn = FakeConnection(video_approved=False)
        app = _build_app(initiate_conn)

        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r1 = await client.post(
                    "/ar/expert-calls",
                    json={"vin": _VIN, "stationId": _STATION, "sessionId": _SESSION_ID},
                    headers={"Authorization": _BEARER},
                )
        assert r1.status_code == 201
        call_id = r1.json()["callId"]
        assert r1.json()["callMode"] == "voice"

        end_conn = FakeConnection(
            existing_call={
                "id": call_id,
                "vin": _VIN,
                "station_id": _STATION,
                "call_mode": "voice",
                "status": "active",
            }
        )
        end_app = _build_app(end_conn)
        mock_vdt, captured = _mock_vdt_capturing(_AUDIT_ENTRY_ID)

        with (
            patch("app.expert_call_service.httpx.AsyncClient", return_value=mock_vdt),
            patch("app.routers.expert_calls.publish", new=AsyncMock()),
        ):
            transport2 = ASGITransport(app=end_app)
            async with AsyncClient(transport=transport2, base_url="http://test") as client:
                r2 = await client.post(
                    f"/ar/expert-calls/{call_id}/end",
                    json={"expertUserId": _EXPERT_USER, "durationSeconds": 33},
                    headers={"Authorization": _BEARER},
                )

        assert r2.status_code == 200
        assert len(captured) == 1
        payload = captured[0]
        assert payload["callMode"] == "voice"
        assert payload["biometricDataIncluded"] is False
        assert payload["gazeDataIncluded"] is False
