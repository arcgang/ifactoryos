"""Tests for GET /andon/alerts/{alertId} and POST /andon/alerts/{alertId}/acknowledge.

Unit coverage:
  - alert fetch returns trace arrays and crossing fields
  - acknowledge transitions open → acknowledged
  - 409 on double-acknowledge
  - 403 for caller lacking shift-lead role
  - audit log payload shape (action_type, entity_id, user_id, hashes)
  - Kafka dispatch and adoption event payloads

Integration (pool-stub):
  - full dispatch flow against in-process fakes for DB + Kafka
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.andon_service import AlreadyAcknowledgedError, acknowledge_alert, fetch_alert, fetch_weld_trace
from app.audit import AuditStore, sha256_hex

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALERT_ID = str(uuid.UUID("550e8400-e29b-41d4-a716-446655440000"))
_STATION_ID = "weld-cell-03"
_LINE_ID = "line-b"
_DETECTED_AT = datetime(2040, 6, 1, 6, 0, 0, tzinfo=timezone.utc)
_RECEIVED_AT = datetime(2040, 6, 1, 6, 0, 1, tzinfo=timezone.utc)
_CORRELATION_ID = str(uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))

_OPEN_ALERT_ROW = {
    "alert_id": uuid.UUID(_ALERT_ID),
    "opcenter_event_id": "evt-001",
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
    "kafka_offset": 100,
    "correlation_id": uuid.UUID(_CORRELATION_ID),
    "created_at": _RECEIVED_AT,
    "updated_at": _RECEIVED_AT,
}

_WELD_TRACE_ROWS = [
    {"time": datetime(2040, 6, 1, 5, 59, 0, tzinfo=timezone.utc), "tag_name": "weld_current_a", "value": 8500.0},
    {"time": datetime(2040, 6, 1, 5, 59, 30, tzinfo=timezone.utc), "tag_name": "weld_current_a", "value": 8600.0},
    {"time": datetime(2040, 6, 1, 5, 59, 0, tzinfo=timezone.utc), "tag_name": "weld_voltage_v", "value": 220.0},
    {"time": datetime(2040, 6, 1, 5, 59, 30, tzinfo=timezone.utc), "tag_name": "weld_voltage_v", "value": 221.5},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(fetchrow_result=None, fetch_result=None, fetchval_result=None):
    """Return a mock asyncpg connection."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    conn.fetch = AsyncMock(return_value=fetch_result or [])
    conn.fetchval = AsyncMock(return_value=fetchval_result)
    return conn


def _wait_for_audit(store: AuditStore, action: str, deadline: float = 1.0) -> None:
    start = time.monotonic()
    while time.monotonic() - start < deadline:
        for rec in store.records:
            if rec.action_type == action:
                return
        time.sleep(0.005)
    pytest.fail(f"Audit write for {action!r} did not appear within {deadline:.1f}s")


# ---------------------------------------------------------------------------
# Unit: fetch_alert
# ---------------------------------------------------------------------------

