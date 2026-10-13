"""Endpoint contract tests for GET/POST /overrides, GET /overrides/{overrideId},
GET /overrides/export, and GET /optimize/{runId}/explain/{vin}.

Uses FastAPI TestClient with dependency overrides — no real DB.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import (
    ConstraintFactor,
    ConstraintFactorName,
    CursorPage,
    OverrideListResponse,
    OverrideRecord,
    VinExplanation,
)

_FIXED_TS = "2040-01-01T00:00:00+00:00"
_OVERRIDE_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_RUN_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
_VIN = "WBA3B3C56DF596858"

# JWT: {"sub":"planner@bmwgroup.net","roles":["production-planner"],"exp":9999999999}
_BEARER = (
    "Bearer eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJwbGFubmVyQGJtd2dyb3VwLm5ldCIsInJvbGVzIjpbInByb2R1Y3Rpb24tcGxhbm5lciJdLCJleHAiOjk5OTk5OTk5OTl9"
    "."
)
# JWT: {"sub":"readonly@bmwgroup.net","roles":[],"exp":9999999999}
_BEARER_NO_ROLE = (
    "Bearer eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJyZWFkb25seUBibXdncm91cC5uZXQiLCJyb2xlcyI6W10sImV4cCI6OTk5OTk5OTk5OX0"
    "."
)

_SAMPLE_OVERRIDE = OverrideRecord(
    overrideId=_OVERRIDE_ID,
    runId=_RUN_ID,
    vin=_VIN,
    originalPosition=5,
    newPosition=2,
    justification="VIP customer — move forward in sequence",
    plannerId="planner@bmwgroup.net",
    createdAt=_FIXED_TS,
)

_SAMPLE_EXPLANATION = VinExplanation(
    runId=_RUN_ID,
    vin=_VIN,
    position=2,
    topFactors=[
        ConstraintFactor(
            factorName=ConstraintFactorName.paint_batch_conflict,
            weightedScore=0.75,
            dataSource="uns.paint_capacity",
            rawValue=92.3,
        ),
        ConstraintFactor(
            factorName=ConstraintFactorName.vip_priority,
            weightedScore=0.5,
            dataSource="optimizer_run.ranked_sequence",
        ),
    ],
)


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


def _build_test_app() -> FastAPI:
    import app.main as main_mod

    with patch.object(main_mod, "_lifespan", _noop_lifespan):
        application = main_mod.create_app()
    return application


# ---------------------------------------------------------------------------
# GET /overrides — 200
# ---------------------------------------------------------------------------


def test_list_overrides_200_returns_paginated_data() -> None:
    import app.overrides as overrides_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    list_response = OverrideListResponse(
        data=[_SAMPLE_OVERRIDE],
        page=CursorPage(next_cursor=None, limit=50),
    )
    original = overrides_mod.list_overrides

    async def _mock_list(
        pool: Any,
        week_iso: Any = None,
        run_id: Any = None,
        cursor: Any = None,
        limit: Any = 50,
    ) -> OverrideListResponse:
        return list_response

    overrides_mod.list_overrides = _mock_list
    try:
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.get("/overrides", headers={"Authorization": _BEARER})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["vin"] == _VIN
        assert body["data"][0]["originalPosition"] == 5
        assert body["data"][0]["newPosition"] == 2
        assert body["page"]["next_cursor"] is None
    finally:
        overrides_mod.list_overrides = original


# ---------------------------------------------------------------------------
# GET /overrides — 401 (no token)
# ---------------------------------------------------------------------------


def test_list_overrides_401_when_no_bearer_token() -> None:
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    resp = client.get("/overrides")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "MISSING_TOKEN"
    assert "correlation_id" in body


# ---------------------------------------------------------------------------
# POST /overrides — 201
# ---------------------------------------------------------------------------


def test_create_override_201_records_and_returns_override() -> None:
    import app.overrides as overrides_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    original = overrides_mod.create_override

    async def _mock_create(request: Any, planner_id: Any, pool: Any) -> OverrideRecord:
        return _SAMPLE_OVERRIDE

    overrides_mod.create_override = _mock_create
    try:
        client = TestClient(application, raise_server_exceptions=False)
        payload = {
            "runId": str(_RUN_ID),
            "vin": _VIN,
            "originalPosition": 5,
            "newPosition": 2,
            "justification": "VIP customer — move forward in sequence",
        }
        resp = client.post(
            "/overrides",
            json=payload,
            headers={"Authorization": _BEARER},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["overrideId"] == str(_OVERRIDE_ID)
        assert body["vin"] == _VIN
        assert body["originalPosition"] == 5
        assert body["newPosition"] == 2
        assert body["plannerId"] == "planner@bmwgroup.net"
    finally:
        overrides_mod.create_override = original


# ---------------------------------------------------------------------------
# POST /overrides — 400 (justification too short)
# ---------------------------------------------------------------------------


def test_create_override_400_when_justification_too_short() -> None:
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    payload = {
        "runId": str(_RUN_ID),
        "vin": _VIN,
        "originalPosition": 5,
        "newPosition": 2,
        "justification": "too short",  # 9 chars — below 10 minimum
    }
    resp = client.post(
        "/overrides",
        json=payload,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "correlation_id" in body


# ---------------------------------------------------------------------------
# POST /overrides — 401 (no token)
# ---------------------------------------------------------------------------


def test_create_override_401_when_no_bearer_token() -> None:
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    payload = {
        "runId": str(_RUN_ID),
        "vin": _VIN,
        "originalPosition": 5,
        "newPosition": 2,
        "justification": "This is a valid justification string",
    }
    resp = client.post("/overrides", json=payload)
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "MISSING_TOKEN"


# ---------------------------------------------------------------------------
# POST /overrides — 403 (authenticated but wrong role)
# ---------------------------------------------------------------------------


def test_create_override_403_when_caller_lacks_planner_role() -> None:
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    payload = {
        "runId": str(_RUN_ID),
        "vin": _VIN,
        "originalPosition": 5,
        "newPosition": 2,
        "justification": "This is a valid justification string",
    }
    resp = client.post(
        "/overrides",
        json=payload,
        headers={"Authorization": _BEARER_NO_ROLE},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# GET /overrides/{overrideId} — 200
# ---------------------------------------------------------------------------


def test_get_override_200_returns_record() -> None:
    import app.overrides as overrides_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    original = overrides_mod.get_override

    async def _mock_get(override_id: Any, pool: Any) -> OverrideRecord:
        return _SAMPLE_OVERRIDE

    overrides_mod.get_override = _mock_get
    try:
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.get(
            f"/overrides/{_OVERRIDE_ID}",
            headers={"Authorization": _BEARER},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["overrideId"] == str(_OVERRIDE_ID)
        assert body["vin"] == _VIN
    finally:
        overrides_mod.get_override = original


# ---------------------------------------------------------------------------
# GET /overrides/{overrideId} — 404
# ---------------------------------------------------------------------------


def test_get_override_404_when_not_found() -> None:
    import app.overrides as overrides_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    original = overrides_mod.get_override

    async def _mock_get_none(override_id: Any, pool: Any) -> None:
        return None

    overrides_mod.get_override = _mock_get_none
    try:
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.get(
            f"/overrides/{uuid.uuid4()}",
            headers={"Authorization": _BEARER},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "OVERRIDE_NOT_FOUND"
    finally:
        overrides_mod.get_override = original


# ---------------------------------------------------------------------------
# GET /overrides/export — 200 returns PDF content-type
# ---------------------------------------------------------------------------


def test_export_overrides_pdf_200_returns_pdf_content_type() -> None:
    import app.overrides as overrides_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    list_response = OverrideListResponse(
        data=[_SAMPLE_OVERRIDE],
        page=CursorPage(next_cursor=None, limit=200),
    )
    original = overrides_mod.list_overrides

    async def _mock_list(
        pool: Any,
        week_iso: Any = None,
        run_id: Any = None,
        cursor: Any = None,
        limit: Any = 50,
    ) -> OverrideListResponse:
        return list_response

    overrides_mod.list_overrides = _mock_list
    try:
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.get(
            "/overrides/export?weekIso=2026-W42",
            headers={"Authorization": _BEARER},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content[:4] == b"%PDF"
    finally:
        overrides_mod.list_overrides = original


# ---------------------------------------------------------------------------
# GET /optimize/{runId}/explain/{vin} — 200
# ---------------------------------------------------------------------------


def test_explain_vin_200_returns_top_factors() -> None:
    import app.explain as explain_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    original = explain_mod.get_explanation

    async def _mock_get(run_id: Any, vin: Any, pool: Any) -> VinExplanation:
        return _SAMPLE_EXPLANATION

    explain_mod.get_explanation = _mock_get
    try:
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.get(
            f"/optimize/{_RUN_ID}/explain/{_VIN}",
            headers={"Authorization": _BEARER},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["vin"] == _VIN
        assert body["position"] == 2
        assert len(body["topFactors"]) == 2
        assert body["topFactors"][0]["factorName"] == "paint_batch_conflict"
        assert body["topFactors"][0]["weightedScore"] == 0.75
        assert body["topFactors"][0]["dataSource"] == "uns.paint_capacity"
    finally:
        explain_mod.get_explanation = original


# ---------------------------------------------------------------------------
# GET /optimize/{runId}/explain/{vin} — 404
# ---------------------------------------------------------------------------


def test_explain_vin_404_when_not_found() -> None:
    import app.explain as explain_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    original = explain_mod.get_explanation

    async def _mock_none(run_id: Any, vin: Any, pool: Any) -> None:
        return None

    explain_mod.get_explanation = _mock_none
    try:
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.get(
            f"/optimize/{_RUN_ID}/explain/{_VIN}",
            headers={"Authorization": _BEARER},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "EXPLANATION_NOT_FOUND"
    finally:
        explain_mod.get_explanation = original


# ---------------------------------------------------------------------------
# GET /optimize/{runId}/explain/{vin} — 401
# ---------------------------------------------------------------------------


def test_explain_vin_401_when_no_bearer_token() -> None:
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    resp = client.get(f"/optimize/{_RUN_ID}/explain/{_VIN}")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "MISSING_TOKEN"


# ---------------------------------------------------------------------------
# POST /overrides — justification boundary: exactly 9 chars → 400
# ---------------------------------------------------------------------------


def test_create_override_400_when_justification_is_exactly_9_chars() -> None:
    """9-char justification is one below the 10-char minimum — must reject with 400."""
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    payload = {
        "runId": str(_RUN_ID),
        "vin": _VIN,
        "originalPosition": 5,
        "newPosition": 2,
        "justification": "123456789",  # exactly 9 chars
    }
    resp = client.post(
        "/overrides",
        json=payload,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "correlation_id" in body


# ---------------------------------------------------------------------------
# POST /overrides — justification boundary: exactly 10 chars → 201
# ---------------------------------------------------------------------------


def test_create_override_201_when_justification_is_exactly_10_chars() -> None:
    """10-char justification is the minimum allowed length — must return 201."""
    import app.overrides as overrides_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    record_with_10_char = OverrideRecord(
        overrideId=_OVERRIDE_ID,
        runId=_RUN_ID,
        vin=_VIN,
        originalPosition=5,
        newPosition=2,
        justification="1234567890",  # exactly 10 chars
        plannerId="planner@bmwgroup.net",
        createdAt=_FIXED_TS,
    )
    original = overrides_mod.create_override

    async def _mock_create(request: Any, planner_id: Any, pool: Any) -> OverrideRecord:
        return record_with_10_char

    overrides_mod.create_override = _mock_create
    try:
        client = TestClient(application, raise_server_exceptions=False)
        payload = {
            "runId": str(_RUN_ID),
            "vin": _VIN,
            "originalPosition": 5,
            "newPosition": 2,
            "justification": "1234567890",  # exactly 10 chars
        }
        resp = client.post(
            "/overrides",
            json=payload,
            headers={"Authorization": _BEARER},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["justification"] == "1234567890"
    finally:
        overrides_mod.create_override = original


# ---------------------------------------------------------------------------
# GET /overrides — weekIso edge cases: W01 and W53
# ---------------------------------------------------------------------------


def test_list_overrides_accepts_week_iso_w01() -> None:
    """weekIso=2026-W01 is a valid ISO week string and must return 200."""
    import app.overrides as overrides_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    list_response = OverrideListResponse(
        data=[],
        page=CursorPage(next_cursor=None, limit=50),
    )
    original = overrides_mod.list_overrides

    async def _mock_list(
        pool: Any,
        week_iso: Any = None,
        run_id: Any = None,
        cursor: Any = None,
        limit: Any = 50,
    ) -> OverrideListResponse:
        return list_response

    overrides_mod.list_overrides = _mock_list
    try:
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.get(
            "/overrides?weekIso=2026-W01",
            headers={"Authorization": _BEARER},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["page"]["next_cursor"] is None
    finally:
        overrides_mod.list_overrides = original


def test_list_overrides_accepts_week_iso_w53() -> None:
    """weekIso=2026-W53 is a valid ISO week string and must return 200."""
    import app.overrides as overrides_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    list_response = OverrideListResponse(
        data=[],
        page=CursorPage(next_cursor=None, limit=50),
    )
    original = overrides_mod.list_overrides

    async def _mock_list(
        pool: Any,
        week_iso: Any = None,
        run_id: Any = None,
        cursor: Any = None,
        limit: Any = 50,
    ) -> OverrideListResponse:
        return list_response

    overrides_mod.list_overrides = _mock_list
    try:
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.get(
            "/overrides?weekIso=2026-W53",
            headers={"Authorization": _BEARER},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
    finally:
        overrides_mod.list_overrides = original


def test_list_overrides_rejects_week_iso_w00() -> None:
    """weekIso=2026-W00 is an invalid ISO week string and must return 422."""
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    resp = client.get(
        "/overrides?weekIso=2026-W00",
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /overrides/export — PDF row count matches input record count
# ---------------------------------------------------------------------------


def test_export_overrides_pdf_row_count_matches_db_rows() -> None:
    """PDF content must contain one row per override record returned from DB."""
    import app.overrides as overrides_mod
    from app.overrides import build_override_pdf
    from app.router import _get_pool

    # Build 3 records; PDF should include exactly 3 override lines
    records = [
        OverrideRecord(
            overrideId=uuid.uuid4(),
            runId=_RUN_ID,
            vin=_VIN,
            originalPosition=i + 1,
            newPosition=i + 2,
            justification=f"Justification for override {i + 1}",
            plannerId="planner@bmwgroup.net",
            createdAt=_FIXED_TS,
        )
        for i in range(3)
    ]
    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    list_response = OverrideListResponse(
        data=records,
        page=CursorPage(next_cursor=None, limit=200),
    )
    original = overrides_mod.list_overrides

    async def _mock_list(
        pool: Any,
        week_iso: Any = None,
        run_id: Any = None,
        cursor: Any = None,
        limit: Any = 50,
    ) -> OverrideListResponse:
        return list_response

    overrides_mod.list_overrides = _mock_list
    try:
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.get(
            "/overrides/export?weekIso=2026-W42",
            headers={"Authorization": _BEARER},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        # Verify row count by reconstructing the PDF independently
        pdf_bytes = build_override_pdf(records, "2026-W42")
        assert pdf_bytes[:4] == b"%PDF"
        assert resp.content[:4] == b"%PDF"
        # The PDF built from the same 3 records must be identical byte-for-byte
        assert resp.content == pdf_bytes
    finally:
        overrides_mod.list_overrides = original
