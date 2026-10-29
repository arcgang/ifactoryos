"""US-011 acceptance tests — TorqueSubmittedEvent → TorqueRecord linked to VIN in Neo4j.

Cross-task seam tested here: vin-digital-thread-api consumes TorqueSubmittedEvent
from TOPIC_TORQUE_SUBMITTED and executes an idempotent MERGE in Neo4j.  The unit
tests in test_torque_consumer.py cover individual functions; this file proves
the event schema agreed with line-command-api actually flows through the consumer.

AC-1: When a TorqueSubmittedEvent arrives on the correct topic, a TorqueRecord
      node is MERGED in Neo4j and linked via a TORQUE_LOG relationship to the VIN
      node.  The record contains vin, torqueLogId, stationId, fastenerPointId,
      torqueNm, anglesDeg, plcTimestamp, and unsPublishedAt.

AC-1/AC-2 (idempotency): Replaying the same torqueLogId produces no second node —
      the MERGE is idempotent and the consumer does not raise on duplicate delivery.

AC-3 (buffered replay): A message with edgeBuffered=True is still processed and
      linked to the digital thread.  The VDT consumer does not filter by that flag.

Precondition assertions appear first so a run against a missing consumer module
fails loudly rather than trivially passing.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Precondition: consumer module and topic constant must exist
# ---------------------------------------------------------------------------

def test_precondition_torque_consumer_module_importable() -> None:
    """Precondition: app.torque_consumer must be importable — task is merged."""
    try:
        import app.torque_consumer  # noqa: F401
    except ImportError as exc:
        raise AssertionError(
            "app.torque_consumer is not importable; "
            "the vin-digital-thread-api torque consumer task may not be merged"
        ) from exc


def test_precondition_consumer_subscribes_to_correct_uns_topic() -> None:
    """Precondition: consumer must subscribe to the agreed UNS topic."""
    from app.torque_consumer import TOPIC_TORQUE_SUBMITTED

    assert TOPIC_TORQUE_SUBMITTED == "bmw.ifos.munich.line-command.torque.submitted.v1", (
        "TOPIC_TORQUE_SUBMITTED does not match the agreed UNS topic; "
        "line-command-api publishes to this exact name"
    )


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_VIN = "WBA3A5C55FK123456"
_TORQUE_LOG_ID = str(uuid.UUID("00000000-0000-0000-0000-000000000011"))
_STATION_ID = "MARRIAGE-01"
_FASTENER_POINT_ID = "FP-001"
_TORQUE_NM = 120.0
_ANGLES_DEG = 45.0
_PLC_TIMESTAMP = "2099-01-01T06:00:00Z"
_UNS_PUBLISHED_AT = "2099-01-01T06:00:00.250Z"


def _make_event(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "torqueLogId": _TORQUE_LOG_ID,
        "vin": _VIN,
        "stationId": _STATION_ID,
        "fastenerPointId": _FASTENER_POINT_ID,
        "torqueNm": _TORQUE_NM,
        "anglesDeg": _ANGLES_DEG,
        "plcTimestamp": _PLC_TIMESTAMP,
        "unsPublishedAt": _UNS_PUBLISHED_AT,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Fake Neo4j infrastructure — same pattern as test_torque_consumer.py
# ---------------------------------------------------------------------------

class _FakeNeo4jSession:
    """Records every Cypher run() call; each instance starts with an empty list."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, query: str, **kwargs: Any) -> None:
        self.calls.append({"query": query, **kwargs})

    async def __aenter__(self) -> "_FakeNeo4jSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class _FakeNeo4jDriver:
    """Returns the same _FakeNeo4jSession on every session() call on one instance."""

    def __init__(self) -> None:
        self.session_instance = _FakeNeo4jSession()

    def session(self) -> _FakeNeo4jSession:
        return self.session_instance

    async def __aenter__(self) -> "_FakeNeo4jDriver":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


# ===========================================================================
# AC-1: TorqueSubmittedEvent → TorqueRecord node linked to VIN in Neo4j
# ===========================================================================

