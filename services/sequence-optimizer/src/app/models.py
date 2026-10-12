"""Pydantic request/response models matching the OpenAPI contract exactly."""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ConstraintType(str, Enum):
    paint_capacity = "paint_capacity"
    body_wip_queue = "body_wip_queue"
    kitting_readiness = "kitting_readiness"
    maintenance_window = "maintenance_window"
    agv_delay = "agv_delay"
    press_die_overrun = "press_die_overrun"


class ConstraintThreshold(BaseModel):
    thresholdId: UUID
    constraintType: ConstraintType
    warningLevel: float
    criticalLevel: float
    unit: str
    updatedAt: str | None = None
    updatedBy: str | None = None


class CursorPage(BaseModel):
    next_cursor: str | None
    limit: int = Field(ge=1, le=200)


class ThresholdListResponse(BaseModel):
    data: list[ConstraintThreshold]
    page: CursorPage


class ErrorResponse(BaseModel):
    code: str
    message: str
    correlation_id: str
    details: list[dict[str, Any]] = Field(default_factory=list)


class ConstraintAlertEvent(BaseModel):
    constraintType: ConstraintType
    currentValue: float
    warningLevel: float
    detectedAt: str


# ---------------------------------------------------------------------------
# Optimizer request / response models
# ---------------------------------------------------------------------------

class ModelYear(str, Enum):
    MY25 = "MY25"
    MY26 = "MY26"


class ResultType(str, Enum):
    monte_carlo = "monte_carlo"
    heuristic_fallback = "heuristic_fallback"


class VinInput(BaseModel):
    vin: str = Field(pattern=r"^[A-HJ-NPR-Z0-9]{17}$")
    modelYear: ModelYear | None = None
    paintCode: str | None = None
    optionCodes: list[str] = Field(default_factory=list)
    vipPriority: bool = False
    regulatoryHold: bool = False


class MaintenanceWindow(BaseModel):
    windowId: UUID
    stationId: str
    startsAt: str
    endsAt: str
    description: str | None = None


class ConstraintSnapshot(BaseModel):
    snapshotTimestamp: str
    paintCapacityUtilisation: float = Field(ge=0, le=100)
    bodyWipQueueDepth: int = Field(ge=0)
    kittingReadinessPct: float = Field(ge=0, le=100)
    maintenanceWindows: list[MaintenanceWindow] = Field(default_factory=list)
    unsLatencyMs: int | None = None


class OptimizeRequest(BaseModel):
    weekIso: str = Field(pattern=r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")
    vins: list[VinInput] = Field(min_length=1, max_length=500)
    constraintSnapshot: ConstraintSnapshot
    monteCarloRuns: int = Field(default=50, ge=1, le=50)
    idempotencyKey: UUID | None = None
    my26ConfigVersion: str | None = None


class RankedVin(BaseModel):
    vin: str = Field(pattern=r"^[A-HJ-NPR-Z0-9]{17}$")
    position: int = Field(ge=1)
    constraintScore: float
    vipPriority: bool | None = None
    regulatoryHold: bool | None = None


class OptimizeResponse(BaseModel):
    runId: UUID
    weekIso: str
    resultType: ResultType
    rankedSequence: list[RankedVin]
    completedAt: str
    durationMs: int
    confidenceScore: float = Field(ge=0, le=1)
    my26ConfigVersion: str | None = None
    constraintSnapshotTimestamp: str | None = None
