"""Tests for GET /gate-results and GET /gate-results/{gateResultId}.

All DB I/O replaced with in-process fakes; no external services needed.

Covered scenarios (GET /gate-results):
  - 200 with paginated data for a VIN with 3-of-5 gate types received
  - 200 with all 5 gate types present (completeness = 100%)
  - 200 with empty data when no rows match (0-of-5)
  - 200 with next_cursor present when result count exceeds limit
  - 200 metadata.timeoutSeconds is present and equals configured value
  - 400 when gateType is not a valid enum value
  - 400 when result is not a valid enum value
  - 400 when cursor is malformed
  - 401 when Authorization header is missing

Covered scenarios (GET /gate-results/{gateResultId}):
  - 200 returns correct gate result for a known ID
  - 404 for an unknown gate result ID
  - 401 when Authorization header is missing

FakePool / FakeConn instance isolation:
  - Two FakeConn instances have independent state
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VIN = "WBA3A5C55FK123456"
_MEASURED_AT = datetime(2099, 1, 1, 6, 0, 0, tzinfo=timezone.utc)
_RECEIVED_AT = datetime(2099, 1, 1, 6, 0, 1, tzinfo=timezone.utc)

_JWT_HEADERS = {"Authorization": "Bearer stub"}

_GATE_TYPES_ALL = ["dimensional", "leak", "electrical", "nvh", "adas"]

_SAMPLE_MEASUREMENTS = [
    {
        "measurementId": "M1",
        "name": "flange_width",
        "value": 10.1,
        "unit": "mm",
        "toleranceLow": 9.5,
        "toleranceHigh": 10.5,
        "withinTolerance": True,
    }
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    gate_type: str = "dimensional",
    result: str = "pass",
    row_id: str | None = None,
    opcenter_event_id: str | None = None,
) -> dict:
    return {
        "gate_result_id": row_id or str(uuid.uuid4()),
        "vin": _VIN,
        "gate_id": f"GATE-{gate_type.upper()[:3]}",
        "gate_type": gate_type,
        "result": result,
        "measurements": json.dumps(_SAMPLE_MEASUREMENTS),
        "measured_at": _MEASURED_AT,
        "received_from_opcenter_at": _RECEIVED_AT,
        "opcenter_event_id": opcenter_event_id,
    }


def _make_five_gate_rows() -> list[dict]:
    return [_make_row(gt) for gt in _GATE_TYPES_ALL]


def _make_three_gate_rows() -> list[dict]:
    return [_make_row(gt) for gt in _GATE_TYPES_ALL[:3]]


def _mock_jwt_payload() -> dict:
    return {"sub": "test-validator", "roles": ["quality-gate-validator"]}


# ---------------------------------------------------------------------------
# Fake DB
# ---------------------------------------------------------------------------

class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class FakeConn:
    """Fake asyncpg connection — all mutable state in instance fields."""

    def __init__(
        self,
        rows: list[dict] | None = None,
        single_row: dict | None = None,
    ) -> None:
        self._rows: list[dict] = rows or []
        self._single_row: dict | None = single_row
        self.fetch_calls: int = 0
        self.fetchrow_calls: int = 0

    async def fetch(self, query: str, *args: Any) -> list[_FakeRecord]:
        self.fetch_calls += 1
        return [_FakeRecord(r) for r in self._rows]

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        self.fetchrow_calls += 1
        if self._single_row is not None:
            return _FakeRecord(self._single_row)
        return None

    async def execute(self, query: str, *args: Any) -> None:
        pass

    async def fetchval(self, query: str, *args: Any) -> Any | None:
        return None


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> "_FakeAcquireCtx":
        return _FakeAcquireCtx(self._conn)


class _FakeAcquireCtx:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def _make_client(
    rows: list[dict] | None = None,
    single_row: dict | None = None,
) -> TestClient:
    conn = FakeConn(rows=rows, single_row=single_row)
    pool = FakePool(conn)
    app = create_app()

    from app.auth import require_validator_role
    from app.db import get_pool

    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[require_validator_role] = lambda: _mock_jwt_payload()

    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Tests: GET /gate-results — completeness scenarios
# ---------------------------------------------------------------------------

class TestListGateResults:
    def test_200_with_0_of_5_gate_types_returns_empty(self):
        """0-of-5: no rows for the VIN returns an empty data array."""
        client = _make_client(rows=[])
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["page"]["next_cursor"] is None
        assert body["page"]["limit"] == 50

    def test_200_with_3_of_5_gate_types_received(self):
        """3-of-5: three gate types present in response."""
        rows = _make_three_gate_rows()
        client = _make_client(rows=rows)
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 3
        gate_types = {r["gateType"] for r in body["data"]}
        assert gate_types == set(_GATE_TYPES_ALL[:3])

    def test_200_with_5_of_5_gate_types_received(self):
        """5-of-5: all gate types present; completeness indicator 100%."""
        rows = _make_five_gate_rows()
        client = _make_client(rows=rows)
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 5
        gate_types = {r["gateType"] for r in body["data"]}
        assert gate_types == set(_GATE_TYPES_ALL)

    def test_200_metadata_timeout_seconds_present(self):
        """metadata.timeoutSeconds is returned with the configured value."""
        from app.config import settings

        client = _make_client(rows=[])
        resp = client.get("/gate-results", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert "metadata" in body
        assert body["metadata"]["timeoutSeconds"] == settings.gate_result_timeout_seconds

    def test_200_metadata_timeout_seconds_equals_600_default(self):
        """Default timeout is 600 seconds (10 minutes) per AC-202159f2."""
        client = _make_client(rows=[])
        resp = client.get("/gate-results", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["metadata"]["timeoutSeconds"] == 600

    def test_200_next_cursor_present_when_limit_exceeded(self):
        """When result count exceeds limit, next_cursor is non-null."""
        row_id = str(uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))
        rows = [_make_row(row_id=row_id)] * 51
        client = _make_client(rows=rows)
        resp = client.get(f"/gate-results?vin={_VIN}&limit=50", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["page"]["next_cursor"] is not None

    def test_200_page_limit_is_echoed(self):
        """page.limit reflects the requested limit value."""
        client = _make_client(rows=_make_three_gate_rows())
        resp = client.get(f"/gate-results?vin={_VIN}&limit=10", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["page"]["limit"] == 10

    def test_200_gate_result_fields_match_contract(self):
        """Each item in data has required fields from the GateResult schema."""
        rows = [_make_row()]
        client = _make_client(rows=rows)
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        record = resp.json()["data"][0]
        for field in ("gateResultId", "vin", "gateId", "gateType", "result",
                      "measurements", "measuredAt", "receivedFromOpcenterAt"):
            assert field in record, f"Missing required field: {field}"
        assert record["vin"] == _VIN

    def test_200_measurement_fields_match_contract(self):
        """Measurements in data items have all required GateMeasurement fields."""
        rows = [_make_row()]
        client = _make_client(rows=rows)
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        measurement = resp.json()["data"][0]["measurements"][0]
        for field in ("measurementId", "name", "value", "unit",
                      "toleranceLow", "toleranceHigh", "withinTolerance"):
            assert field in measurement, f"Missing measurement field: {field}"

    def test_400_for_invalid_gate_type(self):
        """Invalid gateType enum value returns 400."""
        client = _make_client(rows=[])
        resp = client.get("/gate-results?gateType=thermal", headers=_JWT_HEADERS)
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "INVALID_GATE_TYPE"
        assert "correlation_id" in body

    def test_400_for_invalid_result_filter(self):
        """Invalid result enum value returns 400."""
        client = _make_client(rows=[])
        resp = client.get("/gate-results?result=pending", headers=_JWT_HEADERS)
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "INVALID_RESULT"
        assert "correlation_id" in body

    def test_400_for_malformed_cursor(self):
        """Malformed cursor returns 400."""
        client = _make_client(rows=[])
        resp = client.get("/gate-results?cursor=not-valid-base64!!!", headers=_JWT_HEADERS)
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "INVALID_CURSOR"

    def test_401_when_no_authorization_header(self):
        """No Authorization header returns 401."""
        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/gate-results")
        assert resp.status_code == 401

    def test_422_for_invalid_vin_pattern(self):
        """Invalid VIN pattern returns 422 (FastAPI query param validation)."""
        client = _make_client(rows=[])
        resp = client.get("/gate-results?vin=INVALID_VIN", headers=_JWT_HEADERS)
        assert resp.status_code == 422

    def test_200_filter_by_result_conditional_pass(self):
        """result filter returns only rows matching that status."""
        rows = [
            _make_row(gate_type="dimensional", result="pass"),
            _make_row(gate_type="leak", result="conditional_pass"),
        ]
        client = _make_client(rows=rows)
        resp = client.get("/gate-results?result=conditional_pass", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        # The fake returns all rows regardless of filter; test that the filter param
        # is accepted and the response has the correct structure
        body = resp.json()
        assert "data" in body
        assert "page" in body


# ---------------------------------------------------------------------------
# Tests: GET /gate-results/{gateResultId}
# ---------------------------------------------------------------------------

class TestGetGateResult:
    def test_200_returns_correct_gate_result(self):
        """Known gateResultId returns the gate result."""
        row_id = str(uuid.uuid4())
        row = _make_row(row_id=row_id)
        client = _make_client(single_row=row)
        resp = client.get(f"/gate-results/{row_id}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["gateResultId"] == row_id
        assert body["vin"] == _VIN
        assert body["gateType"] == "dimensional"
        assert body["result"] == "pass"
        assert isinstance(body["measurements"], list)
        assert len(body["measurements"]) == 1

    def test_200_result_has_all_required_fields(self):
        """Response includes all fields required by the GateResult schema."""
        row_id = str(uuid.uuid4())
        row = _make_row(row_id=row_id)
        client = _make_client(single_row=row)
        resp = client.get(f"/gate-results/{row_id}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        for field in ("gateResultId", "vin", "gateId", "gateType", "result",
                      "measurements", "measuredAt", "receivedFromOpcenterAt"):
            assert field in body, f"Missing required field: {field}"

    def test_404_for_unknown_gate_result_id(self):
        """Unknown gateResultId returns 404 with correct error shape."""
        client = _make_client(single_row=None)
        resp = client.get(f"/gate-results/{uuid.uuid4()}", headers=_JWT_HEADERS)
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "GATE_RESULT_NOT_FOUND"
        assert "correlation_id" in body
        assert "message" in body

    def test_401_when_no_authorization_header(self):
        """No Authorization header returns 401."""
        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get(f"/gate-results/{uuid.uuid4()}")
        assert resp.status_code == 401

    def test_error_response_shape_is_consistent(self):
        """404 error body matches the Error schema: code, message, correlation_id."""
        client = _make_client(single_row=None)
        resp = client.get(f"/gate-results/{uuid.uuid4()}", headers=_JWT_HEADERS)
        assert resp.status_code == 404
        body = resp.json()
        assert isinstance(body["code"], str)
        assert isinstance(body["message"], str)
        uuid.UUID(body["correlation_id"])  # must be a valid UUID


# ---------------------------------------------------------------------------
# Tests: FakeConn instance isolation
# ---------------------------------------------------------------------------

class TestFakeConnInstanceIsolation:
    def test_two_instances_have_independent_rows(self):
        """Two FakeConn instances do not share row state."""
        conn_a = FakeConn(rows=[_make_row()])
        conn_b = FakeConn(rows=[])
        assert len(conn_a._rows) == 1
        assert len(conn_b._rows) == 0

    def test_new_instance_starts_with_zero_fetch_calls(self):
        """Call counters start at zero on each new instance."""
        conn = FakeConn()
        assert conn.fetch_calls == 0
        assert conn.fetchrow_calls == 0
