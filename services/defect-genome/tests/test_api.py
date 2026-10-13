"""Integration tests for defect-genome HTTP API endpoints.

Uses FastAPI TestClient with stub asyncpg pool and mocked Kafka producer.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_CLUSTER_ID = uuid.uuid4()
_VIN_A = "WBA3B3C56DF596858"
_VIN_B = "WBA3B3C57DF596859"

_FAR_FUTURE = "2099-01-01T00:00:00+00:00"


def _make_jwt() -> str:
    """Minimal unsigned JWT (alg=none) accepted by the test harness."""
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        b'{"sub":"engineer@bmwgroup.net","exp":9999999999}'
    ).rstrip(b"=").decode()
    return f"{header}.{payload}."


_AUTH = {"Authorization": f"Bearer {_make_jwt()}"}


# ---------------------------------------------------------------------------
# Stub pool
# ---------------------------------------------------------------------------


class _StubConn:
    def __init__(self, pool: "_StubPool") -> None:
        self._pool = pool

    async def __aenter__(self) -> "_StubConn":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def execute(self, query: str, *args: object) -> None:
        if "INSERT INTO cluster_query_result" in query:
            self._pool._query_results.append(args)
        elif "INSERT INTO model_feedback" in query:
            self._pool._feedbacks.append(args)
        elif "UPDATE defect_cluster" in query and "corrective_action_jira_id" in query:
            self._pool._ca_update_called = True

    async def fetchrow(self, query: str, *args: object) -> dict | None:
        if "confidence_threshold_config" in query:
            return {
                "low_confidence_threshold": 0.60,
                "cluster_alert_threshold": 0.80,
                "cluster_alert_min_vins": 3,
                "cluster_alert_window_hours": 4,
                "updated_at": None,
            }
        if "cluster_query_result" in query and "idempotency_key" in query:
            return None
        if "defect_cluster" in query and "cluster_id = $1" in query:
            cid = str(args[0]) if args else ""
            row = self._pool._clusters.get(cid)
            return row
        return None

    async def fetch(self, query: str, *args: object) -> list:
        if "defect_cluster" in query and "WHERE" in query:
            return list(self._pool._clusters.values())
        return []

    async def fetchval(self, query: str, *args: object) -> Any:
        if "UPDATE defect_cluster" in query:
            return str(_CLUSTER_ID)
        return None

    def transaction(self) -> "_TxCtx":
        return _TxCtx(self)


class _TxCtx:
    def __init__(self, conn: _StubConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> "_TxCtx":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


class _StubPool:
    def __init__(self) -> None:
        self._query_results: list = []
        self._feedbacks: list = []
        self._clusters: dict[str, dict] = {}
        self._ca_update_called: bool = False

    def acquire(self) -> _StubConn:
        return _StubConn(self)

    def seed_cluster(self, cluster_id: uuid.UUID | None = None) -> None:
        cid = str(cluster_id or _CLUSTER_ID)
        self._clusters[cid] = {
            "cluster_id": cid,
            "defect_class": "dust",
            "status": "open",
            "root_cause_signature": "stage=3:hum=2:temp=1:v=1:ph=1",
            "confidence_score": 0.85,
            "affected_vin_count": 3,
            "most_recent_vin": _VIN_A,
            "detected_at": _FAR_FUTURE,
            "created_at": _FAR_FUTURE,
            "updated_at": _FAR_FUTURE,
            "top_factors": json.dumps([{
                "rank": 1,
                "factor_name": "humidity_elevated_booth",
                "factor_category": "booth_parameter",
                "probability_score": 0.72,
                "supporting_vin_count": 3,
            }]),
            "process_replay_url": f"https://quality-studio.internal.bmw-factory-os.eu/process-replay/{_VIN_A}",
            "corrective_action_jira_id": None,
        }


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture()
def stub_pool() -> _StubPool:
    return _StubPool()


# ---------------------------------------------------------------------------
# POST /defect/cluster-query
# ---------------------------------------------------------------------------


def test_cluster_query_401_without_token(app_client) -> None:
    resp = app_client.post("/defect/cluster-query", json={"defect_class": "dust"})
    assert resp.status_code == 401


def test_cluster_query_400_missing_defect_class(app_client) -> None:
    resp = app_client.post("/defect/cluster-query", json={}, headers=_AUTH)
    assert resp.status_code == 400


def test_cluster_query_400_error_body_has_required_fields(app_client) -> None:
    resp = app_client.post("/defect/cluster-query", json={}, headers=_AUTH)
    body = resp.json()
    assert "code" in body
    assert "message" in body
    assert "correlation_id" in body


def test_cluster_query_422_for_unsupported_defect_class(app_client) -> None:
    # Pydantic enum validation rejects unknown defect class with 400 (validation error)
    resp = app_client.post(
        "/defect/cluster-query",
        json={"defect_class": "unknown_defect"},
        headers=_AUTH,
    )
    assert resp.status_code == 400


@pytest.mark.parametrize("defect_class", ["dust", "orange_peel", "run", "crater", "pinhole"])
def test_cluster_query_200_for_all_defect_classes(app_client, defect_class: str) -> None:
    pool = _StubPool()
    pool.seed_cluster()
    with (
        patch("app.db.get_pool", return_value=pool),
        patch("app.cluster_service.asyncio.create_task"),
        patch("app.cluster_state._event_window", {}),
    ):
        resp = app_client.post(
            "/defect/cluster-query",
            json={"defect_class": defect_class},
            headers=_AUTH,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["defect_class"] == defect_class
    assert "query_id" in body
    assert "cluster_id" in body
    assert "model_confidence" in body
    assert "low_confidence" in body
    assert "top_factors" in body
    assert len(body["top_factors"]) >= 1


def test_cluster_query_low_confidence_includes_advisor_v1_fallback(app_client) -> None:
    pool = _StubPool()
    pool.seed_cluster()
    # Patch confidence to be below threshold
    with (
        patch("app.db.get_pool", return_value=pool),
        patch("app.cluster_service.asyncio.create_task"),
        patch("app.cluster_service.score_cluster_xgboost", return_value=(
            [{
                "rank": 1,
                "factor_name": "humidity_elevated_booth",
                "factor_category": "booth_parameter",
                "probability_score": 0.40,
                "supporting_vin_count": 1,
            }],
            0.40,
        )),
        patch("app.cluster_state._event_window", {}),
    ):
        resp = app_client.post(
            "/defect/cluster-query",
            json={"defect_class": "dust"},
            headers=_AUTH,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["low_confidence"] is True
    assert body["advisor_v1_fallback"] is not None
    assert body["advisor_v1_fallback"]["rule_id"] == "ADV-DUST-01"


def test_cluster_query_high_confidence_has_no_advisor_fallback(app_client) -> None:
    pool = _StubPool()
    pool.seed_cluster()
    with (
        patch("app.db.get_pool", return_value=pool),
        patch("app.cluster_service.asyncio.create_task"),
        patch("app.cluster_service.score_cluster_xgboost", return_value=(
            [{
                "rank": 1,
                "factor_name": "humidity_elevated_booth",
                "factor_category": "booth_parameter",
                "probability_score": 0.85,
                "supporting_vin_count": 3,
            }],
            0.85,
        )),
        patch("app.cluster_state._event_window", {}),
    ):
        resp = app_client.post(
            "/defect/cluster-query",
            json={"defect_class": "dust"},
            headers=_AUTH,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["low_confidence"] is False
    assert body.get("advisor_v1_fallback") is None


def test_cluster_query_idempotency_key_returns_cached_result(app_client) -> None:
    pool = _StubPool()
    pool.seed_cluster()
    key = str(uuid.uuid4())

    with (
        patch("app.db.get_pool", return_value=pool),
        patch("app.cluster_service.asyncio.create_task"),
        patch("app.cluster_state._event_window", {}),
    ):
        first = app_client.post(
            "/defect/cluster-query",
            json={"defect_class": "dust", "idempotency_key": key},
            headers=_AUTH,
        )
        second = app_client.post(
            "/defect/cluster-query",
            json={"defect_class": "dust", "idempotency_key": key},
            headers=_AUTH,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["query_id"] == second.json()["query_id"]


# ---------------------------------------------------------------------------
# GET /defect/clusters
# ---------------------------------------------------------------------------


def test_list_clusters_401_without_token(app_client) -> None:
    resp = app_client.get("/defect/clusters")
    assert resp.status_code == 401


def test_list_clusters_200_returns_data_and_page(app_client) -> None:
    pool = _StubPool()
    pool.seed_cluster()
    with patch("app.db.get_pool", return_value=pool):
        resp = app_client.get("/defect/clusters", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "page" in body
    assert "next_cursor" in body["page"]
    assert "limit" in body["page"]


def test_list_clusters_respects_limit_parameter(app_client) -> None:
    pool = _StubPool()
    with patch("app.db.get_pool", return_value=pool):
        resp = app_client.get("/defect/clusters?limit=10", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["page"]["limit"] == 10


def test_list_clusters_400_on_invalid_limit(app_client) -> None:
    pool = _StubPool()
    with patch("app.db.get_pool", return_value=pool):
        resp = app_client.get("/defect/clusters?limit=0", headers=_AUTH)
    assert resp.status_code == 400


def test_list_clusters_400_on_limit_too_high(app_client) -> None:
    pool = _StubPool()
    with patch("app.db.get_pool", return_value=pool):
        resp = app_client.get("/defect/clusters?limit=201", headers=_AUTH)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /defect/clusters/{clusterId}
# ---------------------------------------------------------------------------


def test_get_cluster_401_without_token(app_client) -> None:
    resp = app_client.get(f"/defect/clusters/{_CLUSTER_ID}")
    assert resp.status_code == 401


def test_get_cluster_404_for_unknown_id(app_client) -> None:
    pool = _StubPool()
    with patch("app.db.get_pool", return_value=pool):
        resp = app_client.get(f"/defect/clusters/{uuid.uuid4()}", headers=_AUTH)
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "CLUSTER_NOT_FOUND"
    assert "correlation_id" in body


def test_get_cluster_200_for_known_id(app_client) -> None:
    pool = _StubPool()
    pool.seed_cluster(_CLUSTER_ID)
    with patch("app.db.get_pool", return_value=pool):
        resp = app_client.get(f"/defect/clusters/{_CLUSTER_ID}", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["cluster_id"] == str(_CLUSTER_ID)
    assert body["defect_class"] == "dust"
    assert "status" in body
    assert "root_cause_signature" in body
    assert "confidence_score" in body


# ---------------------------------------------------------------------------
# POST /defect/clusters/{clusterId}/corrective-action
# ---------------------------------------------------------------------------

_CA_BODY = {
    "assigned_root_cause_factor_rank": 1,
    "confirmed_by_user_id": "engineer@bmwgroup.net",
}


def test_corrective_action_401_without_token(app_client) -> None:
    resp = app_client.post(f"/defect/clusters/{_CLUSTER_ID}/corrective-action", json=_CA_BODY)
    assert resp.status_code == 401


def test_corrective_action_404_for_unknown_cluster(app_client) -> None:
    pool = _StubPool()
    with patch("app.db.get_pool", return_value=pool):
        resp = app_client.post(
            f"/defect/clusters/{uuid.uuid4()}/corrective-action",
            json=_CA_BODY,
            headers=_AUTH,
        )
    assert resp.status_code == 404


def test_corrective_action_409_when_jira_id_already_set(app_client) -> None:
    pool = _StubPool()
    pool.seed_cluster(_CLUSTER_ID)
    pool._clusters[str(_CLUSTER_ID)]["corrective_action_jira_id"] = "DEFECTGENOME-42"

    with patch("app.db.get_pool", return_value=pool):
        resp = app_client.post(
            f"/defect/clusters/{_CLUSTER_ID}/corrective-action",
            json=_CA_BODY,
            headers=_AUTH,
        )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "CORRECTIVE_ACTION_EXISTS"
    assert "correlation_id" in body


def test_corrective_action_201_on_success(app_client) -> None:
    pool = _StubPool()
    pool.seed_cluster(_CLUSTER_ID)

    mock_jira = AsyncMock(return_value=("DEFECTGENOME-1", "https://jira.bmwgroup.net/browse/DEFECTGENOME-1"))

    with (
        patch("app.db.get_pool", return_value=pool),
        patch("app.cluster_service._call_jira_create_issue", mock_jira),
        patch("app.cluster_service.asyncio.create_task"),
    ):
        resp = app_client.post(
            f"/defect/clusters/{_CLUSTER_ID}/corrective-action",
            json=_CA_BODY,
            headers=_AUTH,
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["jira_ticket_id"] == "DEFECTGENOME-1"
    assert body["jira_ticket_url"] == "https://jira.bmwgroup.net/browse/DEFECTGENOME-1"
    assert body["cluster_id"] == str(_CLUSTER_ID)
    assert "created_at" in body


def test_corrective_action_400_invalid_factor_rank(app_client) -> None:
    pool = _StubPool()
    pool.seed_cluster(_CLUSTER_ID)
    with patch("app.db.get_pool", return_value=pool):
        resp = app_client.post(
            f"/defect/clusters/{_CLUSTER_ID}/corrective-action",
            json={"assigned_root_cause_factor_rank": 6, "confirmed_by_user_id": "user"},
            headers=_AUTH,
        )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /models/confidence-threshold
# ---------------------------------------------------------------------------


def test_get_confidence_threshold_401_without_token(app_client) -> None:
    resp = app_client.get("/models/confidence-threshold")
    assert resp.status_code == 401


def test_get_confidence_threshold_200(app_client) -> None:
    pool = _StubPool()
    with patch("app.db.get_pool", return_value=pool):
        resp = app_client.get("/models/confidence-threshold", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert "low_confidence_threshold" in body
    assert "cluster_alert_threshold" in body
    assert "cluster_alert_min_vins" in body
    assert "cluster_alert_window_hours" in body


def test_get_confidence_threshold_default_values(app_client) -> None:
    pool = _StubPool()
    with patch("app.db.get_pool", return_value=pool):
        resp = app_client.get("/models/confidence-threshold", headers=_AUTH)
    body = resp.json()
    assert body["low_confidence_threshold"] == 0.60
    assert body["cluster_alert_threshold"] == 0.80
    assert body["cluster_alert_min_vins"] == 3
    assert body["cluster_alert_window_hours"] == 4


# ---------------------------------------------------------------------------
# POST /models/feedback
# ---------------------------------------------------------------------------


def test_submit_feedback_401_without_token(app_client) -> None:
    resp = app_client.post("/models/feedback", json={})
    assert resp.status_code == 401


def test_submit_feedback_400_missing_required_fields(app_client) -> None:
    resp = app_client.post("/models/feedback", json={}, headers=_AUTH)
    assert resp.status_code == 400


def test_submit_feedback_202_on_valid_payload(app_client) -> None:
    pool = _StubPool()
    pool.seed_cluster(_CLUSTER_ID)
    with patch("app.db.get_pool", return_value=pool):
        resp = app_client.post(
            "/models/feedback",
            json={
                "cluster_id": str(_CLUSTER_ID),
                "query_id": str(uuid.uuid4()),
                "engineer_user_id": "engineer@bmwgroup.net",
                "accepted_factor_rank": 1,
                "defect_class": "dust",
            },
            headers=_AUTH,
        )
    assert resp.status_code == 202
    body = resp.json()
    assert "feedback_id" in body
    assert "accepted_at" in body


def test_submit_feedback_202_for_all_defect_classes(app_client) -> None:
    pool = _StubPool()
    pool.seed_cluster(_CLUSTER_ID)
    for dc in ["dust", "orange_peel", "run", "crater", "pinhole"]:
        with patch("app.db.get_pool", return_value=pool):
            resp = app_client.post(
                "/models/feedback",
                json={
                    "cluster_id": str(_CLUSTER_ID),
                    "query_id": str(uuid.uuid4()),
                    "engineer_user_id": "engineer@bmwgroup.net",
                    "accepted_factor_rank": 1,
                    "defect_class": dc,
                },
                headers=_AUTH,
            )
        assert resp.status_code == 202
