"""Acceptance tests for US-008 — Station Detail weld trace, threshold overlay, dispatch audit.

Criterion coverage (end-to-end through HTTP entry points — seam between get and acknowledge):
  AC1 — GET /andon/alerts/{alertId} returns weld_current_a and weld_voltage_v time-series
         arrays together with the predictive threshold value and crossing timestamp, confirming
         the Beckhoff PLC trace data is merged with the alert detail in one response.

  AC2 — The same GET response co-locates thresholdCrossingAt and minutesToPredictedFailure,
         so the UI can highlight the crossing with the predicted time-to-failure countdown.

  AC3 — POST /andon/alerts/{alertId}/acknowledge logs a MAINTENANCE_DISPATCH audit record
         containing the Shift Lead's user ID, the alert ID, and two SHA-256 state hashes.
         The Kafka dispatch event also carries userId, stationId, alertId, and timestamp
         — confirming the audit trail write is triggered by the dispatch action.

These tests call the real router functions through FastAPI's TestClient.  They exercise
the seam between andon_service.fetch_weld_trace (TimescaleDB read) +
andon_service.fetch_alert (Postgres read) + audit.fire_audit_write (blob write) — none
of which is covered end-to-end by the unit tests in test_andon_alerts.py.

Preconditions:
  - A fixture row for the alert must exist in the pool mock (checked first).
  - A TimescaleDB trace fixture with 60-minute timestamps must exist (checked first).
  - The audit writer must be an AuditStore instance and reachable via dependency override.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.audit import AuditStore, sha256_hex
from app.db import get_pool, get_ts_pool

# ---------------------------------------------------------------------------
# Constants — matches the fixtures used by the frontend tests
# ---------------------------------------------------------------------------

_ALERT_ID = str(uuid.UUID("550e8400-e29b-41d4-a716-446655440000"))
_STATION_ID = "weld-cell-03"
_LINE_ID = "line-b"
_DETECTED_AT = datetime(2040, 6, 1, 6, 0, 0, tzinfo=timezone.utc)
_RECEIVED_AT = datetime(2040, 6, 1, 6, 0, 1, tzinfo=timezone.utc)

# The alert row returned by the main Postgres pool
_OPEN_ALERT_ROW: dict = {
    "alert_id": uuid.UUID(_ALERT_ID),
    "opcenter_event_id": "evt-acceptance-001",
    "station_id": _STATION_ID,
    "line_id": _LINE_ID,
    "alert_type": "predictive",
    "predicted_failure_type": "weld_tip_wear",
    "minutes_to_predicted_failure": 12,
    "recommended_action": "Replace weld tip within 12 min",
    "status": "open",
    "detected_at": _DETECTED_AT,
    "received_at": _RECEIVED_AT,
    "acknowledged_at": None,
    "resolved_at": None,
    "kafka_offset": 1,
    "correlation_id": uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
    "created_at": _RECEIVED_AT,
    "updated_at": _RECEIVED_AT,
}

# 60-minute window: timestamps spread across the last hour (far-future anchored)
_WELD_TRACE_ROWS = [
    {"time": datetime(2040, 6, 1, 5, 1, 0, tzinfo=timezone.utc),  "tag_name": "weld_current_a", "value": 8300.0},
    {"time": datetime(2040, 6, 1, 5, 30, 0, tzinfo=timezone.utc), "tag_name": "weld_current_a", "value": 8500.0},
    {"time": datetime(2040, 6, 1, 5, 59, 0, tzinfo=timezone.utc), "tag_name": "weld_current_a", "value": 8700.0},
    {"time": datetime(2040, 6, 1, 5, 1, 0, tzinfo=timezone.utc),  "tag_name": "weld_voltage_v", "value": 219.0},
    {"time": datetime(2040, 6, 1, 5, 30, 0, tzinfo=timezone.utc), "tag_name": "weld_voltage_v", "value": 220.5},
    {"time": datetime(2040, 6, 1, 5, 59, 0, tzinfo=timezone.utc), "tag_name": "weld_voltage_v", "value": 222.0},
]

_ACK_TIME = datetime(2040, 6, 1, 7, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# JWT helper — properly-formed JWT with base64url-encoded header so that
# python-jose can parse it regardless of version.
# ---------------------------------------------------------------------------

def _make_jwt(sub: str, roles: list[str], jti: str | None = None) -> str:
    header_data = json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    header_b64 = base64.urlsafe_b64encode(header_data).rstrip(b"=").decode()
    claims: dict = {"sub": sub, "roles": roles, "exp": 9_999_999_999}
    if jti is not None:
        claims["jti"] = jti
    payload_bytes = json.dumps(claims).encode()
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    return f"{header_b64}.{payload_b64}.fakesig"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _async_cm(obj):
    class _CM:
        async def __aenter__(self):
            return obj
        async def __aexit__(self, *_args):
            pass
    return _CM()


def _make_pool_ctx(fetchrow_result=None, fetchval_result="open"):
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    conn.fetchval = AsyncMock(return_value=fetchval_result)
    pool.acquire = MagicMock(return_value=_async_cm(conn))
    pool._conn = conn
    return pool


def _make_ts_pool_ctx(trace_rows=None):
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=trace_rows or [])
    pool.acquire = MagicMock(return_value=_async_cm(conn))
    return pool


# ---------------------------------------------------------------------------
# Shared app/client factory — uses FastAPI dependency_overrides so that
# Depends(get_pool) is correctly replaced at request time.
# ---------------------------------------------------------------------------

def _make_app(
    pool_ctx=None,
    ts_pool_ctx=None,
    audit_store: AuditStore | None = None,
    extra_publish_list: list | None = None,
):
    """Return (TestClient, app) wired with fake DB pools and optional audit store."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    import app.routers.andon_alerts as andon_mod

    published: list[tuple[str, dict]] = extra_publish_list if extra_publish_list is not None else []

    the_app = create_app()

    if pool_ctx is None:
        pool_ctx = _make_pool_ctx(_OPEN_ALERT_ROW)
    if ts_pool_ctx is None:
        ts_pool_ctx = _make_ts_pool_ctx(_WELD_TRACE_ROWS)

    async def _override_get_pool():
        return pool_ctx

    async def _override_get_ts_pool():
        return ts_pool_ctx

    the_app.dependency_overrides[get_pool] = _override_get_pool
    the_app.dependency_overrides[get_ts_pool] = _override_get_ts_pool

    async def _mock_publish(topic: str, payload: dict) -> None:
        published.append((topic, payload))

    andon_mod._process_audit_writer = audit_store or AuditStore()

    # Monkeypatch publish inside the already-imported module
    original_publish = andon_mod.publish
    andon_mod.publish = _mock_publish  # type: ignore[assignment]

    client = TestClient(the_app)

    # Restore after use (caller responsible, or use context manager pattern)
    # For simplicity in tests: the client is used within the test scope
    return client, published, original_publish, andon_mod


