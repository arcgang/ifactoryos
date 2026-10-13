"""Acceptance tests for US-017 — end-to-end seam.

Each unit-test module (test_gate_result_service.py, test_gate_results_router.py,
test_migrations.py) tests one component in isolation.  This file tests the SEAMS
between them, and the API contract that quality-studio depends on:

  Seam A — postgresql-citus migration DDL ↔ opcenter-sync-service validation
            constants (VALID_GATE_TYPES, VALID_RESULTS)
  Seam B — GET /gate-results response ↔ quality-studio GateResultListResponse type

Acceptance criteria exercised:
  AC1  — 3/5 gate types received → absent types identifiable from API response
           (frontend will render them as Pending)
  AC2  — 5/5 gate types received → all present in API response (completeness 100%)
  AC3  — metadata.timeoutSeconds present in every response; default value is 600
           (10 minutes); the field is the mechanism through which the configurable
           threshold reaches the frontend
"""

from __future__ import annotations

import json
import pathlib
import re
import uuid
from contextlib import asynccontextmanager
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
_ALL_GATE_TYPES = ["dimensional", "leak", "electrical", "nvh", "adas"]
_MIGRATIONS_DIR = pathlib.Path(__file__).parent.parent / "migrations"
_MIGRATION_SQL = (_MIGRATIONS_DIR / "20261013_001_gate_result.sql").read_text()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(gate_type: str, result: str = "pass", row_id: str | None = None) -> dict:
    return {
        "gate_result_id": row_id or str(uuid.uuid4()),
        "vin": _VIN,
        "gate_id": f"GATE-{gate_type.upper()[:3]}",
        "gate_type": gate_type,
        "result": result,
        "measurements": json.dumps([]),
        "measured_at": _MEASURED_AT,
        "received_from_opcenter_at": _RECEIVED_AT,
        "opcenter_event_id": None,
    }


def _mock_jwt() -> dict:
    return {"sub": "test-validator", "roles": ["quality-gate-validator"]}


