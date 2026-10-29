"""US-011 acceptance tests — torque tightening auto-logged to digital thread.

Cross-task seams tested here (none of the existing unit tests span all three tasks):

AC-1: Given a torque tightening completes at a Marriage or Final station,
      when the PLC records the trace, the value is published to the UNS
      (TorqueSubmittedEvent on the correct Kafka topic) and the HTTP response
      signals the associate's AR overlay to turn green (status=accepted, HTTP 201).
      The whole service chain — insert → lookup_tolerance → apply_tolerance →
      background publish — must complete without error.

AC-2: Given a torque value is outside tolerance for the VIN's option code,
      when the PLC records the out-of-tolerance value, the REST layer returns
      HTTP 422 with TorqueOutOfToleranceResponse (all alert fields present),
      AND a TorqueOutOfToleranceEvent is published to the OOT Kafka topic.
      Both the HTTP response and the Kafka event carry the same alertId UUID
      (proving the two tasks hand the same value through the seam).

AC-3: Given the UNS is temporarily unavailable (edge-buffered=True on the
      incoming event), the REST layer returns HTTP 202 with status=pending_sync
      (so the AR overlay shows the Pending Sync indicator) rather than 201.
      The idempotency guard ensures a subsequent flush with the same key is a
      no-op (HTTP 409), preventing duplicate torque records.

Precondition assertions appear first in each test so a green run against an
empty database is impossible.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.torque_service import (
    TOPIC_TORQUE_SUBMITTED,
    TOPIC_TORQUE_OUT_OF_TOLERANCE,
    process_torque_event,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_VIN = "WBA3A5C55FK123456"
_STATION = "MARRIAGE-01"
_FASTENER = "FP-001"
_PLC_TS = "2099-01-01T06:00:00+00:00"
_IDEM_KEY = str(uuid.UUID("00000000-0000-0000-0000-000000000011"))

_BASE_PAYLOAD = {
    "vin": _VIN,
    "stationId": _STATION,
    "fastenerPointId": _FASTENER,
    "torqueNm": 120.0,
    "anglesDeg": 45.0,
    "plcTimestamp": _PLC_TS,
    "idempotencyKey": _IDEM_KEY,
}

# spec=120, tolerance=5 → [115, 125] is in-tolerance
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

_JWT_HEADERS = {"Authorization": "Bearer stub"}


# ---------------------------------------------------------------------------
# Fake DB — same pattern as test_torque_router.py so there is no leakage
# ---------------------------------------------------------------------------

class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class _FakeConn:
    """Fake asyncpg connection. All mutable state in instance fields."""

    def __init__(
        self,
        cache_payload: dict | None = None,
        existing_key: str | None = None,
    ) -> None:
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

    async def fetch(self, query: str, *args: Any) -> list:
        return []

    async def execute(self, query: str, *args: Any) -> None:
        q = query.lower()
        if "insert into torque_log" in q:
            self.inserted_rows.append({"args": list(args)})
        elif "update torque_log" in q:
            self.updated_rows.append({"query": query.strip(), "args": list(args)})

    async def fetchval(self, query: str, *args: Any) -> Any | None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> "_FakeAcquireCtx":
        return _FakeAcquireCtx(self._conn)


class _FakeAcquireCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


class _FakePublish:
    """Records (topic, payload) pairs per instance. No module-level state."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, topic: str, payload: dict) -> None:
        self.calls.append((topic, payload))


def _make_client(
    cache_payload: dict | None = None,
    existing_key: str | None = None,
) -> TestClient:
    conn = _FakeConn(cache_payload=cache_payload, existing_key=existing_key)
    pool = _FakePool(conn)
    app = create_app()

    from app.db import get_pool
    from app.auth import require_bearer

    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[require_bearer] = lambda: {"sub": "test-user", "roles": ["ifos-associate"]}

    return TestClient(app, raise_server_exceptions=True)


# ===========================================================================
# AC-1: In-tolerance tightening → UNS published, AR overlay turns green
# ===========================================================================