class TestFetchAlert:
    @pytest.mark.asyncio
    async def test_returns_dict_on_hit(self):
        conn = _make_conn(fetchrow_result=_OPEN_ALERT_ROW)
        result = await fetch_alert(conn, _ALERT_ID)
        assert result is not None
        assert result["alert_id"] == uuid.UUID(_ALERT_ID)
        assert result["station_id"] == _STATION_ID

    @pytest.mark.asyncio
    async def test_returns_none_on_miss(self):
        conn = _make_conn(fetchrow_result=None)
        result = await fetch_alert(conn, _ALERT_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_passes_alert_id_as_parameter(self):
        conn = _make_conn(fetchrow_result=_OPEN_ALERT_ROW)
        await fetch_alert(conn, _ALERT_ID)
        call_args = conn.fetchrow.call_args
        assert _ALERT_ID in call_args.args


# ---------------------------------------------------------------------------
# Unit: fetch_weld_trace
# ---------------------------------------------------------------------------

class TestFetchWeldTrace:
    @pytest.mark.asyncio
    async def test_returns_current_and_voltage_arrays(self):
        conn = _make_conn(fetch_result=_WELD_TRACE_ROWS)
        trace = await fetch_weld_trace(conn, _STATION_ID)
        assert len(trace["weld_current_a"]) == 2
        assert len(trace["weld_voltage_v"]) == 2

    @pytest.mark.asyncio
    async def test_trace_point_shape(self):
        conn = _make_conn(fetch_result=_WELD_TRACE_ROWS)
        trace = await fetch_weld_trace(conn, _STATION_ID)
        pt = trace["weld_current_a"][0]
        assert "timestamp" in pt
        assert "value" in pt
        assert pt["value"] == 8500.0

    @pytest.mark.asyncio
    async def test_empty_trace_on_no_rows(self):
        conn = _make_conn(fetch_result=[])
        trace = await fetch_weld_trace(conn, _STATION_ID)
        assert trace["weld_current_a"] == []
        assert trace["weld_voltage_v"] == []

    @pytest.mark.asyncio
    async def test_station_id_passed_as_parameter(self):
        conn = _make_conn(fetch_result=[])
        await fetch_weld_trace(conn, _STATION_ID)
        call_args = conn.fetch.call_args
        assert _STATION_ID in call_args.args


# ---------------------------------------------------------------------------
# Unit: acknowledge_alert
# ---------------------------------------------------------------------------

class TestAcknowledgeAlert:
    _NOW = datetime(2040, 6, 1, 7, 0, 0, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_returns_acknowledged_at_on_open_alert(self):
        conn = _make_conn(fetchrow_result={"acknowledged_at": self._NOW})
        ack_at = await acknowledge_alert(conn, _ALERT_ID, now=self._NOW)
        assert ack_at == self._NOW

    @pytest.mark.asyncio
    async def test_raises_on_already_acknowledged(self):
        conn = _make_conn(fetchrow_result=None, fetchval_result="acknowledged")
        with pytest.raises(AlreadyAcknowledgedError) as exc_info:
            await acknowledge_alert(conn, _ALERT_ID, now=self._NOW)
        assert "acknowledged" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_raises_on_resolved_state(self):
        conn = _make_conn(fetchrow_result=None, fetchval_result="resolved")
        with pytest.raises(AlreadyAcknowledgedError):
            await acknowledge_alert(conn, _ALERT_ID, now=self._NOW)

    @pytest.mark.asyncio
    async def test_raises_on_false_positive_state(self):
        conn = _make_conn(fetchrow_result=None, fetchval_result="false_positive")
        with pytest.raises(AlreadyAcknowledgedError):
            await acknowledge_alert(conn, _ALERT_ID, now=self._NOW)

    @pytest.mark.asyncio
    async def test_raises_on_not_found(self):
        conn = _make_conn(fetchrow_result=None, fetchval_result=None)
        with pytest.raises(AlreadyAcknowledgedError):
            await acknowledge_alert(conn, _ALERT_ID, now=self._NOW)


# ---------------------------------------------------------------------------
# Unit: HTTP endpoints via TestClient
# ---------------------------------------------------------------------------

# Build a minimal JWT for a given role list
def _make_jwt(sub: str, roles: list[str]) -> str:
    import base64
    payload = json.dumps({"sub": sub, "roles": roles, "exp": 9_999_999_999}).encode()
    b64 = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    return f"header.{b64}.sig"


@pytest.fixture
def http_client(monkeypatch):
    """TestClient with DB pools and Kafka mocked out."""
    from fastapi.testclient import TestClient
    from app.main import create_app

    app = create_app()

    _shift_lead_jwt = _make_jwt("sl@bmwgroup.net", ["ifos-shiftlead"])
    _associate_jwt = _make_jwt("assoc@bmwgroup.net", ["ifos-associate"])

    published_events: list[tuple[str, dict]] = []

    async def _mock_publish(topic: str, payload: dict) -> None:
        published_events.append((topic, payload))

    monkeypatch.setattr("app.routers.andon_alerts.publish", _mock_publish)

    # Pool mocks
    async def _mock_get_pool():
        return _make_pool_ctx()

    async def _mock_get_ts_pool():
        return _make_ts_pool_ctx()

    monkeypatch.setattr("app.routers.andon_alerts.get_pool", _mock_get_pool)
    monkeypatch.setattr("app.routers.andon_alerts.get_ts_pool", _mock_get_ts_pool)

    def _make_pool_ctx():
        pool = MagicMock()
        conn = AsyncMock()
        # default: open alert row
        conn.fetchrow = AsyncMock(return_value=_OPEN_ALERT_ROW)
        conn.fetchval = AsyncMock(return_value="open")
        pool.acquire = MagicMock(return_value=_async_cm(conn))
        pool._conn = conn
        return pool

    def _make_ts_pool_ctx():
        pool = MagicMock()
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=_WELD_TRACE_ROWS)
        pool.acquire = MagicMock(return_value=_async_cm(conn))
        return pool

    return TestClient(app), _shift_lead_jwt, _associate_jwt, published_events


def _async_cm(obj):
    """Return an async context manager yielding obj."""
    class _CM:
        async def __aenter__(self):
            return obj
        async def __aexit__(self, *args):
            pass
    return _CM()


class TestGetAndonAlertEndpoint:
    def test_returns_200_with_trace_and_crossing_fields(self, http_client):
        client, shift_lead_jwt, _, _ = http_client
        resp = client.get(
            f"/andon/alerts/{_ALERT_ID}",
            headers={"Authorization": f"Bearer {shift_lead_jwt}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["alertId"] == _ALERT_ID
        assert data["stationId"] == _STATION_ID
        assert "weldTrace" in data
        assert "weld_current_a" in data["weldTrace"]
        assert "weld_voltage_v" in data["weldTrace"]
        assert len(data["weldTrace"]["weld_current_a"]) == 2
        assert "thresholdCrossingAt" in data
        assert data["minutesToPredictedFailure"] == 12

    def test_returns_401_without_token(self, http_client):
        client, _, _, _ = http_client
        resp = client.get(f"/andon/alerts/{_ALERT_ID}")
        assert resp.status_code == 401

    def test_returns_404_when_alert_missing(self, http_client, monkeypatch):
        client, shift_lead_jwt, _, _ = http_client

        async def _missing_pool():
            pool = MagicMock()
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(return_value=None)
            pool.acquire = MagicMock(return_value=_async_cm(conn))
            return pool

        monkeypatch.setattr("app.routers.andon_alerts.get_pool", _missing_pool)
        resp = client.get(
            f"/andon/alerts/{_ALERT_ID}",
            headers={"Authorization": f"Bearer {shift_lead_jwt}"},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "ALERT_NOT_FOUND"
        assert "correlation_id" in body

    def test_error_shape_has_correlation_id(self, http_client):
        client, _, _, _ = http_client
        resp = client.get(f"/andon/alerts/{_ALERT_ID}")
        body = resp.json()
        assert "correlation_id" in body


class TestAcknowledgeAndonAlertEndpoint:
    def test_returns_200_on_open_alert(self, http_client, monkeypatch):
        client, shift_lead_jwt, _, published = http_client
        ack_time = datetime(2040, 6, 1, 7, 0, 0, tzinfo=timezone.utc)

        async def _ack_pool():
            pool = MagicMock()
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(side_effect=[
                _OPEN_ALERT_ROW,
                {"acknowledged_at": ack_time},
            ])
            pool.acquire = MagicMock(return_value=_async_cm(conn))
            return pool

        monkeypatch.setattr("app.routers.andon_alerts.get_pool", _ack_pool)

        resp = client.post(
            f"/andon/alerts/{_ALERT_ID}/acknowledge",
            json={"disposition": "resolved"},
            headers={"Authorization": f"Bearer {shift_lead_jwt}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["alertId"] == _ALERT_ID
        assert data["status"] == "acknowledged"
        assert "acknowledgedAt" in data
        assert "eventId" in data

    def test_returns_409_on_double_acknowledge(self, http_client, monkeypatch):
        client, shift_lead_jwt, _, _ = http_client

        async def _acked_pool():
            pool = MagicMock()
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(side_effect=[
                _OPEN_ALERT_ROW,
                None,  # UPDATE returns nothing — already acked
            ])
            conn.fetchval = AsyncMock(return_value="acknowledged")
            pool.acquire = MagicMock(return_value=_async_cm(conn))
            return pool

        monkeypatch.setattr("app.routers.andon_alerts.get_pool", _acked_pool)

        resp = client.post(
            f"/andon/alerts/{_ALERT_ID}/acknowledge",
            json={"disposition": "monitoring"},
            headers={"Authorization": f"Bearer {shift_lead_jwt}"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "ALREADY_ACKNOWLEDGED"
        assert "correlation_id" in body

    def test_returns_403_for_associate_role(self, http_client):
        client, _, associate_jwt, _ = http_client
        resp = client.post(
            f"/andon/alerts/{_ALERT_ID}/acknowledge",
            json={"disposition": "escalated"},
            headers={"Authorization": f"Bearer {associate_jwt}"},
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["code"] == "FORBIDDEN"
        assert "correlation_id" in body

    def test_returns_401_without_token(self, http_client):
        client, _, _, _ = http_client
        resp = client.post(
            f"/andon/alerts/{_ALERT_ID}/acknowledge",
            json={"disposition": "resolved"},
        )
        assert resp.status_code == 401

    def test_rejects_note_over_1000_chars(self, http_client):
        client, shift_lead_jwt, _, _ = http_client
        long_note = "x" * 1001
        resp = client.post(
            f"/andon/alerts/{_ALERT_ID}/acknowledge",
            json={"disposition": "resolved", "note": long_note},
            headers={"Authorization": f"Bearer {shift_lead_jwt}"},
        )
        assert resp.status_code == 422

    def test_publishes_maintenance_dispatch_and_adoption_events(self, http_client, monkeypatch):
        client, shift_lead_jwt, _, published = http_client
        ack_time = datetime(2040, 6, 1, 7, 0, 0, tzinfo=timezone.utc)

        async def _ack_pool():
            pool = MagicMock()
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(side_effect=[
                _OPEN_ALERT_ROW,
                {"acknowledged_at": ack_time},
            ])
            pool.acquire = MagicMock(return_value=_async_cm(conn))
            return pool

        monkeypatch.setattr("app.routers.andon_alerts.get_pool", _ack_pool)

        resp = client.post(
            f"/andon/alerts/{_ALERT_ID}/acknowledge",
            json={"disposition": "resolved"},
            headers={"Authorization": f"Bearer {shift_lead_jwt}"},
        )
        assert resp.status_code == 200
        topics = [t for t, _ in published]
        assert "factory.munich.line-command.maintenance-dispatch.v1" in topics
        assert "factory.munich.line-command.adoption.v1" in topics

    def test_dispatch_event_payload_shape(self, http_client, monkeypatch):
        client, shift_lead_jwt, _, published = http_client
        ack_time = datetime(2040, 6, 1, 7, 0, 0, tzinfo=timezone.utc)

        async def _ack_pool():
            pool = MagicMock()
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(side_effect=[
                _OPEN_ALERT_ROW,
                {"acknowledged_at": ack_time},
            ])
            pool.acquire = MagicMock(return_value=_async_cm(conn))
            return pool

        monkeypatch.setattr("app.routers.andon_alerts.get_pool", _ack_pool)

        resp = client.post(
            f"/andon/alerts/{_ALERT_ID}/acknowledge",
            json={"disposition": "escalated"},
            headers={"Authorization": f"Bearer {shift_lead_jwt}"},
        )
        assert resp.status_code == 200

        dispatch_events = [p for t, p in published if "maintenance-dispatch" in t]
        assert len(dispatch_events) == 1
        ev = dispatch_events[0]
        assert "eventId" in ev
        assert ev["userId"] == "sl@bmwgroup.net"
        assert ev["stationId"] == _STATION_ID
        assert ev["alertId"] == _ALERT_ID
        assert "timestamp" in ev

        adoption_events = [p for t, p in published if "adoption" in t]
        assert len(adoption_events) == 1
        adopt = adoption_events[0]
        assert adopt["actionType"] == "maintenance_dispatch"
        assert adopt["userId"] == "sl@bmwgroup.net"
        assert adopt["alertId"] == _ALERT_ID


# ---------------------------------------------------------------------------
# Unit: audit log payload shape
# ---------------------------------------------------------------------------

class TestAuditLogPayloadShape:
    def test_audit_write_fires_with_correct_fields(self, monkeypatch):
        """Audit record has user_id, MAINTENANCE_DISPATCH action, entity=alert_id, hashes."""
        from fastapi.testclient import TestClient
        from app.main import create_app

        app = create_app()
        audit_store = AuditStore()
        ack_time = datetime(2040, 6, 1, 7, 0, 0, tzinfo=timezone.utc)

        async def _ack_pool():
            pool = MagicMock()
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(side_effect=[
                _OPEN_ALERT_ROW,
                {"acknowledged_at": ack_time},
            ])
            pool.acquire = MagicMock(return_value=_async_cm(conn))
            return pool

        async def _ts_pool():
            pool = MagicMock()
            conn = AsyncMock()
            conn.fetch = AsyncMock(return_value=[])
            pool.acquire = MagicMock(return_value=_async_cm(conn))
            return pool

        async def _noop_publish(topic, payload):
            pass

        monkeypatch.setattr("app.routers.andon_alerts.get_pool", _ack_pool)
        monkeypatch.setattr("app.routers.andon_alerts.get_ts_pool", _ts_pool)
        monkeypatch.setattr("app.routers.andon_alerts.publish", _noop_publish)

        import app.routers.andon_alerts as andon_mod
        monkeypatch.setattr(andon_mod, "_process_audit_writer", audit_store)

        shift_lead_jwt = _make_jwt("sl@bmwgroup.net", ["ifos-shiftlead"])
        client = TestClient(app)
        resp = client.post(
            f"/andon/alerts/{_ALERT_ID}/acknowledge",
            json={"disposition": "resolved"},
            headers={"Authorization": f"Bearer {shift_lead_jwt}"},
        )
        assert resp.status_code == 200

        # Poll for audit write (fires on daemon thread)
        start = time.monotonic()
        while time.monotonic() - start < 1.0:
            records = [r for r in audit_store.records if r.action_type == "MAINTENANCE_DISPATCH"]
            if records:
                break
            time.sleep(0.005)

        assert records, "Audit write for MAINTENANCE_DISPATCH did not appear within 1s"
        rec = records[0]
        assert rec.user_id == "sl@bmwgroup.net"
        assert rec.action_type == "MAINTENANCE_DISPATCH"
        assert rec.entity_id == _ALERT_ID
        pre_state = json.dumps({"alert_id": _ALERT_ID, "status": "open"}, sort_keys=True)
        post_state = json.dumps({"alert_id": _ALERT_ID, "status": "acknowledged"}, sort_keys=True)
        assert rec.before_hash == sha256_hex(pre_state)
        assert rec.after_hash == sha256_hex(post_state)
        assert rec.utc_ts


# ---------------------------------------------------------------------------
# Unit: migration file existence (read the generated file, not a hand-copy)
# ---------------------------------------------------------------------------

class TestMigrationFile:
    def test_andon_alert_migration_exists(self):
        import pathlib
        migrations = pathlib.Path(__file__).parents[1] / "migrations"
        sql_files = list(migrations.glob("*_andon_alert.sql"))
        assert sql_files, "No andon_alert migration file found"

    def test_migration_contains_create_table(self):
        import pathlib
        migrations = pathlib.Path(__file__).parents[1] / "migrations"
        sql_files = list(migrations.glob("*_andon_alert.sql"))
        assert sql_files
        content = sql_files[0].read_text()
        assert "CREATE TABLE IF NOT EXISTS andon_alert" in content

    def test_migration_contains_required_indexes(self):
        import pathlib
        migrations = pathlib.Path(__file__).parents[1] / "migrations"
        sql_files = list(migrations.glob("*_andon_alert.sql"))
        content = sql_files[0].read_text()
        assert "idx_andon_alert_status_minutes" in content
        assert "idx_andon_alert_line_station" in content
        assert "idx_andon_alert_detected_at" in content
        assert "uq_andon_alert_opcenter_event_id" in content

    def test_migration_has_begin_commit(self):
        import pathlib
        migrations = pathlib.Path(__file__).parents[1] / "migrations"
        sql_files = list(migrations.glob("*_andon_alert.sql"))
        content = sql_files[0].read_text()
        assert "BEGIN;" in content
        assert "COMMIT;" in content
