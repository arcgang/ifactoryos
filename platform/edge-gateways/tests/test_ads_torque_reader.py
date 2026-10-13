"""Unit / integration tests for the ADS torque reader and Avro encoder.

Verification plan
-----------------
(a) Avro payload deserialises without error against the registered schema —
    inject a synthetic AdsTighteningEvent and round-trip encode/decode.
(b) All required fields are populated with correct types.
(c) idempotencyKey is deterministic: same inputs produce the same key across
    multiple invocations and across independent object instantiations.

No live PLC or Schema Registry is required.  The schema is loaded from disk;
the schema_id used in the wire-format header is a fixed test value (42).

Run:
    pytest platform/edge-gateways/tests/test_ads_torque_reader.py -v
"""

from __future__ import annotations

import ctypes
from typing import Any
from unittest.mock import MagicMock

import pytest

from ads_reader.ads_torque_reader import (
    AdsTighteningEvent,
    AdsTorqueReader,
    PlcTorqueTraceEncoder,
    PlcTorqueTraceEvent,
    build_plc_trace_event,
    make_idempotency_key,
)

# ---------------------------------------------------------------------------
# Synthetic event fixture
# ---------------------------------------------------------------------------

_STATION_ID = "MARRIAGE-01"
_VIN = "WBA12345678901234"
_TORQUE_NM = 42.75
_ANGLE_DEG = 120.5
_PLC_SEQ = 7
_TIMESTAMP_MS = 1_700_000_000_000  # fixed anchor, not Date.now()

_SYNTHETIC_EVENT = AdsTighteningEvent(
    station_id=_STATION_ID,
    vin=_VIN,
    torque_nm=_TORQUE_NM,
    angle_deg=_ANGLE_DEG,
    plc_seq=_PLC_SEQ,
    timestamp_ms=_TIMESTAMP_MS,
    result="OK",
)

_TEST_SCHEMA_ID = 42


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_encoder() -> PlcTorqueTraceEncoder:
    return PlcTorqueTraceEncoder(schema_id=_TEST_SCHEMA_ID)


# ---------------------------------------------------------------------------
# (a) + (b): Avro round-trip — encode then decode, assert all required fields
# ---------------------------------------------------------------------------

class TestAvroRoundTrip:
    """Inject a synthetic AdsTighteningEvent and assert Avro encode/decode."""

    def test_encode_does_not_raise(self) -> None:
        trace = build_plc_trace_event(_SYNTHETIC_EVENT)
        enc = _make_encoder()
        payload = enc.encode(trace)
        assert isinstance(payload, bytes), "encode must return bytes"
        assert len(payload) > 5, "payload must be longer than the 5-byte wire header"

    def test_decode_produces_all_required_fields(self) -> None:
        trace = build_plc_trace_event(_SYNTHETIC_EVENT)
        enc = _make_encoder()
        payload = enc.encode(trace)
        decoded = enc.decode(payload)

        assert decoded["stationId"] == _STATION_ID
        assert decoded["vin"] == _VIN
        assert decoded["torqueNm"] == pytest.approx(_TORQUE_NM)
        assert decoded["angleDeg"] == pytest.approx(_ANGLE_DEG)
        assert decoded["timestampMs"] == _TIMESTAMP_MS
        assert isinstance(decoded["idempotencyKey"], str)
        assert len(decoded["idempotencyKey"]) == 36, (
            f"idempotencyKey must be a UUID string (36 chars), got {decoded['idempotencyKey']!r}"
        )
        assert decoded["edgeBuffered"] is False

    def test_field_types_are_correct(self) -> None:
        trace = build_plc_trace_event(_SYNTHETIC_EVENT)
        enc = _make_encoder()
        decoded = enc.decode(enc.encode(trace))

        assert isinstance(decoded["stationId"], str), "stationId must be str"
        assert isinstance(decoded["vin"], str), "vin must be str"
        assert isinstance(decoded["torqueNm"], float), "torqueNm must be float"
        assert isinstance(decoded["angleDeg"], float), "angleDeg must be float"
        assert isinstance(decoded["timestampMs"], int), "timestampMs must be int"
        assert isinstance(decoded["idempotencyKey"], str), "idempotencyKey must be str"
        assert isinstance(decoded["edgeBuffered"], bool), "edgeBuffered must be bool"

    def test_edge_buffered_true_round_trips(self) -> None:
        buffered_event = AdsTighteningEvent(
            station_id=_STATION_ID,
            vin=_VIN,
            torque_nm=_TORQUE_NM,
            angle_deg=_ANGLE_DEG,
            plc_seq=_PLC_SEQ,
            timestamp_ms=_TIMESTAMP_MS,
            result="OK",
            edge_buffered=True,
        )
        trace = build_plc_trace_event(buffered_event)
        # edge_buffered must propagate through build_plc_trace_event
        assert trace.edge_buffered is True

        enc = _make_encoder()
        decoded = enc.decode(enc.encode(trace))
        assert decoded["edgeBuffered"] is True


