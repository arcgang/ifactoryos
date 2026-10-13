"""Tests for POST /defect/clusters/{clusterId}/corrective-action."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.jira_client import JiraClientError
from app.main import create_app
from app.routers.clusters import get_cluster_store
from app.routers.corrective_action import get_jira_client

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_JWT = (
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJ1c2VyLTEiLCJleHAiOjk5OTk5OTk5OTl9"
    "."
)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_VALID_JWT}"}


def _make_jira_mock(
    *,
    issue_id: str = "10001",
    issue_key: str = "IFOSQUALITY-1",
    raise_error: JiraClientError | None = None,
) -> MagicMock:
    mock = MagicMock()
    if raise_error is not None:
        mock.create_issue = AsyncMock(side_effect=raise_error)
    else:
        mock.create_issue = AsyncMock(
            return_value={
                "id": issue_id,
                "key": issue_key,
                "self": f"https://bmwgroup.atlassian.net/rest/api/3/issue/{issue_id}",
            }
        )
    return mock


def _make_cluster(
    *,
    cluster_id: str | None = None,
    defect_class: str = "crater",
    status: str = "open",
    confidence_score: float = 0.87,
    affected_vin_count: int = 4,
    corrective_action_jira_id: str | None = None,
    top_factors: list[dict[str, Any]] | None = None,
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
        "top_factors": top_factors or [
            {
                "rank": 1,
                "factor_name": "e-coat voltage drop at stage 6",
                "factor_category": "prep_stage",
                "probability_score": 0.74,
                "supporting_vin_count": 3,
            },
        ],
    }


def _make_client(
    jira_mock: MagicMock,
    cluster_store: list[dict[str, Any]] | None = None,
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_jira_client] = lambda: jira_mock
    if cluster_store is not None:
        app.dependency_overrides[get_cluster_store] = lambda: cluster_store
    return TestClient(app, raise_server_exceptions=False)


def _valid_body() -> dict[str, Any]:
    return {
        "assigned_root_cause_factor_rank": 1,
        "confirmed_by_user_id": "user-42",
        "idempotency_key": str(uuid.uuid4()),
        "additional_notes": "Increase bath agitation by 10%",
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestCreateCorrectiveAction:
    def test_returns_201_with_correct_fields(self) -> None:
        cluster_id = str(uuid.uuid4())
        cluster = _make_cluster(cluster_id=cluster_id)
        jira = _make_jira_mock(issue_id="10042", issue_key="IFOSQUALITY-42")
        client = _make_client(jira, [cluster])
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(),
            headers=_auth(),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["jira_ticket_id"] == "10042"
        assert body["cluster_id"] == cluster_id
        assert "jira_ticket_url" in body
        assert "created_at" in body

    def test_prepopulated_summary_contains_cluster_id_and_signature(self) -> None:
        cluster_id = str(uuid.uuid4())
        cluster = _make_cluster(cluster_id=cluster_id)
        jira = _make_jira_mock()
        client = _make_client(jira, [cluster])
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(),
            headers=_auth(),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert cluster_id in body["prepopulated_summary"]
        assert "ecoat_voltage_deviation" in body["prepopulated_summary"]

    def test_calls_jira_create_issue_once(self) -> None:
        cluster_id = str(uuid.uuid4())
        cluster = _make_cluster(cluster_id=cluster_id)
        jira = _make_jira_mock()
        client = _make_client(jira, [cluster])
        client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(),
            headers=_auth(),
        )
        jira.create_issue.assert_awaited_once()

    def test_jira_fields_contain_cluster_id_not_vin(self) -> None:
        """Verify no PII — only aggregate cluster fields are sent to Jira."""
        cluster_id = str(uuid.uuid4())
        cluster = _make_cluster(cluster_id=cluster_id)
        jira = _make_jira_mock()
        client = _make_client(jira, [cluster])
        client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(),
            headers=_auth(),
        )
        call_args = jira.create_issue.call_args
        fields: dict[str, Any] = call_args.args[0]
        assert cluster_id in fields["summary"]
        assert "WBS" not in str(fields)

    def test_jira_payload_includes_all_four_custom_fields(self) -> None:
        cluster_id = str(uuid.uuid4())
        cluster = _make_cluster(cluster_id=cluster_id)
        jira = _make_jira_mock()
        client = _make_client(jira, [cluster])
        client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(),
            headers=_auth(),
        )
        call_args = jira.create_issue.call_args
        fields: dict[str, Any] = call_args.args[0]
        # cluster_id field
        assert "customfield_10100" in fields
        # root_cause_probability field
        assert "customfield_10101" in fields
        # affected_vin_count field
        assert "customfield_10102" in fields
        # prep_stage_adjustment field (populated from top factor rank 1)
        assert "customfield_10103" in fields

    def test_optional_additional_notes_omitted_when_none(self) -> None:
        cluster_id = str(uuid.uuid4())
        cluster = _make_cluster(cluster_id=cluster_id)
        jira = _make_jira_mock()
        client = _make_client(jira, [cluster])
        body = _valid_body()
        body.pop("additional_notes")
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=body,
            headers=_auth(),
        )
        assert resp.status_code == 201

    def test_sets_corrective_action_jira_id_on_cluster(self) -> None:
        cluster_id = str(uuid.uuid4())
        cluster = _make_cluster(cluster_id=cluster_id)
        jira = _make_jira_mock(issue_key="IFOSQUALITY-99")
        client = _make_client(jira, [cluster])
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(),
            headers=_auth(),
        )
        assert resp.status_code == 201
        assert cluster["corrective_action_jira_id"] == "IFOSQUALITY-99"


# ---------------------------------------------------------------------------
# Duplicate / idempotency — 409 when cluster already has a corrective action
# ---------------------------------------------------------------------------


class TestCreateCorrectiveActionDuplicate:
    def test_returns_409_when_corrective_action_already_exists(self) -> None:
        cluster_id = str(uuid.uuid4())
        cluster = _make_cluster(
            cluster_id=cluster_id, corrective_action_jira_id="IFOSQUALITY-1"
        )
        jira = _make_jira_mock()
        client = _make_client(jira, [cluster])
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(),
            headers=_auth(),
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "CORRECTIVE_ACTION_EXISTS"
        assert "correlation_id" in body

    def test_no_jira_call_when_already_exists(self) -> None:
        cluster_id = str(uuid.uuid4())
        cluster = _make_cluster(
            cluster_id=cluster_id, corrective_action_jira_id="IFOSQUALITY-1"
        )
        jira = _make_jira_mock()
        client = _make_client(jira, [cluster])
        client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(),
            headers=_auth(),
        )
        jira.create_issue.assert_not_awaited()


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class TestCreateCorrectiveActionValidation:
    def test_invalid_cluster_id_uuid_returns_400(self) -> None:
        jira = _make_jira_mock()
        client = _make_client(jira, [])
        resp = client.post(
            "/defect/clusters/not-a-uuid/corrective-action",
            json=_valid_body(),
            headers=_auth(),
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "INVALID_CLUSTER_ID"
        assert "correlation_id" in body

    def test_cluster_not_found_returns_404(self) -> None:
        jira = _make_jira_mock()
        client = _make_client(jira, [])
        resp = client.post(
            f"/defect/clusters/{uuid.uuid4()}/corrective-action",
            json=_valid_body(),
            headers=_auth(),
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "CLUSTER_NOT_FOUND"

    def test_missing_required_field_returns_422(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock()
        client = _make_client(jira, [])
        body = _valid_body()
        del body["confirmed_by_user_id"]
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=body,
            headers=_auth(),
        )
        assert resp.status_code == 422

    def test_factor_rank_above_5_returns_422(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock()
        client = _make_client(jira, [])
        body = _valid_body()
        body["assigned_root_cause_factor_rank"] = 6
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=body,
            headers=_auth(),
        )
        assert resp.status_code == 422

    def test_factor_rank_below_1_returns_422(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock()
        client = _make_client(jira, [])
        body = _valid_body()
        body["assigned_root_cause_factor_rank"] = 0
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=body,
            headers=_auth(),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestCreateCorrectiveActionAuth:
    def test_missing_bearer_returns_401(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock()
        client = _make_client(jira, [])
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(),
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "MISSING_TOKEN"


# ---------------------------------------------------------------------------
# Jira error propagation
# ---------------------------------------------------------------------------


class TestCreateCorrectiveActionJiraErrors:
    def test_jira_500_returns_500(self) -> None:
        cluster_id = str(uuid.uuid4())
        cluster = _make_cluster(cluster_id=cluster_id)
        jira = _make_jira_mock(raise_error=JiraClientError(500, "Internal error"))
        client = _make_client(jira, [cluster])
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(),
            headers=_auth(),
        )
        assert resp.status_code == 500
        body = resp.json()
        assert body["code"] == "JIRA_UNAVAILABLE"
        assert "correlation_id" in body

    def test_jira_409_returns_409(self) -> None:
        cluster_id = str(uuid.uuid4())
        cluster = _make_cluster(cluster_id=cluster_id)
        jira = _make_jira_mock(raise_error=JiraClientError(409, "Conflict"))
        client = _make_client(jira, [cluster])
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(),
            headers=_auth(),
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "JIRA_CONFLICT"
