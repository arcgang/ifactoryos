"""Pydantic request/response models for identity-service."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator


_EMPLOYEE_ID_RE = re.compile(r"^\d{8}$")


class PseudonymiseResponse(BaseModel):
    pseudonymous_token: UUID
    created: bool  # True when newly generated, False when returned from existing row


class DeletionRequestBody(BaseModel):
    employee_id: str
    name: str
    requested_at: datetime

    @field_validator("employee_id")
    @classmethod
    def _validate_employee_id(cls, v: str) -> str:
        if not _EMPLOYEE_ID_RE.match(v):
            raise ValueError("employee_id must be an 8-digit numeric string")
        return v


class DeletionRequestResponse(BaseModel):
    action: str
    pseudonymous_token: UUID
    requested_at: str
    correlation_id: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    correlation_id: str
