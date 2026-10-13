"""Tests for the TorqueSubmittedEvent Kafka consumer.

Verifies:
  - A TorqueRecord node is MERGEd with the correct properties.
  - A TORQUE_LOG relationship is created from the VIN node to the TorqueRecord.
  - Replaying the same event is idempotent (MERGE produces no duplicate node).
  - Messages with missing required fields are skipped without raising.
  - Neo4j write timeout is handled gracefully (logged, not raised).
  - Consumer-level instance isolation: each FakeNeo4jSession starts clean.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.torque_consumer import _NEO4J_WRITE_TIMEOUT_S, _process_message, _neo4j_link_torque

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_VIN = "WBA3A5C55FK123456"
_TORQUE_LOG_ID = str(uuid.uuid4())
_STATION_ID = "MARRIAGE-01"
_FASTENER_POINT_ID = "FP-001"
_TORQUE_NM = 42.5
_ANGLES_DEG = 120.0
_PLC_TIMESTAMP = "2026-10-12T06:00:00Z"
_UNS_PUBLISHED_AT = "2026-10-12T06:00:00.500Z"


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
# Fake Neo4j infrastructure
# ---------------------------------------------------------------------------


class FakeNeo4jSession:
    """Records every Cypher run() call; each instance starts with an empty list."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, query: str, **kwargs: Any) -> None:
        self.calls.append({"query": query, **kwargs})

    async def __aenter__(self) -> "FakeNeo4jSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeNeo4jDriver:
    """Returns the same FakeNeo4jSession for all session() calls on one instance."""

    def __init__(self) -> None:
        self.session_instance = FakeNeo4jSession()

    def session(self) -> FakeNeo4jSession:
        return self.session_instance

    async def __aenter__(self) -> "FakeNeo4jDriver":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Instance isolation tests
# ---------------------------------------------------------------------------


def test_fake_neo4j_session_instance_isolation() -> None:
    """New FakeNeo4jSession instances must start with zero calls."""
    s1 = FakeNeo4jSession()
    asyncio.run(s1.run("MATCH (n) RETURN n", x=1))
    s2 = FakeNeo4jSession()
    assert len(s2.calls) == 0, "New instance must start with calls=[]"


def test_fake_neo4j_driver_instance_isolation() -> None:
    """Two FakeNeo4jDriver instances must not share session state."""
    d1 = FakeNeo4jDriver()
    d2 = FakeNeo4jDriver()
    asyncio.run(d1.session().run("MATCH (n) RETURN n"))
    assert len(d2.session_instance.calls) == 0


# ---------------------------------------------------------------------------
# _neo4j_link_torque: happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_neo4j_link_torque_calls_run_once() -> None:
    """_neo4j_link_torque must issue exactly one Cypher statement."""
    driver = FakeNeo4jDriver()
    with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
        await _neo4j_link_torque(_make_event())
    assert len(driver.session_instance.calls) == 1


@pytest.mark.asyncio
async def test_neo4j_link_torque_passes_torque_log_id() -> None:
    driver = FakeNeo4jDriver()
    with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
        await _neo4j_link_torque(_make_event())
    call = driver.session_instance.calls[0]
    assert call["torque_log_id"] == _TORQUE_LOG_ID


@pytest.mark.asyncio
async def test_neo4j_link_torque_passes_vin() -> None:
    driver = FakeNeo4jDriver()
    with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
        await _neo4j_link_torque(_make_event())
    call = driver.session_instance.calls[0]
    assert call["vin"] == _VIN


@pytest.mark.asyncio
async def test_neo4j_link_torque_passes_fastener_properties() -> None:
    driver = FakeNeo4jDriver()
    with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
        await _neo4j_link_torque(_make_event())
    call = driver.session_instance.calls[0]
    assert call["fastener_point_id"] == _FASTENER_POINT_ID
    assert call["torque_nm"] == _TORQUE_NM
    assert call["angles_deg"] == _ANGLES_DEG
    assert call["station_id"] == _STATION_ID
    assert call["plc_timestamp"] == _PLC_TIMESTAMP
    assert call["uns_published_at"] == _UNS_PUBLISHED_AT