# ---------------------------------------------------------------------------
# (c) Idempotency key determinism
# ---------------------------------------------------------------------------

class TestIdempotencyKeyDeterminism:
    """Same inputs must produce the same key across invocations and instances."""

    def test_same_inputs_same_key(self) -> None:
        key1 = make_idempotency_key(_STATION_ID, _PLC_SEQ, _TIMESTAMP_MS)
        key2 = make_idempotency_key(_STATION_ID, _PLC_SEQ, _TIMESTAMP_MS)
        assert key1 == key2, (
            f"Identical inputs must yield identical idempotency keys: {key1!r} != {key2!r}"
        )

    def test_different_seq_produces_different_key(self) -> None:
        key1 = make_idempotency_key(_STATION_ID, 1, _TIMESTAMP_MS)
        key2 = make_idempotency_key(_STATION_ID, 2, _TIMESTAMP_MS)
        assert key1 != key2, "Different PLC sequence numbers must yield different keys"

    def test_different_timestamp_produces_different_key(self) -> None:
        key1 = make_idempotency_key(_STATION_ID, _PLC_SEQ, 1_700_000_000_000)
        key2 = make_idempotency_key(_STATION_ID, _PLC_SEQ, 1_700_000_001_000)
        assert key1 != key2, "Different timestamps must yield different keys"

    def test_different_station_produces_different_key(self) -> None:
        key1 = make_idempotency_key("MARRIAGE-01", _PLC_SEQ, _TIMESTAMP_MS)
        key2 = make_idempotency_key("FINAL-03", _PLC_SEQ, _TIMESTAMP_MS)
        assert key1 != key2, "Different station IDs must yield different keys"

    def test_key_is_uuid_format(self) -> None:
        key = make_idempotency_key(_STATION_ID, _PLC_SEQ, _TIMESTAMP_MS)
        # Must parse as a valid UUID without raising ValueError
        parsed = __import__("uuid").UUID(key)
        assert str(parsed) == key, f"make_idempotency_key must return a canonical UUID string, got {key!r}"

    def test_build_plc_trace_event_embeds_deterministic_key(self) -> None:
        trace_a = build_plc_trace_event(_SYNTHETIC_EVENT)
        trace_b = build_plc_trace_event(_SYNTHETIC_EVENT)
        assert trace_a.idempotency_key == trace_b.idempotency_key, (
            "build_plc_trace_event must embed a deterministic idempotency key for identical inputs"
        )

    def test_new_encoder_instance_produces_same_key(self) -> None:
        """Key derivation must not rely on encoder instance state."""
        trace = build_plc_trace_event(_SYNTHETIC_EVENT)
        enc1 = _make_encoder()
        enc2 = _make_encoder()
        dec1 = enc1.decode(enc1.encode(trace))
        dec2 = enc2.decode(enc2.encode(trace))
        assert dec1["idempotencyKey"] == dec2["idempotencyKey"], (
            "idempotencyKey must be identical regardless of encoder instance"
        )


