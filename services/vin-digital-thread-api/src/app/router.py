"""Route handlers — thin layer between HTTP and ingest_service."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from app.auth import CallerIdentity, require_thread_ingestor
from app.errors import error_response
from app.ingest_service import (
    DuplicateEventError,
    Neo4jUnavailableError,
    Neo4jWriteError,
    VinFormatError,
    ingest_event,
)
from app.models import ThreadEventIngestRequest, ThreadEventIngestResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_VIN_PATH = Path(description="17-character ISO 3779 VIN")


@router.post(
    "/vin/{vin}/thread/events",
    response_model=ThreadEventIngestResponse,
    status_code=201,
    operation_id="ingestVinThreadEvent",
    tags=["Event Ingestion"],
    summary="Ingest a manufacturing event and link it to the VIN node in the digital thread graph",
)
async def ingest_vin_thread_event(
    vin: Annotated[str, _VIN_PATH],
    body: ThreadEventIngestRequest,
    caller: Annotated[CallerIdentity, Depends(require_thread_ingestor)],
    request: Request,
) -> ThreadEventIngestResponse:
    from app.db import get_pool

    correlation_id = str(uuid.uuid4())

    try:
        pool = await get_pool()
        return await ingest_event(
            vin=vin,
            request=body,
            user_id=caller.user_id,
            pool=pool,
        )
    except VinFormatError:
        return error_response(400, "VIN_FORMAT_INVALID", f"VIN does not match ISO 3779: {vin}")  # type: ignore[return-value]
    except DuplicateEventError:
        return error_response(  # type: ignore[return-value]
            409,
            "DUPLICATE_IDEMPOTENCY_KEY",
            "An event with this idempotency key has already been processed",
        )
    except Neo4jUnavailableError:
        return error_response(503, "NEO4J_UNAVAILABLE", "Neo4j cluster is temporarily unavailable")  # type: ignore[return-value]
    except Neo4jWriteError:
        return error_response(500, "NEO4J_WRITE_FAILURE", "Failed to write event to the digital thread graph")  # type: ignore[return-value]
    except HTTPException:
        raise
    except (OSError, RuntimeError, ValueError) as err:
        logger.exception(
            "Unhandled error ingesting event correlation_id=%s", correlation_id
        )
        return error_response(500, "INTERNAL_ERROR", "An unexpected error occurred")  # type: ignore[return-value]