# ---------------------------------------------------------------------------
# Idempotency: replaying the same event must not create a duplicate node.
# The MERGE in the Cypher guarantees this at the DB level; the test asserts
# that _neo4j_link_torque does not raise on a second identical call.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_neo4j_link_torque_idempotent_replay() -> None:
    """Replaying the same torqueLogId must not raise; MERGE is idempotent."""
    driver = FakeNeo4jDriver()
    event = _make_event()
    with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
        await _neo4j_link_torque(event)
        await _neo4j_link_torque(event)
    # Both calls must complete without error; MERGE semantics prevent duplication
    assert len(driver.session_instance.calls) == 2


# ---------------------------------------------------------------------------
# _process_message: full message pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_message_calls_neo4j_link() -> None:
    """_process_message on a valid JSON payload must invoke _neo4j_link_torque once."""
    driver = FakeNeo4jDriver()
    payload = json.dumps(_make_event()).encode()
    with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
        await _process_message(payload)
    assert len(driver.session_instance.calls) == 1


@pytest.mark.asyncio
async def test_process_message_invalid_json_skipped() -> None:
    """Malformed JSON must be skipped without raising."""
    driver = FakeNeo4jDriver()
    with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
        await _process_message(b"not valid json{{{")
    assert len(driver.session_instance.calls) == 0


@pytest.mark.asyncio
async def test_process_message_missing_torque_log_id_skipped() -> None:
    """Event missing torqueLogId must be skipped without raising."""
    event = _make_event()
    del event["torqueLogId"]
    driver = FakeNeo4jDriver()
    with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
        await _process_message(json.dumps(event).encode())
    assert len(driver.session_instance.calls) == 0


@pytest.mark.asyncio
async def test_process_message_missing_vin_skipped() -> None:
    """Event missing vin must be skipped without raising."""
    event = _make_event()
    del event["vin"]
    driver = FakeNeo4jDriver()
    with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
        await _process_message(json.dumps(event).encode())
    assert len(driver.session_instance.calls) == 0


@pytest.mark.asyncio
async def test_process_message_neo4j_timeout_does_not_raise() -> None:
    """A Neo4j write that exceeds the timeout must be caught and logged, not propagated."""

    async def _slow_link(_event: dict[str, Any]) -> None:
        await asyncio.sleep(10)

    with patch("app.torque_consumer._neo4j_link_torque", side_effect=_slow_link):
        with patch("app.torque_consumer._NEO4J_WRITE_TIMEOUT_S", 0.05):
            await _process_message(json.dumps(_make_event()).encode())
    # No exception propagated — test passes if it reaches here


@pytest.mark.asyncio
async def test_process_message_neo4j_os_error_does_not_raise() -> None:
    """An OSError during Neo4j write must be caught and logged, not propagated."""

    async def _failing(_event: dict[str, Any]) -> None:
        raise OSError("connection refused")

    with patch("app.torque_consumer._neo4j_link_torque", side_effect=_failing):
        await _process_message(json.dumps(_make_event()).encode())


@pytest.mark.asyncio
async def test_process_message_neo4j_runtime_error_does_not_raise() -> None:
    """A RuntimeError during Neo4j write must be caught and logged, not propagated."""

    async def _failing(_event: dict[str, Any]) -> None:
        raise RuntimeError("write failed")

    with patch("app.torque_consumer._neo4j_link_torque", side_effect=_failing):
        await _process_message(json.dumps(_make_event()).encode())


# ---------------------------------------------------------------------------
# Cypher uses MERGE — verify query contains MERGE keyword
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_neo4j_link_torque_uses_merge() -> None:
    """Cypher statement must use MERGE to guarantee idempotency."""
    driver = FakeNeo4jDriver()
    with patch("neo4j.AsyncGraphDatabase.driver", return_value=driver):
        await _neo4j_link_torque(_make_event())
    query = driver.session_instance.calls[0]["query"]
    assert "MERGE" in query.upper()
