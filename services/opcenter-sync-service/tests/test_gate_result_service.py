"""Unit tests for gate result service — dedup and validation logic.

Covered scenarios:
  - Duplicate opcenterEventId is silently dropped (returns None)
  - Unique opcenterEventId inserts successfully (returns gate_result_id)
  - None opcenterEventId always inserts (no dedup check)
  - Validation rejects invalid VIN patterns
  - Validation rejects invalid gateType values
  - Validation rejects invalid result values
  - Valid inputs pass validation with no errors
  - FakeConn instance isolation: two instances have independent state
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.gate_result_service import ingest_gate_result, validate_gate_result_payload

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VIN = "WBA3A5C55FK123456"
_GATE_ID = "GATE-DIM"
_MEASURED_AT = datetime(2099, 1, 1, 6, 0, 0, tzinfo=timezone.utc)
_MEASUREMENTS = [
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
# Fake DB connection
# ---------------------------------------------------------------------------

class FakeConn:
    """Fake asyncpg connection for unit tests — all mutable state in instance fields."""

    def __init__(self, *, existing_event_id: str | None = None) -> None:
        self._existing_event_id = existing_event_id
        self.inserted_rows: list[dict] = []

    async def fetchval(self, query: str, *args: Any) -> Any | None:
        q = query.lower()
        if "insert into gate_result" in q:
            # Simulate ON CONFLICT DO NOTHING: return None for duplicate event_id
            opcenter_event_id = args[7]  # 8th parameter ($8)
            if (
                opcenter_event_id is not None
                and opcenter_event_id == self._existing_event_id
            ):
                return None
            # Record the insert
            row_id = args[0]
            self.inserted_rows.append({"gate_result_id": row_id, "args": list(args)})
            return str(row_id)
        return None

    async def fetchrow(self, query: str, *args: Any) -> Any | None:
        return None

    async def execute(self, query: str, *args: Any) -> None:
        pass

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        return []


# ---------------------------------------------------------------------------
# Tests: dedup logic
# ---------------------------------------------------------------------------

class TestDedupLogic:
    @pytest.mark.asyncio
    async def test_duplicate_opcenter_event_id_returns_none(self):
        """Duplicate opcenterEventId is silently dropped; returns None."""
        event_id = str(uuid.uuid4())
        conn = FakeConn(existing_event_id=event_id)
        publish_fn = AsyncMock()

        result = await ingest_gate_result(
            conn,
            vin=_VIN,
            gate_id=_GATE_ID,
            gate_type="dimensional",
            result="pass",
            measurements=_MEASUREMENTS,
            measured_at=_MEASURED_AT,
            opcenter_event_id=event_id,
            publish_fn=publish_fn,
        )

        assert result is None
        assert len(conn.inserted_rows) == 0
        publish_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_unique_opcenter_event_id_inserts_successfully(self):
        """A new opcenterEventId inserts the row and returns its UUID."""
        conn = FakeConn(existing_event_id=None)
        publish_fn = AsyncMock()
        event_id = str(uuid.uuid4())

        result = await ingest_gate_result(
            conn,
            vin=_VIN,
            gate_id=_GATE_ID,
            gate_type="dimensional",
            result="pass",
            measurements=_MEASUREMENTS,
            measured_at=_MEASURED_AT,
            opcenter_event_id=event_id,
            publish_fn=publish_fn,
        )

        assert result is not None
        uuid.UUID(result)  # must be a valid UUID
        assert len(conn.inserted_rows) == 1
        publish_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_none_opcenter_event_id_always_inserts(self):
        """When opcenterEventId is None, no dedup check fires — row always inserted."""
        conn = FakeConn(existing_event_id=None)
        publish_fn = AsyncMock()

        result = await ingest_gate_result(
            conn,
            vin=_VIN,
            gate_id=_GATE_ID,
            gate_type="leak",
            result="conditional_pass",
            measurements=_MEASUREMENTS,
            measured_at=_MEASURED_AT,
            opcenter_event_id=None,
            publish_fn=publish_fn,
        )

        assert result is not None
        uuid.UUID(result)

    @pytest.mark.asyncio
    async def test_kafka_publish_called_with_correct_topic(self):
        """Kafka publish is called on the gate result topic."""
        from app.kafka_producer import TOPIC_GATE_RESULT

        conn = FakeConn()
        publish_fn = AsyncMock()

        await ingest_gate_result(
            conn,
            vin=_VIN,
            gate_id=_GATE_ID,
            gate_type="electrical",
            result="fail",
            measurements=_MEASUREMENTS,
            measured_at=_MEASURED_AT,
            opcenter_event_id=None,
            publish_fn=publish_fn,
        )

        publish_fn.assert_called_once()
        call_args = publish_fn.call_args
        assert call_args[0][0] == TOPIC_GATE_RESULT

    @pytest.mark.asyncio
    async def test_kafka_payload_contains_required_fields(self):
        """Published Kafka payload includes all GateResultEvent required fields."""
        conn = FakeConn()
        publish_fn = AsyncMock()

        await ingest_gate_result(
            conn,
            vin=_VIN,
            gate_id=_GATE_ID,
            gate_type="nvh",
            result="pass",
            measurements=_MEASUREMENTS,
            measured_at=_MEASURED_AT,
            opcenter_event_id=None,
            publish_fn=publish_fn,
        )

        payload = publish_fn.call_args[0][1]
        assert payload["vin"] == _VIN
        assert payload["gateId"] == _GATE_ID
        assert payload["gateType"] == "nvh"
        assert payload["result"] == "pass"
        assert isinstance(payload["measurements"], list)
        assert "gateResultId" in payload
        assert "receivedFromOpcenterAt" in payload
        assert "correlationId" in payload


# ---------------------------------------------------------------------------
# Tests: validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_valid_inputs_return_empty_errors(self):
        errors = validate_gate_result_payload(_VIN, "dimensional", "pass")
        assert errors == []

    def test_invalid_vin_pattern_returns_error(self):
        errors = validate_gate_result_payload("INVALID_VIN_12345", "dimensional", "pass")
        assert len(errors) == 1
        assert "VIN" in errors[0]

    def test_lowercase_vin_returns_error(self):
        errors = validate_gate_result_payload("wba3a5c55fk123456", "dimensional", "pass")
        assert any("VIN" in e for e in errors)

    def test_invalid_gate_type_returns_error(self):
        errors = validate_gate_result_payload(_VIN, "thermal", "pass")
        assert len(errors) == 1
        assert "gateType" in errors[0]

    def test_invalid_result_returns_error(self):
        errors = validate_gate_result_payload(_VIN, "dimensional", "pending")
        assert len(errors) == 1
        assert "result" in errors[0]

    def test_all_valid_gate_types_accepted(self):
        for gate_type in ("dimensional", "leak", "electrical", "nvh", "adas"):
            errors = validate_gate_result_payload(_VIN, gate_type, "pass")
            assert errors == [], f"Expected no errors for gate_type={gate_type!r}"

    def test_all_valid_results_accepted(self):
        for result in ("pass", "conditional_pass", "fail"):
            errors = validate_gate_result_payload(_VIN, "dimensional", result)
            assert errors == [], f"Expected no errors for result={result!r}"

    def test_multiple_errors_returned_for_multiple_invalid_fields(self):
        errors = validate_gate_result_payload("BAD_VIN", "bad_gate", "bad_result")
        assert len(errors) == 3


# ---------------------------------------------------------------------------
# Tests: FakeConn instance isolation
# ---------------------------------------------------------------------------

class TestFakeConnInstanceIsolation:
    @pytest.mark.asyncio
    async def test_two_instances_have_independent_inserted_rows(self):
        conn_a = FakeConn()
        conn_b = FakeConn()
        publish_fn = AsyncMock()

        await ingest_gate_result(
            conn_a,
            vin=_VIN,
            gate_id=_GATE_ID,
            gate_type="dimensional",
            result="pass",
            measurements=_MEASUREMENTS,
            measured_at=_MEASURED_AT,
            opcenter_event_id=None,
            publish_fn=publish_fn,
        )

        assert len(conn_a.inserted_rows) == 1
        assert len(conn_b.inserted_rows) == 0

    def test_new_instance_starts_with_empty_inserted_rows(self):
        conn = FakeConn()
        assert conn.inserted_rows == []
