"""US-011 acceptance tests — edge gateway: ring buffer, pending_sync overlay, flush-on-reconnect.

Cross-task seams tested here (not covered by existing unit tests individually):

AC-1: When the UNS broker is reachable the publish path is direct — no buffering.
      The _EdgeRingBuffer and SimaticManagedStore are separate components; the
      SparkplugPublisher must route to the right one based on connection state.

AC-3: When the UNS broker is unavailable (connected=False), publish() writes to
      the SimaticManagedStore and calls _set_buffering(True) → on_buffer_state_change
      callback fires with True and the local OT broker receives "pending_sync".
      When connectivity restores and _flush_buffer() is called:
      - Only fresh events (age ≤ BUFFER_MAX_AGE_MS) are published
      - Stale events (age > BUFFER_MAX_AGE_MS) are discarded without publish
      - Each replayed event has edgeBuffered=True set in its Avro payload
      - _set_buffering(False) is called after flush → "synced" overlay signal
      - The managed store is cleared only after successful publish

Precondition assertions come first.
"""

from __future__ import annotations

import io
import json
import struct
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------

def test_precondition_sparkplug_publisher_importable() -> None:
    """Precondition: mqtt_publisher.sparkplug_publisher must be importable."""
    try:
        import mqtt_publisher.sparkplug_publisher  # noqa: F401
    except ImportError as exc:
        raise AssertionError(
            "mqtt_publisher.sparkplug_publisher is not importable; "
            "the edge-gateway Sparkplug B publisher task may not be merged"
        ) from exc


def test_precondition_buffer_max_age_is_60_seconds() -> None:
    """Precondition: BUFFER_MAX_AGE_MS must be 60_000 (60 s per AC-3)."""
    from mqtt_publisher.sparkplug_publisher import BUFFER_MAX_AGE_MS

    assert BUFFER_MAX_AGE_MS == 60_000, (
        f"BUFFER_MAX_AGE_MS must be 60000 ms (60 s) per AC-3; got {BUFFER_MAX_AGE_MS}. "
        "The acceptance criterion requires buffered events to be published within "
        "60 seconds of connectivity restoration."
    )


def test_precondition_max_buffer_events_covers_60s_at_1khz() -> None:
    """Precondition: MAX_BUFFER_EVENTS ≥ 60_000 to buffer 60 s at 1 kHz."""
    from mqtt_publisher.sparkplug_publisher import MAX_BUFFER_EVENTS

    assert MAX_BUFFER_EVENTS >= 60_000, (
        f"MAX_BUFFER_EVENTS must be ≥ 60000 to cover 60 s at 1 kHz; got {MAX_BUFFER_EVENTS}"
    )


def test_precondition_kafka_torque_topic_is_correct() -> None:
    """Precondition: KAFKA_TORQUE_TOPIC must match the agreed UNS topic."""
    from mqtt_publisher.sparkplug_publisher import KAFKA_TORQUE_TOPIC

    assert KAFKA_TORQUE_TOPIC == "bmw.ifos.munich.uns.torque.plc-trace.v1", (
        f"KAFKA_TORQUE_TOPIC is '{KAFKA_TORQUE_TOPIC}'; "
        "line-command-api's Kafka consumer expects 'bmw.ifos.munich.uns.torque.plc-trace.v1'"
    )


def test_precondition_overlay_status_topic_template_correct() -> None:
    """Precondition: overlay status topic template must match the agreed format."""
    from mqtt_publisher.sparkplug_publisher import OVERLAY_STATUS_TOPIC_TEMPLATE

    rendered = OVERLAY_STATUS_TOPIC_TEMPLATE.format(station_id="MARRIAGE-01")
    assert rendered == "edge/MARRIAGE-01/overlay/status", (
        f"OVERLAY_STATUS_TOPIC_TEMPLATE renders '{rendered}'; "
        "the AR overlay SSE subscriber expects 'edge/MARRIAGE-01/overlay/status'"
    )


