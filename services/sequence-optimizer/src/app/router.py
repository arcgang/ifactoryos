"""FastAPI router for constraint threshold and sequence optimizer endpoints."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import caller_subject, require_bearer, require_threshold_writer
from app.models import (
    ConstraintThreshold,
    OptimizeRequest,
    OptimizeResponse,
    ThresholdListResponse,
)

router = APIRouter(tags=["Constraints"])


async def _get_pool() -> asyncpg.Pool:
    from app.db import get_pool

    return await get_pool()


@router.get(
    "/constraints/thresholds",
    response_model=ThresholdListResponse,
    operation_id="listConstraintThresholds",
)
async def list_constraint_thresholds(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> ThresholdListResponse:
    from app.thresholds import list_thresholds

    return await list_thresholds(pool=pool, cursor=cursor, limit=limit)


@router.put(
    "/constraints/thresholds",
    response_model=ConstraintThreshold,
    operation_id="upsertConstraintThreshold",
)
async def upsert_constraint_threshold(
    body: ConstraintThreshold,
    payload: dict[str, Any] = Depends(require_threshold_writer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> ConstraintThreshold:
    from app.thresholds import upsert_threshold

    updated_by = caller_subject(payload)
    return await upsert_threshold(pool=pool, threshold=body, updated_by=updated_by)


# ---------------------------------------------------------------------------
# Optimizer endpoints
# ---------------------------------------------------------------------------

optimize_router = APIRouter(tags=["Optimizer"])


@optimize_router.post(
    "/optimize",
    response_model=OptimizeResponse,
    operation_id="runOptimizer",
    status_code=status.HTTP_200_OK,
)
async def run_optimizer(
    body: OptimizeRequest,
    payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> OptimizeResponse:
    from app.kserve_client import make_kserve_client
    from app.optimizer import run_optimize
    from app.validation import validate_option_codes

    user_id = caller_subject(payload)

    # 422: unknown option codes
    unknown = validate_option_codes(body.vins)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "UNKNOWN_OPTION_CODES",
                "message": "One or more VINs carry option codes not present in the constraint model",
                "correlation_id": str(uuid.uuid4()),
                "details": [{"unknown_codes": list(unknown)}],
            },
        )

    kserve_client = make_kserve_client()
    return await run_optimize(
        request=body,
        pool=pool,
        kserve_client=kserve_client,
        user_id=user_id,
    )


@optimize_router.get(
    "/optimize/{runId}",
    response_model=OptimizeResponse,
    operation_id="getOptimizeRun",
)
async def get_optimize_run(
    runId: uuid.UUID,
    _payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> OptimizeResponse:
    from app.optimizer import get_run

    result = await get_run(runId, pool)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "RUN_NOT_FOUND",
                "message": f"Optimisation run {runId} not found",
                "correlation_id": str(uuid.uuid4()),
            },
        )
    return result
