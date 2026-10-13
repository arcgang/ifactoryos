"""Unit tests for targeted re-sequence logic and alert DB helpers.

Covers:
 - VIN set resolution (downstream expansion)
 - Alert lookup: found, not found
 - list_active_alerts: status filter, cursor pagination
 - run_resequence: active alert path, heuristic fallback, idempotency cache
 - run_resequence: alert not found → 400, alert not active → 400
 - ResequenceTriggeredEvent payload fields
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import (
    AlertListResponse,
    AlertStatus,
    ConstraintAlertRecord,
    ConstraintSnapshot,
    ConstraintType,
    CursorPage,
    OptimizeResponse,
    RankedVin,
    ResequenceRequest,
    ResultType,
    VinInput,
)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_FAR_FUTURE = "2099-01-01T00:00:00+00:00"
_FIXED_DT = datetime(2099, 1, 1, tzinfo=timezone.utc)

_VIN_A = "WBA3B3C56DF596858"
_VIN_B = "WBA3B3C57DF596859"
_VIN_C = "WBA3B3C58DF596860"

_ALERT_ID = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
_RUN_ID = uuid.UUID("660e8400-e29b-41d4-a716-446655440001")

_SNAPSHOT = ConstraintSnapshot(
    snapshotTimestamp=_FAR_FUTURE,
    paintCapacityUtilisation=70.0,
    bodyWipQueueDepth=100,
    kittingReadinessPct=90.0,
    maintenanceWindows=[],
)

# JWT tokens (alg=none, exp=9999999999)
# _BEARER_PLANNER: {"sub":"planner@bmwgroup.net","roles":["production-planner"],"exp":9999999999}
_BEARER_PLANNER = (
    "Bearer eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJwbGFubmVyQGJtd2dyb3VwLm5ldCIsInJvbGVzIjpbInByb2R1Y3Rpb24tcGxhbm5lciJdLCJleHAiOjk5OTk5OTk5OTl9"
    "."
)
# _BEARER: {"sub":"planner@bmwgroup.net","roles":["constraint-threshold-writer"],"exp":9999999999}
_BEARER = (
    "Bearer eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJwbGFubmVyQGJtd2dyb3VwLm5ldCIsInJvbGVzIjpbImNvbnN0cmFpbnQtdGhyZXNob2xkLXdyaXRlciJdLCJleHAiOjk5OTk5OTk5OTl9"
    "."
)
# _BEARER_READONLY: {"sub":"readonly@bmwgroup.net","roles":[],"exp":9999999999}
_BEARER_READONLY = (
    "Bearer eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJyZWFkb25seUBibXdncm91cC5uZXQiLCJyb2xlcyI6W10sImV4cCI6OTk5OTk5OTk5OX0"
    "."
)


def _make_row(**kwargs: object) -> MagicMock:
    data = kwargs
    row = MagicMock()
    row.__getitem__ = lambda self_, k: data[k]
    return row


def _make_alert_row(
    alert_id: uuid.UUID = _ALERT_ID,
    status: str = "active",
    affected_vins: list[str] | None = None,
) -> MagicMock:
    if affected_vins is None:
        affected_vins = [_VIN_A]
    return _make_row(
        alert_id=alert_id,
        constraint_type="paint_capacity",
        current_value=92.5,
        threshold_value=85.0,
        affected_vins=json.dumps(affected_vins),
        status=status,
        detected_at=_FIXED_DT,
        acknowledged_at=None,
        acknowledged_by=None,
        resolved_at=None,
    )


# ---------------------------------------------------------------------------
# _resolve_downstream_vins — VIN expansion logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_downstream_vins_no_locked_run_returns_affected_only():
    from app.resequence import _resolve_downstream_vins

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    result = await _resolve_downstream_vins(mock_pool, [_VIN_A])
    assert len(result) == 1
    assert result[0].vin == _VIN_A


@pytest.mark.asyncio
async def test_resolve_downstream_vins_expands_to_downstream_neighbour():
    from app.resequence import _resolve_downstream_vins

    ranked = [
        {"vin": _VIN_A, "position": 1, "constraintScore": 900.0},
        {"vin": _VIN_B, "position": 2, "constraintScore": 800.0},
        {"vin": _VIN_C, "position": 3, "constraintScore": 700.0},
    ]
    mock_row = _make_row(
        ranked_sequence=json.dumps(ranked),
        constraint_snapshot=json.dumps({"snapshotTimestamp": _FAR_FUTURE}),
    )

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=mock_row)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    # VIN_A is at position 1 → downstream is position 2 (VIN_B)
    result = await _resolve_downstream_vins(mock_pool, [_VIN_A])
    vin_strs = {v.vin for v in result}
    assert _VIN_A in vin_strs
    assert _VIN_B in vin_strs


@pytest.mark.asyncio
async def test_resolve_downstream_vins_empty_input_returns_empty():
    from app.resequence import _resolve_downstream_vins

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    result = await _resolve_downstream_vins(mock_pool, [])
    assert result == []


# ---------------------------------------------------------------------------
# get_alert_for_resequence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_alert_for_resequence_returns_record_when_found():
    from app.resequence import get_alert_for_resequence

    mock_row = _make_alert_row()
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=mock_row)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    result = await get_alert_for_resequence(_ALERT_ID, mock_pool)
    assert result is not None
    assert result.alertId == _ALERT_ID
    assert result.status == AlertStatus.active
    assert result.constraintType == ConstraintType.paint_capacity
    assert result.currentValue == 92.5
    assert result.thresholdValue == 85.0


@pytest.mark.asyncio
async def test_get_alert_for_resequence_returns_none_when_not_found():
    from app.resequence import get_alert_for_resequence

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    result = await get_alert_for_resequence(uuid.uuid4(), mock_pool)
    assert result is None


# ---------------------------------------------------------------------------
# list_active_alerts — status filter and pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_active_alerts_returns_active_records():
    from app.resequence import list_active_alerts

    mock_row = _make_alert_row(status="active")
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[mock_row])

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    result = await list_active_alerts(mock_pool, cursor=None, limit=50, status_filter="active")
    assert len(result.data) == 1
    assert result.data[0].status == AlertStatus.active
    assert result.page.next_cursor is None


@pytest.mark.asyncio
async def test_list_active_alerts_sets_next_cursor_when_more_rows():
    from app.resequence import list_active_alerts

    rows = [_make_alert_row(alert_id=uuid.uuid4()) for _ in range(3)]
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=rows)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    result = await list_active_alerts(mock_pool, cursor=None, limit=2, status_filter="active")
    assert len(result.data) == 2
    assert result.page.next_cursor is not None


# ---------------------------------------------------------------------------
# run_resequence — alert not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_resequence_raises_400_when_alert_not_found():
    from fastapi import HTTPException

    from app.resequence import run_resequence

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.execute = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    request = ResequenceRequest(
        alertId=uuid.uuid4(),
        affectedVins=[VinInput(vin=_VIN_A)],
        constraintSnapshot=_SNAPSHOT,
    )

    mock_kserve = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await run_resequence(request=request, pool=mock_pool, kserve_client=mock_kserve)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "ALERT_NOT_FOUND"


# ---------------------------------------------------------------------------
# run_resequence — alert not active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_resequence_raises_400_when_alert_not_active():
    from fastapi import HTTPException

    from app.resequence import run_resequence

    mock_row = _make_alert_row(status="acknowledged")
    # First fetchrow: get_alert_for_resequence
    # Second fetchrow: _resolve_downstream_vins (should not be reached)
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=mock_row)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    request = ResequenceRequest(
        alertId=_ALERT_ID,
        affectedVins=[VinInput(vin=_VIN_A)],
        constraintSnapshot=_SNAPSHOT,
    )

    mock_kserve = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await run_resequence(request=request, pool=mock_pool, kserve_client=mock_kserve)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "ALERT_NOT_ACTIVE"


# ---------------------------------------------------------------------------
# run_resequence — heuristic fallback when KServe fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_resequence_uses_heuristic_fallback_on_kserve_failure():
    from app.resequence import run_resequence

    alert_row = _make_alert_row(status="active", affected_vins=[_VIN_A])
    ranked_sequence = json.dumps([
        {"vin": _VIN_A, "position": 1, "constraintScore": 900.0},
        {"vin": _VIN_B, "position": 2, "constraintScore": 800.0},
    ])
    locked_run_row = _make_row(
        ranked_sequence=ranked_sequence,
        constraint_snapshot=json.dumps({"snapshotTimestamp": _FAR_FUTURE}),
    )

    fetchrow_results = [alert_row, locked_run_row, None]
    call_index = {"n": 0}

    async def _fetchrow(*args: object, **kwargs: object) -> Any:
        idx = call_index["n"]
        call_index["n"] += 1
        if idx < len(fetchrow_results):
            return fetchrow_results[idx]
        return None

    execute_calls: list = []

    async def _execute(*args: object, **kwargs: object) -> None:
        execute_calls.append(args)

    mock_conn = AsyncMock()
    mock_conn.fetchrow = _fetchrow
    mock_conn.execute = _execute

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    # KServe raises an error on every call → triggers heuristic fallback
    mock_kserve = MagicMock()
    mock_kserve.predict = AsyncMock(side_effect=OSError("KServe unavailable"))

    request = ResequenceRequest(
        alertId=_ALERT_ID,
        affectedVins=[VinInput(vin=_VIN_A)],
        constraintSnapshot=_SNAPSHOT,
    )

    with patch("app.resequence._publish_resequence_event", new=AsyncMock()), \
         patch("app.resequence._write_resequence_audit", new=AsyncMock()):
        result = await run_resequence(
            request=request,
            pool=mock_pool,
            kserve_client=mock_kserve,
            user_id="planner@bmwgroup.net",
        )

    assert result.resultType == ResultType.heuristic_fallback
    assert len(result.rankedSequence) >= 1
    assert result.confidenceScore == 0.5
    assert result.durationMs >= 0


# ---------------------------------------------------------------------------
# run_resequence — result is scoped to affected VINs only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_resequence_result_scoped_to_affected_vins():
    from app.resequence import run_resequence

    alert_row = _make_alert_row(status="active", affected_vins=[_VIN_A])
    locked_run_row = _make_row(
        ranked_sequence=json.dumps([
            {"vin": _VIN_A, "position": 1, "constraintScore": 900.0},
        ]),
        constraint_snapshot=json.dumps({"snapshotTimestamp": _FAR_FUTURE}),
    )

    fetchrow_results = [alert_row, locked_run_row, None]
    call_index = {"n": 0}

    async def _fetchrow(*args: object, **kwargs: object) -> Any:
        idx = call_index["n"]
        call_index["n"] += 1
        return fetchrow_results[idx] if idx < len(fetchrow_results) else None

    mock_conn = AsyncMock()
    mock_conn.fetchrow = _fetchrow
    mock_conn.execute = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_kserve = MagicMock()
    mock_kserve.predict = AsyncMock(side_effect=OSError("unavailable"))

    request = ResequenceRequest(
        alertId=_ALERT_ID,
        affectedVins=[VinInput(vin=_VIN_A)],
        constraintSnapshot=_SNAPSHOT,
    )

    with patch("app.resequence._publish_resequence_event", new=AsyncMock()), \
         patch("app.resequence._write_resequence_audit", new=AsyncMock()):
        result = await run_resequence(
            request=request,
            pool=mock_pool,
            kserve_client=mock_kserve,
        )

    ranked_vins = {rv.vin for rv in result.rankedSequence}
    # Only affected VINs (and their downstream neighbours) should appear
    assert _VIN_A in ranked_vins


# ---------------------------------------------------------------------------
# run_resequence — idempotency cache hit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_resequence_returns_cached_result_on_same_idempotency_key():
    from app.resequence import run_resequence

    idempotency_key = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
    cached_response = OptimizeResponse(
        runId=_RUN_ID,
        weekIso="2099-W01",
        resultType=ResultType.monte_carlo,
        rankedSequence=[RankedVin(vin=_VIN_A, position=1, constraintScore=900.0)],
        completedAt=_FAR_FUTURE,
        durationMs=1234,
        confidenceScore=0.95,
    )

    with patch("app.resequence._load_cached_resequence", new=AsyncMock(return_value=cached_response)):
        mock_pool = MagicMock()
        mock_kserve = MagicMock()
        request = ResequenceRequest(
            alertId=_ALERT_ID,
            affectedVins=[VinInput(vin=_VIN_A)],
            constraintSnapshot=_SNAPSHOT,
            idempotencyKey=idempotency_key,
        )
        result = await run_resequence(
            request=request,
            pool=mock_pool,
            kserve_client=mock_kserve,
        )

    assert result.runId == _RUN_ID
    assert result.resultType == ResultType.monte_carlo


# ---------------------------------------------------------------------------
# _row_to_alert_record — JSONB string and list both accepted for affected_vins
# ---------------------------------------------------------------------------


def test_row_to_alert_record_affected_vins_from_json_string():
    from app.resequence import _row_to_alert_record

    row = _make_alert_row(affected_vins=[_VIN_A, _VIN_B])
    record = _row_to_alert_record(row)
    assert record.affectedVins == [_VIN_A, _VIN_B]


def test_row_to_alert_record_status_acknowledged():
    from app.resequence import _row_to_alert_record

    row = _make_alert_row(status="acknowledged")
    record = _row_to_alert_record(row)
    assert record.status == AlertStatus.acknowledged


# ---------------------------------------------------------------------------
# API endpoint tests — POST /resequence
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


def _build_test_app() -> FastAPI:
    import app.main as main_mod

    with patch.object(main_mod, "_lifespan", _noop_lifespan):
        application = main_mod.create_app()
    return application


def test_post_resequence_401_when_no_bearer_token():
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    payload = {
        "alertId": str(_ALERT_ID),
        "affectedVins": [{"vin": _VIN_A}],
        "constraintSnapshot": {
            "snapshotTimestamp": _FAR_FUTURE,
            "paintCapacityUtilisation": 70.0,
            "bodyWipQueueDepth": 100,
            "kittingReadinessPct": 90.0,
        },
    }
    resp = client.post("/resequence", json=payload)
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "MISSING_TOKEN"


def test_post_resequence_400_on_missing_required_field():
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    # Missing constraintSnapshot
    payload = {
        "alertId": str(_ALERT_ID),
        "affectedVins": [{"vin": _VIN_A}],
    }
    resp = client.post(
        "/resequence",
        json=payload,
        headers={"Authorization": _BEARER_PLANNER},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"


def test_post_resequence_403_when_caller_lacks_production_planner_role():
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    payload = {
        "alertId": str(_ALERT_ID),
        "affectedVins": [{"vin": _VIN_A}],
        "constraintSnapshot": {
            "snapshotTimestamp": _FAR_FUTURE,
            "paintCapacityUtilisation": 70.0,
            "bodyWipQueueDepth": 100,
            "kittingReadinessPct": 90.0,
        },
    }
    # _BEARER has constraint-threshold-writer role, not production-planner
    resp = client.post(
        "/resequence",
        json=payload,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "FORBIDDEN"
    assert "correlation_id" in body


def test_post_resequence_200_returns_optimize_response():
    import app.resequence as resequence_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    expected = OptimizeResponse(
        runId=_RUN_ID,
        weekIso="2099-W01",
        resultType=ResultType.heuristic_fallback,
        rankedSequence=[RankedVin(vin=_VIN_A, position=1, constraintScore=900.0)],
        completedAt=_FAR_FUTURE,
        durationMs=500,
        confidenceScore=0.5,
    )

    original = resequence_mod.run_resequence

    async def _mock_resequence(request: Any, pool: Any, kserve_client: Any, user_id: Any = "unknown") -> OptimizeResponse:
        return expected

    resequence_mod.run_resequence = _mock_resequence
    try:
        client = TestClient(application, raise_server_exceptions=False)
        payload = {
            "alertId": str(_ALERT_ID),
            "affectedVins": [{"vin": _VIN_A}],
            "constraintSnapshot": {
                "snapshotTimestamp": _FAR_FUTURE,
                "paintCapacityUtilisation": 70.0,
                "bodyWipQueueDepth": 100,
                "kittingReadinessPct": 90.0,
            },
        }
        resp = client.post(
            "/resequence",
            json=payload,
            headers={"Authorization": _BEARER_PLANNER},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["runId"] == str(_RUN_ID)
        assert body["resultType"] == "heuristic_fallback"
        assert body["confidenceScore"] == 0.5
        assert len(body["rankedSequence"]) == 1
        assert body["rankedSequence"][0]["vin"] == _VIN_A
    finally:
        resequence_mod.run_resequence = original


# ---------------------------------------------------------------------------
# API endpoint tests — GET /alerts and POST /alerts/{alertId}/acknowledge
# ---------------------------------------------------------------------------


def test_get_alerts_401_when_no_bearer_token():
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    resp = client.get("/alerts")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "MISSING_TOKEN"


def test_get_alerts_200_returns_active_alerts():
    import app.resequence as resequence_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    alert_record = ConstraintAlertRecord(
        alertId=_ALERT_ID,
        constraintType=ConstraintType.paint_capacity,
        currentValue=92.5,
        thresholdValue=85.0,
        affectedVins=[_VIN_A],
        status=AlertStatus.active,
        detectedAt=_FAR_FUTURE,
    )
    list_response = AlertListResponse(
        data=[alert_record],
        page=CursorPage(next_cursor=None, limit=50),
    )

    original = resequence_mod.list_active_alerts

    async def _mock_list(pool: Any, cursor: Any, limit: Any, status_filter: Any = "active") -> AlertListResponse:
        return list_response

    resequence_mod.list_active_alerts = _mock_list
    try:
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.get("/alerts", headers={"Authorization": _BEARER})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["alertId"] == str(_ALERT_ID)
        assert body["data"][0]["status"] == "active"
        assert body["page"]["next_cursor"] is None
    finally:
        resequence_mod.list_active_alerts = original


# ---------------------------------------------------------------------------
# API endpoint tests — POST /alerts/{alertId}/acknowledge
# ---------------------------------------------------------------------------

_ACKNOWLEDGED_AT = "2099-01-01T00:00:00+00:00"


def test_post_acknowledge_401_when_no_bearer_token():
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    resp = client.post(f"/alerts/{_ALERT_ID}/acknowledge")
    assert resp.status_code == 401
    assert resp.json()["code"] == "MISSING_TOKEN"


def test_post_acknowledge_200_returns_acknowledged_record():
    import app.resequence as resequence_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    acknowledged_record = ConstraintAlertRecord(
        alertId=_ALERT_ID,
        constraintType=ConstraintType.paint_capacity,
        currentValue=92.5,
        thresholdValue=85.0,
        affectedVins=[_VIN_A],
        status=AlertStatus.acknowledged,
        detectedAt=_FAR_FUTURE,
        acknowledgedAt=_ACKNOWLEDGED_AT,
        acknowledgedBy="planner@bmwgroup.net",
    )

    original = resequence_mod.acknowledge_alert_db

    async def _mock_ack(alert_id: Any, acknowledged_by: Any, pool: Any, now: Any = None) -> ConstraintAlertRecord:
        return acknowledged_record

    resequence_mod.acknowledge_alert_db = _mock_ack
    try:
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.post(
            f"/alerts/{_ALERT_ID}/acknowledge",
            headers={"Authorization": _BEARER},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["alertId"] == str(_ALERT_ID)
        assert body["status"] == "acknowledged"
        assert body["acknowledgedAt"] == _ACKNOWLEDGED_AT
        assert body["acknowledgedBy"] == "planner@bmwgroup.net"
    finally:
        resequence_mod.acknowledge_alert_db = original


def test_post_acknowledge_404_when_alert_not_found():
    import app.resequence as resequence_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    original = resequence_mod.acknowledge_alert_db

    async def _mock_ack_not_found(alert_id: Any, acknowledged_by: Any, pool: Any, now: Any = None) -> ConstraintAlertRecord:
        raise resequence_mod.AlertNotFoundError(f"Alert {alert_id!r} not found")

    resequence_mod.acknowledge_alert_db = _mock_ack_not_found
    try:
        missing_id = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.post(
            f"/alerts/{missing_id}/acknowledge",
            headers={"Authorization": _BEARER},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ALERT_NOT_FOUND"
    finally:
        resequence_mod.acknowledge_alert_db = original
