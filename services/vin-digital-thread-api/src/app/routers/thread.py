"""GET /vin/{vin}/thread and GET /vin/{vin}/thread/completeness."""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import require_quality_reader
from app.models import (
    ErrorResponse,
    ProcessReplayResponse,
    ThreadCompletenessResponse,
    ThreadSnapshotResponse,
    VinThreadResponse,
)

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

router = APIRouter(tags=["Digital Thread"])


def _corr() -> str:
    return str(uuid.uuid4())


def _err(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": _corr()}


def _validate_vin(vin: str) -> None:
    if not _VIN_RE.match(vin):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_err("VIN_FORMAT_INVALID", "VIN must be 17 characters matching ^[A-HJ-NPR-Z0-9]{17}$"),
        )


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
    operation_id="getVinThread",
    summary="Retrieve the full digital thread for a VIN",
)
async def get_vin_thread(
    vin: str,
    cursor: Annotated[str | None, Query(description="Opaque pagination cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Max events to return")] = 100,
    shopFilter: Annotated[str | None, Query(description="Comma-separated shop codes")] = None,
    fromTs: Annotated[str | None, Query(description="ISO 8601 UTC lower bound")] = None,
    toTs: Annotated[str | None, Query(description="ISO 8601 UTC upper bound")] = None,
    _jwt: dict[str, Any] = Depends(require_quality_reader),
) -> VinThreadResponse:
    from app.db import get_pool
    from app.thread_query_service import (
        Neo4jReadError,
        Neo4jUnavailableError,
        VinNotFoundError,
        get_vin_thread as _get_vin_thread,
    )

    _validate_vin(vin)

    shop_list: list[str] | None = None
    if shopFilter:
        shop_list = [s.strip() for s in shopFilter.split(",") if s.strip()]

    if cursor is not None:
        from app.thread_query_service import _decode_cursor
        try:
            _decode_cursor(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_err("INVALID_CURSOR", "Pagination cursor is malformed"),
            )

    try:
        pool = await get_pool()
        return await _get_vin_thread(
            vin=vin,
            cursor=cursor,
            limit=limit,
            shop_filter=shop_list,
            from_ts=fromTs,
            to_ts=toTs,
            pool=pool,
        )
    except VinNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_err("VIN_NOT_FOUND", f"VIN {vin!r} not found in the digital thread graph"),
        )
    except Neo4jUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_err("NEO4J_UNAVAILABLE", "Neo4j cluster is temporarily unavailable"),
        )
    except Neo4jReadError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_err("NEO4J_READ_FAILURE", "Failed to read from the digital thread graph"),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_err("INTERNAL_ERROR", "An unexpected error occurred"),
        )


@router.get(
    "/vin/{vin}/thread/replay",
    response_model=ProcessReplayResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    operation_id="getVinProcessReplay",
    summary="Retrieve the Process Replay payload for a VIN",
)
async def get_vin_process_replay(
    vin: str,
    cursor: Annotated[str | None, Query(description="Opaque pagination cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Max parameter samples per page")] = 200,
    _jwt: dict[str, Any] = Depends(require_quality_reader),
) -> ProcessReplayResponse:
    from app.db import get_pool
    from app.thread_query_service import (
        Neo4jReadError,
        Neo4jUnavailableError,
        VinNotFoundError,
        get_process_replay,
    )

    _validate_vin(vin)

    if cursor is not None:
        from app.thread_query_service import _decode_cursor
        try:
            _decode_cursor(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_err("INVALID_CURSOR", "Pagination cursor is malformed"),
            )

    try:
        pool = await get_pool()
        return await get_process_replay(vin=vin, cursor=cursor, limit=limit, pool=pool)
    except VinNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_err("VIN_NOT_FOUND", f"VIN {vin!r} not found in the digital thread graph"),
        )
    except Neo4jUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_err("NEO4J_UNAVAILABLE", "Neo4j cluster is temporarily unavailable"),
        )
    except Neo4jReadError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_err("NEO4J_READ_FAILURE", "Failed to read from the digital thread graph"),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_err("INTERNAL_ERROR", "An unexpected error occurred"),
        )


@router.get(
    "/vin/{vin}/thread/snapshot",
    response_model=ThreadSnapshotResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    operation_id="getVinThreadSnapshot",
    summary="Return all parameter values for a VIN at a specific timestamp",
)
async def get_vin_thread_snapshot(
    vin: str,
    atTs: Annotated[str, Query(description="ISO 8601 UTC timestamp for the scrub point")],
    _jwt: dict[str, Any] = Depends(require_quality_reader),
) -> ThreadSnapshotResponse:
    from app.db import get_pool
    from app.thread_query_service import (
        Neo4jReadError,
        Neo4jUnavailableError,
        VinNotFoundError,
        get_thread_snapshot,
    )

    _validate_vin(vin)

    from datetime import datetime
    try:
        datetime.fromisoformat(atTs.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_err("INVALID_TIMESTAMP", "atTs must be a valid ISO 8601 UTC timestamp"),
        )

    try:
        pool = await get_pool()
        return await get_thread_snapshot(vin=vin, at_ts=atTs, pool=pool)
    except VinNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_err("VIN_NOT_FOUND", f"VIN {vin!r} not found or no data at {atTs!r}"),
        )
    except Neo4jUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_err("NEO4J_UNAVAILABLE", "Neo4j cluster is temporarily unavailable"),
        )
    except Neo4jReadError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_err("NEO4J_READ_FAILURE", "Failed to read snapshot from the digital thread graph"),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_err("INTERNAL_ERROR", "An unexpected error occurred"),
        )


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
    operation_id="getVinThreadCompleteness",
    summary="Return per-stage completeness metrics for a VIN's digital thread",
)
async def get_vin_thread_completeness(
    vin: str,
    _jwt: dict[str, Any] = Depends(require_quality_reader),
) -> ThreadCompletenessResponse:
    from app.db import get_pool
    from app.thread_query_service import (
        Neo4jReadError,
        Neo4jUnavailableError,
        VinNotFoundError,
        get_thread_completeness,
    )

    _validate_vin(vin)

    try:
        pool = await get_pool()
        return await get_thread_completeness(vin=vin, pool=pool)
    except VinNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_err("VIN_NOT_FOUND", f"VIN {vin!r} not found in the digital thread graph"),
        )
    except Neo4jUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_err("NEO4J_UNAVAILABLE", "Neo4j cluster is temporarily unavailable"),
        )
    except Neo4jReadError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_err("NEO4J_READ_FAILURE", "Failed to read completeness from graph"),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_err("INTERNAL_ERROR", "An unexpected error occurred"),
        )
