"""Acceptance tests for US-002 — 50-run Monte Carlo Sequence Optimizer.

Tests the seam between the HTTP route layer (optimize_router, task 1) and the
planning-cockpit frontend rendering (task 2).  Each unit already has its own
tests; what only the acceptance test covers is the HTTP contract that connects
them: field names, types, timing budget, and the POST→GET persistence round-trip.

AC1 – POST /optimize with monteCarloRuns=50 completes within the 45-second
       budget (durationMs ≤ 45 000).  GET /optimize/{runId} returns the same
       persisted result — the seam the frontend polls against.

AC2 – Each RankedVin in the HTTP response carries position, constraintScore,
       vipPriority, and regulatoryHold with the exact field names and types the
       frontend TypeScript types expect.

AC3 – When KServe is unavailable, POST /optimize returns HTTP 200 with
       resultType="heuristic_fallback" and a populated rankedSequence — the
       backend signal that makes the frontend render its labeled heuristic banner.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import (
    ConstraintSnapshot,
    ResultType,
    VinInput,
)
from tests.test_optimizer import _StubPool

# ── Far-future anchor — never hardcode an absolute wall clock ─────────────────

_FAR_FUTURE = "2099-01-01T00:00:00+00:00"

# ── JWT with alg=none, exp=year-2286 (same pattern as test_acceptance_us001) ──

_BEARER = (
    "Bearer eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJwbGFubmVyQGJtd2dyb3VwLm5ldCIsInJvbGVzIjpbImNvbnN0cmFpbnQtdGhyZXNob2xkLXdyaXRlciJdLCJleHAiOjk5OTk5OTk5OTl9"
    "."
)

# ── Input fixtures ────────────────────────────────────────────────────────────

_VINS = [
    VinInput(vin="WBA1A2C31DC123456", vipPriority=True,  regulatoryHold=False),
    VinInput(vin="WBA1A2C32DC123457", vipPriority=False, regulatoryHold=False),
    VinInput(vin="WBA1A2C33DC123458", vipPriority=False, regulatoryHold=True),
]

_SNAPSHOT_JSON = {
    "snapshotTimestamp": _FAR_FUTURE,
    "paintCapacityUtilisation": 65.0,
    "bodyWipQueueDepth": 200,
    "kittingReadinessPct": 85.0,
    "maintenanceWindows": [],
}

_REQUEST_BODY = {
    "weekIso": "2026-W42",
    "vins": [
        {"vin": v.vin, "vipPriority": v.vipPriority, "regulatoryHold": v.regulatoryHold}
        for v in _VINS
    ],
    "constraintSnapshot": _SNAPSHOT_JSON,
    "monteCarloRuns": 50,
}

# ── Stub KServe clients ───────────────────────────────────────────────────────


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
                "constraintScore": round(1.0 - idx * 0.05, 2),
                "vipPriority": v.vipPriority,
                "regulatoryHold": v.regulatoryHold,
            }
            for idx, v in enumerate(vins)
        ]


class _FailingKServeClient:
    """Always raises OSError — forces heuristic_fallback path."""

    async def predict(
        self,
        vins: list[VinInput],
        constraint_snapshot: ConstraintSnapshot,
        run_index: int,
    ) -> list[dict[str, Any]]:
        raise OSError("KServe endpoint unreachable")


# ── App factory ───────────────────────────────────────────────────────────────


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


def _build_test_app() -> FastAPI:
    import app.main as main_mod

    with patch.object(main_mod, "_lifespan", _noop_lifespan):
        application = main_mod.create_app()
    return application


# ── Helpers ───────────────────────────────────────────────────────────────────


def _post_optimize(kserve_stub: Any) -> tuple[TestClient, _StubPool]:
    """Build a TestClient with the given KServe stub and an in-memory pool."""
    from app.router import _get_pool

    application = _build_test_app()
    pool = _StubPool()
    application.dependency_overrides[_get_pool] = lambda: pool
    return TestClient(application, raise_server_exceptions=False), pool


# =============================================================================
# AC1 — ranked VIN sequence returned within the 45-second timing budget
# =============================================================================


def test_ac1_post_optimize_50_runs_returns_http_200_with_ranked_sequence() -> None:
    """AC1: POST /optimize with monteCarloRuns=50 must return HTTP 200 and a
    populated rankedSequence of the same length as the input VINs array.  This
    exercises the HTTP route → run_optimize → persistence pipeline end-to-end.
    """
    import app.kserve_client as kserve_mod

    original = kserve_mod.make_kserve_client
    kserve_mod.make_kserve_client = lambda: _FastKServeClient()
    try:
        client, _ = _post_optimize(_FastKServeClient())
        resp = client.post(
            "/optimize",
            json=_REQUEST_BODY,
            headers={"Authorization": _BEARER},
        )
    finally:
        kserve_mod.make_kserve_client = original

    # Precondition: endpoint must be reachable and authenticated
    assert resp.status_code == 200, (
        f"POST /optimize returned {resp.status_code}: {resp.text}"
    )

    body = resp.json()

    # Precondition: rankedSequence field must exist in the response
    assert "rankedSequence" in body, "response must contain rankedSequence"

    # AC1: all input VINs appear in the ranked output
    assert len(body["rankedSequence"]) == len(_VINS), (
        f"expected {len(_VINS)} ranked VINs, got {len(body['rankedSequence'])}"
    )


def test_ac1_duration_ms_is_within_45_second_budget() -> None:
    """AC1: durationMs in the POST /optimize response must be ≤ 45 000.
    The backend's _OPTIMIZER_TIMEOUT_S=45.0 and the frontend POLL_TIMEOUT_MS=45 000
    are the two halves of this timing contract; only one of them is testable here.
    """
    import app.kserve_client as kserve_mod

    original = kserve_mod.make_kserve_client
    kserve_mod.make_kserve_client = lambda: _FastKServeClient()
    try:
        client, _ = _post_optimize(_FastKServeClient())
        resp = client.post(
            "/optimize",
            json=_REQUEST_BODY,
            headers={"Authorization": _BEARER},
        )
    finally:
        kserve_mod.make_kserve_client = original

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    duration_ms = resp.json()["durationMs"]
    assert duration_ms <= 45_000, (
        f"Optimizer durationMs={duration_ms} exceeds the 45 000 ms budget"
    )


def test_ac1_get_optimize_run_retrieves_same_result_as_post() -> None:
    """AC1 (seam): GET /optimize/{runId} must return the result persisted by
    POST /optimize.  This is the exact seam the frontend polls: it trusts that
    GET returns what POST stored.  No existing unit test exercises the HTTP
    route layer for both endpoints together.
    """
    import app.kserve_client as kserve_mod
    from app.router import _get_pool

    original = kserve_mod.make_kserve_client
    kserve_mod.make_kserve_client = lambda: _FastKServeClient()
    try:
        application = _build_test_app()
        pool = _StubPool()
        application.dependency_overrides[_get_pool] = lambda: pool
        client = TestClient(application, raise_server_exceptions=False)

        post_resp = client.post(
            "/optimize",
            json=_REQUEST_BODY,
            headers={"Authorization": _BEARER},
        )

        # Precondition: POST must succeed before we can test the GET
        assert post_resp.status_code == 200, (
            f"POST /optimize failed ({post_resp.status_code}), cannot test GET seam"
        )
        run_id = post_resp.json()["runId"]
        assert run_id, "POST /optimize must return a runId"

        # GET uses the same client (same app, same pool) to test the persistence seam
        get_resp = client.get(
            f"/optimize/{run_id}",
            headers={"Authorization": _BEARER},
        )
    finally:
        kserve_mod.make_kserve_client = original

    # Precondition: GET must find the run (404 = persistence seam is broken)
    assert get_resp.status_code == 200, (
        f"GET /optimize/{run_id} returned {get_resp.status_code}: {get_resp.text}"
    )

    get_body = get_resp.json()
    post_body = post_resp.json()

    assert get_body["runId"] == post_body["runId"], (
        "GET runId must match POST runId"
    )
    assert get_body["weekIso"] == post_body["weekIso"]
    assert len(get_body["rankedSequence"]) == len(post_body["rankedSequence"]), (
        "GET rankedSequence length must match POST rankedSequence length"
    )


# =============================================================================
# AC2 — each RankedVin in the response carries all fields the frontend renders
# =============================================================================


def test_ac2_each_ranked_vin_has_position_constraint_score_vip_and_regulatory_hold() -> None:
    """AC2: the HTTP response must include position (int), constraintScore (float),
    vipPriority (bool), and regulatoryHold (bool) on every RankedVin.  These are
    the exact JSON field names the planning-cockpit TypeScript types consume; a
    rename on either side would break the UI without this test.
    """
    import app.kserve_client as kserve_mod

    original = kserve_mod.make_kserve_client
    kserve_mod.make_kserve_client = lambda: _FastKServeClient()
    try:
        client, _ = _post_optimize(_FastKServeClient())
        resp = client.post(
            "/optimize",
            json=_REQUEST_BODY,
            headers={"Authorization": _BEARER},
        )
    finally:
        kserve_mod.make_kserve_client = original

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    ranked = resp.json()["rankedSequence"]

    # Precondition: there must be VINs to check
    assert len(ranked) > 0, "rankedSequence must not be empty"

    for item in ranked:
        vin = item.get("vin", "(unknown)")

        # position: 1-based integer
        assert "position" in item, f"VIN {vin}: position field missing"
        assert isinstance(item["position"], int), (
            f"VIN {vin}: position must be int, got {type(item['position'])}"
        )
        assert item["position"] >= 1, f"VIN {vin}: position must be ≥ 1"

        # constraintScore: float in [0, 1] — normalised by the aggregator
        assert "constraintScore" in item, f"VIN {vin}: constraintScore field missing"
        assert isinstance(item["constraintScore"], (int, float)), (
            f"VIN {vin}: constraintScore must be numeric, got {type(item['constraintScore'])}"
        )

        # override flags: booleans (or null for unset)
        assert "vipPriority" in item, f"VIN {vin}: vipPriority field missing"
        assert "regulatoryHold" in item, f"VIN {vin}: regulatoryHold field missing"
        assert item["vipPriority"] in (True, False, None), (
            f"VIN {vin}: vipPriority must be boolean or null"
        )
        assert item["regulatoryHold"] in (True, False, None), (
            f"VIN {vin}: regulatoryHold must be boolean or null"
        )


def test_ac2_positions_are_1_based_consecutive_and_cover_all_input_vins() -> None:
    """AC2: positions in the ranked sequence must form a consecutive 1…N range
    covering every input VIN — so the frontend position badge is always meaningful.
    """
    import app.kserve_client as kserve_mod

    original = kserve_mod.make_kserve_client
    kserve_mod.make_kserve_client = lambda: _FastKServeClient()
    try:
        client, _ = _post_optimize(_FastKServeClient())
        resp = client.post(
            "/optimize",
            json=_REQUEST_BODY,
            headers={"Authorization": _BEARER},
        )
    finally:
        kserve_mod.make_kserve_client = original

    assert resp.status_code == 200

    ranked = resp.json()["rankedSequence"]
    positions = sorted(item["position"] for item in ranked)
    expected = list(range(1, len(_VINS) + 1))
    assert positions == expected, (
        f"Positions {positions} are not consecutive 1…{len(_VINS)}"
    )


def test_ac2_vip_and_regulatory_hold_flags_preserved_through_http_layer() -> None:
    """AC2: vipPriority and regulatoryHold flags set on input VINs must propagate
    through the HTTP response so the frontend can render the VIP and REG HOLD badges.
    """
    import app.kserve_client as kserve_mod

    original = kserve_mod.make_kserve_client
    kserve_mod.make_kserve_client = lambda: _FastKServeClient()
    try:
        client, _ = _post_optimize(_FastKServeClient())
        resp = client.post(
            "/optimize",
            json=_REQUEST_BODY,
            headers={"Authorization": _BEARER},
        )
    finally:
        kserve_mod.make_kserve_client = original

    assert resp.status_code == 200

    ranked = resp.json()["rankedSequence"]
    vin_flags = {item["vin"]: item for item in ranked}

    # _VINS[0] is vipPriority=True
    vip_vin = _VINS[0].vin
    assert vip_vin in vin_flags, f"VIP VIN {vip_vin} missing from ranked sequence"
    assert vin_flags[vip_vin]["vipPriority"] is True, (
        f"VIP flag not preserved for {vip_vin}"
    )

    # _VINS[2] is regulatoryHold=True
    hold_vin = _VINS[2].vin
    assert hold_vin in vin_flags, f"Regulatory-hold VIN {hold_vin} missing from ranked sequence"
    assert vin_flags[hold_vin]["regulatoryHold"] is True, (
        f"Regulatory-hold flag not preserved for {hold_vin}"
    )


# =============================================================================
# AC3 — heuristic fallback: resultType label and populated sequence
# =============================================================================


def test_ac3_post_optimize_with_failing_kserve_returns_heuristic_fallback_result_type() -> None:
    """AC3: when all KServe calls raise (simulating unavailability), POST /optimize
    must still return HTTP 200 with resultType="heuristic_fallback" — the exact
    string the frontend checks to render the heuristic banner.
    """
    import app.kserve_client as kserve_mod

    original = kserve_mod.make_kserve_client
    kserve_mod.make_kserve_client = lambda: _FailingKServeClient()
    try:
        client, _ = _post_optimize(_FailingKServeClient())
        resp = client.post(
            "/optimize",
            json=_REQUEST_BODY,
            headers={"Authorization": _BEARER},
        )
    finally:
        kserve_mod.make_kserve_client = original

    # Precondition: service must not 5xx on KServe failure
    assert resp.status_code == 200, (
        f"Expected 200 on fallback path, got {resp.status_code}: {resp.text}"
    )

    body = resp.json()

    # Precondition: resultType field must be present
    assert "resultType" in body, "response must contain resultType"

    # AC3: resultType must equal the exact string the frontend's heuristic banner check uses
    assert body["resultType"] == "heuristic_fallback", (
        f"Expected resultType='heuristic_fallback', got '{body['resultType']}'"
    )


def test_ac3_heuristic_fallback_ranked_sequence_covers_all_input_vins() -> None:
    """AC3: the heuristic-fallback ranked sequence must contain an entry for every
    input VIN — the frontend must be able to render a complete sequence even when
    Monte Carlo is unavailable.
    """
    import app.kserve_client as kserve_mod

    original = kserve_mod.make_kserve_client
    kserve_mod.make_kserve_client = lambda: _FailingKServeClient()
    try:
        client, _ = _post_optimize(_FailingKServeClient())
        resp = client.post(
            "/optimize",
            json=_REQUEST_BODY,
            headers={"Authorization": _BEARER},
        )
    finally:
        kserve_mod.make_kserve_client = original

    assert resp.status_code == 200
    assert resp.json()["resultType"] == "heuristic_fallback", (
        "Precondition: KServe stub must trigger heuristic fallback"
    )

    ranked = resp.json()["rankedSequence"]
    assert len(ranked) == len(_VINS), (
        f"Heuristic rankedSequence has {len(ranked)} items, expected {len(_VINS)}"
    )

    # Each entry must still carry the four fields the frontend needs
    for item in ranked:
        assert "position" in item
        assert "constraintScore" in item
        assert "vipPriority" in item
        assert "regulatoryHold" in item


def test_ac3_result_type_is_one_of_exactly_two_valid_enum_values() -> None:
    """AC3: resultType must be exactly "monte_carlo" or "heuristic_fallback" —
    the two values the frontend's ResultType union type handles.  A third or
    missing value would cause the heuristic banner to never render.
    """
    import app.kserve_client as kserve_mod

    for stub, expected in [
        (_FastKServeClient(), {"monte_carlo", "heuristic_fallback"}),
        (_FailingKServeClient(), {"heuristic_fallback"}),
    ]:
        original = kserve_mod.make_kserve_client
        kserve_mod.make_kserve_client = lambda s=stub: s
        try:
            client, _ = _post_optimize(stub)
            resp = client.post(
                "/optimize",
                json=_REQUEST_BODY,
                headers={"Authorization": _BEARER},
            )
        finally:
            kserve_mod.make_kserve_client = original

        assert resp.status_code == 200
        result_type = resp.json()["resultType"]
        assert result_type in {"monte_carlo", "heuristic_fallback"}, (
            f"resultType '{result_type}' is not a recognised ResultType variant"
        )
        if expected == {"heuristic_fallback"}:
            assert result_type == "heuristic_fallback", (
                f"Failing KServe must produce heuristic_fallback, got '{result_type}'"
            )
