"""Tests for POST /defect/clusters/{clusterId}/corrective-action."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.jira_client import JiraClientError
from app.main import create_app
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


def _make_client(jira_mock: MagicMock) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_jira_client] = lambda: jira_mock
    return TestClient(app, raise_server_exceptions=False)


def _valid_body(cluster_id: str | None = None) -> dict[str, Any]:
    return {
        "cluster_id": cluster_id or str(uuid.uuid4()),
        "root_cause_signature": "ecoat_voltage_deviation",
        "confidence_score": 0.87,
        "affected_vin_count": 4,
        "recommended_prep_stage_adjustment": "Increase bath agitation by 10%",
        "idempotency_key": str(uuid.uuid4()),
    }


# ---------------------------------------------------------------------------
# POST /defect/clusters/{clusterId}/corrective-action — happy path
# ---------------------------------------------------------------------------


class TestCreateCorrectiveAction:
    def test_returns_201_with_jira_issue_fields(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock(issue_id="10042", issue_key="IFOSQUALITY-42")
        client = _make_client(jira)
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(cluster_id),
            headers=_auth(),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["jira_issue_id"] == "10042"
        assert body["jira_issue_key"] == "IFOSQUALITY-42"
        assert body["cluster_id"] == cluster_id
        assert "jira_issue_url" in body

    def test_calls_jira_create_issue_once(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock()
        client = _make_client(jira)
        client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(cluster_id),
            headers=_auth(),
        )
        jira.create_issue.assert_awaited_once()

    def test_jira_fields_contain_cluster_id_not_vin(self) -> None:
        """Verify no PII — only aggregate cluster fields are sent to Jira."""
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock()
        client = _make_client(jira)
        client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(cluster_id),
            headers=_auth(),
        )
        call_args = jira.create_issue.call_args
        fields: dict[str, Any] = call_args.args[0]
        # Summary must contain cluster_id (opaque UUID) not any VIN
        assert cluster_id in fields["summary"]
        assert "WBS" not in str(fields)

    def test_optional_prep_stage_adjustment_omitted_when_none(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock()
        client = _make_client(jira)
        body = _valid_body(cluster_id)
        body.pop("recommended_prep_stage_adjustment")
        client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=body,
            headers=_auth(),
        )
        call_args = jira.create_issue.call_args
        fields: dict[str, Any] = call_args.args[0]
        # customfield_10103 should not appear if not supplied
        assert "customfield_10103" not in fields

    def test_optional_prep_stage_adjustment_included_when_provided(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock()
        client = _make_client(jira)
        body = _valid_body(cluster_id)
        body["recommended_prep_stage_adjustment"] = "Reduce spray pressure"
        client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=body,
            headers=_auth(),
        )
        call_args = jira.create_issue.call_args
        fields: dict[str, Any] = call_args.args[0]
        assert fields.get("customfield_10103") == "Reduce spray pressure"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class TestCreateCorrectiveActionValidation:
    def test_invalid_cluster_id_uuid_returns_400(self) -> None:
        jira = _make_jira_mock()
        client = _make_client(jira)
        resp = client.post(
            "/defect/clusters/not-a-uuid/corrective-action",
            json=_valid_body("not-a-uuid"),
            headers=_auth(),
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "INVALID_CLUSTER_ID"
        assert "correlation_id" in body

    def test_missing_required_field_returns_422(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock()
        client = _make_client(jira)
        body = _valid_body(cluster_id)
        del body["root_cause_signature"]
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=body,
            headers=_auth(),
        )
        assert resp.status_code == 422

    def test_confidence_score_above_1_returns_422(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock()
        client = _make_client(jira)
        body = _valid_body(cluster_id)
        body["confidence_score"] = 1.5
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=body,
            headers=_auth(),
        )
        assert resp.status_code == 422

    def test_confidence_score_below_0_returns_422(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock()
        client = _make_client(jira)
        body = _valid_body(cluster_id)
        body["confidence_score"] = -0.1
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=body,
            headers=_auth(),
        )
        assert resp.status_code == 422

    def test_affected_vin_count_zero_returns_422(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock()
        client = _make_client(jira)
        body = _valid_body(cluster_id)
        body["affected_vin_count"] = 0
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
        client = _make_client(jira)
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(cluster_id),
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "MISSING_TOKEN"


# ---------------------------------------------------------------------------
# Jira error propagation
# ---------------------------------------------------------------------------


class TestCreateCorrectiveActionJiraErrors:
    def test_jira_500_returns_502(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock(raise_error=JiraClientError(500, "Internal error"))
        client = _make_client(jira)
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(cluster_id),
            headers=_auth(),
        )
        assert resp.status_code == 502
        body = resp.json()
        assert body["code"] == "JIRA_UNAVAILABLE"
        assert "correlation_id" in body

    def test_jira_409_returns_409(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock(raise_error=JiraClientError(409, "Conflict"))
        client = _make_client(jira)
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(cluster_id),
            headers=_auth(),
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "JIRA_CONFLICT"

    def test_jira_400_returns_502(self) -> None:
        cluster_id = str(uuid.uuid4())
        jira = _make_jira_mock(raise_error=JiraClientError(400, "Bad fields"))
        client = _make_client(jira)
        resp = client.post(
            f"/defect/clusters/{cluster_id}/corrective-action",
            json=_valid_body(cluster_id),
            headers=_auth(),
        )
        assert resp.status_code == 502
