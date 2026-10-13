"""POST /vin/{vin}/thread/events — ingest a single manufacturing event."""

from __future__ import annotations

import re
import uuid
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
# In-process idempotency store — acts as a fast-path before the DB check.
# Tests may inject their own implementation via the dependency override.
# ---------------------------------------------------------------------------

class _IdempotencyStore:
    """Minimal in-memory store; production also enforces via PostgreSQL UNIQUE constraint."""

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

    # --- In-memory idempotency fast-path ---
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

    user_id = caller_subject(jwt_payload)

    # --- Full ingest: PostgreSQL idempotency + Neo4j write + Kafka publish ---
    from app.db import get_pool
    from app.ingest_service import (
        DuplicateEventError,
        Neo4jUnavailableError,
        Neo4jWriteError,
        VinFormatError,
        ingest_event,
    )

    try:
        pool = await get_pool()
        result = await ingest_event(vin=vin, request=body, user_id=user_id, pool=pool)
    except DuplicateEventError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_error("DUPLICATE_IDEMPOTENCY_KEY", "Already processed", corr),
        ) from exc
    except VinFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("VIN_FORMAT_INVALID", "VIN does not match ISO 3779", corr),
        ) from exc
    except Neo4jUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_error("NEO4J_UNAVAILABLE", "Neo4j cluster is temporarily unavailable", corr),
        ) from exc
    except Neo4jWriteError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_error("NEO4J_WRITE_FAILURE", "Failed to write event to the graph", corr),
        ) from exc
    except HTTPException:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_error("INTERNAL_ERROR", "An unexpected error occurred", corr),
        ) from exc

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
        utc_ts=result.linkedAt,
    )

    return result
