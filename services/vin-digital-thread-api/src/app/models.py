"""Pydantic request/response models matching the OpenAPI contract exactly."""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, UUID4


class EventType(str, Enum):
    PLC_TRACE = "PLC_TRACE"
    GATE_RESULT = "GATE_RESULT"
    TORQUE_LOG = "TORQUE_LOG"
    AR_INSTRUCTION_EXECUTION = "AR_INSTRUCTION_EXECUTION"
    PAINT_PREP_STAGE = "PAINT_PREP_STAGE"
    BOOTH_PARAMETER = "BOOTH_PARAMETER"
    CLIMATE_DATA = "CLIMATE_DATA"
    SHOP_BOUNDARY_TRANSITION = "SHOP_BOUNDARY_TRANSITION"
    QUALITY_CERTIFICATION = "QUALITY_CERTIFICATION"


class VinStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    FINAL_GATE_COMPLETE = "FINAL_GATE_COMPLETE"
    UNKNOWN = "UNKNOWN"


class ThreadEventIngestRequest(BaseModel):
    idempotencyKey: UUID4
    eventType: EventType
    shop: str
    station: str
    occurredAt: str  # ISO-8601 date-time string
    dataSource: str
    parameters: dict[str, Any]
    stageNumber: int | None = Field(default=None, ge=1, le=14)
    tunnelPositionMm: float | None = None


class ThreadEventIngestResponse(BaseModel):
    vin: str
    eventId: UUID4
    linkedAt: str  # ISO-8601 UTC date-time


class ErrorDetail(BaseModel):
    field: str | None = None
    reason: str | None = None


class ErrorResponse(BaseModel):
    code: str
    message: str
    correlation_id: UUID4
    details: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Read-path response models — GET /vin/{vin}/thread and friends
# ---------------------------------------------------------------------------


class CursorPage(BaseModel):
    next_cursor: str | None
    limit: int = Field(ge=1, le=200)


class ThreadEvent(BaseModel):
    eventId: str
    vin: str
    eventType: EventType
    shop: str
    station: str
    occurredAt: str
    dataSource: str
    parameters: dict[str, Any]
    createdAt: str | None = None
    stageNumber: int | None = None
    tunnelPositionMm: float | None = None


class VinThreadResponse(BaseModel):
    vin: str
    data: list[ThreadEvent]
    page: CursorPage
    vinStatus: VinStatus | None = None


# Process Replay response models


class PrepStageSample(BaseModel):
    ts: str
    parameters: dict[str, Any]
    tunnelPositionMm: float | None = None


class PrepStageTimeSeries(BaseModel):
    stageNumber: int = Field(ge=1, le=14)
    stageName: str
    available: bool
    samples: list[PrepStageSample]


class ClimateSample(BaseModel):
    ts: str
    parameters: dict[str, Any]


class ClimateTimeSeries(BaseModel):
    available: bool
    samples: list[ClimateSample]


class BoothSample(BaseModel):
    ts: str
    parameters: dict[str, Any]


class BoothParameterTimeSeries(BaseModel):
    boothId: str
    available: bool
    samples: list[BoothSample]


class ProcessReplayResponse(BaseModel):
    vin: str
    overallCompletenessPercent: float = Field(ge=0, le=100)
    prepStages: list[PrepStageTimeSeries] = Field(min_length=14, max_length=14)
    boothParameters: list[BoothParameterTimeSeries]
    climateData: ClimateTimeSeries
    page: CursorPage
    replayWindowStart: str | None = None
    replayWindowEnd: str | None = None


# Snapshot response model


class ThreadSnapshotResponse(BaseModel):
    vin: str
    atTs: str
    stageName: str | None
    stageNumber: int | None
    tunnelPositionMm: float | None
    parameters: dict[str, Any]
    dataAvailable: bool = True


# Completeness response models


class StageCompletenessDetail(BaseModel):
    stageNumber: int = Field(ge=1, le=14)
    stageName: str
    available: bool
    completenessPercent: float = Field(ge=0, le=100)
    expectedSampleCount: int
    actualSampleCount: int
    missingParameters: list[str] | None = None


class ThreadCompletenessResponse(BaseModel):
    vin: str
    overallCompletenessPercent: float = Field(ge=0, le=100)
    meetsThreshold: bool | None = None
    stages: list[StageCompletenessDetail]