class TestAC1TorqueRecordLinkedToVIN:
    """AC-1: _process_message links TorqueRecord node to VIN node on a valid event."""

    @pytest.mark.asyncio
    async def test_valid_event_triggers_neo4j_merge(self) -> None:
        """AC-1: A valid TorqueSubmittedEvent byte payload causes exactly one Neo4j MERGE."""
        from app.torque_consumer import _process_message

        driver = _FakeNeo4jDriver()
        with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
            await _process_message(json.dumps(_make_event()).encode())

        assert len(driver.session_instance.calls) == 1, (
            "Exactly one Cypher statement must be executed for a valid TorqueSubmittedEvent; "
            f"got {len(driver.session_instance.calls)}. "
            "The TorqueRecord node is not being created in Neo4j."
        )

    @pytest.mark.asyncio
    async def test_neo4j_statement_uses_merge_for_idempotency(self) -> None:
        """AC-1: the Cypher statement uses MERGE (not CREATE) so replay is idempotent."""
        from app.torque_consumer import _process_message

        driver = _FakeNeo4jDriver()
        with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
            await _process_message(json.dumps(_make_event()).encode())

        cypher = driver.session_instance.calls[0]["query"]
        assert "MERGE" in cypher.upper(), (
            "Cypher must use MERGE (not CREATE) to ensure TorqueRecord creation is idempotent; "
            "replaying the same event must not produce a second node"
        )

    @pytest.mark.asyncio
    async def test_neo4j_statement_carries_torque_log_id(self) -> None:
        """AC-1: torqueLogId is passed to the Cypher statement to key the MERGE."""
        from app.torque_consumer import _process_message

        driver = _FakeNeo4jDriver()
        with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
            await _process_message(json.dumps(_make_event()).encode())

        kwargs = driver.session_instance.calls[0]
        assert kwargs.get("torque_log_id") == _TORQUE_LOG_ID, (
            f"torque_log_id must be passed to Neo4j MERGE as '{_TORQUE_LOG_ID}'; "
            f"got {kwargs.get('torque_log_id')!r}"
        )

    @pytest.mark.asyncio
    async def test_neo4j_statement_carries_vin(self) -> None:
        """AC-1: vin is passed to the Cypher statement to link to the VIN node."""
        from app.torque_consumer import _process_message

        driver = _FakeNeo4jDriver()
        with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
            await _process_message(json.dumps(_make_event()).encode())

        kwargs = driver.session_instance.calls[0]
        assert kwargs.get("vin") == _VIN, (
            f"vin must be passed to Neo4j as '{_VIN}'; "
            f"got {kwargs.get('vin')!r}. "
            "Without the correct VIN the TorqueRecord is not linked to the right digital thread."
        )

    @pytest.mark.asyncio
    async def test_neo4j_statement_carries_fastener_point_id(self) -> None:
        """AC-1: fastenerPointId is preserved in the Neo4j write."""
        from app.torque_consumer import _process_message

        driver = _FakeNeo4jDriver()
        with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
            await _process_message(json.dumps(_make_event()).encode())

        kwargs = driver.session_instance.calls[0]
        assert kwargs.get("fastener_point_id") == _FASTENER_POINT_ID, (
            f"fastener_point_id must be '{_FASTENER_POINT_ID}'; "
            f"got {kwargs.get('fastener_point_id')!r}"
        )

    @pytest.mark.asyncio
    async def test_neo4j_statement_carries_torque_nm(self) -> None:
        """AC-1: torqueNm is preserved in the Neo4j write for quality record completeness."""
        from app.torque_consumer import _process_message

        driver = _FakeNeo4jDriver()
        with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
            await _process_message(json.dumps(_make_event()).encode())

        kwargs = driver.session_instance.calls[0]
        assert kwargs.get("torque_nm") == _TORQUE_NM, (
            f"torque_nm must be {_TORQUE_NM}; got {kwargs.get('torque_nm')!r}"
        )

    @pytest.mark.asyncio
    async def test_neo4j_statement_links_torque_log_relationship_to_vin_node(self) -> None:
        """AC-1: the Cypher creates a TORQUE_LOG relationship from VIN to TorqueRecord."""
        from app.torque_consumer import _process_message

        driver = _FakeNeo4jDriver()
        with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
            await _process_message(json.dumps(_make_event()).encode())

        cypher = driver.session_instance.calls[0]["query"]
        assert "TORQUE_LOG" in cypher, (
            "Cypher must create a TORQUE_LOG relationship; "
            "without it the torque record is not linked to the VIN digital thread"
        )


