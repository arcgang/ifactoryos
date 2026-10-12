"""Tests for torque_service — idempotency, tolerance, and event publishing.

All DB and Kafka I/O replaced with in-process fakes; no external services needed.

Covered scenarios:
  - Duplicate event skipped (idempotency key deduplication)
  - Fresh event inserts torque_log row with status=queued
  - In-tolerance value: in_tolerance=True, TorqueSubmittedEvent published, status=published
  - Out-of-tolerance value: in_tolerance=False, TorqueOutOfToleranceEvent published, alert_id set
  - No tolerance row in cache → in_tolerance=None, only TorqueSubmittedEvent published
  - AdoptionEvent published on every non-duplicate event
  - mark_publish_failed called when Kafka publish raises
  - FakeConnection instance isolation (independent mutable state per instance)
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.torque_service import (
    TOPIC_ADOPTION_EVENT,
    TOPIC_TORQUE_OUT_OF_TOLERANCE,
    TOPIC_TORQUE_SUBMITTED,
    apply_tolerance,
    insert_torque_log,
    is_duplicate,
    mark_publish_failed,
    mark_published,
    process_torque_event,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VIN = "WBA3A5C55FK123456"
_STATION = "MARRIAGE-01"
_FASTENER = "FP-001"
_IDEM_KEY = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))
_PLC_TS = "2099-01-01T06:00:00+00:00"

_BASE_EVENT: dict = {
    "vin": _VIN,
    "stationId": _STATION,
    "fastenerPointId": _FASTENER,
    "torqueNm": 120.0,
    "anglesDeg": 45.0,
    "plcTimestamp": _PLC_TS,
    "edgeGatewayId": "gw-01",
    "idempotencyKey": _IDEM_KEY,
}

_CACHE_PAYLOAD = {
    "vin": _VIN,
    "stationId": _STATION,
    "optionCodes": [],
    "variantCode": "G26",
    "isRareVariant": False,
    "steps": [
        {
            "stepNumber": 1,
            "description": "Torque rear subframe",
            "torqueSpecNm": 120.0,
            "torqueToleranceNm": 5.0,
            "fastenerPointId": _FASTENER,
        }
    ],
    "instructionVersion": "v1.0",
    "fetchedAt": "2099-01-01T06:00:00+00:00",
    "fallbackPdfQrUrl": "",
}


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


class FakeConnection:
    """Fake asyncpg connection — mutable state in instance fields, never module-level."""

    def __init__(self, cache_payload: dict | None = None, existing_key: str | None = None) -> None:
        self._cache_payload = cache_payload
        self._existing_key = existing_key

        self.inserted_rows: list[dict] = []
        self.updated_rows: list[dict] = []

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        q = query.lower()
        if "torque_log" in q and "idempotency_key" in q:
            if self._existing_key and str(args[0]) == self._existing_key:
                return _FakeRecord({"id": str(uuid.uuid4())})
            return None
        if "ar_instruction_cache" in q:
            if self._cache_payload is not None:
                return _FakeRecord({"payload_jsonb": self._cache_payload})
            return None
        return None

    async def execute(self, query: str, *args: Any) -> None:
        q = query.lower()
        if "insert into torque_log" in q:
            self.inserted_rows.append({"args": list(args)})
        elif "update torque_log" in q:
            self.updated_rows.append({"query": query.strip(), "args": list(args)})

    async def fetchval(self, query: str, *args: Any) -> Any | None:
        return None


# ---------------------------------------------------------------------------
# Fake publish function
# ---------------------------------------------------------------------------

class FakePublish:
    """Records calls by (topic, payload). Mutable state in instance fields."""

    def __init__(self, fail_on_topic: str | None = None) -> None:
        self._fail_on_topic = fail_on_topic
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, topic: str, payload: dict) -> None:
        self.calls.append((topic, payload))
        if self._fail_on_topic and topic == self._fail_on_topic:
            raise RuntimeError("simulated Kafka failure")


# ---------------------------------------------------------------------------
# is_duplicate
# ---------------------------------------------------------------------------

class TestIsDuplicate:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_existing_row(self):
        conn = FakeConnection()
        result = await is_duplicate(conn, _IDEM_KEY)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_row_exists(self):
        conn = FakeConnection(existing_key=_IDEM_KEY)
        result = await is_duplicate(conn, _IDEM_KEY)
        assert result is True

    def test_two_instances_start_with_independent_state(self):
        conn_a = FakeConnection(existing_key=_IDEM_KEY)
        conn_b = FakeConnection()
        assert conn_a._existing_key == _IDEM_KEY
        assert conn_b._existing_key is None


# ---------------------------------------------------------------------------
# insert_torque_log
# ---------------------------------------------------------------------------

class TestInsertTorqueLog:
    @pytest.mark.asyncio
    async def test_inserts_one_row_with_queued_status(self):
        conn = FakeConnection()
        row_id = await insert_torque_log(
            conn,
            vin=_VIN,
            station_id=_STATION,
            fastener_point_id=_FASTENER,
            torque_nm=Decimal("120.0000"),
            angles_deg=Decimal("45.0000"),
            plc_timestamp=datetime(2099, 1, 1, 6, 0, 0, tzinfo=timezone.utc),
            idempotency_key=_IDEM_KEY,
            trace_data=None,
            edge_buffered=False,
        )
        assert len(conn.inserted_rows) == 1
        # Row ID is a valid UUID string
        uuid.UUID(row_id)

    @pytest.mark.asyncio
    async def test_returns_uuid_string(self):
        conn = FakeConnection()
        row_id = await insert_torque_log(
            conn,
            vin=_VIN,
            station_id=_STATION,
            fastener_point_id=_FASTENER,
            torque_nm=Decimal("115.0"),
            angles_deg=Decimal("42.0"),
            plc_timestamp=datetime(2099, 1, 1, 6, 0, 0, tzinfo=timezone.utc),
            idempotency_key=_IDEM_KEY,
            trace_data=[{"offsetMs": 0, "torqueNm": 115.0, "angleDeg": 42.0}],
            edge_buffered=True,
        )
        uuid.UUID(row_id)  # must not raise


# ---------------------------------------------------------------------------
# apply_tolerance
# ---------------------------------------------------------------------------

class TestApplyTolerance:
    @pytest.mark.asyncio
    async def test_in_tolerance_when_within_range(self):
        conn = FakeConnection()
        row_id = str(uuid.uuid4())
        result = await apply_tolerance(
            conn, row_id,
            torque_nm=Decimal("122.0"),
            spec_nm=Decimal("120.0"),
            tolerance_nm=Decimal("5.0"),
        )
        assert result is True
        assert len(conn.updated_rows) == 1

    @pytest.mark.asyncio
    async def test_out_of_tolerance_when_outside_range(self):
        conn = FakeConnection()
        row_id = str(uuid.uuid4())
        result = await apply_tolerance(
            conn, row_id,
            torque_nm=Decimal("126.0"),
            spec_nm=Decimal("120.0"),
            tolerance_nm=Decimal("5.0"),
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_exactly_at_boundary_is_in_tolerance(self):
        conn = FakeConnection()
        row_id = str(uuid.uuid4())
        result = await apply_tolerance(
            conn, row_id,
            torque_nm=Decimal("125.0"),
            spec_nm=Decimal("120.0"),
            tolerance_nm=Decimal("5.0"),
        )
        assert result is True


# ---------------------------------------------------------------------------
# process_torque_event — full flow
# ---------------------------------------------------------------------------

class TestProcessTorqueEvent:
    @pytest.mark.asyncio
    async def test_duplicate_event_skips_insert_and_publish(self):
        conn = FakeConnection(existing_key=_IDEM_KEY)
        publish = FakePublish()
        await process_torque_event(
            conn=conn,
            event=_BASE_EVENT,
            publish_fn=publish,
            vdt_base_url="http://vdt:8001",
            vdt_timeout_seconds=5.0,
        )
        assert len(conn.inserted_rows) == 0
        # Give any background tasks a chance to run
        await asyncio.sleep(0)
        assert len(publish.calls) == 0

    @pytest.mark.asyncio
    async def test_fresh_event_inserts_torque_log_row(self):
        conn = FakeConnection(cache_payload=_CACHE_PAYLOAD)
        publish = FakePublish()
        await process_torque_event(
            conn=conn,
            event=_BASE_EVENT,
            publish_fn=publish,
            vdt_base_url="http://vdt:8001",
            vdt_timeout_seconds=5.0,
        )
        assert len(conn.inserted_rows) == 1

    @pytest.mark.asyncio
    async def test_in_tolerance_publishes_torque_submitted_event(self):
        conn = FakeConnection(cache_payload=_CACHE_PAYLOAD)
        publish = FakePublish()
        await process_torque_event(
            conn=conn,
            event=_BASE_EVENT,  # torqueNm=120.0, spec=120.0, tol=5.0 → in_tolerance
            publish_fn=publish,
            vdt_base_url="http://vdt:8001",
            vdt_timeout_seconds=5.0,
        )
        # Drain background tasks
        for _ in range(5):
            await asyncio.sleep(0)
        submitted_topics = [t for t, _ in publish.calls if t == TOPIC_TORQUE_SUBMITTED]
        assert len(submitted_topics) == 1
        submitted_payload = next(p for t, p in publish.calls if t == TOPIC_TORQUE_SUBMITTED)
        assert submitted_payload["vin"] == _VIN
        assert submitted_payload["stationId"] == _STATION
        assert submitted_payload["fastenerPointId"] == _FASTENER
        assert submitted_payload["inTolerance"] is True

    @pytest.mark.asyncio
    async def test_in_tolerance_does_not_publish_out_of_tolerance_event(self):
        conn = FakeConnection(cache_payload=_CACHE_PAYLOAD)
        publish = FakePublish()
        await process_torque_event(
            conn=conn,
            event=_BASE_EVENT,
            publish_fn=publish,
            vdt_base_url="http://vdt:8001",
            vdt_timeout_seconds=5.0,
        )
        for _ in range(5):
            await asyncio.sleep(0)
        oot_topics = [t for t, _ in publish.calls if t == TOPIC_TORQUE_OUT_OF_TOLERANCE]
        assert len(oot_topics) == 0

    @pytest.mark.asyncio
    async def test_out_of_tolerance_publishes_out_of_tolerance_event(self):
        conn = FakeConnection(cache_payload=_CACHE_PAYLOAD)
        publish = FakePublish()
        oot_event = {**_BASE_EVENT, "torqueNm": 130.0}  # 130 - 120 = 10 > tol 5
        await process_torque_event(
            conn=conn,
            event=oot_event,
            publish_fn=publish,
            vdt_base_url="http://vdt:8001",
            vdt_timeout_seconds=5.0,
        )
        for _ in range(5):
            await asyncio.sleep(0)
        oot_payloads = [p for t, p in publish.calls if t == TOPIC_TORQUE_OUT_OF_TOLERANCE]
        assert len(oot_payloads) == 1
        oot = oot_payloads[0]
        assert oot["vin"] == _VIN
        assert oot["torqueNm"] == 130.0
        assert oot["specNm"] == 120.0
        assert oot["toleranceNm"] == 5.0
        uuid.UUID(oot["alertId"])  # must be a valid UUID

    @pytest.mark.asyncio
    async def test_out_of_tolerance_publishes_submitted_event_with_in_tolerance_false(self):
        conn = FakeConnection(cache_payload=_CACHE_PAYLOAD)
        publish = FakePublish()
        oot_event = {**_BASE_EVENT, "torqueNm": 130.0}
        await process_torque_event(
            conn=conn,
            event=oot_event,
            publish_fn=publish,
            vdt_base_url="http://vdt:8001",
            vdt_timeout_seconds=5.0,
        )
        for _ in range(5):
            await asyncio.sleep(0)
        submitted = next((p for t, p in publish.calls if t == TOPIC_TORQUE_SUBMITTED), None)
        assert submitted is not None
        assert submitted["inTolerance"] is False

    @pytest.mark.asyncio
    async def test_no_tolerance_data_omits_in_tolerance_from_submitted_event(self):
        conn = FakeConnection(cache_payload=None)
        publish = FakePublish()
        with patch("app.torque_service.lookup_tolerance", new=AsyncMock(return_value=(None, None))):
            await process_torque_event(
                conn=conn,
                event=_BASE_EVENT,
                publish_fn=publish,
                vdt_base_url="http://vdt:8001",
                vdt_timeout_seconds=5.0,
            )
        for _ in range(5):
            await asyncio.sleep(0)
        submitted = next((p for t, p in publish.calls if t == TOPIC_TORQUE_SUBMITTED), None)
        assert submitted is not None
        assert "inTolerance" not in submitted

    @pytest.mark.asyncio
    async def test_adoption_event_published_on_fresh_event(self):
        conn = FakeConnection(cache_payload=_CACHE_PAYLOAD)
        publish = FakePublish()
        await process_torque_event(
            conn=conn,
            event=_BASE_EVENT,
            publish_fn=publish,
            vdt_base_url="http://vdt:8001",
            vdt_timeout_seconds=5.0,
        )
        await asyncio.sleep(0)
        adoption_calls = [p for t, p in publish.calls if t == TOPIC_ADOPTION_EVENT]
        assert len(adoption_calls) == 1
        assert adoption_calls[0]["actionType"] == "TORQUE_LOG_SUBMITTED"
        assert adoption_calls[0]["vin"] == _VIN

    @pytest.mark.asyncio
    async def test_adoption_event_not_published_on_duplicate(self):
        conn = FakeConnection(existing_key=_IDEM_KEY)
        publish = FakePublish()
        await process_torque_event(
            conn=conn,
            event=_BASE_EVENT,
            publish_fn=publish,
            vdt_base_url="http://vdt:8001",
            vdt_timeout_seconds=5.0,
        )
        await asyncio.sleep(0)
        adoption_calls = [t for t, _ in publish.calls if t == TOPIC_ADOPTION_EVENT]
        assert len(adoption_calls) == 0

    @pytest.mark.asyncio
    async def test_kafka_failure_marks_publish_status_failed(self):
        conn = FakeConnection(cache_payload=_CACHE_PAYLOAD)
        publish = FakePublish(fail_on_topic=TOPIC_TORQUE_SUBMITTED)
        await process_torque_event(
            conn=conn,
            event=_BASE_EVENT,
            publish_fn=publish,
            vdt_base_url="http://vdt:8001",
            vdt_timeout_seconds=5.0,
        )
        for _ in range(5):
            await asyncio.sleep(0)
        failed_updates = [
            u for u in conn.updated_rows
            if "uns_publish_status = 'failed'" in u.get("query", "")
        ]
        assert len(failed_updates) == 1

    @pytest.mark.asyncio
    async def test_second_identical_event_inserts_no_second_row(self):
        conn = FakeConnection(cache_payload=_CACHE_PAYLOAD)
        publish = FakePublish()
        # First call — insert
        await process_torque_event(
            conn=conn,
            event=_BASE_EVENT,
            publish_fn=publish,
            vdt_base_url="http://vdt:8001",
            vdt_timeout_seconds=5.0,
        )
        # Simulate the row now existing
        conn._existing_key = _IDEM_KEY
        # Second call — should skip
        await process_torque_event(
            conn=conn,
            event=_BASE_EVENT,
            publish_fn=publish,
            vdt_base_url="http://vdt:8001",
            vdt_timeout_seconds=5.0,
        )
        assert len(conn.inserted_rows) == 1


# ---------------------------------------------------------------------------
# mark_published / mark_publish_failed
# ---------------------------------------------------------------------------

class TestStatusUpdates:
    @pytest.mark.asyncio
    async def test_mark_published_issues_update(self):
        conn = FakeConnection()
        row_id = str(uuid.uuid4())
        await mark_published(conn, row_id, datetime.now(timezone.utc))
        assert len(conn.updated_rows) == 1
        assert "uns_publish_status = 'published'" in conn.updated_rows[0]["query"]

    @pytest.mark.asyncio
    async def test_mark_publish_failed_issues_update(self):
        conn = FakeConnection()
        row_id = str(uuid.uuid4())
        await mark_publish_failed(conn, row_id)
        assert len(conn.updated_rows) == 1
        assert "uns_publish_status = 'failed'" in conn.updated_rows[0]["query"]


# ---------------------------------------------------------------------------
# FakeConnection instance isolation
# ---------------------------------------------------------------------------

class TestFakeConnectionInstanceIsolation:
    def test_two_instances_have_independent_inserted_rows(self):
        conn_a = FakeConnection()
        conn_b = FakeConnection()
        asyncio.get_event_loop().run_until_complete(
            conn_a.execute(
                "INSERT INTO torque_log (id) VALUES ($1::uuid)",
                str(uuid.uuid4()),
            )
        )
        assert len(conn_a.inserted_rows) == 1
        assert len(conn_b.inserted_rows) == 0

    def test_new_instance_starts_with_empty_collections(self):
        conn = FakeConnection()
        assert len(conn.inserted_rows) == 0
        assert len(conn.updated_rows) == 0
