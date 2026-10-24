"""Pydantic request/response models — match the LLD contract exactly."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

_HASH_RE = re.compile(r"^[a-fA-F0-9]{64}$")
_VIN_LEN = 17


class SourceSystem(str, Enum):
    quality_studio = "quality-studio"
    legacy = "legacy"


class StationResult(str, Enum):
    PASS = "PASS"
    CONDITIONAL = "CONDITIONAL"
    FAIL = "FAIL"


class StationCode(str, Enum):
    DIM = "DIM"
    LEAK = "LEAK"
    ELEC = "ELEC"
    NVH = "NVH"
    ADAS = "ADAS"


class StationResultItem(BaseModel):
    stationCode: StationCode
    result: StationResult
    details: str | None = None


class GateResultSnapshot(BaseModel):
    gateId: str
    stationResults: list[StationResultItem]


class CertificateIssueRequest(BaseModel):
    certificateId: UUID
    vin: str = Field(min_length=_VIN_LEN, max_length=_VIN_LEN)
    gateResultSnapshot: GateResultSnapshot
    healthScore: float = Field(ge=0.0, le=100.0)
    validatorId: UUID
    issuedAt: str
    certificateHash: str
    shiftId: UUID | None = None
    sourceSystem: SourceSystem = SourceSystem.quality_studio

    @field_validator("certificateHash")
    @classmethod
    def _validate_hash(cls, v: str) -> str:
        if not _HASH_RE.match(v):
            raise ValueError("certificateHash must be a 64-character hex SHA-256 digest")
        return v


class CertificateIssueResponse(BaseModel):
    certificateId: UUID
    vin: str
    fabricTransactionId: str
    fabricBlockNumber: int
    anchoredAt: str
    certificateHash: str
    idempotent: bool | None = None


class CertificateRecord(BaseModel):
    certificateId: UUID
    vin: str
    gateResultSnapshot: GateResultSnapshot
    healthScore: float
    validatorId: UUID
    issuedAt: str
    certificateHash: str
    fabricTransactionId: str
    fabricBlockNumber: int
    anchoredAt: str
    sourceSystem: SourceSystem
    shiftId: UUID | None = None


class CursorPage(BaseModel):
    next_cursor: str | None
    limit: int = Field(ge=1, le=200)


class CertificateListResponse(BaseModel):
    data: list[CertificateRecord]
    page: CursorPage


class CertificateVerifyResponse(BaseModel):
    certificateId: UUID
    verified: bool
    storedHash: str
    fabricTransactionId: str
    fabricHash: str | None = None
    verifiedAt: str | None = None
    mismatchReason: str | None = None


class ErrorResponse(BaseModel):
    code: str
    message: str
    correlation_id: str
    details: list[Any] = Field(default_factory=list)
