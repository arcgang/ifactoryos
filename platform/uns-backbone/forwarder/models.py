"""Shared request/response models — do not rename fields; shared contract with Children 0 and 1."""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, field_validator

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class RejectionReason(str, Enum):
    expired_cert = "expired_cert"
    unknown_ca = "unknown_ca"
    no_cert_presented = "no_cert_presented"


class RejectionEvent(BaseModel):
    """Shared payload for POST /rejections/mqtt and POST /rejections/kafka."""

    timestamp: str
    source_ip: str
    gateway_id: str
    rejection_reason: RejectionReason
    correlation_id: str

    @field_validator("timestamp")
    @classmethod
    def _timestamp_is_iso8601(cls, v: str) -> str:
        from datetime import datetime
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as err:
            raise ValueError(f"timestamp must be ISO-8601 UTC: {v!r}") from err
        return v

    @field_validator("correlation_id")
    @classmethod
    def _correlation_id_is_uuid4(cls, v: str) -> str:
        if not _UUID_RE.match(v):
            raise ValueError(f"correlation_id must be a UUID v4: {v!r}")
        return v

    @field_validator("source_ip")
    @classmethod
    def _source_ip_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("source_ip must not be blank")
        return v

    @field_validator("gateway_id")
    @classmethod
    def _gateway_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("gateway_id must not be blank")
        return v
