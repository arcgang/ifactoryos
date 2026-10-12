"""Endpoint contract tests for GET/PUT /constraints/thresholds.

Uses FastAPI TestClient with dependency overrides — no real DB or Kafka.
The lifespan is patched out so the consumer and pool are never started.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import ConstraintThreshold, ConstraintType, CursorPage, ThresholdListResponse

_FIXED_TS = "2040-01-01T00:00:00+00:00"
_THRESHOLD_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")

# JWT tokens: header.payload.signature where alg=none and exp=year-2286
# payload for _BEARER:       {"sub":"planner@bmwgroup.net","roles":["constraint-threshold-writer"],"exp":9999999999}
# payload for _BEARER_NO_ROLE: {"sub":"readonly@bmwgroup.net","roles":[],"exp":9999999999}
_BEARER = (
    "Bearer eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJwbGFubmVyQGJtd2dyb3VwLm5ldCIsInJvbGVzIjpbImNvbnN0cmFpbnQtdGhyZXNob2xkLXdyaXRlciJdLCJleHAiOjk5OTk5OTk5OTl9"
    "."
)
_BEARER_NO_ROLE = (
    "Bearer eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJyZWFkb25seUBibXdncm91cC5uZXQiLCJyb2xlcyI6W10sImV4cCI6OTk5OTk5OTk5OX0"
    "."
)

_SAMPLE_THRESHOLD = ConstraintThreshold(
    thresholdId=_THRESHOLD_ID,
    constraintType=ConstraintType.paint_capacity,
    warningLevel=85.0,
    criticalLevel=75.0,
    unit="%",
    updatedAt=_FIXED_TS,
    updatedBy="planner@bmwgroup.net",
)


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """No-op lifespan — skips DB pool and Kafka consumer for unit tests."""
    yield


def _build_test_app() -> FastAPI:
    """Build the app with a no-op lifespan so TestClient does not need real infra."""
    import app.main as main_mod

    with patch.object(main_mod, "_lifespan", _noop_lifespan):
        application = main_mod.create_app()
    return application


# ---------------------------------------------------------------------------
# GET /constraints/thresholds — 200
# ---------------------------------------------------------------------------


def test_list_thresholds_200_returns_paginated_data():
    import app.thresholds as thresholds_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    list_response = ThresholdListResponse(
        data=[_SAMPLE_THRESHOLD],
        page=CursorPage(next_cursor=None, limit=50),
    )

    original = thresholds_mod.list_thresholds

    async def _mock_list(pool: Any, cursor: Any, limit: Any) -> ThresholdListResponse:
        return list_response

    thresholds_mod.list_thresholds = _mock_list
    try:
        client = TestClient(application, raise_server_exceptions=False)
        resp = client.get("/constraints/thresholds", headers={"Authorization": _BEARER})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["constraintType"] == "paint_capacity"
        assert body["data"][0]["warningLevel"] == 85.0
        assert body["page"]["next_cursor"] is None
        assert body["page"]["limit"] == 50
    finally:
        thresholds_mod.list_thresholds = original


# ---------------------------------------------------------------------------
# GET /constraints/thresholds — 401 (no token)
# ---------------------------------------------------------------------------


def test_list_thresholds_401_when_no_bearer_token():
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    resp = client.get("/constraints/thresholds")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "MISSING_TOKEN"
    assert "correlation_id" in body


# ---------------------------------------------------------------------------
# PUT /constraints/thresholds — 200
# ---------------------------------------------------------------------------


def test_upsert_threshold_200_returns_saved_record():
    import app.thresholds as thresholds_mod
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    original = thresholds_mod.upsert_threshold

    async def _mock_upsert(pool: Any, threshold: Any, updated_by: Any) -> ConstraintThreshold:
        return _SAMPLE_THRESHOLD

    thresholds_mod.upsert_threshold = _mock_upsert
    try:
        client = TestClient(application, raise_server_exceptions=False)
        payload = {
            "thresholdId": str(_THRESHOLD_ID),
            "constraintType": "paint_capacity",
            "warningLevel": 85.0,
            "criticalLevel": 75.0,
            "unit": "%",
        }
        resp = client.put(
            "/constraints/thresholds",
            json=payload,
            headers={"Authorization": _BEARER},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["thresholdId"] == str(_THRESHOLD_ID)
        assert body["constraintType"] == "paint_capacity"
        assert body["warningLevel"] == 85.0
    finally:
        thresholds_mod.upsert_threshold = original


# ---------------------------------------------------------------------------
# PUT /constraints/thresholds — 401 (no token)
# ---------------------------------------------------------------------------


def test_upsert_threshold_401_when_no_bearer_token():
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    payload = {
        "thresholdId": str(_THRESHOLD_ID),
        "constraintType": "paint_capacity",
        "warningLevel": 85.0,
        "criticalLevel": 75.0,
        "unit": "%",
    }
    resp = client.put("/constraints/thresholds", json=payload)
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "MISSING_TOKEN"
    assert "correlation_id" in body


# ---------------------------------------------------------------------------
# PUT /constraints/thresholds — 403 (authenticated but wrong role)
# ---------------------------------------------------------------------------


def test_upsert_threshold_403_when_caller_lacks_writer_role():
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    payload = {
        "thresholdId": str(_THRESHOLD_ID),
        "constraintType": "paint_capacity",
        "warningLevel": 85.0,
        "criticalLevel": 75.0,
        "unit": "%",
    }
    resp = client.put(
        "/constraints/thresholds",
        json=payload,
        headers={"Authorization": _BEARER_NO_ROLE},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "FORBIDDEN"
    assert "correlation_id" in body


# ---------------------------------------------------------------------------
# PUT /constraints/thresholds — 400 (missing required field: criticalLevel)
# ---------------------------------------------------------------------------


def test_upsert_threshold_400_when_critical_level_missing():
    from app.router import _get_pool

    application = _build_test_app()
    application.dependency_overrides[_get_pool] = lambda: MagicMock()

    client = TestClient(application, raise_server_exceptions=False)
    payload = {
        "thresholdId": str(_THRESHOLD_ID),
        "constraintType": "paint_capacity",
        "warningLevel": 85.0,
        # criticalLevel deliberately omitted
        "unit": "%",
    }
    resp = client.put(
        "/constraints/thresholds",
        json=payload,
        headers={"Authorization": _BEARER},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "correlation_id" in body
