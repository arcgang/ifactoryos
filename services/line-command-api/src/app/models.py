"""Pydantic request/response models matching the OpenAPI contract exactly."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal
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
# Andon alert models
# ---------------------------------------------------------------------------

class WeldTracePoint(BaseModel):
    timestamp: datetime
    value: float


class WeldTrace(BaseModel):
    weld_current_a: list[WeldTracePoint]
    weld_voltage_v: list[WeldTracePoint]


AlertStatusEnum = Literal["open", "acknowledged", "resolved", "false_positive"]
AlertTypeEnum = Literal["predictive", "reactive"]


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
    opcenterEventId: str | None = None
    stationId: str
    lineId: str
    alertType: AlertTypeEnum
    predictedFailureType: str
    minutesToPredictedFailure: int | None = None
    recommendedAction: str
    status: AlertStatusEnum
    detectedAt: datetime
    receivedAt: datetime
    acknowledgedAt: datetime | None = None
    resolvedAt: datetime | None = None
    correlationId: UUID
    weldTrace: WeldTrace
    thresholdCrossingAt: datetime
    thresholdValue: float | None = None


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
    status: AlertStatusEnum
    acknowledgedAt: datetime
    eventId: UUID


# ---------------------------------------------------------------------------
# Shared validators
# ---------------------------------------------------------------------------

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


# ---------------------------------------------------------------------------
# Shift Handover (LLD contract)
# ---------------------------------------------------------------------------

class AlertDispositionItem(BaseModel):
    alertId: UUID
    disposition: DispositionEnum
    note: str | None = Field(default=None, max_length=1000)


class HandoverRequest(BaseModel):
    shiftId: UUID
    lineId: str
    shiftStartTs: str
    shiftEndTs: str
    alertDispositions: list[AlertDispositionItem]
    oeeDeltaPct: float
    heldVins: list[str] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=4000)
    idempotencyKey: UUID | None = None

    @field_validator("heldVins")
    @classmethod
    def vins_must_match_pattern(cls, v: list[str]) -> list[str]:
        for vin in v:
            if not _VIN_RE.match(vin):
                raise ValueError(f"VIN must match ^[A-HJ-NPR-Z0-9]{{17}}$: {vin!r}")
        return v


class HandoverResponse(BaseModel):
    handoverId: UUID
    submittedAt: str
    submittedByUserId: str


class HandoverRecord(BaseModel):
    handoverId: UUID
    shiftId: UUID
    lineId: str
    shiftStartTs: str
    shiftEndTs: str
    submittedByUserId: str
    alertDispositions: list[AlertDispositionItem]
    oeeDeltaPct: float
    heldVins: list[str]
    notes: str | None = None
    createdAt: str
    updatedAt: str | None = None


class HandoverListResponse(BaseModel):
    data: list[HandoverRecord]
    page: CursorPage


# Legacy internal shape used by the Command Center list endpoint.
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
