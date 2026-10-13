"""GET /vin/{vin}/thread and GET /vin/{vin}/thread/completeness."""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import require_quality_role
from app.models import (
    ErrorResponse,
    ThreadCompletenessResponse,
    VinThreadResponse,
)

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

router = APIRouter()


def _correlation_id() -> str:
    return str(uuid.uuid4())


def _error(code: str, message: str, correlation_id: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": correlation_id}


@router.get(
    "/vin/{vin}/thread",
    response_model=VinThreadResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["Digital Thread"],
    operation_id="getVinThread",
)
async def get_vin_thread(
    vin: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    shopFilter: str | None = Query(default=None),
    fromTs: str | None = Query(default=None),
    toTs: str | None = Query(default=None),
    _jwt: dict[str, Any] = Depends(require_quality_role),
) -> VinThreadResponse:
    corr = _correlation_id()

    if not _VIN_RE.match(vin):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("VIN_FORMAT_INVALID", "VIN must match ^[A-HJ-NPR-Z0-9]{17}$", corr),
        )

    shop_filter: list[str] | None = None
    if shopFilter:
        shop_filter = [s.strip() for s in shopFilter.split(",") if s.strip()]

    from app.thread_query_service import CursorDecodeError, VinNotFoundError, query_vin_thread

    try:
        return await query_vin_thread(
            vin,
            cursor=cursor,
            limit=limit,
            shop_filter=shop_filter,
            from_ts=fromTs,
            to_ts=toTs,
        )
    except VinNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("VIN_NOT_FOUND", f"VIN {vin!r} not found in the digital thread graph", corr),
        ) from exc
    except CursorDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_CURSOR", "Pagination cursor is malformed", corr),
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_error("NEO4J_UNAVAILABLE", "Neo4j cluster is temporarily unavailable", corr),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_error("INTERNAL_ERROR", "An unexpected error occurred", corr),
        ) from exc


@router.get(
    "/vin/{vin}/thread/completeness",
    response_model=ThreadCompletenessResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    tags=["Process Replay"],
    operation_id="getVinThreadCompleteness",
)
async def get_vin_thread_completeness(
    vin: str,
    _jwt: dict[str, Any] = Depends(require_quality_role),
) -> ThreadCompletenessResponse:
    corr = _correlation_id()

    if not _VIN_RE.match(vin):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("VIN_FORMAT_INVALID", "VIN must match ^[A-HJ-NPR-Z0-9]{17}$", corr),
        )

    from app.db import get_pool
    from app.thread_query_service import VinNotFoundError, query_vin_completeness

    try:
        pool = await get_pool()
        return await query_vin_completeness(vin, pool)
    except VinNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("VIN_NOT_FOUND", f"VIN {vin!r} not found", corr),
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_error("NEO4J_UNAVAILABLE", "Neo4j cluster is temporarily unavailable", corr),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_error("INTERNAL_ERROR", "An unexpected error occurred", corr),
        ) from exc
