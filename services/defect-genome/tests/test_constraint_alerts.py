"""Tests for GET /alerts, GET /alerts/{alertId}, POST /alerts/{alertId}/acknowledge."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.routers.alerts import get_alert_store

_VALID_JWT = (
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJ1c2VyLTEiLCJleHAiOjk5OTk5OTk5OTl9"
    "."
)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_VALID_JWT}"}


def _make_alert(
    *,
    alert_id: str | None = None,
    cluster_id: str | None = None,
    status: str = "new",
    defect_class: str = "crater",
    affected_vin_count: int = 4,
    confidence_score: float = 0.87,
    corrective_action_jira_id: str | None = None,
    acknowledged_by_user_id: str | None = None,
    acknowledged_at: str | None = None,
) -> dict[str, Any]:
    return {
        "alert_id": alert_id or str(uuid.uuid4()),
        "cluster_id": cluster_id or str(uuid.uuid4()),
        "defect_class": defect_class,
        "status": status,
        "affected_vin_count": affected_vin_count,
        "root_cause_signature": "ecoat_voltage_deviation",
        "confidence_score": confidence_score,
        "most_recent_vin": "WBS4E0108RG271034",
        "process_replay_url": "https://quality-studio.internal.bmw-factory-os.eu/process-replay/WBS4E0108RG271034",
        "triggered_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "created_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "acknowledged_at": acknowledged_at,
        "acknowledged_by_user_id": acknowledged_by_user_id,
        "corrective_action_jira_id": corrective_action_jira_id,
        "constraint_type": "paint_capacity",
        "current_value": 1.05,
        "threshold_value": 1.0,
        "affected_vins": ["WBS4E0108RG271034"],
    }


def _make_client(store: list[dict[str, Any]]) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_alert_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /alerts
# ---------------------------------------------------------------------------


class TestListAlerts:
    def test_returns_200_with_empty_list(self) -> None:
        client = _make_client([])
        resp = client.get("/alerts", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["page"]["next_cursor"] is None

    def test_default_status_filter_active_returns_new_alerts(self) -> None:
        store = [_make_alert(status="new"), _make_alert(status="resolved")]
        client = _make_client(store)
        resp = client.get("/alerts", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["status"] == "new"

    def test_status_all_returns_every_alert(self) -> None:
        store = [
            _make_alert(status="new"),
            _make_alert(status="acknowledged"),
            _make_alert(status="resolved"),
        ]
        client = _make_client(store)
        resp = client.get("/alerts?status=all", headers=_auth())
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 3

    def test_status_acknowledged_filters_correctly(self) -> None:
        store = [_make_alert(status="new"), _make_alert(status="acknowledged")]
        client = _make_client(store)
        resp = client.get("/alerts?status=acknowledged", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["status"] == "acknowledged"

    def test_returns_401_without_bearer(self) -> None:
        client = _make_client([])
        resp = client.get("/alerts")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "MISSING_TOKEN"
        assert "correlation_id" in body

    def test_pagination_limit_respected(self) -> None:
        store = [_make_alert(status="new") for _ in range(5)]
        client = _make_client(store)
        resp = client.get("/alerts?status=all&limit=2", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["page"]["limit"] == 2
        assert body["page"]["next_cursor"] is not None

    def test_next_cursor_is_none_on_last_page(self) -> None:
        store = [_make_alert(status="new")]
        client = _make_client(store)
        resp = client.get("/alerts?status=all&limit=10", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["page"]["next_cursor"] is None

    def test_invalid_status_value_rejected(self) -> None:
        client = _make_client([])
        resp = client.get("/alerts?status=invalid_value", headers=_auth())
        assert resp.status_code == 422

    def test_limit_above_200_rejected(self) -> None:
        client = _make_client([])
        resp = client.get("/alerts?limit=201", headers=_auth())
        assert resp.status_code == 422

    def test_limit_below_1_rejected(self) -> None:
        client = _make_client([])
        resp = client.get("/alerts?limit=0", headers=_auth())
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /alerts/{alertId}
# ---------------------------------------------------------------------------


class TestGetAlert:
    def test_returns_200_with_full_alert_body(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id)]
        client = _make_client(store)
        resp = client.get(f"/alerts/{alert_id}", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["alert_id"] == alert_id

    def test_response_contains_all_required_fields(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id)]
        client = _make_client(store)
        resp = client.get(f"/alerts/{alert_id}", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        required = {
            "alert_id", "cluster_id", "defect_class", "status",
            "affected_vin_count", "root_cause_signature", "confidence_score",
            "most_recent_vin", "process_replay_url", "triggered_at",
            "created_at", "updated_at",
        }
        for field in required:
            assert field in body, f"missing field: {field}"

    def test_returns_404_for_unknown_alert_id(self) -> None:
        client = _make_client([])
        unknown_id = str(uuid.uuid4())
        resp = client.get(f"/alerts/{unknown_id}", headers=_auth())
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "ALERT_NOT_FOUND"
        assert "correlation_id" in body

    def test_returns_400_for_non_uuid_alert_id(self) -> None:
        client = _make_client([])
        resp = client.get("/alerts/not-a-uuid", headers=_auth())
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "INVALID_ALERT_ID"

    def test_returns_401_without_bearer(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id)]
        client = _make_client(store)
        resp = client.get(f"/alerts/{alert_id}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /alerts/{alertId}/acknowledge
# ---------------------------------------------------------------------------


class TestAcknowledgeAlert:
    def test_acknowledge_new_alert_returns_200_with_constraint_alert_shape(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id, status="new")]
        client = _make_client(store)
        resp = client.post(
            f"/alerts/{alert_id}/acknowledge",
            headers=_auth(),
        )
        assert resp.status_code == 200
        body = resp.json()
        # ConstraintAlert shape (camelCase)
        assert "alertId" in body
        assert body["alertId"] == alert_id
        assert "constraintType" in body
        assert "currentValue" in body
        assert "thresholdValue" in body
        assert "affectedVins" in body
        assert "status" in body
        assert "detectedAt" in body

    def test_acknowledge_sets_status_to_acknowledged(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id, status="new")]
        client = _make_client(store)
        resp = client.post(f"/alerts/{alert_id}/acknowledge", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "acknowledged"

    def test_constraint_alert_status_values_are_valid(self) -> None:
        """Status field must be active, acknowledged, or resolved."""
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id, status="new")]
        client = _make_client(store)
        resp = client.post(f"/alerts/{alert_id}/acknowledge", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("active", "acknowledged", "resolved")

    def test_returns_404_for_unknown_alert(self) -> None:
        client = _make_client([])
        unknown_id = str(uuid.uuid4())
        resp = client.post(f"/alerts/{unknown_id}/acknowledge", headers=_auth())
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "ALERT_NOT_FOUND"

    def test_returns_400_for_non_uuid_alert_id(self) -> None:
        client = _make_client([])
        resp = client.post("/alerts/not-a-uuid/acknowledge", headers=_auth())
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "INVALID_ALERT_ID"

    def test_returns_401_without_bearer(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id, status="new")]
        client = _make_client(store)
        resp = client.post(f"/alerts/{alert_id}/acknowledge")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "MISSING_TOKEN"

    def test_acknowledge_already_acknowledged_is_idempotent(self) -> None:
        # POST /alerts/{alertId}/acknowledge has no body and no 409 in the LLD
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id, status="acknowledged")]
        client = _make_client(store)
        resp = client.post(f"/alerts/{alert_id}/acknowledge", headers=_auth())
        # Already acknowledged — returns 200 (idempotent)
        assert resp.status_code == 200
