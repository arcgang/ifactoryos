"""Pydantic request/response models — exact match to the OpenAPI contract."""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


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


class ThreadEventIngestRequest(BaseModel):
    idempotencyKey: UUID
    eventType: EventType
    shop: str
    station: str
    occurredAt: str  # ISO-8601 date-time — validated at handler level
    dataSource: str
    parameters: dict[str, Any]
    stageNumber: int | None = Field(default=None, ge=1, le=14)
    tunnelPositionMm: float | None = None


class ThreadEventIngestResponse(BaseModel):
    vin: str
    eventId: UUID
    linkedAt: str  # ISO-8601 UTC date-time


class ErrorResponse(BaseModel):
    code: str
    message: str
    correlation_id: str
    details: list[dict[str, Any]] | None = None
