"""POST /vin/{vin}/thread/events — ingest a single manufacturing event."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.audit import fire_audit_event, sha256_hex
from app.auth import caller_subject, require_ingestor_role
from app.models import (
    ErrorResponse,
    ThreadEventIngestRequest,
    ThreadEventIngestResponse,
)

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

router = APIRouter()


def _correlation_id() -> str:
    return str(uuid.uuid4())


def _error(code: str, message: str, correlation_id: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": correlation_id}


# ---------------------------------------------------------------------------
# In-process idempotency store (replaced by PostgreSQL in production).
# Tests may inject their own implementation via the dependency override.
# ---------------------------------------------------------------------------

class _IdempotencyStore:
    """Minimal in-memory store; a real deployment persists to PostgreSQL."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def check_and_register(self, key: str) -> bool:
        """Return True if the key is new, False if it was already seen."""
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


_default_store = _IdempotencyStore()


def get_idempotency_store() -> _IdempotencyStore:
    return _default_store


@router.post(
    "/vin/{vin}/thread/events",
    status_code=status.HTTP_201_CREATED,
    response_model=ThreadEventIngestResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["Event Ingestion"],
    operation_id="ingestVinThreadEvent",
)
async def ingest_vin_thread_event(
    vin: str,
    body: ThreadEventIngestRequest,
    background: BackgroundTasks,
    jwt_payload: dict[str, Any] = Depends(require_ingestor_role),
    store: _IdempotencyStore = Depends(get_idempotency_store),
) -> ThreadEventIngestResponse:
    corr = _correlation_id()

    # --- VIN format ---
    if not _VIN_RE.match(vin):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("VIN_FORMAT_INVALID", "VIN must match ^[A-HJ-NPR-Z0-9]{17}$", corr),
        )

    # --- Idempotency ---
    idempotency_key = str(body.idempotencyKey)
    if not store.check_and_register(idempotency_key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_error(
                "DUPLICATE_IDEMPOTENCY_KEY",
                f"Event with idempotencyKey {idempotency_key} already processed",
                corr,
            ),
        )

    # --- Assign identifiers ---
    event_id = uuid.uuid4()
    linked_at = datetime.now(timezone.utc).isoformat()
    user_id = caller_subject(jwt_payload)

    # --- Audit write (fire-and-forget, must not block HTTP response) ---
    after_hash = sha256_hex(
        {
            "vin": vin,
            "eventType": body.eventType.value,
            "shop": body.shop,
            "station": body.station,
            "occurredAt": body.occurredAt,
            "dataSource": body.dataSource,
            "idempotencyKey": idempotency_key,
            "parameters": body.parameters,
        }
    )

    background.add_task(
        fire_audit_event,
        user_id=user_id,
        action_type="VIN_THREAD_EVENT_LINKED",
        entity_id=vin,
        after_hash=after_hash,
        utc_ts=linked_at,
    )

    return ThreadEventIngestResponse(
        vin=vin,
        eventId=event_id,
        linkedAt=linked_at,
    )
