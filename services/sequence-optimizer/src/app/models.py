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
