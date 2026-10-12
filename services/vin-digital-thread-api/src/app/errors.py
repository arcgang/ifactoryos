"""Standard error envelope matching the contract's Error schema."""

from __future__ import annotations

import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    code: str
    message: str
    correlation_id: str
    details: list[dict] | None = None


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[dict] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        code=code,
        message=message,
        correlation_id=str(uuid.uuid4()),
        details=details,
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))
