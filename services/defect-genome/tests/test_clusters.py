"""Tests for GET /defect/clusters and GET /defect/clusters/{clusterId}."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.routers.clusters import get_cluster_store

_VALID_JWT = (
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJ1c2VyLTEiLCJleHAiOjk5OTk5OTk5OTl9"
    "."
)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_VALID_JWT}"}


def _make_cluster(
    *,
    cluster_id: str | None = None,
    defect_class: str = "crater",
    status: str = "open",
    confidence_score: float = 0.87,
    affected_vin_count: int = 4,
    corrective_action_jira_id: str | None = None,
) -> dict[str, Any]:
    return {
        "cluster_id": cluster_id or str(uuid.uuid4()),
        "defect_class": defect_class,
        "status": status,
        "root_cause_signature": "ecoat_voltage_deviation",
        "confidence_score": confidence_score,
        "affected_vin_count": affected_vin_count,
        "most_recent_vin": "WBS4E0108RG271034",
        "process_replay_url": "https://quality-studio.internal.bmw-factory-os.eu/process-replay/WBS4E0108RG271034",
        "detected_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "created_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "corrective_action_jira_id": corrective_action_jira_id,
        "top_factors": [
            {
                "rank": 1,
                "factor_name": "e-coat voltage drop at stage 6",
                "factor_category": "prep_stage",
                "probability_score": 0.74,
                "supporting_vin_count": 3,
            }
        ],
    }


def _make_client(store: list[dict[str, Any]]) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_cluster_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /defect/clusters
# ---------------------------------------------------------------------------


class TestListClusters:
    def test_returns_200_with_empty_list(self) -> None:
        client = _make_client([])
        resp = client.get("/defect/clusters", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["page"]["next_cursor"] is None
        assert body["page"]["limit"] == 50

    def test_returns_all_clusters_when_no_filter(self) -> None:
        store = [_make_cluster(status="open"), _make_cluster(status="resolved")]
        client = _make_client(store)
        resp = client.get("/defect/clusters", headers=_auth())
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2

    def test_filters_by_defect_class(self) -> None:
        store = [_make_cluster(defect_class="dust"), _make_cluster(defect_class="crater")]
        client = _make_client(store)
        resp = client.get("/defect/clusters?defectClass=dust", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["defect_class"] == "dust"

    def test_filters_by_status(self) -> None:
        store = [_make_cluster(status="open"), _make_cluster(status="resolved")]
        client = _make_client(store)
        resp = client.get("/defect/clusters?status=open", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["status"] == "open"

    def test_response_includes_all_required_fields(self) -> None:
        cluster_id = str(uuid.uuid4())
        store = [_make_cluster(cluster_id=cluster_id)]
        client = _make_client(store)
        resp = client.get("/defect/clusters", headers=_auth())
        assert resp.status_code == 200
        row = resp.json()["data"][0]
        required = {
            "cluster_id", "defect_class", "status", "root_cause_signature",
            "confidence_score", "affected_vin_count", "most_recent_vin",
            "detected_at", "created_at", "updated_at",
        }
        for field in required:
            assert field in row, f"missing field: {field}"

    def test_returns_401_without_bearer(self) -> None:
        client = _make_client([])
        resp = client.get("/defect/clusters")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "MISSING_TOKEN"
        assert "correlation_id" in body

    def test_pagination_limit_respected(self) -> None:
        store = [_make_cluster() for _ in range(5)]
        client = _make_client(store)
        resp = client.get("/defect/clusters?limit=2", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["page"]["limit"] == 2
        assert body["page"]["next_cursor"] is not None

    def test_next_cursor_is_none_on_last_page(self) -> None:
        store = [_make_cluster()]
        client = _make_client(store)
        resp = client.get("/defect/clusters?limit=10", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["page"]["next_cursor"] is None

    def test_invalid_defect_class_returns_400(self) -> None:
        client = _make_client([])
        resp = client.get("/defect/clusters?defectClass=invalid_class", headers=_auth())
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "INVALID_DEFECT_CLASS"
        assert "correlation_id" in body

    def test_invalid_status_returns_400(self) -> None:
        client = _make_client([])
        resp = client.get("/defect/clusters?status=unknown", headers=_auth())
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "INVALID_STATUS"

    def test_limit_above_200_rejected(self) -> None:
        client = _make_client([])
        resp = client.get("/defect/clusters?limit=201", headers=_auth())
        assert resp.status_code == 422

    def test_limit_below_1_rejected(self) -> None:
        client = _make_client([])
        resp = client.get("/defect/clusters?limit=0", headers=_auth())
        assert resp.status_code == 422

    def test_each_instance_starts_with_independent_store(self) -> None:
        store_a: list[dict[str, Any]] = []
        store_b: list[dict[str, Any]] = []
        client_a = _make_client(store_a)
        client_b = _make_client(store_b)

        resp_a = client_a.get("/defect/clusters", headers=_auth())
        resp_b = client_b.get("/defect/clusters", headers=_auth())
        assert resp_a.json()["data"] == []
        assert resp_b.json()["data"] == []

        store_a.append(_make_cluster())
        resp_a2 = client_a.get("/defect/clusters", headers=_auth())
        resp_b2 = client_b.get("/defect/clusters", headers=_auth())
        assert len(resp_a2.json()["data"]) == 1
        assert len(resp_b2.json()["data"]) == 0


# ---------------------------------------------------------------------------
# GET /defect/clusters/{clusterId}
# ---------------------------------------------------------------------------


class TestGetCluster:
    def test_returns_200_with_full_cluster_body(self) -> None:
        cluster_id = str(uuid.uuid4())
        store = [_make_cluster(cluster_id=cluster_id)]
        client = _make_client(store)
        resp = client.get(f"/defect/clusters/{cluster_id}", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["cluster_id"] == cluster_id

    def test_response_contains_all_required_fields(self) -> None:
        cluster_id = str(uuid.uuid4())
        store = [_make_cluster(cluster_id=cluster_id)]
        client = _make_client(store)
        resp = client.get(f"/defect/clusters/{cluster_id}", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        required = {
            "cluster_id", "defect_class", "status", "root_cause_signature",
            "confidence_score", "affected_vin_count", "most_recent_vin",
            "detected_at", "created_at", "updated_at",
        }
        for field in required:
            assert field in body, f"missing field: {field}"

    def test_response_field_values_match_store(self) -> None:
        cluster_id = str(uuid.uuid4())
        store = [_make_cluster(
            cluster_id=cluster_id,
            status="in_progress",
            defect_class="dust",
            affected_vin_count=7,
            confidence_score=0.92,
        )]
        client = _make_client(store)
        resp = client.get(f"/defect/clusters/{cluster_id}", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "in_progress"
        assert body["defect_class"] == "dust"
        assert body["affected_vin_count"] == 7
        assert body["confidence_score"] == 0.92

    def test_returns_404_for_unknown_cluster_id(self) -> None:
        client = _make_client([])
        unknown_id = str(uuid.uuid4())
        resp = client.get(f"/defect/clusters/{unknown_id}", headers=_auth())
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "CLUSTER_NOT_FOUND"
        assert "correlation_id" in body

    def test_returns_400_for_non_uuid_cluster_id(self) -> None:
        client = _make_client([])
        resp = client.get("/defect/clusters/not-a-uuid", headers=_auth())
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "INVALID_CLUSTER_ID"

    def test_returns_401_without_bearer(self) -> None:
        cluster_id = str(uuid.uuid4())
        store = [_make_cluster(cluster_id=cluster_id)]
        client = _make_client(store)
        resp = client.get(f"/defect/clusters/{cluster_id}")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "MISSING_TOKEN"

    def test_nullable_corrective_action_jira_id_when_none(self) -> None:
        cluster_id = str(uuid.uuid4())
        store = [_make_cluster(cluster_id=cluster_id, corrective_action_jira_id=None)]
        client = _make_client(store)
        resp = client.get(f"/defect/clusters/{cluster_id}", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["corrective_action_jira_id"] is None

    def test_corrective_action_jira_id_populated_when_set(self) -> None:
        cluster_id = str(uuid.uuid4())
        store = [_make_cluster(cluster_id=cluster_id, corrective_action_jira_id="IFOSQUALITY-42")]
        client = _make_client(store)
        resp = client.get(f"/defect/clusters/{cluster_id}", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["corrective_action_jira_id"] == "IFOSQUALITY-42"
