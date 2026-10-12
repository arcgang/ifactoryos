"""FastAPI router for constraint threshold endpoints."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.auth import caller_subject, require_bearer, require_threshold_writer
from app.models import ConstraintThreshold, ThresholdListResponse

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