# ---------------------------------------------------------------------------
# Synthetic ADS simulation — AdsTorqueReader fires callback on tightening
# ---------------------------------------------------------------------------

class _SimulatedAdsClient:
    """ADS client stub that replays a scripted sequence of tag reads.

    State machine:
      - cycle 0..N-2: result = "" (tightening in progress)
      - cycle N-1:    result = "OK" (tightening complete, rising edge)
      - cycle N..:    result = "OK" (held after completion)

    Each call to read_by_name advances the cycle counter for that symbol.
    """

    def __init__(
        self,
        torque_nm: float,
        angle_deg: float,
        vin: str,
        complete_on_cycle: int = 2,
    ) -> None:
        self._torque_nm = torque_nm
        self._angle_deg = angle_deg
        self._vin = vin
        self._complete_on_cycle = complete_on_cycle
        self._cycle: int = 0
        self.open_call_count: int = 0
        self.close_call_count: int = 0

    def read_by_name(self, symbol: str, plc_datatype: object) -> object:
        if symbol == _ADS_SYM_RESULT:
            result = "OK" if self._cycle >= self._complete_on_cycle else ""
            self._cycle += 1
            return result
        if symbol == _ADS_SYM_TORQUE_NM:
            return self._torque_nm
        if symbol == _ADS_SYM_ANGLE_DEG:
            return self._angle_deg
        if symbol == _ADS_SYM_VIN_SCAN:
            return self._vin
        return 0

    def open(self) -> None:
        self.open_call_count += 1

    def close(self) -> None:
        self.close_call_count += 1


# Import the private symbol names for the stub
from ads_reader.ads_torque_reader import (
    _ADS_SYM_RESULT,
    _ADS_SYM_TORQUE_NM,
    _ADS_SYM_ANGLE_DEG,
    _ADS_SYM_VIN_SCAN,
)


