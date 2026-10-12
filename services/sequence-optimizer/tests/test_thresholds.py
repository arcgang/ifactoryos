"""Unit tests for threshold evaluation logic and endpoint contract."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.consumer import build_alert_event, evaluate_breach
from app.models import (
    ConstraintAlertEvent,
    ConstraintThreshold,
    ConstraintType,
    CursorPage,
    ThresholdListResponse,
)

_FIXED_TS = "2040-01-01T00:00:00+00:00"
_FIXED_DT = datetime(2040, 1, 1, tzinfo=timezone.utc)


def _make_row(**kwargs: object) -> MagicMock:
    """Return a MagicMock that supports dict-style key access from a plain dict."""
    data = kwargs
    row = MagicMock()
    row.__getitem__ = lambda self_, k: data[k]
    return row


# ---------------------------------------------------------------------------
# evaluate_breach — pure logic, no I/O
# ---------------------------------------------------------------------------


def test_evaluate_breach_above_warning_returns_true():
    assert evaluate_breach(current_value=91.0, warning_level=85.0) is True


def test_evaluate_breach_below_warning_returns_false():
    assert evaluate_breach(current_value=80.0, warning_level=85.0) is False


def test_evaluate_breach_equal_to_warning_returns_false():
    # strictly greater than — at the threshold is not a breach
    assert evaluate_breach(current_value=85.0, warning_level=85.0) is False


def test_evaluate_breach_zero_value_returns_false():
    assert evaluate_breach(current_value=0.0, warning_level=85.0) is False


# ---------------------------------------------------------------------------
# build_alert_event — pure factory, no I/O
# ---------------------------------------------------------------------------


def test_build_alert_event_paint_capacity_sets_all_fields():
    event = build_alert_event(
        constraint_type=ConstraintType.paint_capacity,
        current_value=92.5,
        warning_level=85.0,
        detected_at=_FIXED_TS,
    )
    assert event.constraintType == ConstraintType.paint_capacity
    assert event.currentValue == 92.5
    assert event.warningLevel == 85.0
    assert event.detectedAt == _FIXED_TS


def test_build_alert_event_agv_delay_sets_constraint_type():
    event = build_alert_event(
        constraint_type=ConstraintType.agv_delay,
        current_value=7.2,
        warning_level=5.0,
        detected_at=_FIXED_TS,
    )
    assert event.constraintType == ConstraintType.agv_delay
    assert event.currentValue == 7.2
    assert event.warningLevel == 5.0


# ---------------------------------------------------------------------------
# ConstraintThreshold model — field presence and nullability
# ---------------------------------------------------------------------------


def test_constraint_threshold_mandatory_fields_present():
    t = ConstraintThreshold(
        thresholdId=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        constraintType=ConstraintType.kitting_readiness,
        warningLevel=70.0,
        criticalLevel=60.0,
        unit="%",
    )
    assert t.thresholdId == uuid.UUID("00000000-0000-0000-0000-000000000001")
    assert t.constraintType == ConstraintType.kitting_readiness
    assert t.warningLevel == 70.0
    assert t.criticalLevel == 60.0
    assert t.unit == "%"
    assert t.updatedAt is None
    assert t.updatedBy is None


def test_constraint_threshold_optional_fields_accepted():
    t = ConstraintThreshold(
        thresholdId=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        constraintType=ConstraintType.body_wip_queue,
        warningLevel=45.0,
        criticalLevel=40.0,
        unit="jobs",
        updatedAt=_FIXED_TS,
        updatedBy="planner@bmwgroup.net",
    )
    assert t.updatedAt == _FIXED_TS
    assert t.updatedBy == "planner@bmwgroup.net"


# ---------------------------------------------------------------------------
# ThresholdListResponse — cursor pagination shape
# ---------------------------------------------------------------------------


def test_threshold_list_response_empty_page_has_null_cursor():
    resp = ThresholdListResponse(
        data=[],
        page=CursorPage(next_cursor=None, limit=50),
    )
    assert resp.data == []
    assert resp.page.next_cursor is None
    assert resp.page.limit == 50


def test_threshold_list_response_with_next_cursor_is_non_null():
    resp = ThresholdListResponse(
        data=[
            ConstraintThreshold(
                thresholdId=uuid.UUID("00000000-0000-0000-0000-000000000003"),
                constraintType=ConstraintType.maintenance_window,
                warningLevel=2.0,
                criticalLevel=1.0,
                unit="hours",
            )
        ],
        page=CursorPage(next_cursor="abc123", limit=1),
    )
    assert len(resp.data) == 1
    assert resp.page.next_cursor == "abc123"


# ---------------------------------------------------------------------------
# thresholds service — list_thresholds (mocked pool)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_thresholds_no_cursor_returns_single_result():
    from app.thresholds import list_thresholds

    _threshold_id = uuid.UUID("00000000-0000-0000-0000-000000000010")

    mock_row = _make_row(
        threshold_id=_threshold_id,
        constraint_type="paint_capacity",
        warning_level=85.0,
        critical_level=75.0,
        unit="%",
        updated_at=_FIXED_DT,
        updated_by="system",
    )

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[mock_row])

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    result = await list_thresholds(pool=mock_pool, cursor=None, limit=50)
    assert len(result.data) == 1
    assert result.data[0].constraintType == ConstraintType.paint_capacity
    assert result.data[0].warningLevel == 85.0
    assert result.page.next_cursor is None


@pytest.mark.asyncio
async def test_list_thresholds_returns_next_cursor_when_rows_exceed_limit():
    from app.thresholds import list_thresholds, _encode_cursor

    # Return limit+1 rows to trigger next_cursor
    rows = []
    for i in range(3):
        tid = uuid.UUID(f"00000000-0000-0000-0000-0000000{i:05d}")
        rows.append(_make_row(
            threshold_id=tid,
            constraint_type="paint_capacity",
            warning_level=85.0,
            critical_level=75.0,
            unit="%",
            updated_at=_FIXED_DT,
            updated_by="system",
        ))

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=rows)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    result = await list_thresholds(pool=mock_pool, cursor=None, limit=2)
    assert len(result.data) == 2
    assert result.page.next_cursor is not None


# ---------------------------------------------------------------------------
# thresholds service — upsert_threshold (mocked pool)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_threshold_agv_delay_returns_saved_record():
    from app.thresholds import upsert_threshold

    _threshold_id = uuid.UUID("00000000-0000-0000-0000-000000000020")

    mock_row = _make_row(
        threshold_id=_threshold_id,
        constraint_type="agv_delay",
        warning_level=5.0,
        critical_level=3.0,
        unit="min",
        updated_at=_FIXED_DT,
        updated_by="planner@bmwgroup.net",
    )

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=mock_row)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    threshold = ConstraintThreshold(
        thresholdId=_threshold_id,
        constraintType=ConstraintType.agv_delay,
        warningLevel=5.0,
        criticalLevel=3.0,
        unit="min",
    )
    result = await upsert_threshold(
        pool=mock_pool, threshold=threshold, updated_by="planner@bmwgroup.net"
    )
    assert result.thresholdId == _threshold_id
    assert result.constraintType == ConstraintType.agv_delay
    assert result.warningLevel == 5.0
    assert result.updatedBy == "planner@bmwgroup.net"


# ---------------------------------------------------------------------------
# Integration-style: upsert idempotence — two calls for the same constraintType
# Validates that ON CONFLICT logic is triggered (both calls reach the DB layer).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_threshold_idempotence_two_calls_both_reach_db():
    """Two consecutive upserts for the same constraintType both succeed and hit the DB."""
    from app.thresholds import upsert_threshold

    _tid_1 = uuid.UUID("00000000-0000-0000-0000-000000000031")
    _tid_2 = uuid.UUID("00000000-0000-0000-0000-000000000032")

    call_count = {"n": 0}

    async def _fetchrow(*args: object, **kwargs: object) -> MagicMock:
        call_count["n"] += 1
        tid = _tid_1 if call_count["n"] == 1 else _tid_2
        return _make_row(
            threshold_id=tid,
            constraint_type="press_die_overrun",
            warning_level=10.0,
            critical_level=8.0,
            unit="mm",
            updated_at=_FIXED_DT,
            updated_by="planner@bmwgroup.net",
        )

    mock_conn = AsyncMock()
    mock_conn.fetchrow = _fetchrow

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    t1 = ConstraintThreshold(
        thresholdId=_tid_1,
        constraintType=ConstraintType.press_die_overrun,
        warningLevel=10.0,
        criticalLevel=8.0,
        unit="mm",
    )
    result_1 = await upsert_threshold(pool=mock_pool, threshold=t1, updated_by="planner@bmwgroup.net")
    assert result_1.thresholdId == _tid_1

    t2 = ConstraintThreshold(
        thresholdId=_tid_2,
        constraintType=ConstraintType.press_die_overrun,
        warningLevel=12.0,
        criticalLevel=9.0,
        unit="mm",
    )
    result_2 = await upsert_threshold(pool=mock_pool, threshold=t2, updated_by="planner@bmwgroup.net")
    assert result_2.thresholdId == _tid_2

    # Both calls reached the DB — ON CONFLICT is handled by Postgres, not app code
    assert call_count["n"] == 2