# ===========================================================================
# AC-1 / AC-2: replay idempotency — same torqueLogId produces no second node
# ===========================================================================

class TestAC1IdempotentReplay:
    """AC-1/AC-2: replaying the same event must be idempotent (MERGE, not double-INSERT)."""

    @pytest.mark.asyncio
    async def test_replaying_same_event_does_not_raise(self) -> None:
        """AC-1: the consumer must not raise on replay of the same torqueLogId."""
        from app.torque_consumer import _process_message

        driver = _FakeNeo4jDriver()
        raw = json.dumps(_make_event()).encode()

        with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
            await _process_message(raw)
            # Replay the same message a second time
            await _process_message(raw)

        # No exception must have been raised by this point
        assert len(driver.session_instance.calls) == 2, (
            "Two MERGE calls are expected (one per _process_message invocation); "
            "the MERGE keyword itself guarantees no duplicate node is created"
        )

    @pytest.mark.asyncio
    async def test_replay_with_edge_buffered_flag_still_processed(self) -> None:
        """AC-3: edgeBuffered=True on a replayed event must not skip processing.

        When the edge gateway reconnects and flushes its managed store, messages
        arrive with edgeBuffered=True.  The VDT consumer must still link the record.
        """
        from app.torque_consumer import _process_message

        driver = _FakeNeo4jDriver()
        event = _make_event(edgeBuffered=True)

        with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
            await _process_message(json.dumps(event).encode())

        assert len(driver.session_instance.calls) == 1, (
            "A message with edgeBuffered=True must still produce one Neo4j MERGE call; "
            "the VDT consumer must not skip buffered-replay messages"
        )


# ===========================================================================
# AC-1: malformed / incomplete events are skipped without crashing the consumer
# ===========================================================================

class TestMalformedEventsSkipped:
    """AC-1: consumer must absorb invalid messages so the loop never dies."""

    @pytest.mark.asyncio
    async def test_invalid_json_produces_no_neo4j_call(self) -> None:
        """AC-1: malformed JSON is logged and skipped; no Neo4j call is made."""
        from app.torque_consumer import _process_message

        driver = _FakeNeo4jDriver()
        with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
            await _process_message(b"not json {{{{")

        assert len(driver.session_instance.calls) == 0, (
            "Malformed JSON must not produce any Neo4j calls; "
            "the consumer must skip it and keep running"
        )

    @pytest.mark.asyncio
    async def test_missing_torque_log_id_produces_no_neo4j_call(self) -> None:
        """AC-1: event missing torqueLogId is skipped (no key for the MERGE)."""
        from app.torque_consumer import _process_message

        driver = _FakeNeo4jDriver()
        event = _make_event()
        del event["torqueLogId"]

        with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
            await _process_message(json.dumps(event).encode())

        assert len(driver.session_instance.calls) == 0, (
            "An event without torqueLogId must be skipped — no key for the MERGE; "
            "the consumer must not crash"
        )

    @pytest.mark.asyncio
    async def test_missing_vin_produces_no_neo4j_call(self) -> None:
        """AC-1: event missing vin is skipped (cannot link to VIN node)."""
        from app.torque_consumer import _process_message

        driver = _FakeNeo4jDriver()
        event = _make_event()
        del event["vin"]

        with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
            await _process_message(json.dumps(event).encode())

        assert len(driver.session_instance.calls) == 0, (
            "An event without vin must be skipped — cannot link to a VIN node; "
            "the consumer must not crash"
        )