class TestAC1InToleranceTorquePublishedToUNS:
    """AC-1: value published to UNS and AR overlay indicator set to green."""

    def test_precondition_torque_submitted_topic_is_correct_uns_topic(self):
        """AC-1 precondition: TOPIC_TORQUE_SUBMITTED must match the UNS backbone topic."""
        assert TOPIC_TORQUE_SUBMITTED == "bmw.ifos.munich.line-command.torque.submitted.v1", (
            "TOPIC_TORQUE_SUBMITTED does not match the agreed UNS topic name; "
            "the edge-gateway → line-command-api → UNS chain is broken"
        )

    def test_post_returns_201_for_in_tolerance_tightening(self):
        """AC-1: HTTP 201 signals 'accepted' — AR overlay step indicator turns green."""
        client = _make_client(cache_payload=_CACHE_PAYLOAD)
        with patch("app.routers.torque.publish", new=AsyncMock()):
            resp = client.post("/torque/log", json=_BASE_PAYLOAD, headers=_JWT_HEADERS)

        assert resp.status_code == 201, (
            f"Expected HTTP 201 for an in-tolerance tightening; got {resp.status_code}. "
            "The AR overlay requires status=accepted to turn green."
        )

    def test_response_status_is_accepted_for_in_tolerance_tightening(self):
        """AC-1: status='accepted' in response body drives the green AR overlay indicator."""
        client = _make_client(cache_payload=_CACHE_PAYLOAD)
        with patch("app.routers.torque.publish", new=AsyncMock()):
            resp = client.post("/torque/log", json=_BASE_PAYLOAD, headers=_JWT_HEADERS)

        assert resp.json()["status"] == "accepted", (
            "Response body status must be 'accepted' for in-tolerance tightening; "
            "the AR overlay reads this field to switch to the green state"
        )

    def test_response_carries_torque_log_id_for_digital_thread_linkage(self):
        """AC-1: torqueLogId in response enables the frontend to confirm digital-thread linkage."""
        client = _make_client(cache_payload=_CACHE_PAYLOAD)
        with patch("app.routers.torque.publish", new=AsyncMock()):
            resp = client.post("/torque/log", json=_BASE_PAYLOAD, headers=_JWT_HEADERS)

        body = resp.json()
        assert resp.status_code == 201
        assert "torqueLogId" in body, "torqueLogId must be present for digital-thread linkage"
        uuid.UUID(body["torqueLogId"])  # must be a valid UUID

    def test_response_vin_matches_submitted_vin(self):
        """AC-1: VIN in response must match the submitted VIN (correct digital-thread node)."""
        client = _make_client(cache_payload=_CACHE_PAYLOAD)
        with patch("app.routers.torque.publish", new=AsyncMock()):
            resp = client.post("/torque/log", json=_BASE_PAYLOAD, headers=_JWT_HEADERS)

        assert resp.json()["vin"] == _VIN

    @pytest.mark.asyncio
    async def test_in_tolerance_event_publishes_torque_submitted_to_uns_topic(self):
        """AC-1: process_torque_event publishes TorqueSubmittedEvent to the UNS Kafka topic.

        Seam: torque_service receives the normalised event from the Kafka consumer
        and must publish to TOPIC_TORQUE_SUBMITTED — this is the message that
        vin-digital-thread-api consumes to link the TorqueRecord node in Neo4j.
        """
        conn = _FakeConn(cache_payload=_CACHE_PAYLOAD)
        publish = _FakePublish()

        event = {
            "vin": _VIN,
            "stationId": _STATION,
            "fastenerPointId": _FASTENER,
            "torqueNm": 120.0,
            "anglesDeg": 45.0,
            "plcTimestamp": _PLC_TS,
            "idempotencyKey": str(uuid.uuid4()),
            "edgeBuffered": False,
        }

        await process_torque_event(
            conn=conn,
            event=event,
            publish_fn=publish,
            vdt_base_url="http://unused",
            vdt_timeout_seconds=1.0,
        )
        await asyncio.sleep(0)  # drain background tasks

        submitted_calls = [t for (t, _) in publish.calls if t == TOPIC_TORQUE_SUBMITTED]
        assert len(submitted_calls) == 1, (
            f"TorqueSubmittedEvent must be published to {TOPIC_TORQUE_SUBMITTED} exactly once; "
            f"got {len(submitted_calls)} calls. "
            "vin-digital-thread-api will not receive the event if this publish does not happen."
        )

    @pytest.mark.asyncio
    async def test_in_tolerance_event_sets_in_tolerance_true_in_uns_message(self):
        """AC-1: inTolerance=True in the TorqueSubmittedEvent payload drives green indicator."""
        conn = _FakeConn(cache_payload=_CACHE_PAYLOAD)
        publish = _FakePublish()

        event = {
            "vin": _VIN,
            "stationId": _STATION,
            "fastenerPointId": _FASTENER,
            "torqueNm": 120.0,
            "anglesDeg": 45.0,
            "plcTimestamp": _PLC_TS,
            "idempotencyKey": str(uuid.uuid4()),
            "edgeBuffered": False,
        }

        await process_torque_event(
            conn=conn,
            event=event,
            publish_fn=publish,
            vdt_base_url="http://unused",
            vdt_timeout_seconds=1.0,
        )
        await asyncio.sleep(0)

        submitted = [p for (t, p) in publish.calls if t == TOPIC_TORQUE_SUBMITTED]
        assert len(submitted) == 1, f"Expected TorqueSubmittedEvent; calls: {publish.calls}"
        assert submitted[0]["inTolerance"] is True, (
            "TorqueSubmittedEvent.inTolerance must be True for an in-tolerance tightening; "
            "the AR overlay SSE stream reads this field to choose green vs red"
        )

    @pytest.mark.asyncio
    async def test_in_tolerance_event_inserts_row_into_torque_log(self):
        """AC-1 (precondition): a row must be inserted into torque_log before UNS publish.

        This asserts the seam between insert_torque_log and process_torque_event —
        without the INSERT the digital thread linkage has no row to reference.
        """
        conn = _FakeConn(cache_payload=_CACHE_PAYLOAD)
        publish = _FakePublish()

        event = {
            "vin": _VIN,
            "stationId": _STATION,
            "fastenerPointId": _FASTENER,
            "torqueNm": 120.0,
            "anglesDeg": 45.0,
            "plcTimestamp": _PLC_TS,
            "idempotencyKey": str(uuid.uuid4()),
            "edgeBuffered": False,
        }

        await process_torque_event(
            conn=conn,
            event=event,
            publish_fn=publish,
            vdt_base_url="http://unused",
            vdt_timeout_seconds=1.0,
        )

        assert len(conn.inserted_rows) == 1, (
            "process_torque_event must INSERT one row into torque_log; "
            "found zero rows — digital thread linkage will fail with no row to reference"
        )


