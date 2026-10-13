"""Beckhoff ADS torque reader and PlcTorqueTraceEvent Avro encoder.

Runs inside the Siemens SIMATIC edge gateway runtime (OT zone, IEC 62443 SL2).

Architecture
------------
AdsTorqueReader polls the Beckhoff PLC via ADS/ADS-UDP at 1 kHz (configurable).
On each tightening completion (TorqueResult tag transitions to "OK" or "NOK"),
it fires an internal callback with an AdsTighteningEvent.

PlcTorqueTraceEncoder maps that event to a PlcTorqueTraceEvent Avro record and
serialises it with Confluent Wire Format framing (5-byte header: 0x00 + schema_id).
The encoded bytes are ready to be placed in the local ring buffer by the publisher
sibling — this module does NOT open any network connections to the IT zone.

ADS recovery
------------
The reader catches pyads.AdsError on every poll and backs off exponentially
(capped at _MAX_RECONNECT_INTERVAL_S).  On reconnect success the poll loop
resumes without requiring a SIMATIC runtime restart.

Idempotency key
---------------
UUID v5 over the namespace DNS + canonical string
  "{station_id}:{plc_seq}:{timestamp_ms}"
Same inputs always produce the same UUID across Python versions and machines.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import struct
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

import avro.schema
import avro.io

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEMA_PATH = Path(__file__).parent.parent / "avro" / "PlcTorqueTraceEvent.avsc"

# Namespace UUID for idempotency key derivation (RFC 4122 §4.3 — named namespace)
# Using DNS namespace is conventional; a project-specific namespace is equally valid.
_IDEMPOTENCY_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_DNS

# ADS tag symbols — must match assembly.yaml tag catalogue
_ADS_SYM_TORQUE_NM = "MAIN.Torque"
_ADS_SYM_ANGLE_DEG = "MAIN.TorqueAngle"
_ADS_SYM_RESULT = "MAIN.TorqueResult"
_ADS_SYM_FASTENER_COUNT = "MAIN.FastenerCount"
_ADS_SYM_VIN_SCAN = "MAIN.VinScan"

# Result values that mark a completed tightening (OK = in-tolerance, NOK = out-of-tolerance)
_TIGHTENING_COMPLETE_RESULTS = frozenset({"OK", "NOK"})

_POLL_INTERVAL_S = 0.001        # 1 kHz
_MAX_RECONNECT_INTERVAL_S = 30.0
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdsTighteningEvent:
    """Raw tightening data read from the PLC ADS interface."""

    station_id: str
    vin: str
    torque_nm: float
    angle_deg: float
    plc_seq: int
    timestamp_ms: int
    result: str  # "OK" or "NOK"
    edge_buffered: bool = False


@dataclass(frozen=True)
class PlcTorqueTraceEvent:
    """Encoded event ready for Avro serialisation."""

    station_id: str
    vin: str
    torque_nm: float
    angle_deg: float
    timestamp_ms: int
    idempotency_key: str
    edge_buffered: bool = False


# ---------------------------------------------------------------------------
# Idempotency key
# ---------------------------------------------------------------------------

def make_idempotency_key(station_id: str, plc_seq: int, timestamp_ms: int) -> str:
    """Return a deterministic UUID v5 string for the given inputs.

    Identical inputs always produce the same UUID, satisfying the
    idempotency requirement across edge-gateway retries (AC-b734032a).
    """
    canonical = f"{station_id}:{plc_seq}:{timestamp_ms}"
    return str(uuid.uuid5(_IDEMPOTENCY_NS, canonical))


# ---------------------------------------------------------------------------
# Avro schema loader (cached at module import)
# ---------------------------------------------------------------------------

def _load_schema() -> avro.schema.Schema:
    raw = _SCHEMA_PATH.read_text(encoding="utf-8")
    return avro.schema.parse(raw)


_AVRO_SCHEMA: avro.schema.Schema = _load_schema()


# ---------------------------------------------------------------------------
# Avro encoder
# ---------------------------------------------------------------------------

class PlcTorqueTraceEncoder:
    """Encodes PlcTorqueTraceEvent to Confluent Wire Format Avro bytes.

    Wire format: 0x00 (magic) + 4-byte big-endian schema_id + Avro binary payload.
    schema_id is provided at construction; it is the ID returned by the Confluent
    Schema Registry after schema registration (handled at deploy time, not here).
    """

    def __init__(self, schema_id: int) -> None:
        self._schema_id = schema_id
        self._schema = _AVRO_SCHEMA
        self._writer = avro.io.DatumWriter(self._schema)

    def encode(self, event: PlcTorqueTraceEvent) -> bytes:
        """Serialise event to Confluent Wire Format bytes."""
        buf = io.BytesIO()
        encoder = avro.io.BinaryEncoder(buf)
        self._writer.write(
            {
                "stationId": event.station_id,
                "vin": event.vin,
                "torqueNm": event.torque_nm,
                "angleDeg": event.angle_deg,
                "timestampMs": event.timestamp_ms,
                "idempotencyKey": event.idempotency_key,
                "edgeBuffered": event.edge_buffered,
            },
            encoder,
        )
        avro_bytes = buf.getvalue()
        # Confluent Wire Format header: magic byte 0x00 + 4-byte big-endian schema ID
        header = struct.pack(">bI", 0, self._schema_id)
        return header + avro_bytes

    def decode(self, data: bytes) -> dict:
        """Deserialise Confluent Wire Format bytes back to a dict (for testing)."""
        if len(data) < 5:
            raise ValueError(f"Payload too short: {len(data)} bytes")
        magic, schema_id = struct.unpack(">bI", data[:5])
        if magic != 0:
            raise ValueError(f"Unexpected magic byte: {magic!r}")
        buf = io.BytesIO(data[5:])
        decoder = avro.io.BinaryDecoder(buf)
        reader = avro.io.DatumReader(self._schema)
        return reader.read(decoder)


# ---------------------------------------------------------------------------
# ADS simulation interface — used in tests without a live PLC
# ---------------------------------------------------------------------------

class AdsClientProtocol(Protocol):
    """Minimal interface the reader needs from an ADS client."""

    def read_by_name(self, symbol: str, plc_datatype: object) -> object:
        ...

    def open(self) -> None:
        ...

    def close(self) -> None:
        ...


# ---------------------------------------------------------------------------
# ADS torque reader
# ---------------------------------------------------------------------------

TighteningCallback = Callable[[AdsTighteningEvent], None]


class AdsTorqueReader:
    """Polls the Beckhoff PLC via ADS at 1 kHz and fires tightening callbacks.

    Parameters
    ----------
    station_id:
        Identifies this assembly station (e.g. "MARRIAGE-01").
    ads_net_id:
        AMS Net ID of the target PLC (e.g. "192.168.40.11.1.1").
    on_tightening:
        Callback invoked on each completed tightening.  Called synchronously
        inside the poll loop — must not block.
    ads_client:
        ADS client instance.  Injected to allow simulation in tests.
    poll_interval_s:
        ADS poll interval in seconds (default 0.001 = 1 kHz).
    """

    def __init__(
        self,
        station_id: str,
        ads_net_id: str,
        on_tightening: TighteningCallback,
        ads_client: AdsClientProtocol,
        poll_interval_s: float = _POLL_INTERVAL_S,
    ) -> None:
        self._station_id = station_id
        self._ads_net_id = ads_net_id
        self._on_tightening = on_tightening
        self._ads_client = ads_client
        self._poll_interval_s = poll_interval_s

        self._running = False
        self._seq: int = 0
        # Track previous result to detect rising edge (idle → complete)
        self._prev_result: str | None = None

    def start(self) -> None:
        """Open the ADS connection and start the poll loop (blocking)."""
        self._running = True
        reconnect_delay = 1.0

        while self._running:
            try:
                self._ads_client.open()
                logger.info(
                    "ADS connection established: station=%s net_id=%s",
                    self._station_id,
                    self._ads_net_id,
                )
                reconnect_delay = 1.0
                self._poll_loop()
            except Exception as exc:
                logger.warning(
                    "ADS connection lost (station=%s): %s — reconnecting in %.1fs",
                    self._station_id,
                    exc,
                    reconnect_delay,
                )
                try:
                    self._ads_client.close()
                except Exception:
                    pass
                if self._running:
                    time.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, _MAX_RECONNECT_INTERVAL_S)

    def stop(self) -> None:
        """Signal the poll loop to exit cleanly."""
        self._running = False

    def _poll_loop(self) -> None:
        """Inner loop — exits when _running is False or an ADS exception propagates."""
        import ctypes  # pyads uses ctypes constants for data types

        while self._running:
            result_str = str(self._ads_client.read_by_name(_ADS_SYM_RESULT, ctypes.c_char_p))

            # Detect tightening completion: result changes from non-complete to complete
            if (
                result_str in _TIGHTENING_COMPLETE_RESULTS
                and self._prev_result not in _TIGHTENING_COMPLETE_RESULTS
            ):
                torque_nm = float(self._ads_client.read_by_name(_ADS_SYM_TORQUE_NM, ctypes.c_double))
                angle_deg = float(self._ads_client.read_by_name(_ADS_SYM_ANGLE_DEG, ctypes.c_double))
                vin_raw = str(self._ads_client.read_by_name(_ADS_SYM_VIN_SCAN, ctypes.c_char_p))
                vin = vin_raw.strip() if _VIN_RE.match(vin_raw.strip()) else ""

                self._seq += 1
                timestamp_ms = int(time.time() * 1000)

                event = AdsTighteningEvent(
                    station_id=self._station_id,
                    vin=vin,
                    torque_nm=torque_nm,
                    angle_deg=angle_deg,
                    plc_seq=self._seq,
                    timestamp_ms=timestamp_ms,
                    result=result_str,
                )
                try:
                    self._on_tightening(event)
                except Exception as cb_exc:
                    logger.error(
                        "Tightening callback raised (station=%s seq=%d): %s",
                        self._station_id,
                        self._seq,
                        cb_exc,
                    )

            self._prev_result = result_str
            time.sleep(self._poll_interval_s)


# ---------------------------------------------------------------------------
# Encoder pipeline helper
# ---------------------------------------------------------------------------

def build_plc_trace_event(
    ads_event: AdsTighteningEvent,
) -> PlcTorqueTraceEvent:
    """Map an AdsTighteningEvent to a PlcTorqueTraceEvent (no I/O)."""
    idempotency_key = make_idempotency_key(
        ads_event.station_id,
        ads_event.plc_seq,
        ads_event.timestamp_ms,
    )
    return PlcTorqueTraceEvent(
        station_id=ads_event.station_id,
        vin=ads_event.vin,
        torque_nm=ads_event.torque_nm,
        angle_deg=ads_event.angle_deg,
        timestamp_ms=ads_event.timestamp_ms,
        idempotency_key=idempotency_key,
        edge_buffered=ads_event.edge_buffered,
    )
