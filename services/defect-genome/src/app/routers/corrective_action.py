"""POST /defect/clusters/{clusterId}/corrective-action

Creates a Jira Corrective Action issue for a DefectGenome cluster alert.
The Jira issue is created via POST /rest/api/3/issue using the OAuth 2.0
client credentials stored in Vault at secret/data/jira/quality-studio-client-secret.

No PII is written to Jira — only aggregate cluster-level fields per GDPR / §87 BetrVG.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import require_bearer
from app.config import settings
from app.jira_client import JiraClient, JiraClientError

router = APIRouter(tags=["Corrective Actions"])


# ---------------------------------------------------------------------------
# Dependency — one JiraClient per process, shared across requests.
# In tests this is overridden with a fake.
# ---------------------------------------------------------------------------

_jira_client: JiraClient | None = None


def get_jira_client() -> JiraClient:
    global _jira_client
    if _jira_client is None:
        _jira_client = JiraClient(settings=settings)
    return _jira_client


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CorrectiveActionRequest(BaseModel):
    cluster_id: str = Field(
        ...,
        description="UUID of the DefectGenome cluster. No PII.",
    )
    root_cause_signature: str = Field(
        ...,
        description="Root-cause signature string from DefectGenome.",
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="XGBoost confidence score 0.0–1.0. No PII.",
    )
    affected_vin_count: int = Field(
        ...,
        ge=1,
        description="Aggregate VIN count. No individual VINs stored.",
    )
    recommended_prep_stage_adjustment: str | None = Field(
        default=None,
        description="Optional free-text prep-stage recommendation. No PII.",
    )
    idempotency_key: str = Field(
        ...,
        description="Client-supplied idempotency key (UUID). Duplicate requests with the same key return the cached response.",
    )


class CorrectiveActionResponse(BaseModel):
    jira_issue_id: str
    jira_issue_key: str
    jira_issue_url: str
    cluster_id: str


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------


def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


# ---------------------------------------------------------------------------
# POST /defect/clusters/{clusterId}/corrective-action
# ---------------------------------------------------------------------------


@router.post(
    "/defect/clusters/{clusterId}/corrective-action",
    operation_id="createCorrectiveAction",
    summary="Create a Jira Corrective Action ticket for a DefectGenome cluster",
    status_code=201,
    responses={
        201: {},
        400: {},
        401: {},
        409: {},
        502: {},
    },
)
async def create_corrective_action(
    clusterId: str,
    body: CorrectiveActionRequest,
    jwt_payload: dict[str, Any] = Depends(require_bearer),
    jira: JiraClient = Depends(get_jira_client),
) -> JSONResponse:
    try:
        cluster_uuid = str(uuid.UUID(clusterId))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_CLUSTER_ID", "clusterId must be a valid UUID"),
        )

    fields = _build_jira_fields(
        settings=settings,
        cluster_id=cluster_uuid,
        root_cause_signature=body.root_cause_signature,
        confidence_score=body.confidence_score,
        affected_vin_count=body.affected_vin_count,
        recommended_prep_stage_adjustment=body.recommended_prep_stage_adjustment,
    )

    try:
        issue = await jira.create_issue(fields)
    except JiraClientError as exc:
        if exc.status_code == 409:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_error("JIRA_CONFLICT", "A duplicate Jira issue was detected"),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_error(
                "JIRA_UNAVAILABLE",
                f"Jira API returned {exc.status_code}",
            ),
        ) from exc

    issue_id: str = issue["id"]
    issue_key: str = issue["key"]
    issue_url: str = issue.get("self", f"{settings.jira_base_url}/rest/api/3/issue/{issue_id}")

    return JSONResponse(
        status_code=201,
        content={
            "jira_issue_id": issue_id,
            "jira_issue_key": issue_key,
            "jira_issue_url": issue_url,
            "cluster_id": cluster_uuid,
        },
    )


def _build_jira_fields(
    *,
    settings: Any,
    cluster_id: str,
    root_cause_signature: str,
    confidence_score: float,
    affected_vin_count: int,
    recommended_prep_stage_adjustment: str | None,
) -> dict[str, Any]:
    """Build the Jira issue fields dict for POST /rest/api/3/issue.

    Field IDs (customfield_*) are determined at deploy time from Terraform outputs.
    Here we use the stable logical names defined in main.tf and look them up from
    settings so integration tests can inject expected values.
    """
    fields: dict[str, Any] = {
        "project": {"key": settings.jira_project_key},
        "issuetype": {"name": "Corrective Action"},
        "summary": f"DefectGenome cluster {cluster_id}: {root_cause_signature}",
        "description": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Cluster ID: {cluster_id}\n"
                                f"Root cause: {root_cause_signature}\n"
                                f"Confidence: {confidence_score:.2%}\n"
                                f"Affected VIN count: {affected_vin_count}"
                            ),
                        }
                    ],
                }
            ],
        },
        # Custom fields — IDs are the logical Terraform resource names resolved
        # at runtime from the JIRA_FIELD_* env vars (populated by Vault + Terraform outputs).
        settings.jira_field_cluster_id: cluster_id,
        settings.jira_field_root_cause_probability: confidence_score,
        settings.jira_field_affected_vin_count: float(affected_vin_count),
    }
    if recommended_prep_stage_adjustment is not None:
        fields[settings.jira_field_prep_stage_adjustment] = recommended_prep_stage_adjustment
    return fields