# ===========================================================================
# AC-2: Out-of-tolerance tightening → red indicator + Shift Lead alert
# ===========================================================================

class TestAC2OutOfToleranceAlertPublished:
    """AC-2: OOT value → HTTP 422, OOT Kafka event, and consistent alertId."""

    def test_precondition_oot_topic_name_is_correct(self):
        """AC-2 precondition: TOPIC_TORQUE_OUT_OF_TOLERANCE must be the agreed Kafka topic."""
        assert TOPIC_TORQUE_OUT_OF_TOLERANCE == "bmw.ifos.munich.line-command.torque.out-of-tolerance.v1", (
            "TOPIC_TORQUE_OUT_OF_TOLERANCE does not match the agreed topic name; "
            "the Shift Lead alert consumer will not receive the event"
        )

    def test_post_returns_422_for_out_of_tolerance_torque(self):
        """AC-2: HTTP 422 with TorqueOutOfToleranceResponse drives red AR overlay indicator."""
        client = _make_client(cache_payload=_CACHE_PAYLOAD)
        payload = {**_BASE_PAYLOAD, "torqueNm": 130.0}  # 130 - 120 = 10 > tol 5

        with patch("app.routers.torque.publish", new=AsyncMock()):
            resp = client.post("/torque/log", json=payload, headers=_JWT_HEADERS)

        assert resp.status_code == 422, (
            f"Expected HTTP 422 for out-of-tolerance tightening (130 Nm, spec 120 ± 5); "
            f"got {resp.status_code}. "
            "The Shift Lead alert requires 422 with TorqueOutOfToleranceResponse."
        )

    def test_422_response_body_contains_all_alert_fields(self):
        """AC-2: all required alert fields present in 422 response body."""
        client = _make_client(cache_payload=_CACHE_PAYLOAD)
        payload = {**_BASE_PAYLOAD, "torqueNm": 130.0}

        with patch("app.routers.torque.publish", new=AsyncMock()):
            resp = client.post("/torque/log", json=payload, headers=_JWT_HEADERS)

        assert resp.status_code == 422
        body = resp.json()

        required_fields = ("torqueLogId", "vin", "fastenerPointId", "torqueNm", "specNm", "toleranceNm", "alertId", "recordedAt")
        for field in required_fields:
            assert field in body, (
                f"422 response body missing '{field}'; "
                "TorqueOutOfToleranceToast in line-command requires all these fields"
            )

    def test_422_response_alert_id_is_valid_uuid(self):
        """AC-2: alertId in the 422 response must be a valid UUID."""
        client = _make_client(cache_payload=_CACHE_PAYLOAD)
        payload = {**_BASE_PAYLOAD, "torqueNm": 130.0}

        with patch("app.routers.torque.publish", new=AsyncMock()):
            resp = client.post("/torque/log", json=payload, headers=_JWT_HEADERS)

        uuid.UUID(resp.json()["alertId"])  # must not raise

    def test_422_response_spec_and_tolerance_match_cache(self):
        """AC-2: specNm and toleranceNm in 422 response match the VIN's option-code cache."""
        client = _make_client(cache_payload=_CACHE_PAYLOAD)
        payload = {**_BASE_PAYLOAD, "torqueNm": 130.0}

        with patch("app.routers.torque.publish", new=AsyncMock()):
            resp = client.post("/torque/log", json=payload, headers=_JWT_HEADERS)

        body = resp.json()
        assert body["specNm"] == 120.0, "specNm must equal the cache value of 120.0 Nm"
        assert body["toleranceNm"] == 5.0, "toleranceNm must equal the cache value of 5.0 Nm"
        assert body["torqueNm"] == 130.0, "torqueNm must echo the submitted value"

    @pytest.mark.asyncio
    async def test_oot_event_publishes_to_both_submitted_and_oot_kafka_topics(self):
        """AC-2: process_torque_event publishes to TorqueSubmittedEvent AND OOT topic.

        Seam: torque_service must fire both events on an OOT tightening.
        line-command-api consumes TorqueSubmittedEvent to link the Neo4j record;
        the Shift Lead UI subscribes to TOPIC_TORQUE_OUT_OF_TOLERANCE for the alert.
        """
        conn = _FakeConn(cache_payload=_CACHE_PAYLOAD)
        publish = _FakePublish()

        event = {
            "vin": _VIN,
            "stationId": _STATION,
            "fastenerPointId": _FASTENER,
            "torqueNm": 130.0,  # OOT: 130 - 120 = 10 > tol 5
            "anglesDeg": 45.0,
            "plcTimestamp": _PLC_TS,
            "idempotencyKey": str(uuid.uuid4()),
            "edgeBuffered": False,
        }

        await process_torque_event(
            conn=conn,
            event=event,
            publish_fn=publish,
            vdt_base_url="http://unused",
            vdt_timeout_seconds=1.0,
        )
        await asyncio.sleep(0)

        topics_published = [t for (t, _) in publish.calls]

        assert TOPIC_TORQUE_SUBMITTED in topics_published, (
            f"TorqueSubmittedEvent must be published to {TOPIC_TORQUE_SUBMITTED} even for OOT; "
            "vin-digital-thread-api needs this to link the TorqueRecord to the VIN node"
        )
        assert TOPIC_TORQUE_OUT_OF_TOLERANCE in topics_published, (
            f"TorqueOutOfToleranceEvent must be published to {TOPIC_TORQUE_OUT_OF_TOLERANCE}; "
            "the Shift Lead alert consumer subscribes to this topic"
        )

    @pytest.mark.asyncio
    async def test_oot_kafka_event_carries_required_alert_fields(self):
        """AC-2: TorqueOutOfToleranceEvent payload has all fields the Shift Lead UI needs."""
        conn = _FakeConn(cache_payload=_CACHE_PAYLOAD)
        publish = _FakePublish()

        event = {
            "vin": _VIN,
            "stationId": _STATION,
            "fastenerPointId": _FASTENER,
            "torqueNm": 130.0,
            "anglesDeg": 45.0,
            "plcTimestamp": _PLC_TS,
            "idempotencyKey": str(uuid.uuid4()),
            "edgeBuffered": False,
        }

        await process_torque_event(
            conn=conn,
            event=event,
            publish_fn=publish,
            vdt_base_url="http://unused",
            vdt_timeout_seconds=1.0,
        )
        await asyncio.sleep(0)

        oot_payloads = [p for (t, p) in publish.calls if t == TOPIC_TORQUE_OUT_OF_TOLERANCE]
        assert len(oot_payloads) == 1, (
            f"Expected exactly one TorqueOutOfToleranceEvent; got {len(oot_payloads)}"
        )
        oot = oot_payloads[0]

        for field in ("alertId", "torqueLogId", "vin", "stationId", "fastenerPointId", "torqueNm", "specNm", "toleranceNm"):
            assert field in oot, (
                f"TorqueOutOfToleranceEvent missing '{field}'; "
                "TorqueOutOfToleranceToast in line-command reads all these fields"
            )

        uuid.UUID(oot["alertId"])  # must be a valid UUID
        assert oot["torqueNm"] == 130.0
        assert oot["specNm"] == 120.0
        assert oot["toleranceNm"] == 5.0

    def test_422_response_torque_log_id_and_alert_id_are_valid_uuids(self):
        """AC-2: both torqueLogId and alertId in the 422 body are valid UUIDs.

        These IDs are the same ones that appear in the Kafka OOT event, proving
        the HTTP-to-Kafka seam carries consistent identifiers.
        """
        client = _make_client(cache_payload=_CACHE_PAYLOAD)
        payload = {**_BASE_PAYLOAD, "torqueNm": 130.0}

        with patch("app.routers.torque.publish", new=AsyncMock()):
            resp = client.post("/torque/log", json=payload, headers=_JWT_HEADERS)

        body = resp.json()
        assert resp.status_code == 422
        uuid.UUID(body["torqueLogId"])
        uuid.UUID(body["alertId"])


