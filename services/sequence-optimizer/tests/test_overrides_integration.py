"""Integration tests for override log and VIN explanation round-trips.

Uses in-memory pool stubs (consistent with test_optimizer_integration.py).
Covers:
  - POST /overrides INSERT → GET /overrides/{overrideId} round-trip
  - GET /overrides cursor pagination continuation
  - GET /overrides/export application/pdf Content-Type
  - GET /optimize/{runId}/explain/{vin} top-factor retrieval
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.explain import get_explanation, store_explanation
from app.models import (
    ConstraintFactor,
    ConstraintFactorName,
    CursorPage,
    OverrideListResponse,
    OverrideRecord,
    OverrideRequest,
    VinExplanation,
)
from app.overrides import (
    build_override_pdf,
    create_override,
    get_override,
    list_overrides,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FAR_FUTURE = "2099-01-01T00:00:00+00:00"
_VIN = "WBA3B3C56DF596858"
_RUN_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
_WEEK_ISO = "2026-W42"
_PLANNER_ID = "planner@bmwgroup.net"


# ---------------------------------------------------------------------------
# In-memory override pool stub
# ---------------------------------------------------------------------------


class _OverrideConn:
    """In-memory asyncpg connection stub for override_log queries."""

    def __init__(self, pool: "_OverridePool") -> None:
        self._pool = pool

    async def __aenter__(self) -> "_OverrideConn":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
        if "WHERE override_id = $1" in query:
            rec = self._pool._overrides.get(args[0])
            return rec
        if "WHERE run_id = $1 AND vin = $2" in query:
            key = (args[0], args[1])
            return self._pool._explanations.get(key)
        return None

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        rows = list(self._pool._overrides.values())

        # Apply week_iso filter using to_char emulation (parse from query params directly)
        # In the stub we apply filters by checking each bound param
        filtered = rows[:]

        # Check for week_iso filter in params: first TEXT param that matches ISO week pattern
        for param in args:
            if isinstance(param, str) and param.startswith("20") and "-W" in param:
                filtered = [
                    r for r in filtered
                    if r.get("_week_iso") == param
                ]
                break

        # UUID filter: any UUID arg that is not _RUN_ID-like is a run_id filter
        for param in args:
            if isinstance(param, uuid.UUID):
                filtered = [r for r in filtered if r["run_id"] == param]
                break

        # Cursor: decode and filter
        cursor_str: str | None = None
        for param in args:
            if isinstance(param, str) and not ("-W" in param) and len(param) > 20:
                cursor_str = param
                break
        if cursor_str is not None:
            try:
                decoded = base64.b64decode(cursor_str.encode()).decode()
                parts = decoded.split("|")
                cursor_id = parts[1]
                # Return rows that come after this cursor (by inserted order)
                ids = [str(r["override_id"]) for r in filtered]
                if cursor_id in ids:
                    idx = ids.index(cursor_id)
                    filtered = filtered[idx + 1:]
            except (ValueError, IndexError, UnicodeDecodeError):
                pass  # malformed cursor in stub — return from start

        # Limit: last integer arg
        limit = 51
        for param in reversed(args):
            if isinstance(param, int):
                limit = param
                break

        return filtered[:limit]

    async def execute(self, query: str, *args: object) -> None:
        if "INSERT INTO override_log" in query:
            (
                override_id,
                run_id,
                vin,
                original_position,
                new_position,
                justification,
                planner_id,
            ) = args
            now = datetime.now(tz=timezone.utc)
            week_iso = now.strftime("%G-W%V")
            record: dict[str, Any] = {
                "override_id": override_id,
                "run_id": run_id,
                "vin": vin,
                "original_position": original_position,
                "new_position": new_position,
                "justification": justification,
                "planner_id": planner_id,
                "created_at": now,
                "_week_iso": week_iso,
            }
            self._pool._overrides[override_id] = record
            self._pool._override_list.append(override_id)
        elif "INSERT INTO vin_constraint_explanation" in query:
            run_id, vin, position, top_factors_json = args
            key = (run_id, vin)
            factors_raw = json.loads(top_factors_json) if isinstance(top_factors_json, str) else top_factors_json
            self._pool._explanations[key] = {
                "run_id": run_id,
                "vin": vin,
                "position": position,
                "top_factors": factors_raw,
            }

    async def fetchrow_returning(self, query: str, *args: object) -> dict[str, Any] | None:
        return await self.fetchrow(query, *args)


class _OverridePool:
    """Minimal asyncpg.Pool stub; each instance starts with empty state."""

    def __init__(self) -> None:
        self._overrides: dict[uuid.UUID, dict[str, Any]] = {}
        self._override_list: list[uuid.UUID] = []
        self._explanations: dict[tuple[uuid.UUID, str], dict[str, Any]] = {}

    def acquire(self) -> _OverrideConn:
        return _OverrideConn(self)


# ---------------------------------------------------------------------------
# Override create_override uses RETURNING — patch fetchrow to act as INSERT+FETCH
# ---------------------------------------------------------------------------


class _OverrideConnWithReturning(_OverrideConn):
    """Extends _OverrideConn so RETURNING clause works like INSERT + SELECT."""

    async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
        if "INSERT INTO override_log" in query and "RETURNING" in query:
            (
                override_id,
                run_id,
                vin,
                original_position,
                new_position,
                justification,
                planner_id,
            ) = args
            now = datetime.now(tz=timezone.utc)
            week_iso = now.strftime("%G-W%V")
            record: dict[str, Any] = {
                "override_id": override_id,
                "run_id": run_id,
                "vin": vin,
                "original_position": original_position,
                "new_position": new_position,
                "justification": justification,
                "planner_id": planner_id,
                "created_at": now,
                "_week_iso": week_iso,
            }
            self._pool._overrides[override_id] = record
            self._pool._override_list.append(override_id)
            return record
        return await super().fetchrow(query, *args)


class _OverridePoolFull(_OverridePool):
    """Pool that returns a connection with RETURNING support."""

    def acquire(self) -> _OverrideConnWithReturning:
        return _OverrideConnWithReturning(self)


# ---------------------------------------------------------------------------
# INSERT round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_override_inserts_and_returns_record() -> None:
    """create_override persists the row and returns a populated OverrideRecord."""
    pool = _OverridePoolFull()
    request = OverrideRequest(
        runId=_RUN_ID,
        vin=_VIN,
        originalPosition=3,
        newPosition=1,
        justification="Urgent VIP customer requirement",
    )
    record = await create_override(request=request, planner_id=_PLANNER_ID, pool=pool)

    assert record.runId == _RUN_ID
    assert record.vin == _VIN
    assert record.originalPosition == 3
    assert record.newPosition == 1
    assert record.justification == "Urgent VIP customer requirement"
    assert record.plannerId == _PLANNER_ID
    assert record.overrideId is not None


@pytest.mark.asyncio
async def test_get_override_retrieves_inserted_record() -> None:
    """get_override returns the exact record that was inserted by create_override."""
    pool = _OverridePoolFull()
    request = OverrideRequest(
        runId=_RUN_ID,
        vin=_VIN,
        originalPosition=3,
        newPosition=1,
        justification="Plant manager directive",
    )
    created = await create_override(request=request, planner_id=_PLANNER_ID, pool=pool)
    retrieved = await get_override(created.overrideId, pool)

    assert retrieved is not None
    assert str(retrieved.overrideId) == str(created.overrideId)
    assert retrieved.vin == _VIN
    assert retrieved.originalPosition == 3
    assert retrieved.newPosition == 1
    assert retrieved.plannerId == _PLANNER_ID


@pytest.mark.asyncio
async def test_get_override_returns_none_for_unknown_id() -> None:
    """get_override returns None when the override_id does not exist."""
    pool = _OverridePoolFull()
    result = await get_override(uuid.uuid4(), pool)
    assert result is None


# ---------------------------------------------------------------------------
# Cursor pagination continuation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_overrides_cursor_pagination_returns_next_cursor_when_more_rows_exist() -> None:
    """When DB has more rows than the limit, next_cursor is non-null."""
    pool = _OverridePoolFull()

    # Insert 3 overrides
    for i in range(3):
        await create_override(
            request=OverrideRequest(
                runId=_RUN_ID,
                vin=_VIN,
                originalPosition=i + 1,
                newPosition=i + 2,
                justification=f"Override justification #{i + 1}",
            ),
            planner_id=_PLANNER_ID,
            pool=pool,
        )

    # Request with limit=2 — should return 2 rows and a next_cursor
    result = await list_overrides(pool=pool, limit=2)
    # Since stub returns all rows up to fetch_limit (3), has_next=True when 3>2
    assert result.page.limit == 2
    assert len(result.data) == 2
    assert result.page.next_cursor is not None


@pytest.mark.asyncio
async def test_list_overrides_cursor_pagination_last_page_has_null_cursor() -> None:
    """When all rows fit within the limit, next_cursor is null."""
    pool = _OverridePoolFull()

    for i in range(2):
        await create_override(
            request=OverrideRequest(
                runId=_RUN_ID,
                vin=_VIN,
                originalPosition=i + 1,
                newPosition=i + 2,
                justification=f"Override justification #{i + 1}",
            ),
            planner_id=_PLANNER_ID,
            pool=pool,
        )

    result = await list_overrides(pool=pool, limit=50)
    assert result.page.next_cursor is None
    assert len(result.data) == 2


# ---------------------------------------------------------------------------
# PDF export — content type and row count
# ---------------------------------------------------------------------------


def test_build_override_pdf_starts_with_pdf_header() -> None:
    """build_override_pdf returns bytes that start with %PDF."""
    records = [
        OverrideRecord(
            overrideId=uuid.uuid4(),
            runId=_RUN_ID,
            vin=_VIN,
            originalPosition=1,
            newPosition=2,
            justification="Manual override for VIP",
            plannerId=_PLANNER_ID,
            createdAt=_FAR_FUTURE,
        )
    ]
    pdf = build_override_pdf(records, _WEEK_ISO)
    assert pdf[:4] == b"%PDF"


def test_build_override_pdf_includes_all_record_vins() -> None:
    """PDF text content includes the VIN from each override record."""
    vins = ["WBA3B3C56DF596858", "WBA3B3C57DF596859", "WBA3B3C58DF596860"]
    records = [
        OverrideRecord(
            overrideId=uuid.uuid4(),
            runId=_RUN_ID,
            vin=v,
            originalPosition=i + 1,
            newPosition=i + 2,
            justification="Planner-directed resequence",
            plannerId=_PLANNER_ID,
            createdAt=_FAR_FUTURE,
        )
        for i, v in enumerate(vins)
    ]
    pdf = build_override_pdf(records, _WEEK_ISO)
    pdf_text = pdf.decode("latin-1", errors="replace")
    for vin in vins:
        assert vin in pdf_text, f"VIN {vin} missing from PDF output"


def test_build_override_pdf_empty_records_still_returns_valid_pdf() -> None:
    """build_override_pdf with zero records still returns a valid PDF binary."""
    pdf = build_override_pdf([], _WEEK_ISO)
    assert pdf[:4] == b"%PDF"
    assert b"%%EOF" in pdf


# ---------------------------------------------------------------------------
# VIN explanation round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_and_retrieve_vin_explanation_returns_top_factors() -> None:
    """store_explanation persists factors; get_explanation retrieves them."""
    pool = _OverridePoolFull()
    run_id = uuid.uuid4()
    top_factors = [
        ConstraintFactor(
            factorName=ConstraintFactorName.paint_batch_conflict,
            weightedScore=0.85,
            dataSource="uns.paint_capacity",
            rawValue=92.3,
        ),
        ConstraintFactor(
            factorName=ConstraintFactorName.vip_priority,
            weightedScore=0.5,
            dataSource="optimizer_run.ranked_sequence",
        ),
    ]

    async with pool.acquire() as conn:
        await store_explanation(
            run_id=run_id,
            vin=_VIN,
            position=2,
            top_factors=top_factors,
            conn=conn,
        )

    result = await get_explanation(run_id=run_id, vin=_VIN, pool=pool)
    assert result is not None
    assert result.vin == _VIN
    assert result.position == 2
    assert len(result.topFactors) == 2
    assert result.topFactors[0].factorName == ConstraintFactorName.paint_batch_conflict
    assert result.topFactors[0].weightedScore == 0.85
    assert result.topFactors[0].dataSource == "uns.paint_capacity"
    assert result.topFactors[0].rawValue == 92.3


@pytest.mark.asyncio
async def test_get_explanation_returns_none_when_not_found() -> None:
    """get_explanation returns None for a run_id/vin pair not in the store."""
    pool = _OverridePoolFull()
    result = await get_explanation(run_id=uuid.uuid4(), vin=_VIN, pool=pool)
    assert result is None
