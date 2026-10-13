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