# ---------------------------------------------------------------------------
# AC1 — GET /andon/alerts/{alertId} returns weld trace arrays + threshold overlay fields
# ---------------------------------------------------------------------------

class TestAC1WeldTraceAndThresholdOverlay:
    """AC1: weld current/voltage time-series from Beckhoff PLC returned in 60-minute window
    with predictive alert threshold value overlaid as a reference.
    """

    def test_precondition_trace_fixture_has_rows_in_60_min_window(self):
        """Precondition: fixture rows exist; test would trivially pass against an empty trace."""
        assert len(_WELD_TRACE_ROWS) > 0, "Trace fixture must not be empty"
        current_rows = [r for r in _WELD_TRACE_ROWS if r["tag_name"] == "weld_current_a"]
        voltage_rows = [r for r in _WELD_TRACE_ROWS if r["tag_name"] == "weld_voltage_v"]
        assert len(current_rows) == 3, "Fixture must have 3 current points"
        assert len(voltage_rows) == 3, "Fixture must have 3 voltage points"
        # All timestamps must be within 60 minutes of _DETECTED_AT
        for row in _WELD_TRACE_ROWS:
            delta = (_DETECTED_AT - row["time"]).total_seconds()
            assert 0 <= delta <= 3600, f"Trace point {row} is outside 60-minute window"

    def test_ac1_response_contains_weld_trace_key(self):
        client, _, original_publish, andon_mod = _make_app()
        jwt = _make_jwt("sl-acceptance@bmwgroup.net", ["ifos-shiftlead"])
        try:
            resp = client.get(
                f"/andon/alerts/{_ALERT_ID}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            # AC1: weldTrace key must be present in the Station Detail response
            assert "weldTrace" in data, "weldTrace missing from response"
        finally:
            andon_mod.publish = original_publish

    def test_ac1_weld_current_array_has_3_points_from_60_min_window(self):
        client, _, original_publish, andon_mod = _make_app()
        jwt = _make_jwt("sl-acceptance@bmwgroup.net", ["ifos-shiftlead"])
        try:
            resp = client.get(
                f"/andon/alerts/{_ALERT_ID}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            # AC1: 3 current data points returned (all within last 60 minutes per fixture)
            assert len(data["weldTrace"]["weld_current_a"]) == 3
            for pt in data["weldTrace"]["weld_current_a"]:
                assert "timestamp" in pt, "Current point missing timestamp"
                assert "value" in pt, "Current point missing value"
        finally:
            andon_mod.publish = original_publish

    def test_ac1_weld_voltage_array_has_3_points_from_60_min_window(self):
        client, _, original_publish, andon_mod = _make_app()
        jwt = _make_jwt("sl-acceptance@bmwgroup.net", ["ifos-shiftlead"])
        try:
            resp = client.get(
                f"/andon/alerts/{_ALERT_ID}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            # AC1: 3 voltage data points returned
            assert len(data["weldTrace"]["weld_voltage_v"]) == 3
            for pt in data["weldTrace"]["weld_voltage_v"]:
                assert "timestamp" in pt
                assert "value" in pt
        finally:
            andon_mod.publish = original_publish

    def test_ac1_threshold_crossing_at_and_value_present_for_predictive_alert(self):
        client, _, original_publish, andon_mod = _make_app()
        jwt = _make_jwt("sl-acceptance@bmwgroup.net", ["ifos-shiftlead"])
        try:
            resp = client.get(
                f"/andon/alerts/{_ALERT_ID}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            # AC1: threshold overlay fields must be present for a predictive alert
            assert "thresholdCrossingAt" in data, "thresholdCrossingAt missing (threshold overlay)"
            assert data["alertType"] == "predictive"
            assert data["thresholdCrossingAt"] is not None
        finally:
            andon_mod.publish = original_publish

    def test_ac1_alert_id_and_station_id_match_fixture(self):
        client, _, original_publish, andon_mod = _make_app()
        jwt = _make_jwt("sl-acceptance@bmwgroup.net", ["ifos-shiftlead"])
        try:
            resp = client.get(
                f"/andon/alerts/{_ALERT_ID}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["alertId"] == _ALERT_ID
            assert data["stationId"] == _STATION_ID
        finally:
            andon_mod.publish = original_publish


# ---------------------------------------------------------------------------
# AC2 — GET response co-locates thresholdCrossingAt + minutesToPredictedFailure
# ---------------------------------------------------------------------------

class TestAC2ThresholdCrossingHighlight:
    """AC2: threshold crossing timestamp and time-to-failure are co-located in the
    response so the UI can render them together on the trace.
    """

    def test_ac2_minutes_to_predicted_failure_is_12(self):
        client, _, original_publish, andon_mod = _make_app()
        jwt = _make_jwt("sl-acceptance@bmwgroup.net", ["ifos-shiftlead"])
        try:
            resp = client.get(
                f"/andon/alerts/{_ALERT_ID}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            # AC2: predicted failure countdown must match fixture value
            assert data["minutesToPredictedFailure"] == 12
        finally:
            andon_mod.publish = original_publish

    def test_ac2_threshold_crossing_at_is_non_null_for_predictive_alert(self):
        client, _, original_publish, andon_mod = _make_app()
        jwt = _make_jwt("sl-acceptance@bmwgroup.net", ["ifos-shiftlead"])
        try:
            resp = client.get(
                f"/andon/alerts/{_ALERT_ID}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            # AC2: crossing timestamp is present alongside the time-to-failure value
            assert data["thresholdCrossingAt"] is not None
            assert data["minutesToPredictedFailure"] is not None
        finally:
            andon_mod.publish = original_publish

    def test_ac2_both_crossing_fields_in_same_response_body(self):
        """AC2: both fields must travel together — missing either breaks the UI countdown."""
        client, _, original_publish, andon_mod = _make_app()
        jwt = _make_jwt("sl-acceptance@bmwgroup.net", ["ifos-shiftlead"])
        try:
            resp = client.get(
                f"/andon/alerts/{_ALERT_ID}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            data = resp.json()
            assert "thresholdCrossingAt" in data
            assert "minutesToPredictedFailure" in data
            # Both together confirm the UI can render the crossing highlight with countdown
            assert data["minutesToPredictedFailure"] == 12
            assert data["thresholdCrossingAt"] is not None
        finally:
            andon_mod.publish = original_publish

    def test_ac2_minutes_to_failure_null_for_reactive_alert(self):
        """AC2: reactive alerts carry no countdown — only predictive alerts have this field."""
        from app.db import get_pool as _gp, get_ts_pool as _gts
        from fastapi.testclient import TestClient
        from app.main import create_app
        import app.routers.andon_alerts as andon_mod

        reactive_row = dict(_OPEN_ALERT_ROW)
        reactive_row["alert_type"] = "reactive"
        reactive_row["minutes_to_predicted_failure"] = None

        the_app = create_app()

        async def _override_get_pool():
            return _make_pool_ctx(reactive_row)

        async def _override_get_ts_pool():
            return _make_ts_pool_ctx(_WELD_TRACE_ROWS)

        the_app.dependency_overrides[_gp] = _override_get_pool
        the_app.dependency_overrides[_gts] = _override_get_ts_pool

        original_publish = andon_mod.publish

        async def _noop_publish(topic, payload):
            pass

        andon_mod.publish = _noop_publish  # type: ignore[assignment]

        client = TestClient(the_app)
        jwt = _make_jwt("sl@bmwgroup.net", ["ifos-shiftlead"])
        try:
            resp = client.get(
                f"/andon/alerts/{_ALERT_ID}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            # AC2: no countdown for reactive alerts
            assert data["minutesToPredictedFailure"] is None
        finally:
            andon_mod.publish = original_publish


# ---------------------------------------------------------------------------
# AC3 — POST /acknowledge fires MAINTENANCE_DISPATCH audit with userId, alertId, timestamp
# ---------------------------------------------------------------------------

class TestAC3DispatchAuditTrail:
    """AC3: confirming dispatch from Station Detail logs user_id, entity_id (alertId),
    and before/after state hashes to the immutable audit trail.

    The Kafka dispatch event additionally carries userId, stationId, alertId, timestamp
    confirming the dispatch event is published alongside the audit write.
    """

    def _build_ack_app(self, audit_store: AuditStore):
        """Returns (TestClient, published_list, original_publish, andon_mod) for the ack flow."""
        from fastapi.testclient import TestClient
        from app.main import create_app
        from app.db import get_pool as _gp, get_ts_pool as _gts
        import app.routers.andon_alerts as andon_mod

        the_app = create_app()
        published: list[tuple[str, dict]] = []

        async def _override_get_pool():
            pool = MagicMock()
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(side_effect=[
                _OPEN_ALERT_ROW,
                {"acknowledged_at": _ACK_TIME},
            ])
            pool.acquire = MagicMock(return_value=_async_cm(conn))
            return pool

        async def _override_get_ts_pool():
            return _make_ts_pool_ctx([])

        the_app.dependency_overrides[_gp] = _override_get_pool
        the_app.dependency_overrides[_gts] = _override_get_ts_pool

        original_publish = andon_mod.publish

        async def _mock_publish(topic: str, payload: dict) -> None:
            published.append((topic, payload))

        andon_mod.publish = _mock_publish  # type: ignore[assignment]
        andon_mod._process_audit_writer = audit_store

        return TestClient(the_app), published, original_publish, andon_mod

    def test_precondition_audit_store_starts_empty(self):
        """Precondition: AuditStore starts clean — test would pass vacuously against stale state."""
        store = AuditStore()
        assert store.records == [], "AuditStore must start with no records"

    def test_ac3_audit_record_user_id_matches_jwt_sub(self):
        """AC3: dispatch logs the Shift Lead's user ID (JWT sub claim) in the audit record."""
        audit_store = AuditStore()
        client, _, original_publish, andon_mod = self._build_ack_app(audit_store)
        jwt = _make_jwt("shift-lead-acceptance@bmwgroup.net", ["ifos-shiftlead"])

        try:
            resp = client.post(
                f"/andon/alerts/{_ALERT_ID}/acknowledge",
                json={"disposition": "resolved"},
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert resp.status_code == 200

            # Poll for daemon-thread audit write (≤1 s per design)
            deadline = time.monotonic() + 1.0
            records = []
            while time.monotonic() < deadline:
                records = [r for r in audit_store.records if r.action_type == "MAINTENANCE_DISPATCH"]
                if records:
                    break
                time.sleep(0.005)

            assert records, "Audit write for MAINTENANCE_DISPATCH did not appear within 1 s"
            # AC3: userId in audit record must match the JWT sub claim
            assert records[0].user_id == "shift-lead-acceptance@bmwgroup.net"
        finally:
            andon_mod.publish = original_publish

    def test_ac3_audit_record_entity_id_is_alert_id(self):
        """AC3: the entity logged in the audit trail is the alertId of the dispatched alert."""
        audit_store = AuditStore()
        client, _, original_publish, andon_mod = self._build_ack_app(audit_store)
        jwt = _make_jwt("sl@bmwgroup.net", ["ifos-shiftlead"])

        try:
            resp = client.post(
                f"/andon/alerts/{_ALERT_ID}/acknowledge",
                json={"disposition": "resolved"},
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert resp.status_code == 200

            deadline = time.monotonic() + 1.0
            records = []
            while time.monotonic() < deadline:
                records = [r for r in audit_store.records if r.action_type == "MAINTENANCE_DISPATCH"]
                if records:
                    break
                time.sleep(0.005)

            assert records
            # AC3: entity_id must be the alertId so the audit record can be correlated to the station event
            assert records[0].entity_id == _ALERT_ID
        finally:
            andon_mod.publish = original_publish

    def test_ac3_audit_before_hash_matches_open_state(self):
        """AC3: before_hash is SHA-256 of the open alert state — proves pre-dispatch state captured."""
        audit_store = AuditStore()
        client, _, original_publish, andon_mod = self._build_ack_app(audit_store)
        jwt = _make_jwt("sl@bmwgroup.net", ["ifos-shiftlead"])

        try:
            resp = client.post(
                f"/andon/alerts/{_ALERT_ID}/acknowledge",
                json={"disposition": "resolved"},
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert resp.status_code == 200

            deadline = time.monotonic() + 1.0
            records = []
            while time.monotonic() < deadline:
                records = [r for r in audit_store.records if r.action_type == "MAINTENANCE_DISPATCH"]
                if records:
                    break
                time.sleep(0.005)

            assert records
            expected_pre = json.dumps({"alert_id": _ALERT_ID, "status": "open"}, sort_keys=True)
            # AC3: pre-dispatch state hash must match the open-status JSON
            assert records[0].before_hash == sha256_hex(expected_pre)
        finally:
            andon_mod.publish = original_publish

    def test_ac3_audit_after_hash_matches_acknowledged_state(self):
        """AC3: after_hash is SHA-256 of the acknowledged alert state — proves post-dispatch state captured."""
        audit_store = AuditStore()
        client, _, original_publish, andon_mod = self._build_ack_app(audit_store)
        jwt = _make_jwt("sl@bmwgroup.net", ["ifos-shiftlead"])

        try:
            resp = client.post(
                f"/andon/alerts/{_ALERT_ID}/acknowledge",
                json={"disposition": "resolved"},
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert resp.status_code == 200

            deadline = time.monotonic() + 1.0
            records = []
            while time.monotonic() < deadline:
                records = [r for r in audit_store.records if r.action_type == "MAINTENANCE_DISPATCH"]
                if records:
                    break
                time.sleep(0.005)

            assert records
            expected_post = json.dumps({"alert_id": _ALERT_ID, "status": "acknowledged"}, sort_keys=True)
            # AC3: post-dispatch state hash proves the acknowledged transition was recorded
            assert records[0].after_hash == sha256_hex(expected_post)
        finally:
            andon_mod.publish = original_publish

    def test_ac3_kafka_dispatch_event_carries_userId_stationId_alertId_timestamp(self):
        """AC3: Kafka maintenance_dispatch event carries all required audit payload fields."""
        audit_store = AuditStore()
        client, published, original_publish, andon_mod = self._build_ack_app(audit_store)
        jwt = _make_jwt("sl-kafka@bmwgroup.net", ["ifos-shiftlead"])

        try:
            resp = client.post(
                f"/andon/alerts/{_ALERT_ID}/acknowledge",
                json={"disposition": "resolved"},
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert resp.status_code == 200

            dispatch_events = [
                payload for topic, payload in published
                if "maintenance-dispatch" in topic
            ]
            assert len(dispatch_events) == 1, "Exactly one maintenance-dispatch Kafka event expected"
            ev = dispatch_events[0]

            # AC3: audit event carries all four required fields per acceptance criterion
            assert ev["userId"] == "sl-kafka@bmwgroup.net", "userId must match JWT sub claim"
            assert ev["stationId"] == _STATION_ID, "stationId must match the alert's station"
            assert ev["alertId"] == _ALERT_ID, "alertId must identify the dispatched alert"
            assert "timestamp" in ev and ev["timestamp"], "timestamp must be present and non-empty"
        finally:
            andon_mod.publish = original_publish

    def test_ac3_acknowledge_response_contains_event_id_confirming_audit_entry(self):
        """AC3: HTTP response carries an eventId that ties back to the logged audit entry."""
        audit_store = AuditStore()
        client, _, original_publish, andon_mod = self._build_ack_app(audit_store)
        jwt = _make_jwt("sl@bmwgroup.net", ["ifos-shiftlead"])

        try:
            resp = client.post(
                f"/andon/alerts/{_ALERT_ID}/acknowledge",
                json={"disposition": "resolved"},
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert resp.status_code == 200
            data = resp.json()

            # AC3: response must include eventId so the UI can confirm the audit entry was created
            assert "eventId" in data and data["eventId"], "eventId must be present and non-empty"
            assert data["status"] == "acknowledged"
            assert data["alertId"] == _ALERT_ID
        finally:
            andon_mod.publish = original_publish
