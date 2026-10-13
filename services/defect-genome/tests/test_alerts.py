"""Tests for GET /defect-genome/alerts and POST /defect-genome/alerts/{alertId}/acknowledge."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.routers.alerts import get_alert_store

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FAR_FUTURE = "2099-01-01T00:00:00+00:00"
_PROCESS_REPLAY_URL = "https://quality-studio.internal.bmw-factory-os.eu/process-replay/WBS4E0108RG271034"


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
        "process_replay_url": _PROCESS_REPLAY_URL,
        "triggered_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "created_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "acknowledged_at": acknowledged_at,
        "acknowledged_by_user_id": acknowledged_by_user_id,
        "corrective_action_jira_id": corrective_action_jira_id,
    }


_VALID_JWT = (
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJ1c2VyLTEiLCJleHAiOjk5OTk5OTk5OTl9"
    "."
)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_VALID_JWT}"}


def _make_client(store: list[dict[str, Any]]) -> TestClient:
    """Create a TestClient with a fresh app backed by the given store."""
    app = create_app()
    app.dependency_overrides[get_alert_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /defect-genome/alerts
# ---------------------------------------------------------------------------

class TestListClusterAlerts:
    def test_returns_200_with_empty_list(self) -> None:
        client = _make_client([])
        resp = client.get("/defect-genome/alerts", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["page"]["next_cursor"] is None
        assert body["page"]["limit"] == 50

    def test_returns_all_alerts_when_no_filter(self) -> None:
        store = [_make_alert(status="new"), _make_alert(status="resolved")]
        client = _make_client(store)
        resp = client.get("/defect-genome/alerts", headers=_auth())
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2

    def test_filters_by_status_new(self) -> None:
        store = [_make_alert(status="new"), _make_alert(status="resolved")]
        client = _make_client(store)
        resp = client.get("/defect-genome/alerts?status=new", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["status"] == "new"

    def test_filters_by_status_acknowledged(self) -> None:
        store = [_make_alert(status="acknowledged"), _make_alert(status="in_progress")]
        client = _make_client(store)
        resp = client.get("/defect-genome/alerts?status=acknowledged", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["status"] == "acknowledged"

    def test_response_includes_all_required_fields(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id)]
        client = _make_client(store)
        resp = client.get("/defect-genome/alerts", headers=_auth())
        assert resp.status_code == 200
        row = resp.json()["data"][0]
        required = {
            "alert_id", "cluster_id", "defect_class", "status",
            "affected_vin_count", "root_cause_signature", "confidence_score",
            "most_recent_vin", "process_replay_url", "triggered_at",
            "created_at", "updated_at",
        }
        for field in required:
            assert field in row, f"missing field: {field}"

    def test_returns_401_without_bearer(self) -> None:
        client = _make_client([])
        resp = client.get("/defect-genome/alerts")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "MISSING_TOKEN"
        assert "correlation_id" in body

    def test_pagination_limit_respected(self) -> None:
        store = [_make_alert() for _ in range(5)]
        client = _make_client(store)
        resp = client.get("/defect-genome/alerts?limit=2", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["page"]["limit"] == 2
        assert body["page"]["next_cursor"] is not None

    def test_next_cursor_is_none_on_last_page(self) -> None:
        store = [_make_alert()]
        client = _make_client(store)
        resp = client.get("/defect-genome/alerts?limit=10", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["page"]["next_cursor"] is None

    def test_invalid_status_value_rejected(self) -> None:
        client = _make_client([])
        resp = client.get("/defect-genome/alerts?status=invalid_value", headers=_auth())
        assert resp.status_code == 422

    def test_limit_above_200_rejected(self) -> None:
        client = _make_client([])
        resp = client.get("/defect-genome/alerts?limit=201", headers=_auth())
        assert resp.status_code == 422

    def test_limit_below_1_rejected(self) -> None:
        client = _make_client([])
        resp = client.get("/defect-genome/alerts?limit=0", headers=_auth())
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /defect-genome/alerts/{alertId}/acknowledge
# ---------------------------------------------------------------------------

class TestAcknowledgeClusterAlert:
    def test_acknowledge_new_alert_returns_200_with_acknowledged_status(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id, status="new")]
        client = _make_client(store)
        resp = client.post(
            f"/defect-genome/alerts/{alert_id}/acknowledge",
            json={"acknowledged_by_user_id": "user-42"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "acknowledged"
        assert body["alert_id"] == alert_id
        assert body["acknowledged_by_user_id"] == "user-42"
        assert body["acknowledged_at"] is not None

    def test_acknowledge_with_jira_id_sets_in_progress(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id, status="new")]
        client = _make_client(store)
        resp = client.post(
            f"/defect-genome/alerts/{alert_id}/acknowledge",
            json={
                "acknowledged_by_user_id": "user-42",
                "corrective_action_jira_id": "IFOSQUALITY-101",
            },
            headers=_auth(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "in_progress"
        assert body["corrective_action_jira_id"] == "IFOSQUALITY-101"

    def test_acknowledge_already_acknowledged_returns_409(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id, status="acknowledged")]
        client = _make_client(store)
        resp = client.post(
            f"/defect-genome/alerts/{alert_id}/acknowledge",
            json={"acknowledged_by_user_id": "user-42"},
            headers=_auth(),
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "ALREADY_ACKNOWLEDGED"
        assert "correlation_id" in body

    def test_acknowledge_in_progress_returns_409(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id, status="in_progress")]
        client = _make_client(store)
        resp = client.post(
            f"/defect-genome/alerts/{alert_id}/acknowledge",
            json={"acknowledged_by_user_id": "user-42"},
            headers=_auth(),
        )
        assert resp.status_code == 409

    def test_acknowledge_resolved_returns_409(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id, status="resolved")]
        client = _make_client(store)
        resp = client.post(
            f"/defect-genome/alerts/{alert_id}/acknowledge",
            json={"acknowledged_by_user_id": "user-42"},
            headers=_auth(),
        )
        assert resp.status_code == 409

    def test_acknowledge_unknown_alert_returns_404(self) -> None:
        client = _make_client([])
        unknown_id = str(uuid.uuid4())
        resp = client.post(
            f"/defect-genome/alerts/{unknown_id}/acknowledge",
            json={"acknowledged_by_user_id": "user-42"},
            headers=_auth(),
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "ALERT_NOT_FOUND"
        assert "correlation_id" in body

    def test_acknowledge_invalid_uuid_returns_400(self) -> None:
        client = _make_client([])
        resp = client.post(
            "/defect-genome/alerts/not-a-uuid/acknowledge",
            json={"acknowledged_by_user_id": "user-42"},
            headers=_auth(),
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "INVALID_ALERT_ID"

    def test_acknowledge_missing_body_returns_422(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id, status="new")]
        client = _make_client(store)
        resp = client.post(
            f"/defect-genome/alerts/{alert_id}/acknowledge",
            json={},
            headers=_auth(),
        )
        assert resp.status_code == 422

    def test_acknowledge_returns_401_without_bearer(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id, status="new")]
        client = _make_client(store)
        resp = client.post(
            f"/defect-genome/alerts/{alert_id}/acknowledge",
            json={"acknowledged_by_user_id": "user-42"},
        )
        assert resp.status_code == 401

    def test_response_includes_all_required_cluster_alert_fields(self) -> None:
        alert_id = str(uuid.uuid4())
        store = [_make_alert(alert_id=alert_id, status="new")]
        client = _make_client(store)
        resp = client.post(
            f"/defect-genome/alerts/{alert_id}/acknowledge",
            json={"acknowledged_by_user_id": "user-42"},
            headers=_auth(),
        )
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

    def test_each_instance_starts_with_independent_store(self) -> None:
        """Verify that separate TestClient instances do not share mutable store state."""
        store_a: list[dict[str, Any]] = []
        store_b: list[dict[str, Any]] = []
        client_a = _make_client(store_a)
        client_b = _make_client(store_b)

        resp_a = client_a.get("/defect-genome/alerts", headers=_auth())
        resp_b = client_b.get("/defect-genome/alerts", headers=_auth())

        assert resp_a.json()["data"] == []
        assert resp_b.json()["data"] == []
        # Mutate store_a only
        store_a.append(_make_alert())
        resp_a2 = client_a.get("/defect-genome/alerts", headers=_auth())
        resp_b2 = client_b.get("/defect-genome/alerts", headers=_auth())
        assert len(resp_a2.json()["data"]) == 1
        assert len(resp_b2.json()["data"]) == 0


# ---------------------------------------------------------------------------
# alerts_service unit tests
# ---------------------------------------------------------------------------

class TestAlertsService:
    def test_encode_decode_cursor_roundtrip(self) -> None:
        from app.alerts_service import decode_cursor, encode_cursor
        original = str(uuid.uuid4())
        assert decode_cursor(encode_cursor(original)) == original

    def test_decode_cursor_returns_none_on_garbage(self) -> None:
        from app.alerts_service import decode_cursor
        assert decode_cursor("!!!not-base64!!!") is None

    def test_build_alert_list_response_paginates(self) -> None:
        from app.alerts_service import build_alert_list_response
        rows = [_make_alert() for _ in range(3)]
        result = build_alert_list_response(rows, limit=2)
        assert len(result["data"]) == 2
        assert result["page"]["next_cursor"] is not None
        assert result["page"]["limit"] == 2

    def test_build_alert_list_response_last_page(self) -> None:
        from app.alerts_service import build_alert_list_response
        rows = [_make_alert()]
        result = build_alert_list_response(rows, limit=10)
        assert result["page"]["next_cursor"] is None