class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class _FakeConn:
    """Fake asyncpg connection — all mutable state in instance fields."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows: list[dict] = rows or []
        self.fetch_calls: int = 0

    async def fetch(self, query: str, *args: Any) -> list[_FakeRecord]:
        self.fetch_calls += 1
        return [_FakeRecord(r) for r in self._rows]

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        return None

    async def execute(self, query: str, *args: Any) -> None:
        pass

    async def fetchval(self, query: str, *args: Any) -> Any | None:
        return None


class _FakeAcquireCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquireCtx:
        return _FakeAcquireCtx(self._conn)


def _make_client(rows: list[dict] | None = None) -> TestClient:
    conn = _FakeConn(rows=rows)
    pool = _FakePool(conn)
    app = create_app()
    from app.auth import require_validator_role
    from app.db import get_pool
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[require_validator_role] = lambda: _mock_jwt()
    return TestClient(app, raise_server_exceptions=True)


def _extract_check_in_values(sql: str, column: str) -> frozenset[str]:
    """Parse values from a SQL CHECK (column IN ('a','b','c')) clause."""
    pattern = rf"CHECK\s*\(\s*{re.escape(column)}\s+IN\s+\(([^)]+)\)\s*\)"
    match = re.search(pattern, sql, re.IGNORECASE)
    assert match, f"No CHECK ... IN constraint found for column {column!r} in migration SQL"
    values_str = match.group(1)
    return frozenset(re.findall(r"'([^']+)'", values_str))


# ---------------------------------------------------------------------------
# Precondition: migration file must exist and declare gate_result
# ---------------------------------------------------------------------------


class TestPrecondition_MigrationExists:
    def test_migration_file_exists(self) -> None:
        """Precondition: the gate_result migration SQL must exist on disk."""
        migration = _MIGRATIONS_DIR / "20261013_001_gate_result.sql"
        assert migration.exists(), (
            f"Migration file not found at {migration}; the postgresql-citus task "
            "must be applied before acceptance tests can pass"
        )

    def test_migration_declares_gate_result_table(self) -> None:
        """Precondition: migration SQL must CREATE TABLE gate_result."""
        assert "CREATE TABLE IF NOT EXISTS gate_result" in _MIGRATION_SQL, (
            "gate_result table declaration missing from migration; "
            "acceptance tests require the schema to be present"
        )


# ---------------------------------------------------------------------------
# Seam A — migration DDL ↔ service validation constants
# ---------------------------------------------------------------------------


class TestSeam_MigrationToService:
    """The migration CHECK constraints must exactly match the service VALID_* sets.

    If the service allows a gate_type or result value the migration does not, inserts
    that pass service validation will fail at the database constraint level.  The
    reverse (migration allows values the service rejects) is also a contract breach.
    """

    def test_migration_gate_type_check_values_equal_service_valid_gate_types(self) -> None:
        """AC1/AC2 seam: gate_type CHECK constraint ↔ VALID_GATE_TYPES are identical."""
        from app.gate_result_service import VALID_GATE_TYPES  # noqa: PLC0415

        migration_values = _extract_check_in_values(_MIGRATION_SQL, "gate_type")
        assert migration_values == VALID_GATE_TYPES, (
            f"Migration gate_type CHECK {migration_values!r} "
            f"differs from service VALID_GATE_TYPES {set(VALID_GATE_TYPES)!r}; "
            "a value accepted by one layer will be rejected by the other"
        )

    def test_migration_result_check_values_equal_service_valid_results(self) -> None:
        """AC1/AC2 seam: result CHECK constraint ↔ VALID_RESULTS are identical."""
        from app.gate_result_service import VALID_RESULTS  # noqa: PLC0415

        migration_values = _extract_check_in_values(_MIGRATION_SQL, "result")
        assert migration_values == VALID_RESULTS, (
            f"Migration result CHECK {migration_values!r} "
            f"differs from service VALID_RESULTS {set(VALID_RESULTS)!r}"
        )

    def test_gate_type_set_has_exactly_five_members(self) -> None:
        """The gate type domain is exactly 5 values (dimensional, leak, electrical, nvh, adas)."""
        from app.gate_result_service import VALID_GATE_TYPES  # noqa: PLC0415

        assert len(VALID_GATE_TYPES) == 5, (
            f"Expected 5 gate types but service defines {len(VALID_GATE_TYPES)}: "
            f"{sorted(VALID_GATE_TYPES)}"
        )


# ---------------------------------------------------------------------------
# AC1 — 3/5 gate types received: absent types identifiable from response
# ---------------------------------------------------------------------------


class TestCriterion1_PartialCompleteness:
    """Gate Command shows missing gate types as 'Pending' (AC1).

    The API response for a VIN with 3/5 gate results received must contain
    exactly those 3 gate types in data.  The frontend derives 'pending' gate
    types by diffing ALL_GATE_TYPES against the received set.
    """

    def test_response_contains_exactly_the_3_received_gate_types(self) -> None:
        """3/5 received: data array has 3 items, one per received gate type."""
        rows = [_make_row(gt) for gt in ["dimensional", "leak", "electrical"]]
        client = _make_client(rows=rows)
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 3
        received_types = {r["gateType"] for r in body["data"]}
        assert received_types == {"dimensional", "leak", "electrical"}

    def test_absent_gate_types_are_not_present_in_response_data(self) -> None:
        """nvh and adas are absent from data; frontend infers these are Pending."""
        rows = [_make_row(gt) for gt in ["dimensional", "leak", "electrical"]]
        client = _make_client(rows=rows)
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        received_types = {r["gateType"] for r in resp.json()["data"]}
        pending = {"dimensional", "leak", "electrical", "nvh", "adas"} - received_types
        assert pending == {"nvh", "adas"}, (
            f"Expected {{nvh, adas}} to be absent from response but got pending={pending}"
        )

    def test_each_data_item_carries_all_fields_required_by_frontend_contract(self) -> None:
        """AC1 seam: response fields must match quality-studio GateResultRecord interface.

        Frontend expects: gateResultId, vin, gateId, gateType, result,
        measurements, measuredAt, receivedFromOpcenterAt, opcenterEventId.
        """
        rows = [_make_row("dimensional")]
        client = _make_client(rows=rows)
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        record = resp.json()["data"][0]
        frontend_required_fields = [
            "gateResultId",
            "vin",
            "gateId",
            "gateType",
            "result",
            "measurements",
            "measuredAt",
            "receivedFromOpcenterAt",
        ]
        for field in frontend_required_fields:
            assert field in record, (
                f"Field {field!r} absent from API response; "
                "quality-studio GateResultRecord interface requires it"
            )


# ---------------------------------------------------------------------------
# AC2 — all 5 gate types received: completeness = 100%
# ---------------------------------------------------------------------------


class TestCriterion2_FullCompleteness:
    """Gate Command enables the Certify button when all 5 gate results arrive (AC2)."""

    def test_response_contains_all_5_gate_types(self) -> None:
        """5/5 received: data array contains one entry per gate type."""
        rows = [_make_row(gt) for gt in _ALL_GATE_TYPES]
        client = _make_client(rows=rows)
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 5
        received_types = {r["gateType"] for r in body["data"]}
        assert received_types == set(_ALL_GATE_TYPES), (
            f"Expected all 5 gate types but got {received_types}"
        )

    def test_completeness_indicator_reachable_when_all_5_results_differ_by_type(self) -> None:
        """5/5 unique gate types → frontend can derive isComplete=true (receivedCount==5)."""
        rows = [_make_row(gt) for gt in _ALL_GATE_TYPES]
        client = _make_client(rows=rows)
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        received_types = {r["gateType"] for r in resp.json()["data"]}
        # Frontend: isComplete = receivedCount === ALL_GATE_TYPES.length
        assert len(received_types) == 5

    def test_all_results_have_valid_result_values_for_certify_flow(self) -> None:
        """Each gate result carries a result value the frontend badge renderer handles."""
        rows = [
            _make_row("dimensional", "pass"),
            _make_row("leak", "conditional_pass"),
            _make_row("electrical", "fail"),
            _make_row("nvh", "pass"),
            _make_row("adas", "pass"),
        ]
        client = _make_client(rows=rows)
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        from app.gate_result_service import VALID_RESULTS  # noqa: PLC0415
        for item in resp.json()["data"]:
            assert item["result"] in VALID_RESULTS, (
                f"result={item['result']!r} not in VALID_RESULTS; "
                "frontend badge renderer only handles pass/conditional_pass/fail"
            )


# ---------------------------------------------------------------------------
# AC3 — configurable timeout threshold via metadata.timeoutSeconds
# ---------------------------------------------------------------------------


class TestCriterion3_TimeoutConfiguration:
    """The timeout threshold is configurable (AC3).

    The backend exposes `metadata.timeoutSeconds` in every GET /gate-results
    response so the frontend can synchronise its countdown to the server-side
    configured value.  The default must be 600 (10 minutes).
    """

    def test_metadata_timeout_seconds_is_present_in_list_response(self) -> None:
        """Precondition for AC3: metadata.timeoutSeconds must be in every response."""
        client = _make_client(rows=[])
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert "metadata" in body, (
            "metadata field absent from GET /gate-results response; "
            "quality-studio cannot read the configurable timeout threshold"
        )
        assert "timeoutSeconds" in body["metadata"], (
            "metadata.timeoutSeconds absent; frontend uses this to set its countdown"
        )

    def test_metadata_timeout_seconds_default_is_600(self) -> None:
        """AC3: default timeout threshold is 600 seconds (10 minutes)."""
        client = _make_client(rows=[])
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        timeout = resp.json()["metadata"]["timeoutSeconds"]
        assert timeout == 600, (
            f"Default timeout is {timeout}s, expected 600s (10 minutes) per AC3"
        )

    def test_metadata_timeout_seconds_is_a_positive_integer(self) -> None:
        """metadata.timeoutSeconds must be a positive integer (frontend uses it in setInterval)."""
        client = _make_client(rows=[])
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        timeout = resp.json()["metadata"]["timeoutSeconds"]
        assert isinstance(timeout, int), f"Expected int, got {type(timeout).__name__}"
        assert timeout > 0, "timeoutSeconds must be positive"

    def test_metadata_timeout_seconds_reflects_settings_value(self) -> None:
        """metadata.timeoutSeconds equals the application Settings value."""
        from app.config import settings  # noqa: PLC0415

        client = _make_client(rows=[])
        resp = client.get(f"/gate-results?vin={_VIN}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["metadata"]["timeoutSeconds"] == settings.gate_result_timeout_seconds

    def test_gate_result_timeout_seconds_setting_is_configurable(self) -> None:
        """AC3: timeout is configurable, not a magic literal — it lives in Settings."""
        from app.config import Settings  # noqa: PLC0415

        fields = Settings.model_fields
        assert "gate_result_timeout_seconds" in fields, (
            "gate_result_timeout_seconds must be a Settings field so it is "
            "configurable via environment variable"
        )

    def test_metadata_present_when_no_vin_filter_applied(self) -> None:
        """metadata.timeoutSeconds appears in the response even without a VIN filter."""
        client = _make_client(rows=[])
        resp = client.get("/gate-results", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        assert "timeoutSeconds" in resp.json().get("metadata", {})