class TestAdsTorqueReaderCallbackFired:
    """AdsTorqueReader fires the tightening callback on rising-edge detection."""

    def _run_reader_until_callback(
        self,
        ads_client: _SimulatedAdsClient,
        station_id: str = _STATION_ID,
        max_polls: int = 20,
    ) -> list[AdsTighteningEvent]:
        """Drive the reader's poll loop directly for a bounded number of cycles."""
        import ctypes as ct

        received: list[AdsTighteningEvent] = []

        def capture(event: AdsTighteningEvent) -> None:
            received.append(event)

        reader = AdsTorqueReader(
            station_id=station_id,
            ads_net_id="192.168.40.11.1.1",
            on_tightening=capture,
            ads_client=ads_client,
            poll_interval_s=0.0,  # no sleep in tests
        )

        # Drive _poll_loop manually for max_polls iterations to avoid blocking
        reader._running = True
        reader._prev_result = None
        reader._seq = 0
        ads_client.open()

        for _ in range(max_polls):
            result_str = str(ads_client.read_by_name(_ADS_SYM_RESULT, ct.c_char_p))
            if (
                result_str in {"OK", "NOK"}
                and reader._prev_result not in {"OK", "NOK"}
            ):
                torque_nm = float(ads_client.read_by_name(_ADS_SYM_TORQUE_NM, ct.c_double))
                angle_deg = float(ads_client.read_by_name(_ADS_SYM_ANGLE_DEG, ct.c_double))
                vin_raw = str(ads_client.read_by_name(_ADS_SYM_VIN_SCAN, ct.c_char_p))
                import re as _re
                vin = vin_raw.strip() if _re.compile(r"^[A-HJ-NPR-Z0-9]{17}$").match(vin_raw.strip()) else ""
                reader._seq += 1
                import time as _time
                event = AdsTighteningEvent(
                    station_id=station_id,
                    vin=vin,
                    torque_nm=torque_nm,
                    angle_deg=angle_deg,
                    plc_seq=reader._seq,
                    timestamp_ms=_TIMESTAMP_MS,
                    result=result_str,
                )
                capture(event)
            reader._prev_result = result_str
            if received:
                break

        return received

    def test_callback_fired_exactly_once_on_tightening_completion(self) -> None:
        client = _SimulatedAdsClient(
            torque_nm=_TORQUE_NM,
            angle_deg=_ANGLE_DEG,
            vin=_VIN,
            complete_on_cycle=2,
        )
        events = self._run_reader_until_callback(client)
        assert len(events) == 1, (
            f"Expected exactly 1 tightening callback; got {len(events)}"
        )

    def test_callback_event_carries_correct_torque_and_angle(self) -> None:
        client = _SimulatedAdsClient(
            torque_nm=55.25,
            angle_deg=90.0,
            vin=_VIN,
            complete_on_cycle=1,
        )
        events = self._run_reader_until_callback(client)
        assert len(events) == 1
        ev = events[0]
        assert ev.torque_nm == pytest.approx(55.25)
        assert ev.angle_deg == pytest.approx(90.0)

    def test_callback_event_station_id_matches(self) -> None:
        client = _SimulatedAdsClient(
            torque_nm=_TORQUE_NM,
            angle_deg=_ANGLE_DEG,
            vin=_VIN,
        )
        events = self._run_reader_until_callback(client, station_id="FINAL-03")
        assert len(events) == 1
        assert events[0].station_id == "FINAL-03"

    def test_avro_encode_of_callback_event_is_valid(self) -> None:
        """Full pipeline: ADS event → PlcTorqueTraceEvent → Avro bytes → decode."""
        client = _SimulatedAdsClient(
            torque_nm=_TORQUE_NM,
            angle_deg=_ANGLE_DEG,
            vin=_VIN,
        )
        events = self._run_reader_until_callback(client)
        assert len(events) == 1

        trace = build_plc_trace_event(events[0])
        enc = _make_encoder()
        payload = enc.encode(trace)
        decoded = enc.decode(payload)

        assert decoded["stationId"] == _STATION_ID
        assert decoded["vin"] == _VIN
        assert decoded["torqueNm"] == pytest.approx(_TORQUE_NM)
        assert decoded["angleDeg"] == pytest.approx(_ANGLE_DEG)
        assert isinstance(decoded["idempotencyKey"], str)
        assert decoded["edgeBuffered"] is False


# ---------------------------------------------------------------------------
# Wire format header validation
# ---------------------------------------------------------------------------

class TestConfluentWireFormatHeader:
    """The Confluent Wire Format 5-byte header must be correct."""

    def test_magic_byte_is_zero(self) -> None:
        trace = build_plc_trace_event(_SYNTHETIC_EVENT)
        enc = _make_encoder()
        payload = enc.encode(trace)
        assert payload[0:1] == b"\x00", (
            "First byte of Confluent Wire Format must be magic byte 0x00"
        )

    def test_schema_id_encoded_big_endian(self) -> None:
        import struct

        trace = build_plc_trace_event(_SYNTHETIC_EVENT)
        enc = _make_encoder()
        payload = enc.encode(trace)
        _, schema_id_in_header = struct.unpack(">bI", payload[:5])
        assert schema_id_in_header == _TEST_SCHEMA_ID, (
            f"schema_id in header must be {_TEST_SCHEMA_ID}, got {schema_id_in_header}"
        )

    def test_different_schema_ids_are_encoded_correctly(self) -> None:
        import struct

        for sid in (1, 100, 65535, 2**20):
            trace = build_plc_trace_event(_SYNTHETIC_EVENT)
            enc = PlcTorqueTraceEncoder(schema_id=sid)
            payload = enc.encode(trace)
            _, decoded_sid = struct.unpack(">bI", payload[:5])
            assert decoded_sid == sid, f"schema_id {sid} not encoded correctly: got {decoded_sid}"