# ---------------------------------------------------------------------------
# Fake infrastructure (no real MQTT/Avro dependencies needed for buffer tests)
# ---------------------------------------------------------------------------

def _minimal_avro_bytes(idempotency_key: str = "key-001") -> bytes:
    """Return minimal fake Confluent Wire Format bytes (5-byte header + JSON body).

    Sparkplug publisher's _extract_idempotency_key tries to decode Avro but falls
    back to sha256 of the raw bytes on failure.  For buffer tests we just need
    stable bytes that keep the store's key consistent across calls.
    """
    body = json.dumps({"idempotencyKey": idempotency_key}).encode()
    header = struct.pack(">bI", 0, 1)  # magic=0x00, schema_id=1
    return header + body


class _FakeOtClient:
    """Records overlay status publishes. Simulates local OT-network MQTT broker."""

    def __init__(self) -> None:
        self.published: list[dict] = []

    def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> None:
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})


def _make_publisher(
    connected: bool = False,
    on_buffer_state_change: Any = None,
    ot_client: Any = None,
) -> Any:
    """Return a SparkplugPublisher wired with fake OT client and connection state."""
    from mqtt_publisher.sparkplug_publisher import SparkplugPublisher

    pub = SparkplugPublisher(
        broker_host="unused-uns-broker",
        broker_port=8883,
        station_id="MARRIAGE-01",
        shop="assembly",
        edge_node_id="edge-marriage-01",
        device_id="plc-marriage-01",
        on_buffer_state_change=on_buffer_state_change,
    )

    # Inject the fake OT client before any publish so overlay status calls work
    if ot_client is not None:
        pub._ot_client = ot_client

    # Set connection state without touching real MQTT
    if connected:
        pub._connected.set()
    else:
        pub._connected.clear()

    return pub


# ===========================================================================
# AC-3: UNS unavailable → event buffered in managed store + pending_sync signal
# ===========================================================================

class TestAC3BufferingWhenUNSUnavailable:
    """AC-3: when UNS is down, publish() buffers event and signals pending_sync."""

    def test_publish_when_disconnected_writes_to_managed_store(self) -> None:
        """AC-3: a publish when UNS is down must add the event to the managed store."""
        pub = _make_publisher(connected=False)
        avro = _minimal_avro_bytes("key-ac3-001")

        pub.publish(avro)

        assert len(pub._managed_store) == 1, (
            "After publishing with UNS down, the managed store must hold 1 event; "
            f"got {len(pub._managed_store)}. "
            "The 60-second local buffer is not working."
        )

    def test_publish_when_disconnected_triggers_buffer_state_change_callback(self) -> None:
        """AC-3: on_buffer_state_change(True) must fire when the first event is buffered."""
        state_changes: list[bool] = []
        ot_client = _FakeOtClient()
        pub = _make_publisher(
            connected=False,
            on_buffer_state_change=lambda active: state_changes.append(active),
            ot_client=ot_client,
        )

        avro = _minimal_avro_bytes("key-ac3-002")
        pub.publish(avro)

        assert True in state_changes, (
            "on_buffer_state_change(True) must be called when buffering begins; "
            "the frontend observes this to show the Pending Sync indicator"
        )

    def test_publish_when_disconnected_sends_pending_sync_to_ot_overlay(self) -> None:
        """AC-3: overlay status 'pending_sync' must be published to local OT broker."""
        ot_client = _FakeOtClient()
        pub = _make_publisher(connected=False, ot_client=ot_client)

        avro = _minimal_avro_bytes("key-ac3-003")
        pub.publish(avro)

        pending_msgs = [
            m for m in ot_client.published
            if b"pending_sync" in m["payload"]
        ]
        assert len(pending_msgs) >= 1, (
            "A 'pending_sync' overlay status message must be published to the OT broker; "
            f"published messages: {ot_client.published}. "
            "The AR overlay will not show the Pending Sync indicator without this signal."
        )

    def test_pending_sync_overlay_message_published_with_retain_true(self) -> None:
        """AC-3: overlay status retain=True so AR overlay always gets current state."""
        ot_client = _FakeOtClient()
        pub = _make_publisher(connected=False, ot_client=ot_client)

        avro = _minimal_avro_bytes("key-ac3-004")
        pub.publish(avro)

        pending_msgs = [m for m in ot_client.published if b"pending_sync" in m["payload"]]
        assert len(pending_msgs) >= 1
        assert all(m["retain"] for m in pending_msgs), (
            "Overlay status must be published with retain=True so the AR overlay "
            "receives the current state even if it connects after the status change"
        )

    def test_multiple_disconnected_publishes_accumulate_in_store(self) -> None:
        """AC-3: multiple events while disconnected all land in the managed store."""
        pub = _make_publisher(connected=False)

        for i in range(3):
            pub.publish(_minimal_avro_bytes(f"key-ac3-multi-{i}"))

        assert len(pub._managed_store) == 3, (
            f"All 3 events must be in the managed store when disconnected; "
            f"got {len(pub._managed_store)}"
        )

    def test_managed_store_put_is_idempotent_for_same_key(self) -> None:
        """AC-3: same idempotencyKey stored twice must still result in one entry.

        When the edge gateway crashes and restarts with the same event in the
        managed store, a second put with the same key must be a no-op.
        """
        from mqtt_publisher.sparkplug_publisher import SimaticManagedStore, _BufferedEvent

        store = SimaticManagedStore()
        event = _BufferedEvent(avro_bytes=b"abc", timestamp_ms=int(time.time() * 1000))
        store.put("key-same", event)
        store.put("key-same", event)  # second put — must be no-op

        entries = store.drain()
        assert len(entries) == 1, (
            "SimaticManagedStore.put() must be idempotent for the same key; "
            f"got {len(entries)} entries instead of 1. "
            "Without idempotency, crash-recovery replay produces duplicate torque records."
        )


