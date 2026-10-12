"""Pydantic request/response models matching the OpenAPI contract exactly."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import AnyUrl, BaseModel, Field


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


DispositionEnum = Literal["resolved", "escalated", "monitoring"]


class AlertAcknowledgeRequest(BaseModel):
    disposition: DispositionEnum
    note: str | None = Field(default=None, max_length=1000)


class AlertAcknowledgeResponse(BaseModel):
    alertId: UUID
    status: AlertStatusEnum
    acknowledgedAt: datetime
    eventId: UUID
