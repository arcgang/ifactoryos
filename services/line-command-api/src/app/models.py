"""Pydantic request/response models matching the OpenAPI contract exactly."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
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
