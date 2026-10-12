"""Unit and integration-style tests for GET /ar/instructions/{vin}.

Uses in-process fakes for PostgreSQL (asyncpg), VDT HTTP client, and Kafka
so the test suite runs without external services. Each test class instantiates
its own fake, verifying instance isolation (mutable state in instance fields).

Covered scenarios per each distinct HTTP status the endpoint can return:
  200 cache-hit path  — returns cached payload, inserts ar_session
  200 cache-miss path — calls VDT, stores in cache, inserts ar_session
  200 rare variant    — isRareVariant=True and variantDeltaNote propagated
  400 invalid VIN     — 17-char pattern rejected
  401 no token        — MISSING_TOKEN error code
  403 wrong role      — FORBIDDEN error code
  504 VDT timeout     — failure_log inserted, audit fired, fallback returned
  idempotent session  — repeated calls both succeed (ON CONFLICT DO NOTHING)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Test constants — no absolute timestamps; use far-future or offset anchors
# ---------------------------------------------------------------------------

_VIN = "WBA3A5C55FK123456"
_STATION = "MARRIAGE-01"
_DEVICE = "HoloLens2"

# JWT: sub=user-123, roles=[ifos-associate]  (header.payload.sig — not verified in tests)
_BEARER_ASSOCIATE = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbImlmb3MtYXNzb2NpYXRlIl19"
    ".sig"
)
# JWT: sub=sl-456, roles=[ifos-shiftlead]
_BEARER_SHIFTLEAD = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJzbC00NTYiLCJyb2xlcyI6WyJpZm9zLXNoaWZ0bGVhZCJdfQ"
    ".sig"
)
# JWT: sub=bad, roles=[planner] — insufficient role
_BEARER_WRONG_ROLE = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJiYWQiLCJyb2xlcyI6WyJwbGFubmVyIl19"
    ".sig"
)

_CACHE_PAYLOAD: dict[str, Any] = {
    "vin": _VIN,
    "stationId": _STATION,
    "optionCodes": ["ZM7", "P0B"],
    "variantCode": "G26",
    "isRareVariant": False,
    "steps": [
        {
            "stepNumber": 1,
            "description": "Torque rear subframe",
            "torqueSpecNm": 120.0,
            "torqueToleranceNm": 5.0,
            "fastenerPointId": "FP-001",
            "isVariantDelta": False,
        }
    ],
    "instructionVersion": "v1.2.3",
    "fetchedAt": "2099-01-01T06:00:00+00:00",  # far future — never expires in tests
    "fallbackPdfQrUrl": f"https://docs.internal.ifos.example/instructions/{_VIN}.pdf",
}

_VDT_THREAD_RESPONSE: dict[str, Any] = {
    "optionCodes": ["ZM7", "P0B"],
    "variantCode": "G26",
    "isRareVariant": False,
    "instructionVersion": "v1.2.3",
    "steps": [
        {
            "stepNumber": 1,
            "description": "Torque rear subframe",
            "torqueSpecNm": 120.0,
            "torqueToleranceNm": 5.0,
            "fastenerPointId": "FP-001",
            "isVariantDelta": False,
        }
    ],
}

_VDT_RARE_VARIANT_RESPONSE: dict[str, Any] = {
    "optionCodes": ["ZM7", "P0B", "M_PERF"],
    "variantCode": "G26_M",
    "isRareVariant": True,
    "instructionVersion": "v1.2.4",
    "steps": [
        {
            "stepNumber": 1,
            "description": "Torque rear subframe (M-sport)",
            "torqueSpecNm": 145.0,
            "torqueToleranceNm": 3.0,
            "fastenerPointId": "FP-001M",
            "isVariantDelta": True,
            "variantDeltaNote": "Higher torque spec for M Performance rear subframe",
        }
    ],
}


# ---------------------------------------------------------------------------
# Fake DB infrastructure — mutable state in instance fields, never module-level
# ---------------------------------------------------------------------------

class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


class FakeConnection:
    """Fake asyncpg connection — each instantiation starts with clean state."""

    def __init__(self, cache_row: dict | None = None) -> None:
        self._cache_row = cache_row
        self._inserted_cache: list[dict] = []
        self._inserted_failure_log: list[dict] = []
        self._inserted_sessions: list[dict] = []

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        if "ar_instruction_cache" in query.lower() and self._cache_row is not None:
            return _FakeRecord(
                {
                    "payload_jsonb": self._cache_row,
                    "fallback_pdf_qr_url": self._cache_row.get("fallbackPdfQrUrl", ""),
                    "fetched_at": datetime.now(timezone.utc),
                }
            )
        return None

    async def execute(self, query: str, *args: Any) -> None:
        q = query.lower()
        if "ar_instruction_cache" in q:
            self._inserted_cache.append({"args": list(args)})
        elif "ar_instruction_failure_log" in q:
            self._inserted_failure_log.append({"args": list(args)})
        elif "ar_session" in q:
            self._inserted_sessions.append({"args": list(args)})


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
# App builder — wires dependency overrides for pool, audit writer, and Kafka
# ---------------------------------------------------------------------------

def _build_app(
    conn: FakeConnection,
) -> tuple[Any, FakePool]:
    from app.audit import AuditStore
    from app.main import create_app
    from app.routers.ar_instructions import get_audit_writer, get_pool

    app = create_app()
    fake_pool = FakePool(conn)
    fake_writer = AuditStore()

    app.dependency_overrides[get_pool] = lambda: fake_pool
    app.dependency_overrides[get_audit_writer] = lambda: fake_writer

    return app, fake_pool


# ---------------------------------------------------------------------------
# Helper: build a mock VDT HTTP response
# ---------------------------------------------------------------------------

def _mock_vdt(response_data: dict) -> Any:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = response_data
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _mock_vdt_timeout() -> Any:
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("upstream timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ---------------------------------------------------------------------------
# RBAC tests
# ---------------------------------------------------------------------------

class TestRbac:
    @pytest.mark.asyncio
    async def test_missing_bearer_returns_401_with_MISSING_TOKEN(self):
        conn = FakeConnection()
        app, _ = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                )
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "MISSING_TOKEN"
        assert "correlation_id" in body

    @pytest.mark.asyncio
    async def test_wrong_role_returns_403_with_FORBIDDEN(self):
        conn = FakeConnection()
        app, _ = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER_WRONG_ROLE},
                )
        assert resp.status_code == 403
        body = resp.json()
        assert body["code"] == "FORBIDDEN"
        assert "correlation_id" in body


# ---------------------------------------------------------------------------
# VIN validation tests
# ---------------------------------------------------------------------------

class TestVinValidation:
    @pytest.mark.asyncio
    async def test_vin_shorter_than_17_chars_returns_400(self):
        conn = FakeConnection()
        app, _ = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/ar/instructions/TOOSHORT",
                params={"stationId": _STATION, "deviceType": _DEVICE},
                headers={"Authorization": _BEARER_ASSOCIATE},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "VIN_FORMAT_INVALID"

    @pytest.mark.asyncio
    async def test_vin_with_excluded_chars_I_O_Q_returns_400(self):
        conn = FakeConnection()
        app, _ = _build_app(conn)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/ar/instructions/IIIIIIIIIIIIIIIII",  # 'I' excluded from VIN alphabet
                params={"stationId": _STATION, "deviceType": _DEVICE},
                headers={"Authorization": _BEARER_ASSOCIATE},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "VIN_FORMAT_INVALID"


# ---------------------------------------------------------------------------
# Cache-hit path — 200
# ---------------------------------------------------------------------------

class TestCacheHit:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_200_with_correct_vin_and_steps(self):
        conn = FakeConnection(cache_row=_CACHE_PAYLOAD)
        app, _ = _build_app(conn)
        with patch("app.routers.ar_instructions.publish", new=AsyncMock()):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["vin"] == _VIN
        assert body["stationId"] == _STATION
        assert body["optionCodes"] == ["ZM7", "P0B"]
        assert body["instructionVersion"] == "v1.2.3"
        assert len(body["steps"]) == 1
        assert body["steps"][0]["stepNumber"] == 1
        assert body["steps"][0]["torqueSpecNm"] == 120.0

    @pytest.mark.asyncio
    async def test_cache_hit_inserts_exactly_1_ar_session_row(self):
        conn = FakeConnection(cache_row=_CACHE_PAYLOAD)
        app, _ = _build_app(conn)
        with patch("app.routers.ar_instructions.publish", new=AsyncMock()):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert len(conn._inserted_sessions) == 1

    @pytest.mark.asyncio
    async def test_ifos_shiftlead_role_is_also_permitted(self):
        conn = FakeConnection(cache_row=_CACHE_PAYLOAD)
        app, _ = _build_app(conn)
        with patch("app.routers.ar_instructions.publish", new=AsyncMock()):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Cache-miss path — 200
# ---------------------------------------------------------------------------

class TestCacheMiss:
    @pytest.mark.asyncio
    async def test_cache_miss_calls_vdt_and_returns_200_with_correct_fields(self):
        conn = FakeConnection(cache_row=None)
        app, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt(_VDT_THREAD_RESPONSE)),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["vin"] == _VIN
        assert body["variantCode"] == "G26"
        assert body["isRareVariant"] is False
        assert len(body["steps"]) == 1
        assert body["steps"][0]["fastenerPointId"] == "FP-001"

    @pytest.mark.asyncio
    async def test_cache_miss_stores_1_row_in_ar_instruction_cache(self):
        conn = FakeConnection(cache_row=None)
        app, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt(_VDT_THREAD_RESPONSE)),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert len(conn._inserted_cache) == 1

    @pytest.mark.asyncio
    async def test_cache_miss_inserts_exactly_1_ar_session_row(self):
        conn = FakeConnection(cache_row=None)
        app, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt(_VDT_THREAD_RESPONSE)),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert len(conn._inserted_sessions) == 1

    @pytest.mark.asyncio
    async def test_rare_variant_isRareVariant_true_and_variantDeltaNote_propagated(self):
        conn = FakeConnection(cache_row=None)
        app, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt(_VDT_RARE_VARIANT_RESPONSE)),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["isRareVariant"] is True
        assert body["variantCode"] == "G26_M"
        step = body["steps"][0]
        assert step["isVariantDelta"] is True
        assert step["variantDeltaNote"] == "Higher torque spec for M Performance rear subframe"


# ---------------------------------------------------------------------------
# 504 timeout/fallback path
# ---------------------------------------------------------------------------

class TestTimeoutFallback:
    @pytest.mark.asyncio
    async def test_vdt_timeout_returns_504_with_fallback_fields(self):
        conn = FakeConnection(cache_row=None)
        app, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt_timeout()),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert resp.status_code == 504
        body = resp.json()
        assert body["vin"] == _VIN
        assert body["stationId"] == _STATION
        assert _VIN in body["fallbackPdfQrUrl"]
        assert "failureLoggedAt" in body
        assert "correlationId" in body
        # correlationId must be a valid UUID string
        uuid.UUID(body["correlationId"])

    @pytest.mark.asyncio
    async def test_vdt_timeout_inserts_exactly_1_failure_log_row(self):
        conn = FakeConnection(cache_row=None)
        app, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt_timeout()),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert len(conn._inserted_failure_log) == 1

    @pytest.mark.asyncio
    async def test_vdt_timeout_inserts_0_cache_rows_and_0_session_rows(self):
        conn = FakeConnection(cache_row=None)
        app, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt_timeout()),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert len(conn._inserted_cache) == 0
        assert len(conn._inserted_sessions) == 0


# ---------------------------------------------------------------------------
# Idempotent session insert — two calls both return 200 (ON CONFLICT DO NOTHING)
# ---------------------------------------------------------------------------

class TestIdempotentSession:
    @pytest.mark.asyncio
    async def test_two_requests_with_same_vin_both_return_200(self):
        conn = FakeConnection(cache_row=_CACHE_PAYLOAD)
        app, _ = _build_app(conn)
        with patch("app.routers.ar_instructions.publish", new=AsyncMock()):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r1 = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
                r2 = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER_ASSOCIATE},
                )
        assert r1.status_code == 200
        assert r2.status_code == 200


# ---------------------------------------------------------------------------
# FakeConnection instance-isolation test
# ---------------------------------------------------------------------------

class TestFakeConnectionInstanceIsolation:
    def test_two_instances_have_independent_inserted_sessions_lists(self):
        conn_a = FakeConnection()
        conn_b = FakeConnection()
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            conn_a.execute("INSERT INTO ar_session VALUES ($1)", "x")
        )
        assert len(conn_a._inserted_sessions) == 1
        assert len(conn_b._inserted_sessions) == 0

    def test_new_instance_inserted_sessions_starts_at_0(self):
        conn = FakeConnection()
        assert len(conn._inserted_sessions) == 0
        assert len(conn._inserted_cache) == 0
        assert len(conn._inserted_failure_log) == 0
