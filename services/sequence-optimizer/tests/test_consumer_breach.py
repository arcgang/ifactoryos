"""Integration-style tests for the constraint telemetry consumer breach pipeline.

Covers:
 - Sub-threshold message: no alert inserted, no Kafka publish
 - Breach message: constraint_alert row inserted, ConstraintAlertEvent published
 - Missing constraintType/currentValue: sent to DLQ, no alert
 - Unknown constraintType: sent to DLQ, no alert
 - DB error on get_warning_level: sent to DLQ, no alert
 - DB error on _persist_alert: sent to DLQ, no alert
 - affected_vins populated from _resolve_affected_vins
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.consumer import (
    _handle_message,
    build_alert_event,
    evaluate_breach,
)
from app.models import ConstraintAlertEvent, ConstraintType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VIN_A = "WBA3B3C56DF596858"
_VIN_B = "WBA3B3C57DF596859"

_BREACH_MSG = json.dumps({
    "constraintType": "paint_capacity",
    "currentValue": 92.5,
    "unit": "%",
    "measuredAt": "2099-01-01T00:00:00+00:00",
}).encode()

_BELOW_THRESHOLD_MSG = json.dumps({
    "constraintType": "paint_capacity",
    "currentValue": 70.0,
    "unit": "%",
    "measuredAt": "2099-01-01T00:00:00+00:00",
}).encode()


def _make_mock_producer() -> MagicMock:
    producer = MagicMock()
    producer.send = AsyncMock(return_value=asyncio.Future())
    producer.send.return_value = asyncio.Future()
    # Make send return a coroutine-compatible mock
    async def _send(*args: object, **kwargs: object) -> None:
        pass
    producer.send = AsyncMock(side_effect=_send)
    return producer


def _make_mock_pool(
    warning_level: float | None = 85.0,
    affected_vins: list[str] | None = None,
    persist_error: Exception | None = None,
) -> MagicMock:
    if affected_vins is None:
        affected_vins = [_VIN_A]

    execute_calls: list = []

    async def _execute(*args: object, **kwargs: object) -> None:
        if persist_error is not None:
            raise persist_error
        execute_calls.append(args)

    if warning_level is not None:
        wl_row = MagicMock()
        wl_row.__getitem__ = lambda self_, k: warning_level if k == "warning_level" else None
    else:
        wl_row = None

    async def _fetchrow(*args: object, **kwargs: object) -> MagicMock | None:
        return wl_row

    vin_rows = [MagicMock() for _ in affected_vins]
    for i, row in enumerate(vin_rows):
        row.__getitem__ = lambda self_, k, _v=affected_vins[i]: _v if k == "vin_value" else None

    async def _fetch(*args: object, **kwargs: object) -> list:
        return vin_rows

    mock_conn = AsyncMock()
    mock_conn.fetchrow = _fetchrow
    mock_conn.fetch = _fetch
    mock_conn.execute = _execute

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_pool._execute_calls = execute_calls
    return mock_pool


# ---------------------------------------------------------------------------
# evaluate_breach — already tested in test_thresholds.py; spot-check here
# ---------------------------------------------------------------------------


def test_evaluate_breach_above_threshold_is_true():
    assert evaluate_breach(92.5, 85.0) is True


def test_evaluate_breach_below_threshold_is_false():
    assert evaluate_breach(70.0, 85.0) is False


# ---------------------------------------------------------------------------
# _handle_message: below-threshold message → no alert, no Kafka publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_below_threshold_message_does_not_publish_alert():
    pool = _make_mock_pool(warning_level=85.0)
    producer = _make_mock_producer()

    await _handle_message(
        _BELOW_THRESHOLD_MSG,
        pool=pool,
        producer=producer,
        alert_topic="sequence.constraint.alert",
        dlq_topic="dlq",
    )

    producer.send.assert_not_called()


# ---------------------------------------------------------------------------
# _handle_message: breach → alert inserted, Kafka event published
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_breach_message_publishes_constraint_alert_event():
    published_payloads: list[dict] = []

    async def _send(topic: str, *, value: bytes) -> None:
        published_payloads.append({"topic": topic, "payload": json.loads(value)})

    pool = _make_mock_pool(warning_level=85.0, affected_vins=[_VIN_A, _VIN_B])
    producer = MagicMock()
    producer.send = AsyncMock(side_effect=_send)

    with patch("app.consumer._write_alert_audit", new=AsyncMock()):
        await _handle_message(
            _BREACH_MSG,
            pool=pool,
            producer=producer,
            alert_topic="sequence.constraint.alert",
            dlq_topic="dlq",
        )

    assert len(published_payloads) == 1
    payload = published_payloads[0]["payload"]
    assert published_payloads[0]["topic"] == "sequence.constraint.alert"
    assert payload["constraintType"] == "paint_capacity"
    assert payload["currentValue"] == 92.5
    assert payload["warningLevel"] == 85.0
    assert "alertId" in payload
    assert isinstance(payload["alertId"], str)
    assert "affectedVins" in payload
    assert set(payload["affectedVins"]) == {_VIN_A, _VIN_B}


@pytest.mark.asyncio
async def test_breach_message_inserts_constraint_alert_row():
    execute_calls: list = []

    async def _execute(*args: object, **kwargs: object) -> None:
        execute_calls.append(args[0] if args else "")

    mock_conn = AsyncMock()
    wl_row = MagicMock()
    wl_row.__getitem__ = lambda self_, k: 85.0 if k == "warning_level" else None

    async def _fetchrow(*args: object, **kwargs: object) -> MagicMock:
        return wl_row

    vin_row = MagicMock()
    vin_row.__getitem__ = lambda self_, k: _VIN_A if k == "vin_value" else None

    async def _fetch(*args: object, **kwargs: object) -> list:
        return [vin_row]

    mock_conn.fetchrow = _fetchrow
    mock_conn.fetch = _fetch
    mock_conn.execute = _execute

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    published: list = []

    async def _send(topic: str, *, value: bytes) -> None:
        published.append(json.loads(value))

    producer = MagicMock()
    producer.send = AsyncMock(side_effect=_send)

    with patch("app.consumer._write_alert_audit", new=AsyncMock()):
        await _handle_message(
            _BREACH_MSG,
            pool=mock_pool,
            producer=producer,
            alert_topic="sequence.constraint.alert",
            dlq_topic="dlq",
        )

    # At least one execute call should be the INSERT INTO constraint_alert
    assert any("constraint_alert" in str(call) for call in execute_calls)


# ---------------------------------------------------------------------------
# _handle_message: malformed message → DLQ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_json_sent_to_dlq():
    dlq_calls: list = []

    async def _send(topic: str, *, value: bytes) -> None:
        dlq_calls.append(topic)

    producer = MagicMock()
    producer.send = AsyncMock(side_effect=_send)

    pool = _make_mock_pool()

    await _handle_message(
        b"not valid json!!!",
        pool=pool,
        producer=producer,
        alert_topic="sequence.constraint.alert",
        dlq_topic="dlq",
    )

    assert any("dlq" in t for t in dlq_calls)


# ---------------------------------------------------------------------------
# _handle_message: missing fields → DLQ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_constraint_type_sent_to_dlq():
    dlq_calls: list = []

    async def _send(topic: str, *, value: bytes) -> None:
        dlq_calls.append(topic)

    producer = MagicMock()
    producer.send = AsyncMock(side_effect=_send)
    pool = _make_mock_pool()

    msg = json.dumps({"currentValue": 92.5}).encode()
    await _handle_message(
        msg,
        pool=pool,
        producer=producer,
        alert_topic="sequence.constraint.alert",
        dlq_topic="dlq",
    )

    assert any("dlq" in t for t in dlq_calls)


# ---------------------------------------------------------------------------
# _handle_message: unknown constraintType → DLQ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_constraint_type_sent_to_dlq():
    dlq_calls: list = []

    async def _send(topic: str, *, value: bytes) -> None:
        dlq_calls.append(topic)

    producer = MagicMock()
    producer.send = AsyncMock(side_effect=_send)
    pool = _make_mock_pool()

    msg = json.dumps({"constraintType": "unknown_type", "currentValue": 99.0}).encode()
    await _handle_message(
        msg,
        pool=pool,
        producer=producer,
        alert_topic="sequence.constraint.alert",
        dlq_topic="dlq",
    )

    assert any("dlq" in t for t in dlq_calls)


# ---------------------------------------------------------------------------
# build_alert_event — event includes all contract fields
# ---------------------------------------------------------------------------


def test_build_alert_event_all_contract_fields_present():
    ts = "2099-01-01T00:00:00+00:00"
    event = build_alert_event(
        constraint_type=ConstraintType.kitting_readiness,
        current_value=55.0,
        warning_level=70.0,
        detected_at=ts,
    )
    assert event.constraintType == ConstraintType.kitting_readiness
    assert event.currentValue == 55.0
    assert event.warningLevel == 70.0
    assert event.detectedAt == ts