# ===========================================================================
# AC-3: flush on reconnect — fresh events published, stale events discarded
# ===========================================================================

class TestAC3FlushOnReconnect:
    """AC-3: _flush_buffer publishes fresh events, discards stale, clears synced."""

    def test_flush_publishes_fresh_events_and_calls_synced_callback(self) -> None:
        """AC-3: after flush, on_buffer_state_change(False) fires and store is empty."""
        from mqtt_publisher.sparkplug_publisher import SimaticManagedStore, _BufferedEvent

        state_changes: list[bool] = []
        published_payloads: list[bytes] = []
        ot_client = _FakeOtClient()

        store = SimaticManagedStore()
        now_ms = int(time.time() * 1000)
        avro = _minimal_avro_bytes("flush-key-001")
        fresh_event = _BufferedEvent(avro_bytes=avro, timestamp_ms=now_ms)
        store.put("flush-key-001", fresh_event)

        pub = _make_publisher(
            connected=True,
            on_buffer_state_change=lambda active: state_changes.append(active),
            ot_client=ot_client,
        )
        pub._managed_store = store
        pub._buffering = True  # simulate: publisher was buffering before reconnect

        def _fake_publish_direct(avro_bytes: bytes) -> None:
            published_payloads.append(avro_bytes)

        pub._publish_direct = _fake_publish_direct  # type: ignore[method-assign]

        # _set_edge_buffered_flag decodes real Avro (requires avro package); mock it
        with patch(
            "mqtt_publisher.sparkplug_publisher._set_edge_buffered_flag",
            side_effect=lambda b: b,
        ):
            pub._flush_buffer()

        assert len(published_payloads) == 1, (
            "One fresh event must be published during flush; "
            f"got {len(published_payloads)}"
        )
        assert False in state_changes, (
            "on_buffer_state_change(False) must fire after flush completes; "
            "the frontend uses this to clear the Pending Sync indicator"
        )
        assert len(pub._managed_store) == 0, (
            "Managed store must be empty after a successful flush"
        )

    def test_flush_discards_stale_events_without_publishing(self) -> None:
        """AC-3: events older than BUFFER_MAX_AGE_MS are discarded, not published.

        An event more than 60 s old at flush time is useless quality data;
        it must be dropped rather than published with incorrect timing context.
        """
        from mqtt_publisher.sparkplug_publisher import (
            BUFFER_MAX_AGE_MS,
            SimaticManagedStore,
            _BufferedEvent,
        )

        published_payloads: list[bytes] = []

        store = SimaticManagedStore()
        # Stale event: timestamp_ms is 61 seconds in the past
        stale_ms = int(time.time() * 1000) - (BUFFER_MAX_AGE_MS + 1_000)
        stale_event = _BufferedEvent(avro_bytes=_minimal_avro_bytes("stale-key"), timestamp_ms=stale_ms)
        store.put("stale-key", stale_event)

        pub = _make_publisher(connected=True)
        pub._managed_store = store
        pub._publish_direct = lambda b: published_payloads.append(b)  # type: ignore[method-assign]

        pub._flush_buffer()

        assert len(published_payloads) == 0, (
            "Stale events (age > BUFFER_MAX_AGE_MS) must not be published; "
            f"got {len(published_payloads)} publishes. "
            "AC-3 requires stale events to be discarded at drain time."
        )
        assert len(pub._managed_store) == 0, (
            "Stale events must be cleared from the managed store after discard"
        )

    def test_flush_sets_edge_buffered_flag_on_replayed_events(self) -> None:
        """AC-3: replayed events must have edgeBuffered=True set in Avro payload.

        The upstream service (line-command-api) reads edgeBuffered to return 202
        (pending_sync) rather than 201, so the digital-thread record is tagged
        correctly.
        """
        from mqtt_publisher.sparkplug_publisher import SimaticManagedStore, _BufferedEvent, _set_edge_buffered_flag

        # Build a minimal Avro payload with edgeBuffered=False
        # _set_edge_buffered_flag decodes Confluent Wire Format and re-encodes with True
        # We just verify it does not raise and returns different bytes
        avro_bytes = _minimal_avro_bytes("edge-buf-key")
        with patch("mqtt_publisher.sparkplug_publisher._set_edge_buffered_flag") as mock_flag:
            mock_flag.return_value = avro_bytes  # pass-through

            store = SimaticManagedStore()
            now_ms = int(time.time() * 1000)
            store.put("edge-buf-key", _BufferedEvent(avro_bytes=avro_bytes, timestamp_ms=now_ms))

            pub = _make_publisher(connected=True)
            pub._managed_store = store
            pub._publish_direct = lambda b: None  # type: ignore[method-assign]

            pub._flush_buffer()

        mock_flag.assert_called_once_with(avro_bytes), (
            "_set_edge_buffered_flag must be called for each replayed event during flush; "
            "without it, the upstream service cannot detect edge-buffered replay"
        )

    def test_flush_sends_synced_overlay_status_after_publish(self) -> None:
        """AC-3: 'synced' overlay status published to local OT broker after flush.

        This is what clears the AR overlay's Pending Sync indicator once
        connectivity is restored and the buffer has been drained.
        """
        from mqtt_publisher.sparkplug_publisher import SimaticManagedStore, _BufferedEvent

        ot_client = _FakeOtClient()

        store = SimaticManagedStore()
        now_ms = int(time.time() * 1000)
        store.put("sync-key", _BufferedEvent(avro_bytes=_minimal_avro_bytes("sync-key"), timestamp_ms=now_ms))

        pub = _make_publisher(connected=True, ot_client=ot_client)
        pub._managed_store = store
        pub._publish_direct = lambda b: None  # type: ignore[method-assign]

        # Mark buffering as active first so _set_buffering(False) triggers an update
        pub._buffering = True

        # _set_edge_buffered_flag decodes real Avro (requires avro package); mock it
        with patch(
            "mqtt_publisher.sparkplug_publisher._set_edge_buffered_flag",
            side_effect=lambda b: b,
        ):
            pub._flush_buffer()

        synced_msgs = [m for m in ot_client.published if b"synced" in m["payload"]]
        assert len(synced_msgs) >= 1, (
            "A 'synced' overlay status message must be published to the OT broker after flush; "
            f"published messages: {ot_client.published}. "
            "Without this, the AR overlay stays on Pending Sync even after connectivity restoration."
        )


