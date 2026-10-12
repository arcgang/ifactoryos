"""
Acceptance tests — US-010: AR Workbench RFID-triggered overlay.

Verifies the story's acceptance criteria at the seam between the two merged tasks:
  line-command-api (backend): GET /ar/instructions/{vin}
  line-command (frontend):    ARWorkbenchPage / ARWorkInstructions

AC1: Overlay returns correct option codes, torque specs, and step sequence.
AC2: Rare variant response carries isRareVariant=True with per-step delta indicators.
AC3: VDT timeout → 504 body with VIN + QR URL; AR_OVERLAY_LOAD_FAILURE audit event fired.

Preconditions are asserted first in each class — a missing route returns 404, not 401.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Test constants — no absolute timestamps; far-future anchors used in fixtures
# ---------------------------------------------------------------------------

_VIN = "WBA3A5C55FK123456"
_STATION = "MARRIAGE-01"
_DEVICE = "HoloLens2"

# JWT: sub=user-123, roles=[ifos-associate]
_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbImlmb3MtYXNzb2NpYXRlIl19"
    ".sig"
)

# VDT response for a standard i4 — two steps with distinct torque values
_VDT_STANDARD: dict[str, Any] = {
    "optionCodes": ["SA258", "SA386", "SA4AC"],
    "variantCode": "G26",
    "isRareVariant": False,
    "instructionVersion": "v4.2.1",
    "steps": [
        {
            "stepNumber": 1,
            "description": "Torque rear subframe bolts",
            "torqueSpecNm": 120.0,
            "torqueToleranceNm": 5.0,
            "fastenerPointId": "FP-REAR-SUB",
            "isVariantDelta": False,
        },
        {
            "stepNumber": 2,
            "description": "Torque front crossmember bolts",
            "torqueSpecNm": 80.0,
            "torqueToleranceNm": 3.0,
            "fastenerPointId": "FP-FRONT-CROSS",
            "isVariantDelta": False,
        },
    ],
}

# VDT response for a rare M Performance variant — step 1 differs from standard
_VDT_RARE_VARIANT: dict[str, Any] = {
    "optionCodes": ["SA258", "SA3A5", "SA4T9"],
    "variantCode": "G26_M",
    "isRareVariant": True,
    "instructionVersion": "v4.2.4",
    "steps": [
        {
            "stepNumber": 1,
            "description": "Torque rear subframe bolts (M Performance spec)",
            "torqueSpecNm": 145.0,
            "torqueToleranceNm": 3.0,
            "fastenerPointId": "FP-REAR-SUB-M",
            "isVariantDelta": True,
            "variantDeltaNote": "Higher torque for M Performance reinforced rear subframe",
        },
        {
            "stepNumber": 2,
            "description": "Torque front crossmember bolts",
            "torqueSpecNm": 80.0,
            "torqueToleranceNm": 3.0,
            "fastenerPointId": "FP-FRONT-CROSS",
            "isVariantDelta": False,
        },
    ],
}

# ---------------------------------------------------------------------------
# In-process fakes — mutable state in instance fields only (no module-level vars)
# ---------------------------------------------------------------------------


class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


class FakeConnection:
    """Instance-isolated asyncpg fake — each instantiation starts clean."""

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
    """Instance-isolated asyncpg pool."""

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


def _build_app(conn: FakeConnection) -> tuple[Any, FakePool, Any]:
    from app.audit import AuditStore
    from app.main import create_app
    from app.routers.ar_instructions import get_audit_writer, get_pool

    app = create_app()
    fake_pool = FakePool(conn)
    fake_writer = AuditStore()
    app.dependency_overrides[get_pool] = lambda: fake_pool
    app.dependency_overrides[get_audit_writer] = lambda: fake_writer
    return app, fake_pool, fake_writer


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
# Preconditions — route must be registered before any behavioural tests run
# ---------------------------------------------------------------------------


class TestPreconditions:
    """Assert GET /ar/instructions/{vin} route is registered."""

    @pytest.mark.asyncio
    async def test_ar_instructions_route_registered_returns_401_not_404(self) -> None:
        """Route must exist: unauthenticated request returns 401, not 404.

        A 404 here means the router is not mounted and every downstream test
        would be vacuously reporting on a missing route.
        """
        conn = FakeConnection()
        app, _, _ = _build_app(conn)
        with patch("app.kafka_producer._producer", new=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                )
        assert resp.status_code == 401, (
            f"Expected 401 (route exists, auth required); got {resp.status_code}. "
            "The GET /ar/instructions/{vin} route must be registered in the app."
        )


# ---------------------------------------------------------------------------
# AC1: Overlay returns correct option codes, torque specs, and step sequence
# ---------------------------------------------------------------------------


class TestAC1_OverlayDataShape:
    """AC1: VIN-specific overlay must carry option codes, torque specs, and step sequence."""

    @pytest.mark.asyncio
    async def test_ac1_response_option_codes_match_vdt_payload(self) -> None:
        conn = FakeConnection(cache_row=None)
        app, _, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt(_VDT_STANDARD)),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["optionCodes"] == ["SA258", "SA386", "SA4AC"]

    @pytest.mark.asyncio
    async def test_ac1_every_step_carries_torqueSpecNm_and_torqueToleranceNm(self) -> None:
        conn = FakeConnection(cache_row=None)
        app, _, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt(_VDT_STANDARD)),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER},
                )
        steps = resp.json()["steps"]
        assert len(steps) == 2, f"Expected 2 steps; got {len(steps)}"
        for s in steps:
            assert isinstance(s["torqueSpecNm"], (int, float)), (
                f"step {s['stepNumber']}: torqueSpecNm must be numeric"
            )
            assert isinstance(s["torqueToleranceNm"], (int, float)), (
                f"step {s['stepNumber']}: torqueToleranceNm must be numeric"
            )
        # Spot-check exact values from fixture
        assert steps[0]["torqueSpecNm"] == 120.0
        assert steps[0]["torqueToleranceNm"] == 5.0
        assert steps[1]["torqueSpecNm"] == 80.0
        assert steps[1]["torqueToleranceNm"] == 3.0

    @pytest.mark.asyncio
    async def test_ac1_step_sequence_preserves_order_and_fastener_ids(self) -> None:
        conn = FakeConnection(cache_row=None)
        app, _, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt(_VDT_STANDARD)),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER},
                )
        steps = resp.json()["steps"]
        assert steps[0]["stepNumber"] == 1
        assert steps[0]["fastenerPointId"] == "FP-REAR-SUB"
        assert steps[0]["description"] == "Torque rear subframe bolts"
        assert steps[1]["stepNumber"] == 2
        assert steps[1]["fastenerPointId"] == "FP-FRONT-CROSS"

    @pytest.mark.asyncio
    async def test_ac1_response_carries_vin_and_station_id(self) -> None:
        conn = FakeConnection(cache_row=None)
        app, _, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt(_VDT_STANDARD)),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER},
                )
        body = resp.json()
        assert body["vin"] == _VIN
        assert body["stationId"] == _STATION


# ---------------------------------------------------------------------------
# AC2: Rare variant response carries delta indicators
# ---------------------------------------------------------------------------


class TestAC2_RareVariantDeltaIndicators:
    """AC2: rare variant steps must carry isRareVariant=True and per-step delta fields."""

    @pytest.mark.asyncio
    async def test_ac2_rare_variant_response_has_isRareVariant_true_and_variant_code(
        self,
    ) -> None:
        conn = FakeConnection(cache_row=None)
        app, _, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt(_VDT_RARE_VARIANT)),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["isRareVariant"] is True, "isRareVariant must be True for M Performance variant"
        assert body["variantCode"] == "G26_M"

    @pytest.mark.asyncio
    async def test_ac2_delta_step_has_isVariantDelta_true_and_variantDeltaNote(self) -> None:
        conn = FakeConnection(cache_row=None)
        app, _, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt(_VDT_RARE_VARIANT)),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER},
                )
        steps = resp.json()["steps"]
        delta_steps = [s for s in steps if s.get("isVariantDelta") is True]
        assert len(delta_steps) == 1, (
            f"Expected exactly 1 delta step; got {len(delta_steps)}. "
            "Each variant-specific step must carry isVariantDelta=True."
        )
        step = delta_steps[0]
        assert step["variantDeltaNote"] == "Higher torque for M Performance reinforced rear subframe"
        # Delta torque differs from standard (120 Nm) to signal the difference
        assert step["torqueSpecNm"] == 145.0
        assert step["fastenerPointId"] == "FP-REAR-SUB-M"

    @pytest.mark.asyncio
    async def test_ac2_non_delta_steps_have_isVariantDelta_false(self) -> None:
        conn = FakeConnection(cache_row=None)
        app, _, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt(_VDT_RARE_VARIANT)),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER},
                )
        steps = resp.json()["steps"]
        standard_steps = [s for s in steps if s.get("isVariantDelta") is False]
        assert len(standard_steps) == 1
        assert standard_steps[0]["fastenerPointId"] == "FP-FRONT-CROSS"

    @pytest.mark.asyncio
    async def test_ac2_rare_variant_option_codes_include_M_variant_codes(self) -> None:
        conn = FakeConnection(cache_row=None)
        app, _, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt(_VDT_RARE_VARIANT)),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER},
                )
        body = resp.json()
        # SA3A5 and SA4T9 are M Performance option codes absent from standard i4
        assert "SA3A5" in body["optionCodes"]
        assert "SA4T9" in body["optionCodes"]


# ---------------------------------------------------------------------------
# AC3: VDT timeout → 504 fallback body + failure log + AUDIT TRAIL
# ---------------------------------------------------------------------------


class TestAC3_FailureAuditAndFallback:
    """AC3: VDT timeout must produce a 504 with VIN+QR, log the failure, and fire an audit event."""

    @pytest.mark.asyncio
    async def test_ac3_vdt_timeout_returns_504_with_vin_and_fallback_qr_url_containing_vin(
        self,
    ) -> None:
        conn = FakeConnection(cache_row=None)
        app, _, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt_timeout()),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER},
                )
        assert resp.status_code == 504
        body = resp.json()
        assert body["vin"] == _VIN
        assert body["stationId"] == _STATION
        assert _VIN in body["fallbackPdfQrUrl"], (
            f"fallbackPdfQrUrl must embed the VIN so the associate can scan for "
            f"this vehicle's PDF; got {body['fallbackPdfQrUrl']!r}"
        )
        assert "failureLoggedAt" in body
        assert "correlationId" in body

    @pytest.mark.asyncio
    async def test_ac3_vdt_timeout_fires_AR_OVERLAY_LOAD_FAILURE_audit_event(self) -> None:
        """AC3 audit trail criterion: the failure must be logged with AR_OVERLAY_LOAD_FAILURE.

        fire_audit_write is patched here so the assertion is synchronous
        (no daemon-thread race).  The call arguments are the contract.
        """
        conn = FakeConnection(cache_row=None)
        app, _, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt_timeout()),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
            patch("app.routers.ar_instructions.fire_audit_write") as mock_fire_audit,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER},
                )

        assert mock_fire_audit.call_count == 1, (
            f"Expected 1 fire_audit_write call on VDT timeout; "
            f"got {mock_fire_audit.call_count}.  "
            "The failure must be written to the immutable audit log."
        )
        call_kwargs = mock_fire_audit.call_args.kwargs
        assert call_kwargs["action_type"] == "AR_OVERLAY_LOAD_FAILURE", (
            f"Audit action_type must be 'AR_OVERLAY_LOAD_FAILURE'; "
            f"got {call_kwargs.get('action_type')!r}"
        )
        assert call_kwargs["entity_id"] == _VIN, (
            f"Audit entity_id must be the VIN; got {call_kwargs.get('entity_id')!r}"
        )

    @pytest.mark.asyncio
    async def test_ac3_vdt_timeout_inserts_failure_log_row_with_upstream_timeout_reason(
        self,
    ) -> None:
        conn = FakeConnection(cache_row=None)
        app, _, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt_timeout()),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
            patch("app.routers.ar_instructions.fire_audit_write"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER},
                )
        assert len(conn._inserted_failure_log) == 1, (
            f"Expected 1 ar_instruction_failure_log row; "
            f"got {len(conn._inserted_failure_log)}."
        )
        failure_args = conn._inserted_failure_log[0]["args"]
        assert "upstream_timeout" in failure_args, (
            f"failure_reason must be 'upstream_timeout'; args were {failure_args!r}"
        )

    @pytest.mark.asyncio
    async def test_ac3_standard_success_path_does_not_fire_audit_event(self) -> None:
        """Sanity check: no audit event is fired for a successful overlay load."""
        conn = FakeConnection(cache_row=None)
        app, _, _ = _build_app(conn)
        with (
            patch("app.ar_service.httpx.AsyncClient", return_value=_mock_vdt(_VDT_STANDARD)),
            patch("app.routers.ar_instructions.publish", new=AsyncMock()),
            patch("app.routers.ar_instructions.fire_audit_write") as mock_fire_audit,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get(
                    f"/ar/instructions/{_VIN}",
                    params={"stationId": _STATION, "deviceType": _DEVICE},
                    headers={"Authorization": _BEARER},
                )
        assert mock_fire_audit.call_count == 0, (
            "fire_audit_write must not be called on a successful overlay load"
        )
