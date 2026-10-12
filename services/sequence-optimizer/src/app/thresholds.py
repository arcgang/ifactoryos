"""Threshold service — pure business logic; no HTTP dependencies."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone

import asyncpg

from app.models import (
    ConstraintThreshold,
    ConstraintType,
    CursorPage,
    ThresholdListResponse,
)

_PAGE_DEFAULT = 50
_PAGE_MAX = 200


def _encode_cursor(threshold_id: str) -> str:
    return base64.urlsafe_b64encode(threshold_id.encode()).decode()


def _decode_cursor(cursor: str) -> str:
    return base64.urlsafe_b64decode(cursor.encode()).decode()


def _row_to_threshold(row: asyncpg.Record) -> ConstraintThreshold:
    return ConstraintThreshold(
        thresholdId=row["threshold_id"],
        constraintType=ConstraintType(row["constraint_type"]),
        warningLevel=row["warning_level"],
        criticalLevel=row["critical_level"],
        unit=row["unit"],
        updatedAt=row["updated_at"].isoformat() if row["updated_at"] else None,
        updatedBy=row["updated_by"],
    )


async def list_thresholds(
    pool: asyncpg.Pool,
    cursor: str | None,
    limit: int | None,
) -> ThresholdListResponse:
    page_limit = min(limit or _PAGE_DEFAULT, _PAGE_MAX)

    async with pool.acquire() as conn:
        if cursor is not None:
            after_id = _decode_cursor(cursor)
            rows = await conn.fetch(
                """
                SELECT threshold_id, constraint_type, warning_level, critical_level,
                       unit, updated_at, updated_by
                FROM constraint_threshold
                WHERE threshold_id > $1::uuid
                ORDER BY threshold_id
                LIMIT $2
                """,
                after_id,
                page_limit + 1,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT threshold_id, constraint_type, warning_level, critical_level,
                       unit, updated_at, updated_by
                FROM constraint_threshold
                ORDER BY threshold_id
                LIMIT $1
                """,
                page_limit + 1,
            )

    has_more = len(rows) > page_limit
    page_rows = rows[:page_limit]
    next_cursor = (
        _encode_cursor(str(page_rows[-1]["threshold_id"])) if has_more else None
    )

    return ThresholdListResponse(
        data=[_row_to_threshold(r) for r in page_rows],
        page=CursorPage(next_cursor=next_cursor, limit=page_limit),
    )


async def upsert_threshold(
    pool: asyncpg.Pool,
    threshold: ConstraintThreshold,
    updated_by: str,
) -> ConstraintThreshold:
    now = datetime.now(tz=timezone.utc)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO constraint_threshold
                (threshold_id, constraint_type, warning_level, critical_level,
                 unit, updated_at, updated_by)
            VALUES ($1::uuid, $2::constraint_type, $3, $4, $5, $6, $7)
            ON CONFLICT (constraint_type) DO UPDATE
                SET threshold_id   = EXCLUDED.threshold_id,
                    warning_level  = EXCLUDED.warning_level,
                    critical_level = EXCLUDED.critical_level,
                    unit           = EXCLUDED.unit,
                    updated_at     = EXCLUDED.updated_at,
                    updated_by     = EXCLUDED.updated_by
            RETURNING threshold_id, constraint_type, warning_level, critical_level,
                      unit, updated_at, updated_by
            """,
            str(threshold.thresholdId),
            threshold.constraintType.value,
            threshold.warningLevel,
            threshold.criticalLevel,
            threshold.unit,
            now,
            updated_by,
        )

    return _row_to_threshold(row)


async def get_warning_level(
    pool: asyncpg.Pool,
    constraint_type: str,
) -> float | None:
    """Return the warningLevel for the given constraintType, or None if not configured."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT warning_level
            FROM constraint_threshold
            WHERE constraint_type = $1::constraint_type
            """,
            constraint_type,
        )
    if row is None:
        return None
    return row["warning_level"]