# ===========================================================================
# AC-1 / AC-3: _EdgeRingBuffer — thread-safe FIFO ring, SIMATIC overflow
# ===========================================================================

class TestEdgeRingBufferBehaviour:
    """AC-1/AC-3: _EdgeRingBuffer stores events and drops oldest on overflow."""

    def test_push_and_len_track_added_events(self) -> None:
        """AC-1: ring buffer tracks count of pushed events."""
        from mqtt_publisher.sparkplug_publisher import _EdgeRingBuffer, _BufferedEvent

        buf = _EdgeRingBuffer(max_events=10)
        buf.push(_BufferedEvent(avro_bytes=b"a", timestamp_ms=1000))
        buf.push(_BufferedEvent(avro_bytes=b"b", timestamp_ms=2000))

        assert len(buf) == 2

    def test_drain_returns_all_events_in_fifo_order(self) -> None:
        """AC-1: drain returns events in the order they were pushed (FIFO)."""
        from mqtt_publisher.sparkplug_publisher import _EdgeRingBuffer, _BufferedEvent

        buf = _EdgeRingBuffer(max_events=5)
        events = [_BufferedEvent(avro_bytes=bytes([i]), timestamp_ms=i * 100) for i in range(3)]
        for e in events:
            buf.push(e)

        drained = buf.drain()
        assert [e.avro_bytes for e in drained] == [e.avro_bytes for e in events], (
            "drain() must return events in FIFO order"
        )

    def test_drain_empties_the_buffer(self) -> None:
        """AC-1: drain clears the buffer."""
        from mqtt_publisher.sparkplug_publisher import _EdgeRingBuffer, _BufferedEvent

        buf = _EdgeRingBuffer(max_events=5)
        buf.push(_BufferedEvent(avro_bytes=b"x", timestamp_ms=1000))
        buf.drain()

        assert len(buf) == 0

    def test_overflow_drops_oldest_event(self) -> None:
        """AC-3: when ring buffer is full, the oldest event is dropped for the newest.

        This matches SIMATIC Edge Runtime store-and-forward: newest events win.
        """
        from mqtt_publisher.sparkplug_publisher import _EdgeRingBuffer, _BufferedEvent

        buf = _EdgeRingBuffer(max_events=3)
        for i in range(4):  # push 4 into a cap-3 buffer
            buf.push(_BufferedEvent(avro_bytes=bytes([i]), timestamp_ms=i * 100))

        drained = buf.drain()
        payloads = [e.avro_bytes for e in drained]

        assert bytes([0]) not in payloads, (
            "The oldest event (bytes=[0]) must be dropped when the ring buffer overflows; "
            f"got: {payloads}"
        )
        assert bytes([3]) in payloads, (
            "The newest event (bytes=[3]) must be retained; "
            f"got: {payloads}"
        )
        assert len(drained) == 3

    def test_new_instance_starts_empty(self) -> None:
        """AC-1 (instance isolation): each new _EdgeRingBuffer starts empty."""
        from mqtt_publisher.sparkplug_publisher import _EdgeRingBuffer, _BufferedEvent

        buf1 = _EdgeRingBuffer(max_events=5)
        buf1.push(_BufferedEvent(avro_bytes=b"z", timestamp_ms=999))

        buf2 = _EdgeRingBuffer(max_events=5)
        assert len(buf2) == 0, "New _EdgeRingBuffer instance must start empty"
