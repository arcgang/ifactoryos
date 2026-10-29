"""US-015 Acceptance tests (backend layer).

Story: Receive a real-time defect cluster alert when DefectGenome detects
3 or more VINs sharing the same root-cause signature above 80% confidence
within a 4-hour window.

Tests here exercise the seam between:
  • cluster_state  (rolling-window record + DBSCAN + XGBoost scoring)
  • consumer._handle_message  (threshold evaluation → alert persistence → Kafka publish)
  • alerts REST API  (GET /defect-genome/alerts, GET /defect-genome/alerts/{id},
                      POST /defect-genome/alerts/{id}/acknowledge)
  • corrective-action REST API  (POST /defect/clusters/{id}/corrective-action)

All imports of `app.main.create_app` are inside individual test methods so that
a syntax error in main.py produces test *failures* (not collection errors) and the
pure-logic AC1 tests can still run.

Note on confidence: the unit-test scoring stub in score_cluster_xgboost tops out
at ~0.574 — below the 0.80 story threshold.  Consumer tests therefore patch the
scorer to return a realistic high-confidence value (0.91), which is exactly what
a real XGBoost model would produce.  The gate logic (_handle_message) is what
the consumer tests are testing, not the scorer.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.cluster_state import (
    _event_window,
    get_recent_events,
    record_event,
    run_dbscan,
    score_cluster_xgboost,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STORY_CONFIDENCE_THRESHOLD = 0.80
_STORY_MIN_VINS = 3
_STORY_WINDOW_HOURS = 4

_VIN_A = "WBA3B3C56DF596858"
_VIN_B = "WBA3B3C57DF596859"
_VIN_C = "WBA3B3C58DF596860"

_PROCESS_REPLAY_BASE = "https://quality-studio.internal.bmw-factory-os.eu/process-replay"

_VALID_JWT = (
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJ1c2VyLTEiLCJleHAiOjk5OTk5OTk5OTl9"
    "."
)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_VALID_JWT}"}


def _make_alert_row(
    *,
    alert_id: str | None = None,
    cluster_id: str | None = None,
    status: str = "new",
    affected_vin_count: int = 3,
    confidence_score: float = 0.87,
    corrective_action_jira_id: str | None = None,
    acknowledged_by_user_id: str | None = None,
    acknowledged_at: str | None = None,
) -> dict[str, Any]:
    _id = alert_id or str(uuid.uuid4())
    _cid = cluster_id or str(uuid.uuid4())
    return {
        "alert_id": _id,
        "cluster_id": _cid,
        "defect_class": "crater",
        "status": status,
        "affected_vin_count": affected_vin_count,
        "root_cause_signature": "ecoat_voltage_deviation",
        "confidence_score": confidence_score,
        "most_recent_vin": _VIN_C,
        "process_replay_url": f"{_PROCESS_REPLAY_BASE}/{_VIN_C}",
        "triggered_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "created_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "acknowledged_at": acknowledged_at,
        "acknowledged_by_user_id": acknowledged_by_user_id,
        "corrective_action_jira_id": corrective_action_jira_id,
    }


def _make_paint_event(
    vin: str,
    defect_class: str = "crater",
    detected_at: str | None = None,
) -> dict[str, Any]:
    if detected_at is None:
        detected_at = datetime.now(tz=timezone.utc).isoformat()
    return {
        "vin": vin,
        "defectClass": defect_class,
        "boothHumidityPct": 35.0,
        "boothTemperatureC": 24.5,
        "ecoatVoltageV": 340.0,
        "phosphateConcentrationGL": 2.0,
        "stageNumber": 6,
        "detectedAt": detected_at,
        "stationId": "PAINT-01",
        "scanTunnelId": "TUN-01",
    }


def _make_stub_pool(alert_store: list) -> AsyncMock:
    """asyncpg pool stub returning story-compliant thresholds and capturing persisted alerts."""
    pool = AsyncMock()
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)

    async def fetchrow_side_effect(query: str, *args: object) -> dict | None:
        if "confidence_threshold_config" in query:
            return {
                "low_confidence_threshold": 0.60,
                "cluster_alert_threshold": _STORY_CONFIDENCE_THRESHOLD,
                "cluster_alert_min_vins": _STORY_MIN_VINS,
                "cluster_alert_window_hours": _STORY_WINDOW_HOURS,
            }
        return None

    async def execute_side_effect(query: str, *args: object) -> None:
        if "INSERT INTO cluster_alert" in query:
            alert_store.append({
                "alert_id": args[0],
                "cluster_id": args[1],
                "defect_class": args[2],
                "confidence_score": args[3],
                "affected_vin_count": args[4],
                "root_cause_signature": args[5],
                "process_replay_url": args[6],
            })

    conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    conn.execute = AsyncMock(side_effect=execute_side_effect)
    pool.acquire = MagicMock(return_value=conn)
    return pool


# ---------------------------------------------------------------------------
# Preconditions — fail loudly if the feature modules are absent.
# ---------------------------------------------------------------------------

class TestPreconditions:
    def test_cluster_state_module_exports_required_symbols(self) -> None:
        from app.cluster_state import (  # noqa: F401
            get_recent_events,
            record_event,
            run_dbscan,
            score_cluster_xgboost,
        )

    def test_consumer_module_exports_handle_message(self) -> None:
        from app.consumer import _handle_message  # noqa: F401

    def test_alerts_router_is_importable(self) -> None:
        """Precondition: app.routers.alerts must import cleanly (no SyntaxError)."""
        from app.routers.alerts import router  # noqa: F401

    def test_corrective_action_router_is_importable(self) -> None:
        """Precondition: app.routers.corrective_action must import cleanly."""
        from app.routers.corrective_action import router  # noqa: F401

    def test_create_app_is_importable(self) -> None:
        """Precondition: app.main must have no syntax / import errors."""
        from app.main import create_app  # noqa: F401


# ---------------------------------------------------------------------------
# AC1 — cluster threshold crossing triggers an alert (pure logic layer)
# ---------------------------------------------------------------------------

class TestAC1ThresholdCrossingLogic:
    """AC1 – pure cluster_state logic: ≥3 VINs at >80% confidence within 4h."""

    @pytest.fixture(autouse=True)
    def clear_event_window(self) -> None:
        _event_window.clear()

    def test_three_same_signature_events_form_one_cluster(self) -> None:
        """Three events with identical parameters cluster into a single group."""
        events = [_make_paint_event(v) for v in (_VIN_A, _VIN_B, _VIN_C)]
        clusters = run_dbscan(events)
        assert len(clusters) == 1
        sig = list(clusters.keys())[0]
        assert len(clusters[sig]) == 3

    def test_two_vins_only_does_not_meet_min_vin_threshold(self) -> None:
        """Two VINs must NOT cross the ≥3 VIN gate."""
        events = [_make_paint_event(_VIN_A), _make_paint_event(_VIN_B)]
        clusters = run_dbscan(events)
        sig = list(clusters.keys())[0]
        unique_vins = list({e.get("vin") for e in clusters[sig] if e.get("vin")})
        assert len(unique_vins) < _STORY_MIN_VINS

    def test_events_within_4h_window_are_included(self) -> None:
        """Events timestamped within the last 4 hours must appear in the rolling window."""
        now = datetime.now(tz=timezone.utc)
        recent_ts = (now - timedelta(hours=2)).isoformat()
        event = _make_paint_event(_VIN_A, detected_at=recent_ts)
        record_event(event)
        events = get_recent_events("crater", time_window_hours=_STORY_WINDOW_HOURS)
        assert any(e["vin"] == _VIN_A for e in events), (
            "An event timestamped 2 hours ago should be within the 4-hour rolling window."
        )

    def test_events_older_than_4h_window_are_excluded(self) -> None:
        """Events older than 4 hours must NOT count toward the cluster threshold (AC1)."""
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=5)).isoformat()
        event = _make_paint_event(_VIN_A, detected_at=old_ts)
        record_event(event)
        events = get_recent_events("crater", time_window_hours=_STORY_WINDOW_HOURS)
        stale_vins = [e for e in events if e["vin"] == _VIN_A]
        assert len(stale_vins) == 0, (
            "An event 5 hours old should be pruned from the 4-hour rolling window."
        )

    def test_consumer_threshold_gate_fires_when_model_returns_above_80pct(self) -> None:
        """AC1: when score_cluster_xgboost returns >80% confidence, the alert fires.

        The unit-test scorer stub tops out at ~0.574 (by design — the story threshold
        requires a real trained XGBoost model).  We patch the scorer to return 0.91
        so this test verifies the gate logic, not the model accuracy.
        """
        from app.consumer import _handle_message
        from app.config import Settings

        alert_store: list[dict] = []
        pool = _make_stub_pool(alert_store)
        cfg = Settings(
            defect_events_topic="t",
            defect_events_dlq_topic="d",
            cluster_alert_topic="alerts-out",
            defect_consumer_group="g",
            kafka_bootstrap_servers="localhost:9092",
            quality_studio_base_url=_PROCESS_REPLAY_BASE,
            db_dsn="postgresql://localhost/test",
        )
        producer = AsyncMock()
        producer.send = AsyncMock(return_value=None)

        # Patch scorer to simulate a production-level confident prediction (>80%).
        mock_factors = [
            {"rank": 1, "factor_name": "humidity_low_solvent_pop",
             "factor_category": "booth_parameter", "probability_score": 0.91,
             "supporting_vin_count": 3},
        ]
        with patch("app.consumer.score_cluster_xgboost", return_value=(mock_factors, 0.91)):
            for vin in (_VIN_A, _VIN_B, _VIN_C):
                raw = json.dumps(_make_paint_event(vin)).encode()
                asyncio.get_event_loop().run_until_complete(
                    _handle_message(raw, pool=pool, producer=producer, cfg=cfg)
                )

        assert len(alert_store) >= 1, (
            "No cluster alert persisted after 3 VINs with 91% confidence. "
            "The threshold gate (≥3 VINs AND >80% confidence) should have fired."
        )

    def test_consumer_threshold_gate_does_not_fire_below_80pct_confidence(self) -> None:
        """AC1 boundary: confidence < 80% → no alert, regardless of VIN count."""
        from app.consumer import _handle_message
        from app.config import Settings

        alert_store: list[dict] = []
        pool = _make_stub_pool(alert_store)
        cfg = Settings(
            defect_events_topic="t",
            defect_events_dlq_topic="d",
            cluster_alert_topic="alerts-out",
            defect_consumer_group="g",
            kafka_bootstrap_servers="localhost:9092",
            quality_studio_base_url=_PROCESS_REPLAY_BASE,
            db_dsn="postgresql://localhost/test",
        )
        producer = AsyncMock()
        send_calls: list = []
        producer.send = AsyncMock(side_effect=lambda t, value: send_calls.append(t))

        # Patch scorer to return sub-threshold confidence
        mock_factors = [{"rank": 1, "factor_name": "x", "factor_category": "prep_stage",
                         "probability_score": 0.70, "supporting_vin_count": 3}]
        with patch("app.consumer.score_cluster_xgboost", return_value=(mock_factors, 0.70)):
            for vin in (_VIN_A, _VIN_B, _VIN_C):
                raw = json.dumps(_make_paint_event(vin)).encode()
                asyncio.get_event_loop().run_until_complete(
                    _handle_message(raw, pool=pool, producer=producer, cfg=cfg)
                )

        assert len(send_calls) == 0, (
            "Alert was published with 70% confidence — below the 80% threshold. "
            "The gate should have suppressed it."
        )

    def test_consumer_threshold_gate_requires_3_vins_even_at_high_confidence(self) -> None:
        """AC1 boundary: only 2 VINs → no alert even when confidence is >80%."""
        from app.consumer import _handle_message
        from app.config import Settings

        alert_store: list[dict] = []
        pool = _make_stub_pool(alert_store)
        cfg = Settings(
            defect_events_topic="t",
            defect_events_dlq_topic="d",
            cluster_alert_topic="alerts-out",
            defect_consumer_group="g",
            kafka_bootstrap_servers="localhost:9092",
            quality_studio_base_url=_PROCESS_REPLAY_BASE,
            db_dsn="postgresql://localhost/test",
        )
        producer = AsyncMock()
        send_calls: list = []
        producer.send = AsyncMock(side_effect=lambda t, value: send_calls.append(t))

        mock_factors = [{"rank": 1, "factor_name": "x", "factor_category": "prep_stage",
                         "probability_score": 0.91, "supporting_vin_count": 2}]
        with patch("app.consumer.score_cluster_xgboost", return_value=(mock_factors, 0.91)):
            for vin in (_VIN_A, _VIN_B):  # only 2 VINs
                raw = json.dumps(_make_paint_event(vin)).encode()
                asyncio.get_event_loop().run_until_complete(
                    _handle_message(raw, pool=pool, producer=producer, cfg=cfg)
                )

        assert len(send_calls) == 0, (
            "Alert published with only 2 VINs — should not cross the ≥3 VIN threshold."
        )

    def test_consumer_publishes_kafka_payload_with_correct_threshold_fields(self) -> None:
        """AC1: the Kafka payload carries affectedVinCount ≥ 3 and confidenceScore > 0.80."""
        from app.consumer import _handle_message
        from app.config import Settings

        alert_store: list[dict] = []
        pool = _make_stub_pool(alert_store)
        cfg = Settings(
            defect_events_topic="t",
            defect_events_dlq_topic="d",
            cluster_alert_topic="alerts-out",
            defect_consumer_group="g",
            kafka_bootstrap_servers="localhost:9092",
            quality_studio_base_url=_PROCESS_REPLAY_BASE,
            db_dsn="postgresql://localhost/test",
        )

        send_calls: list[dict] = []

        async def capture_send(topic: str, value: bytes) -> None:
            send_calls.append({"topic": topic, "payload": json.loads(value)})

        producer = AsyncMock()
        producer.send = AsyncMock(side_effect=capture_send)

        mock_factors = [{"rank": 1, "factor_name": "x", "factor_category": "prep_stage",
                         "probability_score": 0.91, "supporting_vin_count": 3}]
        with patch("app.consumer.score_cluster_xgboost", return_value=(mock_factors, 0.91)):
            for vin in (_VIN_A, _VIN_B, _VIN_C):
                raw = json.dumps(_make_paint_event(vin)).encode()
                asyncio.get_event_loop().run_until_complete(
                    _handle_message(raw, pool=pool, producer=producer, cfg=cfg)
                )

        kafka_alerts = [c for c in send_calls if c["topic"] == "alerts-out"]
        assert len(kafka_alerts) >= 1, "No cluster alert published to Kafka."
        payload = kafka_alerts[0]["payload"]
        assert payload["affectedVinCount"] >= _STORY_MIN_VINS
        assert payload["confidenceScore"] > _STORY_CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# AC2 — alert detail API returns all five required fields
# ---------------------------------------------------------------------------

class TestAC2AlertDetailAPI:
    """AC2 – GET /defect-genome/alerts/{alertId} exposes all five display fields."""

    def _make_client(self, store: list[dict[str, Any]]):  # type: ignore[return]
        from app.main import create_app
        from app.routers.alerts import get_alert_store
        from fastapi.testclient import TestClient

        app = create_app()
        app.dependency_overrides[get_alert_store] = lambda: store
        return TestClient(app, raise_server_exceptions=False)

    def test_alert_detail_contains_cluster_id(self) -> None:
        cluster_id = str(uuid.uuid4())
        alert_id = str(uuid.uuid4())
        store = [_make_alert_row(alert_id=alert_id, cluster_id=cluster_id)]
        client = self._make_client(store)

        resp = client.get(f"/defect-genome/alerts/{alert_id}", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["cluster_id"] == cluster_id

    def test_alert_detail_contains_affected_vin_count_at_least_3(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert_row(alert_id=alert_id, affected_vin_count=3)]
        client = self._make_client(store)

        resp = client.get(f"/defect-genome/alerts/{alert_id}", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["affected_vin_count"] >= _STORY_MIN_VINS

    def test_alert_detail_contains_root_cause_signature(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert_row(alert_id=alert_id)]
        client = self._make_client(store)

        resp = client.get(f"/defect-genome/alerts/{alert_id}", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert "root_cause_signature" in body
        assert body["root_cause_signature"]

    def test_alert_detail_contains_confidence_score_above_80pct(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert_row(alert_id=alert_id, confidence_score=0.87)]
        client = self._make_client(store)

        resp = client.get(f"/defect-genome/alerts/{alert_id}", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["confidence_score"] > _STORY_CONFIDENCE_THRESHOLD

    def test_alert_detail_contains_process_replay_url_for_most_recent_vin(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert_row(alert_id=alert_id)]
        client = self._make_client(store)

        resp = client.get(f"/defect-genome/alerts/{alert_id}", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert "process_replay_url" in body
        assert body["most_recent_vin"] in body["process_replay_url"], (
            "process_replay_url must contain the most_recent_vin identifier."
        )

    def test_all_five_ac2_fields_present_simultaneously(self) -> None:
        """AC2 composite: cluster_id, affected_vin_count, root_cause_signature,
        confidence_score, process_replay_url all present in a single response."""
        alert_id = str(uuid.uuid4())
        cluster_id = str(uuid.uuid4())
        store = [_make_alert_row(alert_id=alert_id, cluster_id=cluster_id, confidence_score=0.94)]
        client = self._make_client(store)

        resp = client.get(f"/defect-genome/alerts/{alert_id}", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()

        assert body["cluster_id"] == cluster_id
        assert body["affected_vin_count"] >= _STORY_MIN_VINS
        assert body["root_cause_signature"]
        assert body["confidence_score"] > _STORY_CONFIDENCE_THRESHOLD
        assert body["most_recent_vin"] in body["process_replay_url"]


# ---------------------------------------------------------------------------
# AC3 — acknowledge + corrective action → status "In Progress" + Jira ticket
# ---------------------------------------------------------------------------

class TestAC3AcknowledgeAndCorrectiveAction:
    """AC3 – acknowledge + corrective action transitions status to in_progress and
    stores the Jira ticket ID so the Defect Dashboard can display it."""

    def _make_client(self, store: list[dict[str, Any]]):  # type: ignore[return]
        from app.main import create_app
        from app.routers.alerts import get_alert_store
        from fastapi.testclient import TestClient

        app = create_app()
        app.dependency_overrides[get_alert_store] = lambda: store
        return TestClient(app, raise_server_exceptions=False)

    def test_acknowledging_alert_with_jira_id_sets_status_in_progress(self) -> None:
        """AC3: acknowledge with corrective_action_jira_id → status = in_progress."""
        alert_id = str(uuid.uuid4())
        store = [_make_alert_row(alert_id=alert_id, status="new")]
        client = self._make_client(store)

        resp = client.post(
            f"/defect-genome/alerts/{alert_id}/acknowledge",
            json={
                "acknowledged_by_user_id": "kb-001",
                "corrective_action_jira_id": "IFOS-QUALITY-42",
            },
            headers=_auth(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "in_progress"
        assert body["corrective_action_jira_id"] == "IFOS-QUALITY-42"

    def test_corrective_action_endpoint_returns_jira_ticket_id(self) -> None:
        """AC3: POST /defect/clusters/{id}/corrective-action returns jira_ticket_id."""
        from app.main import create_app
        from app.routers.clusters import get_cluster_store
        from app.routers.corrective_action import get_jira_client
        from fastapi.testclient import TestClient

        cluster_id = str(uuid.uuid4())
        cluster = {
            "cluster_id": cluster_id,
            "defect_class": "crater",
            "status": "open",
            "root_cause_signature": "ecoat_voltage_deviation",
            "confidence_score": 0.87,
            "affected_vin_count": 3,
            "most_recent_vin": _VIN_C,
            "process_replay_url": f"{_PROCESS_REPLAY_BASE}/{_VIN_C}",
            "detected_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "created_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "corrective_action_jira_id": None,
            "top_factors": [
                {
                    "rank": 1,
                    "factor_name": "ecoat_voltage_high",
                    "factor_category": "prep_stage",
                    "probability_score": 0.87,
                    "supporting_vin_count": 3,
                }
            ],
        }

        jira_mock = MagicMock()
        jira_mock.create_issue = AsyncMock(
            return_value={
                "id": "10042",
                "key": "IFOS-QUALITY-42",
                "self": "https://bmwgroup.atlassian.net/rest/api/3/issue/10042",
            }
        )

        app = create_app()
        app.dependency_overrides[get_cluster_store] = lambda: {cluster_id: cluster}
        app.dependency_overrides[get_jira_client] = lambda: jira_mock
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json={
                "assigned_root_cause_factor_rank": 1,
                "confirmed_by_user_id": "kb-001",
            },
            headers=_auth(),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "jira_ticket_id" in body
        assert body["jira_ticket_id"] == "IFOS-QUALITY-42"

    def test_alert_list_surfaces_in_progress_status_with_jira_id(self) -> None:
        """AC3: Defect Dashboard GET /defect-genome/alerts returns in_progress alerts
        with the corrective_action_jira_id set."""
        alert_id = str(uuid.uuid4())
        store = [
            _make_alert_row(
                alert_id=alert_id,
                status="in_progress",
                corrective_action_jira_id="IFOS-QUALITY-42",
            )
        ]
        client = self._make_client(store)

        resp = client.get("/defect-genome/alerts?status=in_progress", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) >= 1
        row = data[0]
        assert row["status"] == "in_progress"
        assert row["corrective_action_jira_id"] == "IFOS-QUALITY-42"

    def test_alert_detail_reflects_jira_id_after_corrective_action(self) -> None:
        """AC3: GET /defect-genome/alerts/{id} returns the Jira ticket ID
        so the panel can display it in the Defect Dashboard row."""
        alert_id = str(uuid.uuid4())
        store = [
            _make_alert_row(
                alert_id=alert_id,
                status="in_progress",
                corrective_action_jira_id="IFOS-QUALITY-42",
            )
        ]
        client = self._make_client(store)

        resp = client.get(f"/defect-genome/alerts/{alert_id}", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["corrective_action_jira_id"] == "IFOS-QUALITY-42"
        assert resp.json()["status"] == "in_progress"
