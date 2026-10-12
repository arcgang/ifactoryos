"""AR instruction service — cache lookup, VDT fetch, failure logging, session recording.

All database and HTTP I/O is handled here; the router stays thin.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import httpx
from fastapi import HTTPException

from app.models import (
    ArInstructionResponse,
    ArInstructionStep,
    DeviceType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB connection protocol — allows test injection without asyncpg import
# ---------------------------------------------------------------------------

class DBConnection(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Any | None: ...
    async def execute(self, query: str, *args: Any) -> None: ...


# ---------------------------------------------------------------------------
# Cache lookup
# ---------------------------------------------------------------------------

async def fetch_from_cache(
    conn: DBConnection,
    vin: str,
    station_id: str,
) -> ArInstructionResponse | None:
    row = await conn.fetchrow(
        """
        SELECT payload_jsonb, fallback_pdf_qr_url, fetched_at
        FROM ar_instruction_cache
        WHERE vin = $1
          AND station_id = $2
          AND expires_at > NOW()
        ORDER BY fetched_at DESC
        LIMIT 1
        """,
        vin,
        station_id,
    )
    if row is None:
        return None
    payload: dict = dict(row["payload_jsonb"])
    return ArInstructionResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# VDT fetch (cache miss path)
# ---------------------------------------------------------------------------

async def fetch_from_vdt(
    vin: str,
    station_id: str,
    vdt_base_url: str,
    timeout_seconds: float,
    fallback_pdf_qr_url_template: str,
) -> ArInstructionResponse:
    """Call vin-digital-thread-api and return a resolved ArInstructionResponse.

    Raises httpx.TimeoutException when the upstream does not respond within
    timeout_seconds — callers handle this to produce the 504 path.
    """
    url = f"{vdt_base_url}/api/v1/vin/{vin}/thread"
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.get(url)

    if resp.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "VIN_NOT_FOUND",
                "message": f"VIN {vin} not found in digital thread",
                "correlation_id": str(uuid.uuid4()),
            },
        )

    resp.raise_for_status()
    thread: dict = resp.json()

    steps = [
        ArInstructionStep(
            stepNumber=s["stepNumber"],
            description=s["description"],
            torqueSpecNm=s["torqueSpecNm"],
            torqueToleranceNm=s["torqueToleranceNm"],
            fastenerPointId=s["fastenerPointId"],
            isVariantDelta=s.get("isVariantDelta"),
            variantDeltaNote=s.get("variantDeltaNote"),
            holographicAssetUrl=s.get("holographicAssetUrl"),
        )
        for s in thread.get("steps", [])
    ]

    fallback_url = fallback_pdf_qr_url_template.format(vin=vin)

    return ArInstructionResponse(
        vin=vin,
        stationId=station_id,
        optionCodes=thread.get("optionCodes", []),
        variantCode=thread.get("variantCode", ""),
        isRareVariant=thread.get("isRareVariant", False),
        steps=steps,
        instructionVersion=thread.get("instructionVersion", ""),
        fetchedAt=datetime.now(timezone.utc),
        fallbackPdfQrUrl=fallback_url,
    )


# ---------------------------------------------------------------------------
# Cache upsert
# ---------------------------------------------------------------------------

async def store_in_cache(
    conn: DBConnection,
    response: ArInstructionResponse,
    ttl_seconds: int,
) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    payload = response.model_dump(mode="json")
    await conn.execute(
        """
        INSERT INTO ar_instruction_cache
            (vin, station_id, option_codes, variant_code, is_rare_variant,
             instruction_version, payload_jsonb, fallback_pdf_qr_url,
             fetched_at, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, NOW(), $9)
        """,
        response.vin,
        response.stationId,
        response.optionCodes,
        response.variantCode,
        response.isRareVariant,
        response.instructionVersion,
        json.dumps(payload),
        response.fallbackPdfQrUrl or "",
        expires_at,
    )


# ---------------------------------------------------------------------------
# Failure log insert (append-only)
# ---------------------------------------------------------------------------

async def log_failure(
    conn: DBConnection,
    vin: str,
    station_id: str,
    device_type: DeviceType,
    failure_reason: str,
    correlation_id: str,
) -> datetime:
    failed_at = datetime.now(timezone.utc)
    await conn.execute(
        """
        INSERT INTO ar_instruction_failure_log
            (vin, station_id, device_type, failure_reason, correlation_id, failed_at)
        VALUES ($1, $2, $3, $4, $5::uuid, $6)
        """,
        vin,
        station_id,
        device_type.value,
        failure_reason,
        correlation_id,
        failed_at,
    )
    return failed_at


# ---------------------------------------------------------------------------
# AR session insert (idempotent via UNIQUE constraint)
# ---------------------------------------------------------------------------

async def insert_ar_session(
    conn: DBConnection,
    vin: str,
    station_id: str,
    device_type: DeviceType,
    user_id: str,
    idempotency_key: str,
) -> str:
    """Insert an ar_session row. Returns the event_id for the Kafka event.

    Uses ON CONFLICT DO NOTHING to honour the UNIQUE(idempotency_key) constraint
    idempotently. Returns the event_id whether the row was new or pre-existing.
    """
    event_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO ar_session
            (vin, station_id, device_type, user_id, idempotency_key, event_id)
        VALUES ($1, $2, $3, $4, $5::uuid, $6::uuid)
        ON CONFLICT (idempotency_key) DO NOTHING
        """,
        vin,
        station_id,
        device_type.value,
        user_id,
        idempotency_key,
        event_id,
    )
    return event_id
