"""Unit tests for POST /optimize and GET /optimize/{runId}.

Covers:
 - Monte Carlo path (all 50 runs complete, sequence sorted by position)
 - Heuristic fallback on KServe timeout and on KServe error
 - Idempotency key caching
 - 422 on unknown option code
 - GET /optimize/{runId}: stored result returned; 404 for unknown runId
 - Heuristic scorer formula per the settled decision
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import patch

import pytest

from app.models import (
    ConstraintSnapshot,
    OptimizeRequest,
    OptimizeResponse,
    RankedVin,
    ResultType,
    VinInput,
)
from app.optimizer import (
    _CONFIDENCE_FALLBACK_THRESHOLD,
    _aggregate_monte_carlo_results,
    apply_heuristic,
    get_run,
    run_optimize,
    score_vin_heuristic,
)

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

# Far-future timestamp avoids "stale snapshot" confusion in any future validator
_FAR_FUTURE = "2099-01-01T00:00:00+00:00"

_VIN_A = "WBA3B3C56DF596858"
_VIN_B = "WBA3B3C57DF596859"
_VIN_C = "WBA3B3C58DF596860"

_SNAPSHOT = ConstraintSnapshot(
    snapshotTimestamp=_FAR_FUTURE,
    paintCapacityUtilisation=70.0,
    bodyWipQueueDepth=100,
    kittingReadinessPct=90.0,
    maintenanceWindows=[],
)

_VINS_2 = [
    VinInput(vin=_VIN_A, vipPriority=False, regulatoryHold=False),
    VinInput(vin=_VIN_B, vipPriority=False, regulatoryHold=False),
]


def _make_request(
    vins: list[VinInput] | None = None,
    monte_carlo_runs: int = 2,
    idempotency_key: uuid.UUID | None = None,
) -> OptimizeRequest:
    return OptimizeRequest(
        weekIso="2026-W42",
        vins=vins or _VINS_2,
        constraintSnapshot=_SNAPSHOT,
        monteCarloRuns=monte_carlo_runs,
        idempotencyKey=idempotency_key,
    )


# ---------------------------------------------------------------------------
# Stub KServe client
# ---------------------------------------------------------------------------


class _StubKServeClient:
    """Deterministic stub: returns a ranked list in input order, or raises."""

    def __init__(self, raises: Exception | None = None) -> None:
        self._raises = raises
        self.call_count: int = 0

    async def predict(
        self,
        vins: list[VinInput],
        constraint_snapshot: ConstraintSnapshot,
        run_index: int,
    ) -> list[dict[str, Any]]:
        self.call_count += 1
        if self._raises is not None:
            raise self._raises
        return [
            {
                "vin": v.vin,
                "position": idx + 1,
                "constraintScore": float(100 - idx),
                "vipPriority": v.vipPriority,
                "regulatoryHold": v.regulatoryHold,
            }
            for idx, v in enumerate(vins)
        ]


# ---------------------------------------------------------------------------
# In-memory pool / connection stubs
# ---------------------------------------------------------------------------


class _StubConn:
    """Minimal asyncpg connection stub that handles the two queries used by optimizer."""

    def __init__(self, pool: "_StubPool") -> None:
        self._pool = pool

    async def __aenter__(self) -> "_StubConn":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def execute(self, query: str, *args: object) -> None:
        if "INSERT INTO optimizer_run" not in query:
            return
        (
            run_id, week_iso, result_type, ranked_sequence,
            confidence_score, duration_ms, my26_config_version,
            constraint_snapshot, idempotency_key, completed_at,
        ) = args
        record: dict[str, Any] = {
            "run_id": run_id,
            "week_iso": week_iso,
            "result_type": result_type,
            "ranked_sequence": ranked_sequence,
            "confidence_score": confidence_score,
            "duration_ms": duration_ms,
            "my26_config_version": my26_config_version,
            "constraint_snapshot": constraint_snapshot,
            "idempotency_key": idempotency_key,
            "completed_at": completed_at,
        }
        self._pool._runs[run_id] = record
        if idempotency_key is not None:
            self._pool._idem_map[idempotency_key] = run_id

    async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
        if "WHERE run_id = $1" in query:
            return self._pool._runs.get(args[0])
        if "WHERE idempotency_key = $1" in query:
            run_id = self._pool._idem_map.get(args[0])
            return self._pool._runs.get(run_id) if run_id else None
        return None


class _StubPool:
    """Minimal asyncpg.Pool stub; each instance starts with empty state."""

    def __init__(self) -> None:
        self._runs: dict[uuid.UUID, dict[str, Any]] = {}
        self._idem_map: dict[uuid.UUID, uuid.UUID] = {}

    def acquire(self) -> _StubConn:
        return _StubConn(self)


# ---------------------------------------------------------------------------
# Heuristic scorer unit tests
# ---------------------------------------------------------------------------


def test_score_normal_vin_baseline_is_1000() -> None:
    vin = VinInput(vin=_VIN_A)
    assert score_vin_heuristic(vin, None, _SNAPSHOT, 0) == 1000.0


def test_score_vip_priority_adds_500() -> None:
    vin = VinInput(vin=_VIN_A, vipPriority=True)
    assert score_vin_heuristic(vin, None, _SNAPSHOT, 0) == 1500.0


def test_score_regulatory_hold_sets_base_to_zero() -> None:
    vin = VinInput(vin=_VIN_A, regulatoryHold=True)
    assert score_vin_heuristic(vin, None, _SNAPSHOT, 0) == 0.0


def test_score_paint_changeover_subtracts_200() -> None:
    vin = VinInput(vin=_VIN_A, paintCode="RED")
    assert score_vin_heuristic(vin, "BLUE", _SNAPSHOT, 0) == 800.0


def test_score_low_kitting_subtracts_correct_penalty() -> None:
    # kittingReadinessPct=60 → (80-60)*5 = 100 penalty
    snapshot = ConstraintSnapshot(
        snapshotTimestamp=_FAR_FUTURE,
        paintCapacityUtilisation=70.0,
        bodyWipQueueDepth=100,
        kittingReadinessPct=60.0,
        maintenanceWindows=[],
    )
    vin = VinInput(vin=_VIN_A)
    assert score_vin_heuristic(vin, None, snapshot, 0) == 900.0


def test_score_no_paint_penalty_when_codes_match() -> None:
    vin = VinInput(vin=_VIN_A, paintCode="RED")
    assert score_vin_heuristic(vin, "RED", _SNAPSHOT, 0) == 1000.0


def test_score_no_kitting_penalty_at_exactly_80_pct() -> None:
    snapshot = ConstraintSnapshot(
        snapshotTimestamp=_FAR_FUTURE,
        paintCapacityUtilisation=70.0,
        bodyWipQueueDepth=100,
        kittingReadinessPct=80.0,
        maintenanceWindows=[],
    )
    vin = VinInput(vin=_VIN_A)
    assert score_vin_heuristic(vin, None, snapshot, 0) == 1000.0


# ---------------------------------------------------------------------------
# apply_heuristic order and field completeness
# ---------------------------------------------------------------------------


def test_heuristic_regulatory_hold_placed_at_end() -> None:
    vins = [
        VinInput(vin=_VIN_A, regulatoryHold=True),
        VinInput(vin=_VIN_B, regulatoryHold=False),
    ]
    result = apply_heuristic(vins, _SNAPSHOT)
    assert result[0].vin == _VIN_B
    assert result[1].vin == _VIN_A


def test_heuristic_positions_are_1_based_consecutive() -> None:
    result = apply_heuristic(_VINS_2, _SNAPSHOT)
    assert [rv.position for rv in result] == list(range(1, len(_VINS_2) + 1))


def test_heuristic_all_ranked_vins_carry_vip_and_hold_flags() -> None:
    vins = [
        VinInput(vin=_VIN_A, vipPriority=True, regulatoryHold=False),
        VinInput(vin=_VIN_B, vipPriority=False, regulatoryHold=True),
    ]
    result = apply_heuristic(vins, _SNAPSHOT)
    for rv in result:
        assert rv.vipPriority is not None
        assert rv.regulatoryHold is not None


def test_heuristic_output_length_equals_input_length() -> None:
    vins = [VinInput(vin=v) for v in [_VIN_A, _VIN_B, _VIN_C]]
    assert len(apply_heuristic(vins, _SNAPSHOT)) == 3


def test_heuristic_vip_scores_higher_than_normal() -> None:
    vins = [
        VinInput(vin=_VIN_A, vipPriority=False),
        VinInput(vin=_VIN_B, vipPriority=True),
    ]
    result = apply_heuristic(vins, _SNAPSHOT)
    # VIP gets +500 so it should rank first
    assert result[0].vin == _VIN_B


# ---------------------------------------------------------------------------
# Monte Carlo aggregation
# ---------------------------------------------------------------------------


def test_aggregate_positions_in_ascending_order() -> None:
    run_results = [
        [{"vin": _VIN_B, "position": 1, "constraintScore": 90.0},
         {"vin": _VIN_A, "position": 2, "constraintScore": 80.0}],
        [{"vin": _VIN_A, "position": 1, "constraintScore": 85.0},
         {"vin": _VIN_B, "position": 2, "constraintScore": 75.0}],
    ]
    vins = [VinInput(vin=_VIN_A), VinInput(vin=_VIN_B)]
    ranked, _ = _aggregate_monte_carlo_results(run_results, vins)
    positions = [rv.position for rv in ranked]
    assert positions == sorted(positions)


def test_aggregate_confidence_in_zero_to_one_range() -> None:
    run_results = [[{"vin": _VIN_A, "position": 1, "constraintScore": 90.0}]]
    vins = [VinInput(vin=_VIN_A)]
    _, confidence = _aggregate_monte_carlo_results(run_results, vins)
    assert 0.0 <= confidence <= 1.0


def test_aggregate_perfect_agreement_gives_confidence_one() -> None:
    run_results = [
        [{"vin": _VIN_A, "position": 1, "constraintScore": 90.0}],
        [{"vin": _VIN_A, "position": 1, "constraintScore": 88.0}],
    ]
    vins = [VinInput(vin=_VIN_A)]
    _, confidence = _aggregate_monte_carlo_results(run_results, vins)
    assert confidence == 1.0


# ---------------------------------------------------------------------------
# run_optimize — Monte Carlo path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monte_carlo_invokes_all_50_runs() -> None:
    pool = _StubPool()
    client = _StubKServeClient()
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        await run_optimize(_make_request(monte_carlo_runs=50), pool, client)
    assert client.call_count == 50


@pytest.mark.asyncio
async def test_monte_carlo_result_type_is_monte_carlo() -> None:
    pool = _StubPool()
    client = _StubKServeClient()
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        response = await run_optimize(_make_request(monte_carlo_runs=2), pool, client)
    assert response.resultType == ResultType.monte_carlo


@pytest.mark.asyncio
async def test_monte_carlo_ranked_sequence_length_equals_input_count() -> None:
    pool = _StubPool()
    client = _StubKServeClient()
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        response = await run_optimize(_make_request(monte_carlo_runs=2, vins=_VINS_2), pool, client)
    assert len(response.rankedSequence) == len(_VINS_2)


@pytest.mark.asyncio
async def test_monte_carlo_ranked_sequence_sorted_ascending_by_position() -> None:
    pool = _StubPool()
    client = _StubKServeClient()
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        response = await run_optimize(_make_request(monte_carlo_runs=2, vins=_VINS_2), pool, client)
    positions = [rv.position for rv in response.rankedSequence]
    assert positions == sorted(positions)


@pytest.mark.asyncio
async def test_monte_carlo_each_ranked_vin_carries_constraint_score_and_flags() -> None:
    pool = _StubPool()
    client = _StubKServeClient()
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        response = await run_optimize(_make_request(monte_carlo_runs=2, vins=_VINS_2), pool, client)
    for rv in response.rankedSequence:
        assert rv.constraintScore is not None
        assert rv.vipPriority is not None
        assert rv.regulatoryHold is not None


@pytest.mark.asyncio
async def test_monte_carlo_confidence_score_between_0_and_1() -> None:
    pool = _StubPool()
    client = _StubKServeClient()
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        response = await run_optimize(_make_request(monte_carlo_runs=2), pool, client)
    assert 0.0 <= response.confidenceScore <= 1.0


@pytest.mark.asyncio
async def test_monte_carlo_duration_ms_is_non_negative() -> None:
    pool = _StubPool()
    client = _StubKServeClient()
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        response = await run_optimize(_make_request(monte_carlo_runs=2), pool, client)
    assert response.durationMs >= 0


# ---------------------------------------------------------------------------
# Heuristic fallback — KServe failures trigger fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kserve_timeout_triggers_heuristic_fallback() -> None:
    pool = _StubPool()
    client = _StubKServeClient(raises=asyncio.TimeoutError("mock timeout"))
    response = await run_optimize(_make_request(monte_carlo_runs=2), pool, client)
    assert response.resultType == ResultType.heuristic_fallback


@pytest.mark.asyncio
async def test_kserve_os_error_triggers_heuristic_fallback() -> None:
    pool = _StubPool()
    client = _StubKServeClient(raises=OSError("connection refused"))
    response = await run_optimize(_make_request(monte_carlo_runs=2), pool, client)
    assert response.resultType == ResultType.heuristic_fallback


@pytest.mark.asyncio
async def test_heuristic_fallback_returns_valid_response_not_504() -> None:
    pool = _StubPool()
    client = _StubKServeClient(raises=asyncio.TimeoutError())
    # Must not raise — always returns a result when heuristic is available
    response = await run_optimize(_make_request(monte_carlo_runs=2), pool, client)
    assert response.rankedSequence


@pytest.mark.asyncio
async def test_heuristic_fallback_sequence_length_equals_input() -> None:
    pool = _StubPool()
    client = _StubKServeClient(raises=asyncio.TimeoutError())
    response = await run_optimize(_make_request(monte_carlo_runs=2, vins=_VINS_2), pool, client)
    assert len(response.rankedSequence) == len(_VINS_2)


@pytest.mark.asyncio
async def test_heuristic_fallback_label_is_heuristic_fallback() -> None:
    pool = _StubPool()
    client = _StubKServeClient(raises=asyncio.TimeoutError())
    response = await run_optimize(_make_request(monte_carlo_runs=2), pool, client)
    assert response.resultType == ResultType.heuristic_fallback


# ---------------------------------------------------------------------------
# Idempotency — second call with same key returns cached result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotency_second_call_returns_same_run_id() -> None:
    pool = _StubPool()
    client = _StubKServeClient()
    key = uuid.uuid4()
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        first = await run_optimize(_make_request(monte_carlo_runs=2, idempotency_key=key), pool, client)
    calls_after_first = client.call_count

    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        second = await run_optimize(_make_request(monte_carlo_runs=2, idempotency_key=key), pool, client)

    assert str(first.runId) == str(second.runId)
    # KServe must not have been called again
    assert client.call_count == calls_after_first


@pytest.mark.asyncio
async def test_idempotency_different_keys_produce_different_run_ids() -> None:
    pool = _StubPool()
    client = _StubKServeClient()
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        first = await run_optimize(_make_request(monte_carlo_runs=2, idempotency_key=uuid.uuid4()), pool, client)
        second = await run_optimize(_make_request(monte_carlo_runs=2, idempotency_key=uuid.uuid4()), pool, client)
    assert str(first.runId) != str(second.runId)


# ---------------------------------------------------------------------------
# GET /optimize/{runId}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_run_returns_stored_result() -> None:
    pool = _StubPool()
    client = _StubKServeClient()
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        stored = await run_optimize(_make_request(monte_carlo_runs=2), pool, client)
    retrieved = await get_run(stored.runId, pool)
    assert retrieved is not None
    assert str(retrieved.runId) == str(stored.runId)


@pytest.mark.asyncio
async def test_get_run_returns_none_for_unknown_id() -> None:
    pool = _StubPool()
    assert await get_run(uuid.uuid4(), pool) is None


@pytest.mark.asyncio
async def test_get_run_preserves_ranked_sequence_length() -> None:
    pool = _StubPool()
    client = _StubKServeClient()
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        stored = await run_optimize(_make_request(monte_carlo_runs=2, vins=_VINS_2), pool, client)
    retrieved = await get_run(stored.runId, pool)
    assert retrieved is not None
    assert len(retrieved.rankedSequence) == len(_VINS_2)


@pytest.mark.asyncio
async def test_get_run_preserves_week_iso() -> None:
    pool = _StubPool()
    client = _StubKServeClient()
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        stored = await run_optimize(_make_request(monte_carlo_runs=2), pool, client)
    retrieved = await get_run(stored.runId, pool)
    assert retrieved is not None
    assert retrieved.weekIso == "2026-W42"


# ---------------------------------------------------------------------------
# HTTP layer — TestClient
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def _make_jwt() -> str:
    """Construct a minimal unsigned JWT (alg=none) the test harness accepts."""
    import base64

    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        b'{"sub":"planner@bmwgroup.net","exp":9999999999}'
    ).rstrip(b"=").decode()
    return f"{header}.{payload}."


_VALID_BODY: dict[str, Any] = {
    "weekIso": "2026-W42",
    "vins": [{"vin": _VIN_A}, {"vin": _VIN_B}],
    "constraintSnapshot": {
        "snapshotTimestamp": _FAR_FUTURE,
        "paintCapacityUtilisation": 70.0,
        "bodyWipQueueDepth": 100,
        "kittingReadinessPct": 90.0,
    },
    "monteCarloRuns": 2,
}


def test_post_optimize_401_when_no_auth_token(app_client) -> None:
    resp = app_client.post("/optimize", json=_VALID_BODY)
    assert resp.status_code == 401


def test_post_optimize_400_on_invalid_week_iso(app_client) -> None:
    body = {**_VALID_BODY, "weekIso": "bad-value"}
    resp = app_client.post(
        "/optimize",
        json=body,
        headers={"Authorization": f"Bearer {_make_jwt()}"},
    )
    assert resp.status_code == 400


def test_post_optimize_400_error_body_has_code_message_correlation_id(app_client) -> None:
    body = {**_VALID_BODY, "weekIso": "bad-value"}
    resp = app_client.post(
        "/optimize",
        json=body,
        headers={"Authorization": f"Bearer {_make_jwt()}"},
    )
    body_json = resp.json()
    assert "code" in body_json
    assert "message" in body_json
    assert "correlation_id" in body_json


def test_get_optimize_run_401_when_no_auth_token(app_client) -> None:
    resp = app_client.get(f"/optimize/{uuid.uuid4()}")
    assert resp.status_code == 401


def test_get_optimize_run_404_for_unknown_run_id(app_client) -> None:
    with patch("app.db.get_pool") as mock_pool:
        mock_pool.return_value = _StubPool()
        resp = app_client.get(
            f"/optimize/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )
    assert resp.status_code == 404


def test_get_optimize_run_404_error_body_code_is_run_not_found(app_client) -> None:
    with patch("app.db.get_pool") as mock_pool:
        mock_pool.return_value = _StubPool()
        resp = app_client.get(
            f"/optimize/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )
    assert resp.json()["code"] == "RUN_NOT_FOUND"
    assert "correlation_id" in resp.json()


# ---------------------------------------------------------------------------
# 422 — unknown option code
# ---------------------------------------------------------------------------


def test_post_optimize_422_when_vin_carries_unknown_option_code(app_client) -> None:
    from app.validation import set_known_option_codes

    set_known_option_codes(frozenset({"OPT-KNOWN"}))
    try:
        body = {**_VALID_BODY, "vins": [{"vin": _VIN_A, "optionCodes": ["OPT-UNKNOWN"]}]}
        resp = app_client.post(
            "/optimize",
            json=body,
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "UNKNOWN_OPTION_CODES"
    finally:
        set_known_option_codes(frozenset())


def test_post_optimize_200_when_all_option_codes_are_known(app_client) -> None:
    from app.validation import set_known_option_codes

    set_known_option_codes(frozenset({"OPT-KNOWN"}))
    try:
        # Patch pool and kserve so we don't need live infrastructure
        stub_pool = _StubPool()
        stub_client = _StubKServeClient()
        with (
            patch("app.db.get_pool", return_value=stub_pool),
            patch("app.kserve_client.make_kserve_client", return_value=stub_client),
            patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0),
            patch("app.optimizer.asyncio.create_task"),
        ):
            body = {**_VALID_BODY, "vins": [{"vin": _VIN_A, "optionCodes": ["OPT-KNOWN"]}]}
            resp = app_client.post(
                "/optimize",
                json=body,
                headers={"Authorization": f"Bearer {_make_jwt()}"},
            )
        assert resp.status_code == 200
    finally:
        set_known_option_codes(frozenset())
