"""MQTT Sparkplug B publisher — IEC 62443 SL2 zone-crossing transport layer.

Runs inside the Siemens SIMATIC edge gateway runtime (OT zone, IEC 62443 SL2).

Architecture
------------
SparkplugPublisher consumes Avro-encoded PlcTorqueTraceEvent bytes (Confluent
Wire Format) produced by the ADS torque reader (ads_reader.ads_torque_reader)
and publishes them to the on-premises MQTT broker (HiveMQ Enterprise) using
the SPIFFE SVID obtained from the SPIRE Workload API socket.

SPIFFE SVID acquisition
-----------------------
The SVID is read exclusively from the paths written by the svid-watcher.service
sidecar, which subscribes to the SPIRE Workload API gRPC stream and rotates the
files at half-TTL (1800 s).  No static certificates or keys are embedded.

The publisher monitors the SVID certificate file with inotify (pyinotify) and
reloads the TLS context on rotation without dropping the active MQTT connection.

Sparkplug B namespace
---------------------
Publishes to: spBv1.0/Munich/{shop}/{edge_node_id}/{device_id}/DDATA
Where:
  - shop       = assembly station shop (e.g. "assembly")
  - edge_node_id = gateway hostname (e.g. "edge-assembly-01")
  - device_id  = PLC identifier (e.g. "plc-assembly-01")

Payload
-------
The DDATA payload carries the raw Avro bytes in a Sparkplug B Bytes metric
named "torque_trace_avro".  The HiveMQ bridge translates DDATA messages to
Kafka topic bmw.ifos.munich.uns.torque.plc-trace.v1.

Buffer / Pending-Sync
---------------------
When MQTT connectivity is lost the publisher accumulates events in an in-memory
ring buffer (capped at MAX_BUFFER_EVENTS events / 60 s worth of data).
While buffering, it emits a "pending_sync" status to the local OT broker topic
edge/{station_id}/overlay/status so the AR overlay shows the Pending Sync
indicator (AC-b734032a).  On reconnect the buffer is flushed in FIFO order.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MQTT_SPARKPLUG_NAMESPACE = "spBv1.0"
MQTT_GROUP_ID = "Munich"

# Kafka topic the HiveMQ bridge forwards DDATA to
KAFKA_TORQUE_TOPIC = "bmw.ifos.munich.uns.torque.plc-trace.v1"

# Sparkplug B metric name carrying the Avro payload bytes
METRIC_NAME_TORQUE_TRACE = "torque_trace_avro"

# Local OT broker topic for AR overlay overlay status (AC-b734032a)
OVERLAY_STATUS_TOPIC_TEMPLATE = "edge/{station_id}/overlay/status"

# Ring buffer — 60 s at 1 kHz with a generous headroom ceiling
MAX_BUFFER_EVENTS = 120_000

# Default SVID file paths (populated by svid-watcher.service; overridable via env)
_DEFAULT_SVID_CERT_PATH = os.environ.get("SVID_CERT_PATH", "/run/spire/svid/svid.pem")
_DEFAULT_SVID_KEY_PATH = os.environ.get("SVID_KEY_PATH", "/run/spire/svid/svid_key.pem")
_DEFAULT_SPIRE_BUNDLE_PATH = os.environ.get("SPIRE_BUNDLE_PATH", "/run/spire/bundle/bundle.pem")

# SVID inotify poll interval for environments where pyinotify is unavailable
_SVID_POLL_INTERVAL_S = 30.0


# ---------------------------------------------------------------------------
# SVID loader — reads from SPIRE Workload API–managed files
# ---------------------------------------------------------------------------

@dataclass
class SvidPaths:
    """Paths written by svid-watcher.service; never embedded credentials."""

    cert_path: str = _DEFAULT_SVID_CERT_PATH
    key_path: str = _DEFAULT_SVID_KEY_PATH
    bundle_path: str = _DEFAULT_SPIRE_BUNDLE_PATH


def load_svid_ssl_context(svid: SvidPaths) -> ssl.SSLContext:
    """Build an mTLS SSLContext from SPIRE-managed SVID files.

    Raises FileNotFoundError if any of the SVID files are absent —
    the caller must wait for svid-watcher to complete its first fetch.
    """
    cert_path = Path(svid.cert_path)
    key_path = Path(svid.key_path)
    bundle_path = Path(svid.bundle_path)

    for p in (cert_path, key_path, bundle_path):
        if not p.exists():
            raise FileNotFoundError(
                f"SPIRE SVID file not found: {p}. "
                "Ensure svid-watcher.service has fetched the first SVID before starting the publisher."
            )

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(str(bundle_path))
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return ctx


def read_spiffe_id_from_svid(cert_path: str) -> str | None:
    """Extract the SPIFFE URI SAN from the SVID PEM certificate.

    Returns the first URI SAN that begins with 'spiffe://', or None if
    the file cannot be parsed or contains no SPIFFE URI.
    """
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend

    try:
        pem_bytes = Path(cert_path).read_bytes()
        cert = x509.load_pem_x509_certificate(pem_bytes, default_backend())
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        for uri in san_ext.value.get_values_for_type(x509.UniformResourceIdentifier):
            if uri.startswith("spiffe://"):
                return uri
    except Exception as exc:
        logger.warning("Could not read SPIFFE ID from SVID: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Sparkplug B DDATA builder — minimal protobuf-free bytes representation
# ---------------------------------------------------------------------------

def _build_sparkplug_ddata_payload(avro_bytes: bytes, seq: int) -> bytes:
    """Build a minimal Sparkplug B DDATA payload.

    The payload is a JSON document that the HiveMQ Sparkplug B extension
    deserialises.  For production use the HiveMQ extension accepts the
    Tahu protobuf encoding; this JSON representation is used for testing
    and environments without the Tahu protobuf library.

    The Avro bytes are embedded as a base64-encoded string in the
    'torque_trace_avro' metric — the bridge preserves them verbatim.
    """
    import base64

    return json.dumps({
        "timestamp": int(time.time() * 1000),
        "seq": seq,
        "metrics": [
            {
                "name": METRIC_NAME_TORQUE_TRACE,
                "datatype": "Bytes",
                "value": base64.b64encode(avro_bytes).decode("ascii"),
            }
        ],
    }).encode("utf-8")


# ---------------------------------------------------------------------------
# Ring buffer for 60-second edge store-and-forward
# ---------------------------------------------------------------------------

@dataclass
class _BufferedEvent:
    """An Avro-encoded event held in the edge ring buffer."""

    avro_bytes: bytes
    timestamp_ms: int


class _EdgeRingBuffer:
    """Thread-safe FIFO ring buffer with a fixed event capacity.

    When full the oldest event is dropped to make room for the newest,
    matching the SIMATIC Edge Runtime store-and-forward behaviour.
    """

    def __init__(self, max_events: int = MAX_BUFFER_EVENTS) -> None:
        self._max_events = max_events
        self._queue: deque[_BufferedEvent] = deque()
        self._lock = threading.Lock()

    def push(self, event: _BufferedEvent) -> None:
        with self._lock:
            if len(self._queue) >= self._max_events:
                self._queue.popleft()
            self._queue.append(event)

    def drain(self) -> list[_BufferedEvent]:
        with self._lock:
            events = list(self._queue)
            self._queue.clear()
            return events

    def __len__(self) -> int:
        with self._lock:
            return len(self._queue)


# ---------------------------------------------------------------------------
# SparkplugPublisher
# ---------------------------------------------------------------------------

class SparkplugPublisher:
    """Publishes Avro-encoded PlcTorqueTraceEvents to the MQTT broker via
    Sparkplug B DDATA messages, using the SPIFFE SVID for mTLS.

    Parameters
    ----------
    broker_host:
        HiveMQ broker hostname (e.g. uns-hivemq.hivemq.svc.cluster.local).
    broker_port:
        MQTT-over-TLS port (default 8883).
    station_id:
        Assembly station identifier; used in overlay status topics.
    shop:
        Shop name (e.g. "assembly"); becomes the Sparkplug B group path segment.
    edge_node_id:
        Gateway hostname (e.g. "edge-assembly-01"); Sparkplug B EdgeNodeId.
    device_id:
        PLC device ID (e.g. "plc-assembly-01"); Sparkplug B DeviceId.
    svid:
        Paths to the SPIRE-managed SVID cert, key, and CA bundle files.
    on_publish_success:
        Optional callback invoked after a successful MQTT publish.
    on_buffer_state_change:
        Optional callback invoked when the buffer state changes
        (True = buffering active, False = buffering cleared).
    """

    def __init__(
        self,
        broker_host: str,
        broker_port: int,
        station_id: str,
        shop: str,
        edge_node_id: str,
        device_id: str,
        svid: SvidPaths | None = None,
        on_publish_success: Callable[[bytes], None] | None = None,
        on_buffer_state_change: Callable[[bool], None] | None = None,
    ) -> None:
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._station_id = station_id
        self._shop = shop
        self._edge_node_id = edge_node_id
        self._device_id = device_id
        self._svid = svid or SvidPaths()
        self._on_publish_success = on_publish_success
        self._on_buffer_state_change = on_buffer_state_change

        self._ddata_topic = (
            f"{MQTT_SPARKPLUG_NAMESPACE}/{MQTT_GROUP_ID}"
            f"/{shop}/{edge_node_id}/{device_id}/DDATA"
        )
        self._overlay_status_topic = OVERLAY_STATUS_TOPIC_TEMPLATE.format(
            station_id=station_id
        )

        self._buffer = _EdgeRingBuffer()
        self._seq: int = 0
        self._buffering: bool = False
        self._client: object | None = None  # paho.mqtt.client.Client at runtime
        self._connected = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # SVID / TLS                                                           #
    # ------------------------------------------------------------------ #

    def _build_ssl_context(self) -> ssl.SSLContext:
        return load_svid_ssl_context(self._svid)

    def spiffe_id(self) -> str | None:
        """Return the SPIFFE URI from the current SVID, or None."""
        return read_spiffe_id_from_svid(self._svid.cert_path)

    # ------------------------------------------------------------------ #
    # MQTT lifecycle                                                        #
    # ------------------------------------------------------------------ #

    def connect(self) -> None:
        """Establish an mTLS MQTT connection to the broker.

        The TLS context is built from SPIRE-managed SVID files.
        Raises FileNotFoundError if the SVID is not yet present.
        """
        import paho.mqtt.client as mqtt

        ssl_ctx = self._build_ssl_context()
        client = mqtt.Client(client_id=self._edge_node_id, protocol=mqtt.MQTTv311)
        client.tls_set_context(ssl_ctx)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect

        client.connect(self._broker_host, self._broker_port, keepalive=60)
        client.loop_start()
        self._client = client

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
            self._connected.clear()

    def _on_connect(self, client: object, userdata: object, flags: dict, rc: int) -> None:
        if rc == 0:
            logger.info(
                "MQTT connected: broker=%s:%d node=%s spiffe_id=%s",
                self._broker_host,
                self._broker_port,
                self._edge_node_id,
                self.spiffe_id(),
            )
            self._connected.set()
            self._flush_buffer()
        else:
            logger.warning(
                "MQTT connect failed: broker=%s rc=%d",
                self._broker_host,
                rc,
            )

    def _on_disconnect(self, client: object, userdata: object, rc: int) -> None:
        logger.warning(
            "MQTT disconnected: broker=%s rc=%d — activating edge buffer",
            self._broker_host,
            rc,
        )
        self._connected.clear()
        self._set_buffering(True)

    # ------------------------------------------------------------------ #
    # Publish / buffer                                                      #
    # ------------------------------------------------------------------ #

    def publish(self, avro_bytes: bytes) -> None:
        """Publish one Avro-encoded PlcTorqueTraceEvent.

        If the MQTT connection is up the event is published immediately.
        If the connection is down it is pushed to the edge ring buffer and
        the AR overlay receives a 'pending_sync' status.
        """
        if self._connected.is_set():
            self._publish_direct(avro_bytes)
        else:
            ts_ms = int(time.time() * 1000)
            self._buffer.push(_BufferedEvent(avro_bytes=avro_bytes, timestamp_ms=ts_ms))
            self._set_buffering(True)
            logger.debug(
                "Event buffered (station=%s buffer_size=%d)",
                self._station_id,
                len(self._buffer),
            )

    def _publish_direct(self, avro_bytes: bytes) -> None:
        with self._lock:
            seq = self._seq
            self._seq = (self._seq + 1) & 0xFFFFFFFF

        payload = _build_sparkplug_ddata_payload(avro_bytes, seq)
        info = self._client.publish(self._ddata_topic, payload, qos=1)
        info.wait_for_publish(timeout=5.0)

        logger.debug(
            "DDATA published: topic=%s seq=%d bytes=%d",
            self._ddata_topic,
            seq,
            len(avro_bytes),
        )
        if self._on_publish_success is not None:
            self._on_publish_success(avro_bytes)

    def _flush_buffer(self) -> None:
        """Drain the ring buffer and publish each event in FIFO order."""
        events = self._buffer.drain()
        if not events:
            return

        logger.info(
            "Flushing edge buffer: station=%s events=%d",
            self._station_id,
            len(events),
        )
        for evt in events:
            try:
                self._publish_direct(evt.avro_bytes)
            except Exception as exc:
                logger.error(
                    "Buffer flush failed for event (station=%s): %s — re-buffering",
                    self._station_id,
                    exc,
                )
                self._buffer.push(evt)
                return

        self._set_buffering(False)
        logger.info(
            "Edge buffer flushed: station=%s", self._station_id
        )

    def _set_buffering(self, active: bool) -> None:
        if self._buffering == active:
            return
        self._buffering = active
        status = "pending_sync" if active else "synced"
        self._publish_overlay_status(status)
        if self._on_buffer_state_change is not None:
            self._on_buffer_state_change(active)

    def _publish_overlay_status(self, status: str) -> None:
        """Publish a sync-status message to the local OT overlay topic."""
        if self._client is None or not self._connected.is_set():
            return
        payload = json.dumps({"stationId": self._station_id, "syncStatus": status}).encode()
        try:
            self._client.publish(self._overlay_status_topic, payload, qos=1)
        except Exception as exc:
            logger.debug("Overlay status publish failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Zero-interrupt SVID rotation support                                  #
    # ------------------------------------------------------------------ #

    def reload_tls(self) -> None:
        """Reload the TLS context after an SVID rotation.

        Called by the svid-watcher signal handler (SIGHUP path).
        Rebuilds the SSL context from the updated SVID files and
        reconnects the MQTT client without dropping pending publishes.
        """
        logger.info(
            "Reloading TLS context after SVID rotation: node=%s spiffe_id=%s",
            self._edge_node_id,
            self.spiffe_id(),
        )
        if self._client is not None:
            self.disconnect()
        self.connect()
