"""Pydantic request/response models matching the OpenAPI contract exactly."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class DeviceType(str, Enum):
    HOLO_LENS_2 = "HoloLens2"
    IPAD = "iPad"


class ArInstructionStep(BaseModel):
    stepNumber: int
    description: str
    torqueSpecNm: float
    torqueToleranceNm: float
    fastenerPointId: str
    isVariantDelta: bool | None = None
    variantDeltaNote: str | None = None
    holographicAssetUrl: str | None = None


class ArInstructionResponse(BaseModel):
    vin: str
    stationId: str
    optionCodes: list[str]
    variantCode: str
    isRareVariant: bool
    steps: list[ArInstructionStep]
    instructionVersion: str
    fetchedAt: datetime
    fallbackPdfQrUrl: str | None = None


class ArInstructionFallbackResponse(BaseModel):
    vin: str
    stationId: str
    fallbackPdfQrUrl: str
    failureLoggedAt: datetime
    correlationId: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    correlation_id: str
    details: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# OEE Snapshots
# ---------------------------------------------------------------------------

class OeeSnapshot(BaseModel):
    lineId: str
    oeePercent: float
    availability: float
    performance: float
    quality: float
    measuredAt: str
    dataAgeSeconds: int
    stale: bool


class CursorPage(BaseModel):
    next_cursor: str | None
    limit: int


class OeeSnapshotListResponse(BaseModel):
    data: list[OeeSnapshot]
    page: CursorPage


# ---------------------------------------------------------------------------
# Andon Alerts
# ---------------------------------------------------------------------------

class AndonAlertStatus(str, Enum):
    open = "open"
    acknowledged = "acknowledged"
    resolved = "resolved"
    false_positive = "false_positive"


class AndonAlertType(str, Enum):
    predictive = "predictive"
    reactive = "reactive"


class AndonAlert(BaseModel):
    alertId: UUID
    stationId: str
    lineId: str
    alertType: str
    predictedFailureType: str
    minutesToPredictedFailure: int | None
    recommendedAction: str
    status: str
    detectedAt: str
    resolvedAt: str | None = None
    acknowledgedAt: str | None = None
    opcenterEventId: str | None = None


class AndonAlertListResponse(BaseModel):
    data: list[AndonAlert]
    page: CursorPage


class DispositionEnum(str, Enum):
    resolved = "resolved"
    escalated = "escalated"
    monitoring = "monitoring"


class AlertAcknowledgeRequest(BaseModel):
    disposition: DispositionEnum
    note: str | None = Field(default=None, max_length=1000)


class AlertAcknowledgeResponse(BaseModel):
    alertId: UUID
    status: str
    acknowledgedAt: str
    eventId: UUID


# ---------------------------------------------------------------------------
# Shift Handover
# ---------------------------------------------------------------------------

class ShiftHandover(BaseModel):
    id: UUID
    shift_id: UUID
    line_id: str
    shift_start_ts: str
    shift_end_ts: str
    submitted_by_user_id: str
    alert_dispositions: list[Any]
    oee_delta_pct: float
    held_vins: list[str]
    notes: str | None = None
    idempotency_key: UUID
    created_at: str
    updated_at: str


class ShiftHandoverListResponse(BaseModel):
    data: list[ShiftHandover]
    page: CursorPage


# ---------------------------------------------------------------------------
# Expert Calls
# ---------------------------------------------------------------------------

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


class ExpertCallRequest(BaseModel):
    vin: str
    stationId: str
    sessionId: UUID
    idempotencyKey: UUID | None = None

    @field_validator("vin")
    @classmethod
    def vin_must_match_pattern(cls, v: str) -> str:
        if not _VIN_RE.match(v):
            raise ValueError("VIN must match ^[A-HJ-NPR-Z0-9]{17}$")
        return v


class ExpertCallResponse(BaseModel):
    callId: UUID
    callMode: str
    createdAt: datetime


class ExpertCallEndRequest(BaseModel):
    expertUserId: str
    durationSeconds: int = Field(..., ge=0)


class ExpertCallEndResponse(BaseModel):
    callId: UUID
    auditEntryId: UUID
    endedAt: datetime
