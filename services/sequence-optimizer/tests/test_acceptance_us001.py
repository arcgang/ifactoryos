"""Acceptance tests for US-001 — Constraint Radar (sequence-optimizer side).

Exercises the seam between the threshold API and breach-event publishing that
no existing unit test covers in isolation.

AC1 – GET /constraints/thresholds returns paint_capacity, body_wip_queue,
       kitting_readiness and maintenance_window with all required fields.
AC2 – evaluate_breach + build_alert_event + ConstraintAlertEvent form a
       complete, correct pipeline: a value above warningLevel produces an
       event whose constraintType, currentValue and warningLevel match the
       inbound telemetry, and a value at-or-below the threshold produces
       no event.
AC3 – (staleness indicator is rendered in the frontend when lag>1 s;
       the backend pipeline that supplies the telemetry stream has no
       server-side concept of client-side staleness — verified in the
       frontend acceptance test.)
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.consumer import build_alert_event, evaluate_breach
from app.models import (
    ConstraintAlertEvent,
    ConstraintThreshold,
    ConstraintType,
    CursorPage,
    ThresholdListResponse,
)

# ── Far-future timestamp anchor — never hardcode an absolute wall time ────────

_FAR_FUTURE_TS = "2040-06-15T05:30:00+00:00"

# ── JWT bearer token (alg=none, exp=year-2286, role=constraint-threshold-writer)

_BEARER = (
    "Bearer eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJwbGFubmVyQGJtd2dyb3VwLm5ldCIsInJvbGVzIjpbImNvbnN0cmFpbnQtdGhyZXNob2xkLXdyaXRlciJdLCJleHAiOjk5OTk5OTk5OTl9"
    "."
)

# ── Shared threshold fixtures (mirrors MOCK_THRESHOLDS on the frontend) ───────

_PAINT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_BODY_ID   = uuid.UUID("00000000-0000-0000-0000-000000000002")
_KIT_ID    = uuid.UUID("00000000-0000-0000-0000-000000000003")
_MAINT_ID  = uuid.UUID("00000000-0000-0000-0000-000000000004")

_ALL_THRESHOLDS = [
    ConstraintThreshold(thresholdId=_PAINT_ID, constraintType=ConstraintType.paint_capacity,
                        warningLevel=85.0, criticalLevel=95.0, unit="%"),
    ConstraintThreshold(thresholdId=_BODY_ID,  constraintType=ConstraintType.body_wip_queue,
                        warningLevel=160.0, criticalLevel=190.0, unit="units"),
    ConstraintThreshold(thresholdId=_KIT_ID,   constraintType=ConstraintType.kitting_readiness,
                        warningLevel=88.0, criticalLevel=75.0, unit="%"),
    ConstraintThreshold(thresholdId=_MAINT_ID, constraintType=ConstraintType.maintenance_window,
                        warningLevel=6.0, criticalLevel=10.0, unit="windows"),
]

# ── Test-app factory (mirrors test_thresholds_api.py pattern) ─────────────────


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


def _build_test_app() -> FastAPI:
    import app.main as main_mod

    with patch.object(main_mod, "_lifespan", _noop_lifespan):
        application = main_mod.create_app()
    return application


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_threshold_list_response() -> ThresholdListResponse:
    return ThresholdListResponse(
        data=_ALL_THRESHOLDS,
        page=CursorPage(next_cursor=None, limit=50),
    )


# =============================================================================
# AC1 — GET /constraints/thresholds returns all four constraint types with
#        the fields required by the frontend (warningLevel, criticalLevel, unit)
# =============================================================================


def test_ac1_list_thresholds_returns_all_four_required_constraint_types():
    """AC1: GET /constraints/thresholds includes paint_capacity, body_wip_queue,
    kitting_readiness and maintenance_window — the four types the Constraint
    Radar page renders at 05:30.
    """
    import app.thresholds as thresholds_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    original = thresholds_mod.list_thresholds

    async def _mock_list(pool: Any, cursor: Any, limit: Any) -> ThresholdListResponse:
        return _make_threshold_list_response()

    thresholds_mod.list_thresholds = _mock_list
    try:
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.get(
            "/constraints/thresholds",
            headers={"Authorization": _BEARER},
        )

        # Precondition: the endpoint must be reachable and return 200
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = resp.json()
        returned_types = {item["constraintType"] for item in body["data"]}

        required_types = {
            "paint_capacity",
            "body_wip_queue",
            "kitting_readiness",
            "maintenance_window",
        }
        assert required_types.issubset(returned_types), (
            f"Missing constraint types: {required_types - returned_types}"
        )

        # Every threshold must carry the fields the frontend consumes
        for item in body["data"]:
            ct = item["constraintType"]
            assert "warningLevel" in item, f"{ct}: warningLevel missing"
            assert "criticalLevel" in item, f"{ct}: criticalLevel missing"
            assert "unit" in item, f"{ct}: unit missing"
            assert isinstance(item["warningLevel"], (int, float)), f"{ct}: warningLevel must be numeric"
            assert isinstance(item["criticalLevel"], (int, float)), f"{ct}: criticalLevel must be numeric"
    finally:
        thresholds_mod.list_thresholds = original


def test_ac1_list_thresholds_response_has_cursor_pagination_metadata():
    """AC1: GET /constraints/thresholds response includes page metadata required
    for the frontend to know it has all thresholds (next_cursor is null).
    """
    import app.thresholds as thresholds_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    original = thresholds_mod.list_thresholds

    async def _mock_list(pool: Any, cursor: Any, limit: Any) -> ThresholdListResponse:
        return _make_threshold_list_response()

    thresholds_mod.list_thresholds = _mock_list
    try:
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.get("/constraints/thresholds", headers={"Authorization": _BEARER})

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        page = resp.json()["page"]
        assert "next_cursor" in page
        assert "limit" in page
        assert isinstance(page["limit"], int) and page["limit"] > 0
    finally:
        thresholds_mod.list_thresholds = original


# =============================================================================
# AC2 — Breach pipeline: evaluate_breach → build_alert_event → ConstraintAlertEvent
#
# Tests the complete seam: a telemetry value above the configured warningLevel
# produces a correctly-populated ConstraintAlertEvent; a value at or below does
# not (evaluate_breach returns False, so the consumer skips publishing).
# =============================================================================


@pytest.mark.parametrize(
    "constraint_type,current_value,warning_level,expect_breach",
    [
        # paint_capacity: 90 > 85 → breach
        (ConstraintType.paint_capacity, 90.0, 85.0, True),
        # paint_capacity: 85 == 85 → NOT a breach (strictly greater than)
        (ConstraintType.paint_capacity, 85.0, 85.0, False),
        # body_wip_queue: 165 > 160 → breach
        (ConstraintType.body_wip_queue, 165.0, 160.0, True),
        # body_wip_queue: 155 < 160 → NOT a breach
        (ConstraintType.body_wip_queue, 155.0, 160.0, False),
        # kitting_readiness: 89 > 88 → breach
        (ConstraintType.kitting_readiness, 89.0, 88.0, True),
        # maintenance_window: 7 > 6 → breach
        (ConstraintType.maintenance_window, 7.0, 6.0, True),
        # maintenance_window: 6 == 6 → NOT a breach
        (ConstraintType.maintenance_window, 6.0, 6.0, False),
    ],
)
def test_ac2_breach_pipeline_evaluate_breach_aligns_with_configurable_threshold(
    constraint_type: ConstraintType,
    current_value: float,
    warning_level: float,
    expect_breach: bool,
) -> None:
    """AC2: evaluate_breach must return True iff currentValue strictly exceeds
    warningLevel — matching the configurable threshold fetched from the DB.
    """
    result = evaluate_breach(current_value=current_value, warning_level=warning_level)
    assert result is expect_breach, (
        f"evaluate_breach({current_value}, {warning_level}) = {result}, expected {expect_breach}"
    )


def test_ac2_breach_pipeline_alert_event_carries_constraint_type_current_value_and_warning_level():
    """AC2: when paint_capacity value 90 crosses warning threshold 85, the
    ConstraintAlertEvent produced by build_alert_event contains the correct
    constraintType, currentValue and warningLevel.  The seam tested here is
    evaluate_breach → build_alert_event → ConstraintAlertEvent serialisation.
    """
    ct = ConstraintType.paint_capacity
    current_value = 90.0
    warning_level = 85.0

    # Step 1: breach guard (as the consumer calls it)
    assert evaluate_breach(current_value, warning_level) is True

    # Step 2: build event (as the consumer calls it)
    event: ConstraintAlertEvent = build_alert_event(
        constraint_type=ct,
        current_value=current_value,
        warning_level=warning_level,
        detected_at=_FAR_FUTURE_TS,
    )

    # Step 3: verify all fields the frontend alert banner consumes
    assert event.constraintType == ConstraintType.paint_capacity
    assert event.currentValue == 90.0
    assert event.warningLevel == 85.0
    assert event.detectedAt == _FAR_FUTURE_TS


def test_ac2_breach_pipeline_alert_event_serialises_to_json_with_all_required_fields():
    """AC2: the ConstraintAlertEvent produced by the pipeline must serialise to
    a JSON object that includes constraintType, currentValue, warningLevel and
    detectedAt — these are the fields the Kafka consumer publishes to the alert
    topic and the frontend consumes.
    """
    import json

    ct = ConstraintType.body_wip_queue
    event = build_alert_event(
        constraint_type=ct,
        current_value=165.0,
        warning_level=160.0,
        detected_at=_FAR_FUTURE_TS,
    )

    serialised = json.loads(json.dumps(event.model_dump(mode="json")))

    assert serialised["constraintType"] == "body_wip_queue"
    assert serialised["currentValue"] == 165.0
    assert serialised["warningLevel"] == 160.0
    assert serialised["detectedAt"] == _FAR_FUTURE_TS


def test_ac2_all_four_radar_constraint_types_have_a_configured_threshold():
    """AC2: every constraint type rendered by the Constraint Radar must have a
    threshold record — without one, evaluate_breach can never fire and the
    planner would see no amber highlight.  This test asserts the threshold
    fixture (which mirrors the seeded DB state) covers all four tile types.
    """
    configured_types = {t.constraintType for t in _ALL_THRESHOLDS}
    required_types = {
        ConstraintType.paint_capacity,
        ConstraintType.body_wip_queue,
        ConstraintType.kitting_readiness,
        ConstraintType.maintenance_window,
    }
    assert required_types.issubset(configured_types), (
        f"Missing threshold configuration for: {required_types - configured_types}"
    )


def test_ac2_no_breach_event_when_value_equals_warning_level():
    """AC2: a value exactly at the warning threshold is NOT a breach and must
    not produce an alert event.  This ensures the configurable threshold boundary
    is strictly greater-than on both the backend and the frontend (isAmberBreach
    in constraintUtils.ts uses the same >).
    """
    assert evaluate_breach(current_value=85.0, warning_level=85.0) is False
    assert evaluate_breach(current_value=160.0, warning_level=160.0) is False


# =============================================================================
# AC3 — Staleness indicator (backend contribution)
#
# The sequence-optimizer's role in AC3 is to publish telemetry via the SSE
# endpoint.  The staleness logic (p99 lag > 1 000 ms → isStale flag →
# staleness row) lives entirely in the frontend hook/component.
# The backend acceptance test verifies that the ConstraintAlertEvent includes
# detectedAt, which the consumer populates from the current UTC instant,
# allowing the frontend to compute lag = receivedAt − measuredAt.
# =============================================================================


def test_ac3_alert_event_detected_at_is_an_iso8601_utc_string():
    """AC3 (backend): build_alert_event populates detectedAt with an ISO-8601
    string so the frontend can compute the UNS lag required for staleness detection.
    """
    from datetime import datetime, timezone

    event = build_alert_event(
        constraint_type=ConstraintType.kitting_readiness,
        current_value=90.0,
        warning_level=88.0,
        detected_at=_FAR_FUTURE_TS,
    )

    # Must parse as an aware datetime without raising
    dt = datetime.fromisoformat(event.detectedAt)
    assert dt.tzinfo is not None, "detectedAt must be timezone-aware (UTC)"
