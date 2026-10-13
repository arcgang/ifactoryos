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


class ThreadEvent(BaseModel):
    eventId: str = Field(description="Stable unique identifier for this event node in the graph")
    vin: str = Field(description="17-character VIN this event is linked to")
    eventType: EventType
    shop: str = Field(description="Shop code where the event occurred")
    station: str = Field(description="Station identifier within the shop")
    occurredAt: str = Field(description="UTC timestamp when the event occurred at the physical station")
    dataSource: str = Field(description="Originating system identifier")
    parameters: dict[str, Any] = Field(description="Key-value map of process parameters")
    createdAt: str | None = Field(default=None, description="UTC timestamp when written to the digital thread graph")
    stageNumber: int | None = Field(default=None, description="Paint prep stage number 1-14; null for non-paint events")
    tunnelPositionMm: float | None = Field(default=None, description="VIN position in the prep tunnel in mm; null for non-tunnel events")


class CursorPage(BaseModel):
    next_cursor: str | None = Field(description="Opaque; null on the last page")
    limit: int = Field(ge=1, le=200)


class VinThreadResponse(BaseModel):
    vin: str
    data: list[ThreadEvent]
    page: CursorPage
    vinStatus: VinStatus | None = None


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
    meetsThreshold: bool | None = Field(default=None, description="True when overallCompletenessPercent >= 95")
    stages: list[StageCompletenessDetail]


class ErrorDetail(BaseModel):
    field: str | None = None
    reason: str | None = None


class ErrorResponse(BaseModel):
    code: str
    message: str
    correlation_id: UUID4
    details: list[dict[str, Any]] = Field(default_factory=list)
