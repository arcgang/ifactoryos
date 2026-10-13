"""POST /ar/sessions — AR Workbench session start for adoption instrumentation.

Implements AC-9c6cd996, AC-fd1f00c6.
No biometric or gaze data stored or emitted (BetrVG §87 / EA-DAT-002).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import caller_subject, require_bearer
from app.db import get_pool
from app.kafka_producer import TOPIC_ADOPTION_EVENT, TOPIC_AR_SESSION_STARTED, publish
from app.models import DeviceType, ErrorResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AR Instructions"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ArSessionStartRequest(BaseModel):
    vin: str = Field(pattern=r"^[A-HJ-NPR-Z0-9]{17}$")
    stationId: str
    deviceType: DeviceType
    idempotencyKey: uuid.UUID | None = None

    # Strict — reject any extra fields to enforce no-biometric policy
    model_config = {"extra": "forbid"}


class ArSessionStartResponse(BaseModel):
    sessionId: str
    eventId: str
    recordedAt: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


# ---------------------------------------------------------------------------
# POST /ar/sessions
# ---------------------------------------------------------------------------

@router.post(
    "/ar/sessions",
    operation_id="startArSession",
    summary="Record the start of an AR Workbench session for adoption instrumentation",
    status_code=201,
    responses={
        201: {"model": ArSessionStartResponse},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def start_ar_session(
    body: ArSessionStartRequest,
    jwt_payload: dict[str, Any] = Depends(require_bearer),
    pool: Any = Depends(get_pool),
) -> JSONResponse:
    user_id = caller_subject(jwt_payload)
    roles: list[str] = jwt_payload.get("roles", []) or []
    caller_role = roles[0] if roles else "unknown"
    idempotency_key = str(body.idempotencyKey) if body.idempotencyKey else str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    recorded_at = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM ar_session WHERE idempotency_key = $1::uuid",
            idempotency_key,
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_error(
                    "DUPLICATE_AR_SESSION",
                    f"Idempotency key {idempotency_key} already processed",
                ),
            )

        await conn.execute(
            """
            INSERT INTO ar_session
                (id, vin, station_id, device_type, user_id, idempotency_key, event_id)
            VALUES
                ($1::uuid, $2, $3, $4, $5, $6::uuid, $7::uuid)
            """,
            session_id,
            body.vin,
            body.stationId,
            body.deviceType.value,
            user_id,
            idempotency_key,
            event_id,
        )

    asyncio.create_task(
        _publish_ar_session_events(
            session_id=session_id,
            event_id=event_id,
            vin=body.vin,
            station_id=body.stationId,
            device_type=body.deviceType.value,
            user_id=user_id,
            caller_role=caller_role,
            recorded_at=recorded_at.isoformat(),
        )
    )

    resp = ArSessionStartResponse(
        sessionId=session_id,
        eventId=event_id,
        recordedAt=recorded_at,
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=json.loads(resp.model_dump_json()),
    )


async def _publish_ar_session_events(
    session_id: str,
    event_id: str,
    vin: str,
    station_id: str,
    device_type: str,
    user_id: str,
    caller_role: str,
    recorded_at: str,
) -> None:
    # ArSessionStartedEvent — no biometric fields
    try:
        await publish(
            TOPIC_AR_SESSION_STARTED,
            {
                "eventId": event_id,
                "sessionId": session_id,
                "vin": vin,
                "stationId": station_id,
                "deviceType": device_type,
                "userId": user_id,
                "startedAt": recorded_at,
            },
        )
    except Exception as exc:
        logger.warning("ArSessionStartedEvent publish failed for sessionId=%s: %s", session_id, exc)

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        await asyncio.wait_for(
            publish(
                TOPIC_ADOPTION_EVENT,
                {
                    "eventId": event_id,
                    "userId": user_id,
                    "role": caller_role,
                    "actionType": "AR_SESSION_STARTED",
                    "vin": vin,
                    "stationId": station_id,
                    "deviceType": device_type,
                    "occurredAt": now_iso,
                },
            ),
            timeout=1.0,
        )
    except (OSError, asyncio.TimeoutError, RuntimeError) as exc:
        logger.warning("AdoptionEvent publish failed for sessionId=%s: %s", session_id, exc)