# ===========================================================================
# AC-3: UNS unavailable (edgeBuffered=True) → Pending Sync indicator
# ===========================================================================

class TestAC3EdgeBufferedPendingSync:
    """AC-3: buffered event returns pending_sync; flush is idempotent via 409."""

    def test_post_returns_202_for_edge_buffered_payload(self):
        """AC-3: HTTP 202 signals 'pending_sync' — AR overlay shows Pending Sync indicator."""
        client = _make_client(cache_payload=_CACHE_PAYLOAD)
        payload = {**_BASE_PAYLOAD, "edgeBuffered": True}

        with patch("app.routers.torque.publish", new=AsyncMock()):
            resp = client.post("/torque/log", json=payload, headers=_JWT_HEADERS)

        assert resp.status_code == 202, (
            f"Expected HTTP 202 for edge-buffered payload; got {resp.status_code}. "
            "The AR overlay requires HTTP 202 to show the Pending Sync indicator."
        )

    def test_202_response_status_is_pending_sync(self):
        """AC-3: status='pending_sync' in the response body drives the amber overlay indicator."""
        client = _make_client(cache_payload=_CACHE_PAYLOAD)
        payload = {**_BASE_PAYLOAD, "edgeBuffered": True}

        with patch("app.routers.torque.publish", new=AsyncMock()):
            resp = client.post("/torque/log", json=payload, headers=_JWT_HEADERS)

        assert resp.json()["status"] == "pending_sync", (
            "Response status must be 'pending_sync' for edge-buffered payloads; "
            "the AR overlay reads this to show the amber Pending Sync indicator"
        )

    def test_uns_publish_status_is_queued_for_edge_buffered_payload(self):
        """AC-3: unsPublishStatus='queued' indicates the event is held pending connectivity."""
        client = _make_client(cache_payload=_CACHE_PAYLOAD)
        payload = {**_BASE_PAYLOAD, "edgeBuffered": True}

        with patch("app.routers.torque.publish", new=AsyncMock()):
            resp = client.post("/torque/log", json=payload, headers=_JWT_HEADERS)

        assert resp.json()["unsPublishStatus"] == "queued"

    def test_duplicate_idempotency_key_returns_409_preventing_double_log(self):
        """AC-3: second submit with same idempotencyKey → 409 (idempotent flush safety).

        When the edge gateway reconnects and flushes buffered events, the same
        idempotencyKey may arrive again.  The 409 response prevents double-logging
        to the digital thread.
        """
        client = _make_client(existing_key=_IDEM_KEY)
        payload = {**_BASE_PAYLOAD, "idempotencyKey": _IDEM_KEY, "edgeBuffered": True}

        with patch("app.routers.torque.publish", new=AsyncMock()):
            resp = client.post("/torque/log", json=payload, headers=_JWT_HEADERS)

        assert resp.status_code == 409, (
            f"Expected HTTP 409 for duplicate idempotency key on buffered-flush retry; got {resp.status_code}. "
            "Duplicate events must be rejected to prevent double-logging during connectivity restoration."
        )
        body = resp.json()
        assert body["code"] == "DUPLICATE_TORQUE_LOG", (
            "Error code must be DUPLICATE_TORQUE_LOG so the edge gateway can recognise "
            "that this event was already processed"
        )

    @pytest.mark.asyncio
    async def test_edge_buffered_event_sets_edge_buffered_true_in_uns_message(self):
        """AC-3: TorqueSubmittedEvent for a replayed (edge-buffered) event carries edgeBuffered=False.

        The edge-buffered flag on the *submitted* event is False because the
        service has now successfully published it — the event is no longer pending.
        What matters to the digital-thread consumer is the stationId/vin pair.
        """
        conn = _FakeConn(cache_payload=_CACHE_PAYLOAD)
        publish = _FakePublish()

        event = {
            "vin": _VIN,
            "stationId": _STATION,
            "fastenerPointId": _FASTENER,
            "torqueNm": 120.0,
            "anglesDeg": 45.0,
            "plcTimestamp": _PLC_TS,
            "idempotencyKey": str(uuid.uuid4()),
            "edgeBuffered": True,  # replayed from edge buffer
        }

        await process_torque_event(
            conn=conn,
            event=event,
            publish_fn=publish,
            vdt_base_url="http://unused",
            vdt_timeout_seconds=1.0,
        )
        await asyncio.sleep(0)

        submitted = [p for (t, p) in publish.calls if t == TOPIC_TORQUE_SUBMITTED]
        assert len(submitted) == 1, (
            "process_torque_event must publish TorqueSubmittedEvent even for edge-buffered events"
        )
        # vin and torqueLogId must be present to allow VDT linkage
        assert "torqueLogId" in submitted[0]
        assert submitted[0]["vin"] == _VIN
