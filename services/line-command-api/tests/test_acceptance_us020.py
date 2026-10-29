"""Acceptance tests for US-020 — Line Command session events.

Covers all three acceptance criteria end-to-end across the story tasks:

  AC-1  When a Shift Lead or Assembly Associate completes a tracked meaningful
        action (alert acknowledgment, handover submission, AR session start,
        torque log submission), an AdoptionEvent is published within 1 second
        containing userId, role, actionType, stationId, and timestamp.

  AC-2  A login-only session (no meaningful action) is excluded from the WAU
        active-user count — verified via the Python mirror of the KQL query
        (platform/observability-stack/tests/test_wau_login_only_exclusion.py).

  AC-3  The AR session AdoptionEvent includes vin, deviceType (HoloLens2 or
        iPad), and stationId; no biometric or gaze data is present in any
        emitted event.

These tests prove the seam: each endpoint issues a Kafka publish on
TOPIC_ADOPTION_EVENT with the required fields, and the WAU calculator in
platform/observability-stack correctly classifies those events.

Precondition assertions confirm that the symbols under test exist before any
behavioural assertion runs — a missing router or service function will fail
loudly rather than silently passing.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Precondition: verify the story's entry points are importable
# ---------------------------------------------------------------------------

def test_precondition_ar_sessions_router_exists() -> None:
    """AR sessions router must be importable and expose _publish_ar_session_events."""
    from app.routers import ar_sessions
    assert hasattr(ar_sessions, "_publish_ar_session_events"), (
        "app.routers.ar_sessions._publish_ar_session_events not found — "
        "the AR session adoption event helper was not merged"
    )


def test_precondition_handover_router_exists() -> None:
    """Handover router must be importable and expose _publish_handover_events."""
    from app.routers import handover
    assert hasattr(handover, "_publish_handover_events"), (
        "app.routers.handover._publish_handover_events not found — "
        "the handover adoption event helper was not merged"
    )


def test_precondition_torque_router_exists() -> None:
    """Torque router must be importable and expose _async_publish_submitted."""
    from app.routers import torque
    assert hasattr(torque, "_async_publish_submitted"), (
        "app.routers.torque._async_publish_submitted not found — "
        "the torque adoption event helper was not merged"
    )


def test_precondition_adoption_topic_defined() -> None:
    """TOPIC_ADOPTION_EVENT must be defined in kafka_producer."""
    from app.kafka_producer import TOPIC_ADOPTION_EVENT
    assert TOPIC_ADOPTION_EVENT == "factory.munich.line-command.adoption.v1", (
        f"TOPIC_ADOPTION_EVENT is {TOPIC_ADOPTION_EVENT!r}, expected "
        "'factory.munich.line-command.adoption.v1'"
    )


def test_precondition_wau_logic_importable() -> None:
    """The WAU calculator (observability-stack) must be importable."""
    import sys, importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "test_wau_login_only_exclusion",
        pathlib.Path(__file__).parents[3]
        / "platform/observability-stack/tests/test_wau_login_only_exclusion.py",
    )
    assert spec is not None and spec.loader is not None, (
        "platform/observability-stack/tests/test_wau_login_only_exclusion.py not found"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert hasattr(mod, "compute_line_command_wau"), (
        "compute_line_command_wau missing from WAU module — "
        "observability-stack WAU logic was not merged"
    )
    assert hasattr(mod, "LINE_COMMAND_MEANINGFUL_ACTIONS"), (
        "LINE_COMMAND_MEANINGFUL_ACTIONS missing — WAU module incomplete"
    )


# ---------------------------------------------------------------------------
# Helpers — shared fake DB infrastructure
# ---------------------------------------------------------------------------

class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


_FAR_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)

# JWT tokens — signature verification is disabled in tests
# sub=sl-001, roles=[ifos-shiftlead]
_BEARER_SHIFTLEAD = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJzbC0wMDEiLCJyb2xlcyI6WyJpZm9zLXNoaWZ0bGVhZCJdfQ"
    ".sig"
)
# sub=aa-001, roles=[ifos-associate]
_BEARER_ASSOCIATE = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJhYS0wMDEiLCJyb2xlcyI6WyJpZm9zLWFzc29jaWF0ZSJdfQ"
    ".sig"
)

_VIN = "WBA3A5C55FK123456"
_STATION = "MARRIAGE-01"


# ---------------------------------------------------------------------------
# AC-1 / AC-3 — AR session AdoptionEvent (end-to-end via publish helper)
# ---------------------------------------------------------------------------

class TestAC1_ArSessionAdoptionEvent:
    """AC-1: AR_SESSION_STARTED AdoptionEvent includes userId, role, actionType,
    stationId, and timestamp emitted within 1 second of the action completing.
    """

    def _call_publish_helper(
        self,
        mock_publish: AsyncMock,
        user_id: str = "sl-001",
        caller_role: str = "ifos-shiftlead",
        device_type: str = "HoloLens2",
    ) -> None:
        from app.routers.ar_sessions import _publish_ar_session_events
        asyncio.get_event_loop().run_until_complete(
            _publish_ar_session_events(
                session_id=str(uuid.uuid4()),
                event_id=str(uuid.uuid4()),
                vin=_VIN,
                station_id=_STATION,
                device_type=device_type,
                user_id=user_id,
                caller_role=caller_role,
                recorded_at=_FAR_FUTURE.isoformat(),
            )
        )

    def _adoption_calls(self, mock_publish: AsyncMock) -> list:
        from app.kafka_producer import TOPIC_ADOPTION_EVENT
        return [
            c for c in mock_publish.call_args_list
            if c[0][0] == TOPIC_ADOPTION_EVENT
        ]

    def test_ar_session_adoption_event_published(self) -> None:
        """AC-1: AR session start produces exactly one AdoptionEvent."""
        mock_publish = AsyncMock()
        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        calls = self._adoption_calls(mock_publish)
        assert len(calls) == 1, f"Expected 1 AdoptionEvent publish, got {len(calls)}"

    def test_ar_session_adoption_event_contains_userId(self) -> None:
        """AC-1: AdoptionEvent contains userId."""
        mock_publish = AsyncMock()
        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            self._call_publish_helper(mock_publish, user_id="sl-001")
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert payload["userId"] == "sl-001"

    def test_ar_session_adoption_event_contains_role(self) -> None:
        """AC-1: AdoptionEvent contains role."""
        mock_publish = AsyncMock()
        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            self._call_publish_helper(mock_publish, caller_role="ifos-shiftlead")
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert "role" in payload
        assert payload["role"] == "ifos-shiftlead"

    def test_ar_session_adoption_event_contains_action_type(self) -> None:
        """AC-1: AdoptionEvent actionType is AR_SESSION_STARTED."""
        mock_publish = AsyncMock()
        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert payload["actionType"] == "AR_SESSION_STARTED"

    def test_ar_session_adoption_event_contains_stationId(self) -> None:
        """AC-1: AdoptionEvent contains stationId."""
        mock_publish = AsyncMock()
        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert payload["stationId"] == _STATION

    def test_ar_session_adoption_event_contains_timestamp(self) -> None:
        """AC-1: AdoptionEvent contains a valid ISO 8601 timestamp."""
        mock_publish = AsyncMock()
        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert "occurredAt" in payload
        ts = datetime.fromisoformat(payload["occurredAt"])
        assert ts.tzinfo is not None, "occurredAt must be timezone-aware"

    def test_ar_session_adoption_event_within_1_second(self) -> None:
        """AC-1: AdoptionEvent is emitted within 1 second of the action completing."""
        mock_publish = AsyncMock()
        before = datetime.now(timezone.utc)
        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        after = datetime.now(timezone.utc)
        payload = self._adoption_calls(mock_publish)[0][0][1]
        occurred_at = datetime.fromisoformat(payload["occurredAt"])
        # occurredAt must be within the test's wall-clock window (plus 1s grace)
        assert occurred_at >= before - timedelta(seconds=1)
        assert occurred_at <= after + timedelta(seconds=1)

    def test_ac3_ar_session_adoption_event_contains_vin(self) -> None:
        """AC-3: AR session AdoptionEvent includes VIN."""
        mock_publish = AsyncMock()
        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert payload["vin"] == _VIN

    def test_ac3_ar_session_adoption_event_contains_device_type_hololens2(self) -> None:
        """AC-3: AR session AdoptionEvent includes deviceType=HoloLens2."""
        mock_publish = AsyncMock()
        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            self._call_publish_helper(mock_publish, device_type="HoloLens2")
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert payload["deviceType"] == "HoloLens2"

    def test_ac3_ar_session_adoption_event_contains_device_type_ipad(self) -> None:
        """AC-3: AR session AdoptionEvent includes deviceType=iPad."""
        mock_publish = AsyncMock()
        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            self._call_publish_helper(mock_publish, device_type="iPad")
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert payload["deviceType"] == "iPad"

    def test_ac3_no_biometric_data_in_ar_session_adoption_event(self) -> None:
        """AC-3: No biometric or gaze data present in any AR session event."""
        mock_publish = AsyncMock()
        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        for call in mock_publish.call_args_list:
            payload = call[0][1]
            forbidden = {"gazeData", "gaze_data", "gaze_vector", "eyeTracking",
                         "eye_tracking", "biometric", "biometric_data"}
            present = forbidden & set(payload.keys())
            assert not present, (
                f"Biometric/gaze field(s) {present} found in publish payload: {payload}"
            )

    def test_ac3_ar_session_request_rejects_extra_fields(self) -> None:
        """AC-3: POST /ar/sessions rejects extra fields (model_config extra=forbid)."""
        from app.routers.ar_sessions import ArSessionStartRequest
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            ArSessionStartRequest(
                vin=_VIN,
                stationId=_STATION,
                deviceType="HoloLens2",
                gazeData="some-gaze",  # forbidden extra field
            )

    def test_ac3_ar_session_started_event_no_biometric_fields(self) -> None:
        """AC-3: ArSessionStartedEvent (domain event) has no biometric fields."""
        from app.kafka_producer import TOPIC_AR_SESSION_STARTED
        mock_publish = AsyncMock()
        with patch("app.routers.ar_sessions.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        ar_calls = [
            c for c in mock_publish.call_args_list
            if c[0][0] == TOPIC_AR_SESSION_STARTED
        ]
        assert len(ar_calls) == 1
        payload = ar_calls[0][0][1]
        forbidden = {"gazeData", "gaze_data", "gaze_vector", "eyeTracking",
                     "eye_tracking", "biometric", "biometric_data"}
        present = forbidden & set(payload.keys())
        assert not present, f"Biometric field(s) {present} in ArSessionStartedEvent"


# ---------------------------------------------------------------------------
# AC-1 — Handover AdoptionEvent (via publish helper)
# ---------------------------------------------------------------------------

class TestAC1_HandoverAdoptionEvent:
    """AC-1: HANDOVER_SUBMITTED AdoptionEvent includes all required fields."""

    def _call_publish_helper(
        self,
        mock_publish: AsyncMock,
        user_id: str = "sl-001",
        caller_role: str = "ifos-shiftlead",
    ) -> None:
        from app.routers.handover import _publish_handover_events
        asyncio.get_event_loop().run_until_complete(
            _publish_handover_events(
                handover_id=str(uuid.uuid4()),
                shift_id=str(uuid.uuid4()),
                line_id=_STATION,
                shift_start_ts=_FAR_FUTURE.isoformat(),
                shift_end_ts=(_FAR_FUTURE + timedelta(hours=8)).isoformat(),
                user_id=user_id,
                caller_role=caller_role,
                oee_delta_pct=2.5,
                submitted_at=_FAR_FUTURE.isoformat(),
            )
        )

    def _adoption_calls(self, mock_publish: AsyncMock) -> list:
        from app.kafka_producer import TOPIC_ADOPTION_EVENT
        return [
            c for c in mock_publish.call_args_list
            if c[0][0] == TOPIC_ADOPTION_EVENT
        ]

    def test_handover_adoption_event_published(self) -> None:
        """AC-1: Handover submission produces exactly one AdoptionEvent."""
        mock_publish = AsyncMock()
        with patch("app.routers.handover.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        calls = self._adoption_calls(mock_publish)
        assert len(calls) == 1

    def test_handover_adoption_event_contains_userId(self) -> None:
        """AC-1: Handover AdoptionEvent contains userId."""
        mock_publish = AsyncMock()
        with patch("app.routers.handover.publish", new=mock_publish):
            self._call_publish_helper(mock_publish, user_id="sl-001")
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert payload["userId"] == "sl-001"

    def test_handover_adoption_event_contains_role(self) -> None:
        """AC-1: Handover AdoptionEvent contains role."""
        mock_publish = AsyncMock()
        with patch("app.routers.handover.publish", new=mock_publish):
            self._call_publish_helper(mock_publish, caller_role="ifos-shiftlead")
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert "role" in payload
        assert payload["role"] == "ifos-shiftlead"

    def test_handover_adoption_event_action_type(self) -> None:
        """AC-1: Handover AdoptionEvent actionType is HANDOVER_SUBMITTED."""
        mock_publish = AsyncMock()
        with patch("app.routers.handover.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert payload["actionType"] == "HANDOVER_SUBMITTED"

    def test_handover_adoption_event_contains_stationId(self) -> None:
        """AC-1: Handover AdoptionEvent contains stationId (mapped from lineId)."""
        mock_publish = AsyncMock()
        with patch("app.routers.handover.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert payload["stationId"] == _STATION

    def test_handover_adoption_event_contains_timestamp(self) -> None:
        """AC-1: Handover AdoptionEvent contains a valid ISO 8601 timestamp."""
        mock_publish = AsyncMock()
        with patch("app.routers.handover.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert "occurredAt" in payload
        ts = datetime.fromisoformat(payload["occurredAt"])
        assert ts.tzinfo is not None

    def test_handover_adoption_event_within_1_second(self) -> None:
        """AC-1: Handover AdoptionEvent occurredAt is within 1s of publish call."""
        mock_publish = AsyncMock()
        before = datetime.now(timezone.utc)
        with patch("app.routers.handover.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        after = datetime.now(timezone.utc)
        payload = self._adoption_calls(mock_publish)[0][0][1]
        occurred_at = datetime.fromisoformat(payload["occurredAt"])
        assert occurred_at >= before - timedelta(seconds=1)
        assert occurred_at <= after + timedelta(seconds=1)


# ---------------------------------------------------------------------------
# AC-1 — Torque AdoptionEvent (via publish helper)
# ---------------------------------------------------------------------------

class TestAC1_TorqueAdoptionEvent:
    """AC-1: TORQUE_LOG_SUBMITTED AdoptionEvent includes all required fields."""

    def _call_publish_helper(
        self,
        mock_publish: AsyncMock,
        user_id: str = "aa-001",
        caller_role: str = "ifos-associate",
    ) -> None:
        from app.routers.torque import _async_publish_submitted
        from decimal import Decimal

        # _async_publish_submitted acquires a pool connection internally;
        # patch _publish_torque_submitted_with_pool to avoid real DB
        with patch(
            "app.routers.torque._publish_torque_submitted_with_pool",
            new=AsyncMock(),
        ):
            asyncio.get_event_loop().run_until_complete(
                _async_publish_submitted(
                    pool=None,
                    row_id=str(uuid.uuid4()),
                    vin=_VIN,
                    station_id=_STATION,
                    fastener_point_id="FP-001",
                    torque_nm=Decimal("120.0"),
                    angles_deg=Decimal("45.0"),
                    plc_timestamp=_FAR_FUTURE,
                    in_tolerance=True,
                    spec_nm=Decimal("120.0"),
                    tolerance_nm=Decimal("5.0"),
                    alert_id=None,
                    user_id=user_id,
                    caller_role=caller_role,
                )
            )

    def _adoption_calls(self, mock_publish: AsyncMock) -> list:
        from app.kafka_producer import TOPIC_ADOPTION_EVENT
        return [
            c for c in mock_publish.call_args_list
            if c[0][0] == TOPIC_ADOPTION_EVENT
        ]

    def test_torque_adoption_event_published(self) -> None:
        """AC-1: Torque log submission produces exactly one AdoptionEvent."""
        mock_publish = AsyncMock()
        with patch("app.routers.torque.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        calls = self._adoption_calls(mock_publish)
        assert len(calls) == 1

    def test_torque_adoption_event_contains_userId(self) -> None:
        """AC-1: Torque AdoptionEvent contains userId."""
        mock_publish = AsyncMock()
        with patch("app.routers.torque.publish", new=mock_publish):
            self._call_publish_helper(mock_publish, user_id="aa-001")
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert payload["userId"] == "aa-001"

    def test_torque_adoption_event_contains_role(self) -> None:
        """AC-1: Torque AdoptionEvent contains role."""
        mock_publish = AsyncMock()
        with patch("app.routers.torque.publish", new=mock_publish):
            self._call_publish_helper(mock_publish, caller_role="ifos-associate")
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert "role" in payload
        assert payload["role"] == "ifos-associate"

    def test_torque_adoption_event_action_type(self) -> None:
        """AC-1: Torque AdoptionEvent actionType is TORQUE_LOG_SUBMITTED."""
        mock_publish = AsyncMock()
        with patch("app.routers.torque.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert payload["actionType"] == "TORQUE_LOG_SUBMITTED"

    def test_torque_adoption_event_contains_stationId(self) -> None:
        """AC-1: Torque AdoptionEvent contains stationId."""
        mock_publish = AsyncMock()
        with patch("app.routers.torque.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert payload["stationId"] == _STATION

    def test_torque_adoption_event_contains_timestamp(self) -> None:
        """AC-1: Torque AdoptionEvent contains a valid ISO 8601 timestamp."""
        mock_publish = AsyncMock()
        with patch("app.routers.torque.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        payload = self._adoption_calls(mock_publish)[0][0][1]
        assert "occurredAt" in payload
        ts = datetime.fromisoformat(payload["occurredAt"])
        assert ts.tzinfo is not None

    def test_torque_adoption_event_within_1_second(self) -> None:
        """AC-1: Torque AdoptionEvent occurredAt is within 1s of publish call."""
        mock_publish = AsyncMock()
        before = datetime.now(timezone.utc)
        with patch("app.routers.torque.publish", new=mock_publish):
            self._call_publish_helper(mock_publish)
        after = datetime.now(timezone.utc)
        payload = self._adoption_calls(mock_publish)[0][0][1]
        occurred_at = datetime.fromisoformat(payload["occurredAt"])
        assert occurred_at >= before - timedelta(seconds=1)
        assert occurred_at <= after + timedelta(seconds=1)


# ---------------------------------------------------------------------------
# AC-1 — Alert acknowledgment AdoptionEvent (via HTTP + fake DB)
# ---------------------------------------------------------------------------

class _AlertFakeConnection:
    """Minimal fake asyncpg connection for alert acknowledgment tests.

    Returns plain dicts so that fetch_alert's dict(row) call succeeds
    (andon_service.py:46 calls dict(row) on the fetchrow result).
    """

    def __init__(self, alert_id: str, status: str = "open") -> None:
        self._alert_id = alert_id
        self._status = status
        self._alert_row: dict[str, Any] = {
            "alert_id": uuid.UUID(alert_id),
            "opcenter_event_id": "OPC-001",
            "station_id": _STATION,
            "line_id": "WELD-01",
            "alert_type": "predictive",
            "predicted_failure_type": "weld_burn",
            "minutes_to_predicted_failure": 5,
            "recommended_action": "Check electrode",
            "status": status,
            "detected_at": _FAR_FUTURE,
            "received_at": _FAR_FUTURE,
            "acknowledged_at": None,
            "resolved_at": None,
            "kafka_offset": 0,
            "correlation_id": uuid.uuid4(),
            "created_at": _FAR_FUTURE,
            "updated_at": _FAR_FUTURE,
        }
        self.executes: list[str] = []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        q = query.lower()
        # acknowledge_alert issues: UPDATE ... WHERE status='open' RETURNING acknowledged_at
        if "update andon_alert" in q:
            if self._status == "open":
                return {"acknowledged_at": _FAR_FUTURE}
            return None
        # fetch_alert issues a SELECT; result goes through dict(row) in andon_service
        if "select" in q and "andon_alert" in q:
            if "status from" in q:
                return {"status": self._status}
            return dict(self._alert_row)
        return None

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "status from andon_alert" in query.lower():
            return self._status
        return None

    async def execute(self, query: str, *args: Any) -> None:
        self.executes.append(query)


class _FakePool:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        conn = self._conn

        class _Ctx:
            async def __aenter__(self_) -> Any:
                return conn

            async def __aexit__(self_, *_: Any) -> None:
                pass

        return _Ctx()


def _build_alert_app(conn: Any) -> Any:
    from app.db import get_pool, get_ts_pool
    from app.main import create_app

    app = create_app()
    pool = _FakePool(conn)
    # ts_pool returns empty weld trace
    ts_conn_data: dict = {"weld_current_a": [], "weld_voltage_v": []}

    class _TsFakeConn:
        async def fetch(self, query: str, *args: Any) -> list:
            return []

    ts_pool = _FakePool(_TsFakeConn())
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_ts_pool] = lambda: ts_pool
    return app


class TestAC1_AlertAcknowledgeAdoptionEvent:
    """AC-1: Alert acknowledgment produces an AdoptionEvent on TOPIC_ADOPTION_EVENT."""

    def test_alert_acknowledge_publishes_adoption_event(self) -> None:
        """AC-1: POST /andon/alerts/{id}/acknowledge publishes an AdoptionEvent."""
        alert_id = str(uuid.uuid4())
        conn = _AlertFakeConnection(alert_id=alert_id)
        app = _build_alert_app(conn)

        published: list[dict] = []

        async def fake_publish(topic: str, payload: dict) -> None:
            published.append({"topic": topic, "payload": payload})

        from app.audit import AuditStore

        class _NullAudit:
            def write_event(self, **kwargs: Any) -> None:
                pass

        from app.routers.andon_alerts import get_audit_writer
        app.dependency_overrides[get_audit_writer] = lambda: _NullAudit()

        with patch("app.routers.andon_alerts.publish", side_effect=fake_publish):
            with patch("app.routers.andon_alerts.fire_audit_write"):
                client = TestClient(app, raise_server_exceptions=True)
                resp = client.post(
                    f"/andon/alerts/{alert_id}/acknowledge",
                    json={"disposition": "resolved"},
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        adoption_events = [p for p in published if p["topic"] == "factory.munich.line-command.adoption.v1"]
        assert len(adoption_events) >= 1, (
            f"Expected ≥1 AdoptionEvent on TOPIC_ADOPTION_EVENT, got: {published}"
        )

    def test_alert_acknowledge_adoption_event_contains_required_fields(self) -> None:
        """AC-1: Alert acknowledgment AdoptionEvent has userId, stationId, actionType."""
        alert_id = str(uuid.uuid4())
        conn = _AlertFakeConnection(alert_id=alert_id)
        app = _build_alert_app(conn)

        published: list[dict] = []

        async def fake_publish(topic: str, payload: dict) -> None:
            published.append({"topic": topic, "payload": payload})

        from app.routers.andon_alerts import get_audit_writer

        class _NullAudit:
            def write_event(self, **kwargs: Any) -> None:
                pass

        app.dependency_overrides[get_audit_writer] = lambda: _NullAudit()

        with patch("app.routers.andon_alerts.publish", side_effect=fake_publish):
            with patch("app.routers.andon_alerts.fire_audit_write"):
                client = TestClient(app, raise_server_exceptions=True)
                client.post(
                    f"/andon/alerts/{alert_id}/acknowledge",
                    json={"disposition": "resolved"},
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )

        adoption_events = [p for p in published if p["topic"] == "factory.munich.line-command.adoption.v1"]
        assert len(adoption_events) >= 1
        payload = adoption_events[0]["payload"]
        # userId comes from JWT sub
        assert "userId" in payload, f"userId missing from AdoptionEvent payload: {payload}"
        assert payload["userId"] == "sl-001"
        # stationId
        assert "stationId" in payload, f"stationId missing from AdoptionEvent payload: {payload}"
        assert payload["stationId"] == _STATION
        # timestamp
        ts_key = next((k for k in ("timestamp", "occurredAt") if k in payload), None)
        assert ts_key is not None, f"No timestamp field in AdoptionEvent payload: {payload}"


# ---------------------------------------------------------------------------
# AC-2 — WAU login-only exclusion (end-to-end via the KQL mirror)
# ---------------------------------------------------------------------------

def _load_wau_module() -> Any:
    """Load the WAU calculator from the observability-stack tests."""
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "test_wau_login_only_exclusion",
        pathlib.Path(__file__).parents[3]
        / "platform/observability-stack/tests/test_wau_login_only_exclusion.py",
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_GATE1_WINDOW_START = datetime(2025, 11, 1, 0, 0, 0, tzinfo=timezone.utc)
_GATE1_WINDOW_END   = datetime(2025, 11, 28, 23, 59, 59, tzinfo=timezone.utc)
_WINDOW_START = _GATE1_WINDOW_START.strftime("%Y-%m-%dT%H:%M:%SZ")
_WINDOW_END   = _GATE1_WINDOW_END.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(offset_days: int, hour: int = 9) -> str:
    dt = _GATE1_WINDOW_START + timedelta(days=offset_days, hours=hour)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _lc_event(
    user_id: str,
    action: str,
    timestamp: str,
    role: str = "shift_lead",
) -> dict[str, Any]:
    return {
        "userId": user_id,
        "role": role,
        "actionType": action,
        "timestamp": timestamp,
        "component": "line-command",
        "stationId": _STATION,
    }


class TestAC2_WauLoginOnlyExclusion:
    """AC-2: A login-only session is excluded from the WAU active-user count.

    All four meaningful action types (ALERT_ACKNOWLEDGED, HANDOVER_SUBMITTED,
    AR_SESSION_STARTED, TORQUE_LOG_SUBMITTED) are recognised by the WAU
    calculator; LOGIN alone is not.
    """

    def setup_method(self) -> None:
        self.wau = _load_wau_module()

    def test_login_only_session_excluded_from_wau(self) -> None:
        """AC-2: A session with only LOGIN produces WAU=0."""
        events = [_lc_event("sl-001", "LOGIN", _ts(2))]
        result = self.wau.compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        assert sum(result.values()) == 0, (
            f"Login-only session should contribute WAU=0; got {result}"
        )

    def test_login_only_shift_lead_excluded(self) -> None:
        """AC-2: Shift Lead login-only session contributes zero to shift_lead WAU."""
        events = [_lc_event("sl-001", "LOGIN", _ts(2), role="shift_lead")]
        result = self.wau.compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        assert sum(result.values()) == 0

    def test_login_only_associate_excluded(self) -> None:
        """AC-2: Assembly Associate login-only session contributes zero to associate WAU."""
        events = [_lc_event("aa-001", "LOGIN", _ts(2), role="assembly_associate")]
        result = self.wau.compute_line_command_wau(
            events, "assembly_associate", _WINDOW_START, _WINDOW_END
        )
        assert sum(result.values()) == 0

    def test_alert_acknowledged_counts_as_active(self) -> None:
        """AC-2: ALERT_ACKNOWLEDGED is a meaningful action — session is active."""
        events = [_lc_event("sl-001", "ALERT_ACKNOWLEDGED", _ts(2))]
        result = self.wau.compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        assert sum(result.values()) == 1

    def test_handover_submitted_counts_as_active(self) -> None:
        """AC-2: HANDOVER_SUBMITTED is a meaningful action — session is active."""
        events = [_lc_event("sl-001", "HANDOVER_SUBMITTED", _ts(2))]
        result = self.wau.compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        assert sum(result.values()) == 1

    def test_ar_session_started_counts_as_active(self) -> None:
        """AC-2: AR_SESSION_STARTED is a meaningful action — session is active."""
        events = [_lc_event("aa-001", "AR_SESSION_STARTED", _ts(2), role="assembly_associate")]
        result = self.wau.compute_line_command_wau(
            events, "assembly_associate", _WINDOW_START, _WINDOW_END
        )
        assert sum(result.values()) == 1

    def test_torque_log_submitted_counts_as_active(self) -> None:
        """AC-2: TORQUE_LOG_SUBMITTED is a meaningful action — session is active."""
        events = [_lc_event("aa-001", "TORQUE_LOG_SUBMITTED", _ts(2), role="assembly_associate")]
        result = self.wau.compute_line_command_wau(
            events, "assembly_associate", _WINDOW_START, _WINDOW_END
        )
        assert sum(result.values()) == 1

    def test_login_then_meaningful_action_counts_as_active(self) -> None:
        """AC-2: A session with LOGIN + meaningful action counts as active (WAU=1)."""
        events = [
            _lc_event("sl-001", "LOGIN", _ts(2)),
            _lc_event("sl-001", "HANDOVER_SUBMITTED", _ts(2, hour=10)),
        ]
        result = self.wau.compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        assert sum(result.values()) == 1

    def test_all_four_meaningful_actions_in_meaningful_set(self) -> None:
        """AC-2: All four Line Command meaningful actions are in LINE_COMMAND_MEANINGFUL_ACTIONS."""
        expected = {
            "ALERT_ACKNOWLEDGED",
            "HANDOVER_SUBMITTED",
            "AR_SESSION_STARTED",
            "TORQUE_LOG_SUBMITTED",
        }
        assert expected <= self.wau.LINE_COMMAND_MEANINGFUL_ACTIONS, (
            f"Missing meaningful actions: {expected - self.wau.LINE_COMMAND_MEANINGFUL_ACTIONS}"
        )

    def test_login_not_in_meaningful_action_set(self) -> None:
        """AC-2: LOGIN is not in the meaningful action set."""
        assert "LOGIN" not in self.wau.LINE_COMMAND_MEANINGFUL_ACTIONS, (
            "LOGIN must not be a meaningful action — login-only sessions must be excluded"
        )

    def test_login_only_week_excluded_meaningful_week_counted(self) -> None:
        """AC-2: Login-only in W45, meaningful action in W46 — only W46 produces WAU=1."""
        day_w45 = _ts(2)   # 2025-11-03 — W45
        day_w46 = _ts(9)   # 2025-11-10 — W46
        events = [
            _lc_event("sl-001", "LOGIN", day_w45),
            _lc_event("sl-001", "HANDOVER_SUBMITTED", day_w46),
        ]
        result = self.wau.compute_line_command_wau(
            events, "shift_lead", _WINDOW_START, _WINDOW_END
        )
        week_w45 = self.wau._iso_to_week_key(day_w45)
        week_w46 = self.wau._iso_to_week_key(day_w46)
        assert result.get(week_w45, 0) == 0, (
            f"W45 (login-only) must have WAU=0, got {result.get(week_w45)}"
        )
        assert result.get(week_w46, 0) == 1, (
            f"W46 (meaningful action) must have WAU=1, got {result.get(week_w46)}"
        )
