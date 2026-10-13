"""POST /defect/clusters/{clusterId}/corrective-action

Pre-populates and submits a Jira corrective action ticket for a defect cluster.
Credentials are read from Vault at secret/data/jira/quality-studio-client-secret.
Only aggregate cluster-level fields are sent to Jira — no PII per GDPR / §87 BetrVG.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import require_bearer
from app.config import settings
from app.jira_client import JiraClient, JiraClientError
from app.routers.clusters import get_cluster_store

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
    assigned_root_cause_factor_rank: int = Field(
        ...,
        ge=1,
        le=5,
        description="Rank (1–5) of the root-cause factor the engineer has confirmed",
    )
    confirmed_by_user_id: str = Field(
        ...,
        description="BMW Entra ID subject claim of the confirming engineer",
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Client-supplied idempotency key (UUID). Duplicate requests with the same key return the cached response.",
    )
    additional_notes: str | None = Field(
        default=None,
        description="Optional free-text notes for the corrective action ticket.",
    )


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
    summary="Pre-populate and submit a Jira corrective action ticket for a cluster",
    status_code=201,
    responses={
        201: {},
        400: {},
        401: {},
        404: {},
        409: {},
        500: {},
    },
)
async def create_corrective_action(
    clusterId: str,
    body: CorrectiveActionRequest,
    jwt_payload: dict[str, Any] = Depends(require_bearer),
    jira: JiraClient = Depends(get_jira_client),
    cluster_store: list[dict[str, Any]] = Depends(get_cluster_store),
) -> JSONResponse:
    try:
        cluster_uuid = str(uuid.UUID(clusterId))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_CLUSTER_ID", "clusterId must be a valid UUID"),
        )

    cluster = _find_cluster(cluster_store, cluster_uuid)
    if cluster is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("CLUSTER_NOT_FOUND", f"No cluster found with id {clusterId}"),
        )

    if cluster.get("corrective_action_jira_id") is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_error(
                "CORRECTIVE_ACTION_EXISTS",
                "A corrective action already exists for this cluster",
            ),
        )

    top_factors: list[dict[str, Any]] = cluster.get("top_factors") or []
    assigned_factor: dict[str, Any] | None = next(
        (f for f in top_factors if f.get("rank") == body.assigned_root_cause_factor_rank),
        None,
    )
    factor_name = assigned_factor["factor_name"] if assigned_factor else f"rank-{body.assigned_root_cause_factor_rank}"

    prepopulated_summary = (
        f"DefectGenome cluster {cluster_uuid}: {cluster['root_cause_signature']} "
        f"[factor: {factor_name}]"
    )

    fields = _build_jira_fields(
        settings=settings,
        cluster=cluster,
        assigned_factor=assigned_factor,
        prepopulated_summary=prepopulated_summary,
        additional_notes=body.additional_notes,
        confirmed_by_user_id=body.confirmed_by_user_id,
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
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_error(
                "JIRA_UNAVAILABLE",
                f"Jira API returned {exc.status_code}",
            ),
        ) from exc

    issue_id: str = issue["id"]
    issue_url: str = issue.get("self", f"{settings.jira_base_url}/browse/{issue['key']}")
    created_at = datetime.now(timezone.utc).isoformat()

    cluster["corrective_action_jira_id"] = issue["key"]
    cluster["updated_at"] = created_at

    return JSONResponse(
        status_code=201,
        content={
            "jira_ticket_id": issue_id,
            "jira_ticket_url": issue_url,
            "cluster_id": cluster_uuid,
            "created_at": created_at,
            "prepopulated_summary": prepopulated_summary,
        },
    )


def _build_jira_fields(
    *,
    settings: Any,
    cluster: dict[str, Any],
    assigned_factor: dict[str, Any] | None,
    prepopulated_summary: str,
    additional_notes: str | None,
    confirmed_by_user_id: str,
) -> dict[str, Any]:
    """Build Jira issue fields for POST /rest/api/3/issue.

    Only aggregate cluster-level data — no PII per GDPR / Works Council §87 BetrVG.
    Field IDs (customfield_*) come from settings, populated by Vault + Terraform outputs.
    """
    description_text = (
        f"Cluster ID: {cluster['cluster_id']}\n"
        f"Root cause: {cluster['root_cause_signature']}\n"
        f"Confidence: {cluster['confidence_score']:.2%}\n"
        f"Affected VIN count: {cluster['affected_vin_count']}\n"
        f"Confirmed by: {confirmed_by_user_id}"
    )
    if additional_notes:
        description_text += f"\n\nNotes: {additional_notes}"

    fields: dict[str, Any] = {
        "project": {"key": settings.jira_project_key},
        "issuetype": {"name": "Corrective Action"},
        "summary": prepopulated_summary,
        "description": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description_text}],
                }
            ],
        },
        settings.jira_field_cluster_id: str(cluster["cluster_id"]),
        settings.jira_field_root_cause_probability: float(cluster["confidence_score"]),
        settings.jira_field_affected_vin_count: float(cluster["affected_vin_count"]),
    }

    if assigned_factor and assigned_factor.get("factor_name"):
        fields[settings.jira_field_prep_stage_adjustment] = assigned_factor["factor_name"]

    return fields


def _find_cluster(
    store: list[dict[str, Any]], cluster_id: str
) -> dict[str, Any] | None:
    for row in store:
        if str(row["cluster_id"]) == cluster_id:
            return row
    return None
