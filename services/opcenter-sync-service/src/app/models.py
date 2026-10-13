"""Pydantic request/response models matching the OpenAPI contract exactly."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class GateMeasurement(BaseModel):
    measurementId: str
    name: str
    value: float
    unit: str
    toleranceLow: float
    toleranceHigh: float
    withinTolerance: bool
    reworkThresholdLow: float | None = None
    reworkThresholdHigh: float | None = None


class GateResult(BaseModel):
    gateResultId: str
    vin: str = Field(pattern=r"^[A-HJ-NPR-Z0-9]{17}$")
    gateId: str
    gateType: str
    result: str
    measurements: list[GateMeasurement]
    measuredAt: datetime
    receivedFromOpcenterAt: datetime
    opcenterEventId: str | None = None


class CursorPage(BaseModel):
    next_cursor: str | None
    limit: int


class GateResultListMetadata(BaseModel):
    timeoutSeconds: int


class GateResultListResponse(BaseModel):
    data: list[GateResult]
    page: CursorPage
    metadata: GateResultListMetadata | None = None


class ErrorResponse(BaseModel):
    code: str
    message: str
    correlation_id: str
    details: list[dict[str, Any]] | None = None


class GateResultIngestRequest(BaseModel):
    """Internal model for ingesting a gate result from OpCenter."""

    vin: str = Field(pattern=r"^[A-HJ-NPR-Z0-9]{17}$")
    gateId: str
    gateType: str = Field(pattern=r"^(dimensional|leak|electrical|nvh|adas)$")
    result: str = Field(pattern=r"^(pass|conditional_pass|fail)$")
    measurements: list[GateMeasurement] = Field(default_factory=list)
    measuredAt: datetime
    opcenterEventId: str | None = None
