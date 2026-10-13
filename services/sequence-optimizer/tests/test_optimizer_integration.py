"""Integration test (TINT): POST /optimize → GET /optimize/{runId} end-to-end.

Verifies:
  - rankedSequence length equals input vins length
  - resultType in {monte_carlo, heuristic_fallback}
  - confidenceScore in [0, 1]
  - durationMs <= 45000 for the monte_carlo path
  - GET /optimize/{runId} returns the same stored result
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import patch

import pytest

from app.models import (
    ConstraintSnapshot,
    OptimizeRequest,
    OptimizeResponse,
    ResultType,
    VinInput,
)
from app.optimizer import get_run, run_optimize
from tests.test_optimizer import _StubPool

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_FAR_FUTURE = "2099-01-01T00:00:00+00:00"

_VIN_1 = "WBA1A2C31DC123456"
_VIN_2 = "WBA1A2C32DC123457"
_VIN_3 = "WBA1A2C33DC123458"

_VINS = [VinInput(vin=v) for v in [_VIN_1, _VIN_2, _VIN_3]]

_SNAPSHOT = ConstraintSnapshot(
    snapshotTimestamp=_FAR_FUTURE,
    paintCapacityUtilisation=65.0,
    bodyWipQueueDepth=200,
    kittingReadinessPct=85.0,
    maintenanceWindows=[],
)


# ---------------------------------------------------------------------------
# Stub clients
# ---------------------------------------------------------------------------


class _FastKServeClient:
    """Returns instantly — simulates a healthy KServe endpoint."""

    async def predict(
        self,
        vins: list[VinInput],
        constraint_snapshot: ConstraintSnapshot,
        run_index: int,
    ) -> list[dict[str, Any]]:
        return [
            {
                "vin": v.vin,
                "position": idx + 1,
                "constraintScore": float(100 - idx * 5),
                "vipPriority": v.vipPriority,
                "regulatoryHold": v.regulatoryHold,
            }
            for idx, v in enumerate(vins)
        ]


class _TimeoutKServeClient:
    """Raises TimeoutError — forces heuristic_fallback path."""

    async def predict(
        self,
        vins: list[VinInput],
        constraint_snapshot: ConstraintSnapshot,
        run_index: int,
    ) -> list[dict[str, Any]]:
        raise asyncio.TimeoutError("simulated KServe timeout")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_ranked_sequence_length_equals_input_vins_length() -> None:
    pool = _StubPool()
    request = OptimizeRequest(
        weekIso="2026-W42",
        vins=_VINS,
        constraintSnapshot=_SNAPSHOT,
        monteCarloRuns=50,
    )
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        response: OptimizeResponse = await run_optimize(request, pool, _FastKServeClient())
    assert len(response.rankedSequence) == len(_VINS)


@pytest.mark.asyncio
async def test_end_to_end_result_type_in_allowed_values() -> None:
    pool = _StubPool()
    request = OptimizeRequest(
        weekIso="2026-W42",
        vins=_VINS,
        constraintSnapshot=_SNAPSHOT,
        monteCarloRuns=2,
    )
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        response = await run_optimize(request, pool, _FastKServeClient())
    assert response.resultType in {ResultType.monte_carlo, ResultType.heuristic_fallback}


@pytest.mark.asyncio
async def test_end_to_end_confidence_score_in_zero_to_one_range() -> None:
    pool = _StubPool()
    request = OptimizeRequest(
        weekIso="2026-W42",
        vins=_VINS,
        constraintSnapshot=_SNAPSHOT,
        monteCarloRuns=2,
    )
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        response = await run_optimize(request, pool, _FastKServeClient())
    assert 0.0 <= response.confidenceScore <= 1.0


@pytest.mark.asyncio
async def test_end_to_end_monte_carlo_duration_ms_within_45_seconds() -> None:
    pool = _StubPool()
    request = OptimizeRequest(
        weekIso="2026-W42",
        vins=_VINS,
        constraintSnapshot=_SNAPSHOT,
        monteCarloRuns=50,
    )
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        response = await run_optimize(request, pool, _FastKServeClient())
    if response.resultType == ResultType.monte_carlo:
        assert response.durationMs <= 45_000


@pytest.mark.asyncio
async def test_end_to_end_get_returns_same_result_as_post() -> None:
    pool = _StubPool()
    request = OptimizeRequest(
        weekIso="2026-W42",
        vins=_VINS,
        constraintSnapshot=_SNAPSHOT,
        monteCarloRuns=2,
    )
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        posted = await run_optimize(request, pool, _FastKServeClient())

    retrieved = await get_run(posted.runId, pool)
    assert retrieved is not None
    assert str(retrieved.runId) == str(posted.runId)
    assert retrieved.weekIso == posted.weekIso
    assert len(retrieved.rankedSequence) == len(_VINS)


@pytest.mark.asyncio
async def test_end_to_end_heuristic_fallback_path() -> None:
    pool = _StubPool()
    request = OptimizeRequest(
        weekIso="2026-W43",
        vins=_VINS,
        constraintSnapshot=_SNAPSHOT,
        monteCarloRuns=2,
    )
    response = await run_optimize(request, pool, _TimeoutKServeClient())
    assert response.resultType == ResultType.heuristic_fallback
    assert len(response.rankedSequence) == len(_VINS)
    assert 0.0 <= response.confidenceScore <= 1.0
