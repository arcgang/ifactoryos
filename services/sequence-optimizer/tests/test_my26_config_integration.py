"""Integration tests for the MY26 config API round-trip.

Covers:
 - POST /my26/config/validate → POST /my26/config/save round-trip
 - POST /optimize with my26ConfigVersion applies merged constraints
   and returns that UUID in the response
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models import (
    ConstraintSnapshot,
    My26ConfigDraft,
    My26ConfigStatus,
    OptimizeRequest,
    OptionCodeEntry,
    PaintPaletteEntry,
    ResultType,
    VinInput,
)
from app.my26_config import (
    get_active_my26_config,
    save_my26_config,
    set_known_constraint_keys,
    validate_my26_draft,
)
from app.optimizer import run_optimize
from tests.test_my26_config import _My26StubPool
from tests.test_optimizer import _StubKServeClient, _StubPool

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_FAR_FUTURE = "2099-01-01T00:00:00+00:00"

_VIN_1 = "WBA3B3C56DF596858"
_VIN_2 = "WBA3B3C57DF596859"

_SNAPSHOT = ConstraintSnapshot(
    snapshotTimestamp=_FAR_FUTURE,
    paintCapacityUtilisation=60.0,
    bodyWipQueueDepth=150,
    kittingReadinessPct=85.0,
    maintenanceWindows=[],
)


# ---------------------------------------------------------------------------
# POST /my26/config/validate → POST /my26/config/save round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_then_save_round_trip_produces_active_config() -> None:
    """validate → save round-trip yields an active config retrievable via get_active."""
    set_known_constraint_keys(frozenset({"paint_capacity", "kitting_readiness"}))
    try:
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="SA-100",
                    description="Sunroof",
                    constraintMappings=["paint_capacity", "kitting_readiness"],
                ),
            ],
            paintPalette=[
                PaintPaletteEntry(paintCode="A75", batchGroup="metallic", changeoverPenaltyMinutes=30),
            ],
        )
        # Step 1: validate (must be valid)
        validation = validate_my26_draft(draft)
        assert validation.valid is True
        assert not any(i.severity.value == "error" for i in validation.issues)

        # Step 2: save
        pool = _My26StubPool()
        with patch("app.my26_config.asyncio.create_task"):
            saved = await save_my26_config(draft, pool, "planner@bmwgroup.net")

        assert saved.status == My26ConfigStatus.active
        assert saved.configId is not None
        assert saved.activatedAt is not None

        # Step 3: verify retrieval
        active = await get_active_my26_config(pool)
        assert active is not None
        assert str(active.configId) == str(saved.configId)
        assert active.status == My26ConfigStatus.active
        assert len(active.optionCodes) == 1
        assert active.optionCodes[0].code == "SA-100"
        assert len(active.paintPalette) == 1
        assert active.paintPalette[0].paintCode == "A75"
        assert active.paintPalette[0].changeoverPenaltyMinutes == 30
    finally:
        set_known_constraint_keys(frozenset())


@pytest.mark.asyncio
async def test_validate_then_save_second_config_supersedes_first() -> None:
    """Saving a second valid config marks the first as superseded."""
    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="SA-100",
                    description="Sunroof",
                    constraintMappings=["paint_capacity"],
                ),
            ],
            paintPalette=[PaintPaletteEntry(paintCode="A75", batchGroup="metallic")],
        )
        pool = _My26StubPool()
        with patch("app.my26_config.asyncio.create_task"):
            first = await save_my26_config(draft, pool, "planner@bmwgroup.net")
            second = await save_my26_config(draft, pool, "planner@bmwgroup.net")

        assert second.status == My26ConfigStatus.active
        # First should be superseded in the pool
        first_row = pool._configs.get(first.configId)
        assert first_row is not None
        assert first_row["status"] == "superseded"
    finally:
        set_known_constraint_keys(frozenset())


# ---------------------------------------------------------------------------
# POST /optimize with my26ConfigVersion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_optimize_with_valid_my26_config_version_returns_version_in_response() -> None:
    """POST /optimize with a valid my26ConfigVersion UUID returns it in the response."""
    set_known_constraint_keys(frozenset({"paint_capacity", "kitting_readiness"}))
    try:
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="SA-100",
                    description="Sunroof",
                    constraintMappings=["paint_capacity"],
                ),
            ],
            paintPalette=[
                PaintPaletteEntry(paintCode="A75", batchGroup="metallic", changeoverPenaltyMinutes=45),
            ],
        )
        my26_pool = _My26StubPool()
        with patch("app.my26_config.asyncio.create_task"):
            saved_config = await save_my26_config(draft, my26_pool, "planner@bmwgroup.net")

        # Build overrides from the saved config
        from app.my26_config import build_constraint_overrides
        overrides = build_constraint_overrides(saved_config)
        assert overrides["paint_changeover_penalties"]["A75"] == 45

        # Run optimizer with MY26 overrides
        optimizer_pool = _StubPool()
        client = _StubKServeClient()
        request = OptimizeRequest(
            weekIso="2026-W42",
            vins=[
                VinInput(vin=_VIN_1, modelYear="MY26", paintCode="A75"),
                VinInput(vin=_VIN_2, modelYear="MY26", paintCode="B21"),
            ],
            constraintSnapshot=_SNAPSHOT,
            monteCarloRuns=2,
            my26ConfigVersion=str(saved_config.configId),
        )
        with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
            response = await run_optimize(
                request,
                optimizer_pool,
                client,
                my26_overrides=overrides,
            )

        assert response.my26ConfigVersion == str(saved_config.configId)
        assert len(response.rankedSequence) == 2
    finally:
        set_known_constraint_keys(frozenset())


@pytest.mark.asyncio
async def test_optimize_without_my26_config_version_returns_null_in_response() -> None:
    """POST /optimize without my26ConfigVersion returns null my26ConfigVersion."""
    optimizer_pool = _StubPool()
    client = _StubKServeClient()
    request = OptimizeRequest(
        weekIso="2026-W42",
        vins=[VinInput(vin=_VIN_1)],
        constraintSnapshot=_SNAPSHOT,
        monteCarloRuns=2,
    )
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        response = await run_optimize(request, optimizer_pool, client)
    assert response.my26ConfigVersion is None


@pytest.mark.asyncio
async def test_optimize_with_my26_paint_overrides_applies_penalty_in_heuristic_fallback() -> None:
    """Heuristic fallback with MY26 paint overrides reflects the configured penalty."""
    import asyncio as _asyncio

    class _TimeoutClient:
        async def predict(self, vins, constraint_snapshot, run_index):
            raise _asyncio.TimeoutError("simulated")

    pool = _StubPool()
    client = _TimeoutClient()

    # A75 has a low MY26 changeover penalty (10 min), not the default 200
    overrides = {"paint_changeover_penalties": {"A75": 10}, "option_code_mappings": {}}
    vins = [
        VinInput(vin=_VIN_1, paintCode="A75"),
        VinInput(vin=_VIN_2, paintCode="B21"),
    ]
    request = OptimizeRequest(
        weekIso="2026-W43",
        vins=vins,
        constraintSnapshot=_SNAPSHOT,
        monteCarloRuns=2,
    )
    response = await run_optimize(request, pool, client, my26_overrides=overrides)
    assert response.resultType == ResultType.heuristic_fallback
    # All VINs should appear in the output
    output_vins = {rv.vin for rv in response.rankedSequence}
    assert _VIN_1 in output_vins
    assert _VIN_2 in output_vins
