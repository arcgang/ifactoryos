"""Pydantic request/response models matching the OpenAPI contract exactly."""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class DefectClass(str, Enum):
    dust = "dust"
    orange_peel = "orange_peel"
    run = "run"
    crater = "crater"
    pinhole = "pinhole"


class ClusterStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"


class FactorCategory(str, Enum):
    prep_stage = "prep_stage"
    booth_parameter = "booth_parameter"
    climate_condition = "climate_condition"


class RootCauseFactor(BaseModel):
    rank: int = Field(ge=1, le=5)
    factor_name: str
    factor_category: FactorCategory
    probability_score: float = Field(ge=0, le=1)
    supporting_vin_count: int = Field(ge=0)


class AdvisorV1Result(BaseModel):
    rule_id: str
    rule_description: str
    matched_conditions: list[str] = Field(default_factory=list)
    recommended_action: str


class CursorPage(BaseModel):
    next_cursor: str | None
    limit: int = Field(ge=1, le=200)


class ClusterQueryRequest(BaseModel):
    defect_class: DefectClass
    vin_filter: list[str] | None = None
    time_window_hours: int = Field(default=4, ge=1, le=168)
    idempotency_key: UUID | None = None


class ClusterQueryResult(BaseModel):
    query_id: UUID
    cluster_id: UUID
    queried_at: str
    defect_class: DefectClass
    model_confidence: float = Field(ge=0, le=1)
    low_confidence: bool
    top_factors: list[RootCauseFactor] = Field(min_length=1, max_length=5)
    affected_vin_count: int = Field(ge=0)
    affected_vins: list[str] | None = None
    model_version: str | None = None
    advisor_v1_fallback: AdvisorV1Result | None = None


class DefectCluster(BaseModel):
    cluster_id: UUID
    defect_class: DefectClass
    status: ClusterStatus
    root_cause_signature: str
    confidence_score: float = Field(ge=0, le=1)
    affected_vin_count: int = Field(ge=0)
    most_recent_vin: str
    detected_at: str
    created_at: str
    updated_at: str
    top_factors: list[RootCauseFactor] | None = None
    process_replay_url: str | None = None
    corrective_action_jira_id: str | None = None


class ClusterListResponse(BaseModel):
    data: list[DefectCluster]
    page: CursorPage


class CorrectiveActionRequest(BaseModel):
    assigned_root_cause_factor_rank: int = Field(ge=1, le=5)
    confirmed_by_user_id: str
    additional_notes: str | None = None
    idempotency_key: UUID | None = None


class CorrectiveActionResult(BaseModel):
    jira_ticket_id: str
    jira_ticket_url: str
    cluster_id: UUID
    created_at: str
    prepopulated_summary: str | None = None


class ConfidenceThresholdConfig(BaseModel):
    low_confidence_threshold: float = Field(ge=0, le=1)
    cluster_alert_threshold: float = Field(ge=0, le=1)
    cluster_alert_min_vins: int = Field(default=3, ge=1)
    cluster_alert_window_hours: int = Field(default=4, ge=1)
    updated_at: str | None = None


class ModelFeedbackRequest(BaseModel):
    cluster_id: UUID
    query_id: UUID
    engineer_user_id: str
    accepted_factor_rank: int = Field(ge=1, le=5)
    defect_class: DefectClass
    notes: str | None = None
    override_root_cause: str | None = None


class ModelFeedbackAck(BaseModel):
    feedback_id: UUID
    accepted_at: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    correlation_id: UUID
    details: list[dict[str, Any]] = Field(default_factory=list)
