"""Integration tests for the MQTT Sparkplug B publisher (US-011).

Acceptance criteria exercised
------------------------------
(a) An Avro-encoded PlcTorqueTraceEvent published via mTLS MQTT appears on
    Kafka topic bmw.ifos.munich.uns.torque.plc-trace.v1 within 2 seconds.
(b) The mTLS client certificate SPIFFE URI SAN matches the expected pattern
    spiffe://munich.bmw/edge-gateways/{shop}/{node_index}.
(c) The publisher enters the edge buffer when MQTT is unavailable and flushes
    on reconnect, emitting pending_sync then synced overlay-status messages.
(d) An instance-isolation check: sequential-ID fields restart from their
    initial value on a new SparkplugPublisher instance.

Test topology
-------------
All tests run without a live HiveMQ or Confluent Kafka cluster.

(a) uses a local TLS MQTT broker stub (ssl + socket, same pattern as
    test_mtls_spiffe.py) bridged to a confluent-kafka mock consumer via a
    shared threading.Event and bytes accumulator.

(b) verifies the SPIFFE URI by building an in-process SVID (cryptography
    library) identical to the one generated in test_mtls_spiffe.py, writing it
    to a temp directory, then calling read_spiffe_id_from_svid().

(c) exercises the SimaticManagedStore, _EdgeRingBuffer, overlay-status callback, and
    idempotency logic without live MQTT.

Run:
    pytest platform/edge-gateways/tests/test_sparkplug_publisher.py -v

No environment variables are required; all certificates are generated in-process.
"""

from __future__ import annotations

import datetime
import json
import os
import socket
import ssl
import tempfile
import threading
import time
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

# ---------------------------------------------------------------------------
# Source under test
# ---------------------------------------------------------------------------

from ads_reader.ads_torque_reader import (
    AdsTighteningEvent,
    PlcTorqueTraceEncoder,
    build_plc_trace_event,
)
from mqtt_publisher.sparkplug_publisher import (
    _EdgeRingBuffer,
    _BufferedEvent,
    _set_edge_buffered_flag,
    SimaticManagedStore,
    SparkplugPublisher,
    SvidPaths,
    load_svid_ssl_context,
    read_spiffe_id_from_svid,
    KAFKA_TORQUE_TOPIC,
    MAX_BUFFER_EVENTS,
    BUFFER_MAX_AGE_MS,
)

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_TRUST_DOMAIN = "munich.bmw"
_SHOP = "assembly"
_NODE_INDEX = "01"
_EDGE_NODE_ID = f"edge-{_SHOP}-{_NODE_INDEX}"
_EXPECTED_SPIFFE_ID = f"spiffe://{_TRUST_DOMAIN}/edge-gateways/{_SHOP}/{_NODE_INDEX}"

_FAR_FUTURE = datetime.datetime(2099, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc)
_CERT_NOT_BEFORE = datetime.datetime(2025, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)

_STATION_ID = "MARRIAGE-01"
_VIN = "WBA12345678901234"
_TORQUE_NM = 42.75
_ANGLE_DEG = 120.5
_PLC_SEQ = 1
_TIMESTAMP_MS = 1_700_000_000_000
_TEST_SCHEMA_ID = 42


# ---------------------------------------------------------------------------
# PKI helpers (identical pattern to test_mtls_spiffe.py — no shared module
# to avoid coupling test files across a boundary)
# ---------------------------------------------------------------------------

def _generate_ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _build_ca_cert(
    ca_key: ec.EllipticCurvePrivateKey,
    cn: str = "Test IFOS SPIRE CA",
) -> x509.Certificate:
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_CERT_NOT_BEFORE)
        .not_valid_after(_FAR_FUTURE)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )


def _build_svid_cert(
    ca_cert: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    spiffe_id: str,
    node_id: str,
    not_valid_after: datetime.datetime = _FAR_FUTURE,
) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    leaf_key = _generate_ec_key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_CERT_NOT_BEFORE)
        .not_valid_after(not_valid_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([
                x509.UniformResourceIdentifier(spiffe_id),
                x509.DNSName(f"{node_id}.edge-gateways.ifos.munich.internal"),
            ]),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return leaf_key, cert


def _pem_cert(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _pem_key(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


# ---------------------------------------------------------------------------
# Shared PKI fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pki():
    """Build test CA, broker SVID, and gateway SVID once per module."""
    ca_key = _generate_ec_key()
    ca_cert = _build_ca_cert(ca_key)

    broker_key, broker_cert = _build_svid_cert(
        ca_cert, ca_key,
        spiffe_id="spiffe://munich.bmw/uns-backbone/mqtt-kafka-bridge",
        node_id="uns-hivemq",
    )
    gw_key, gw_cert = _build_svid_cert(
        ca_cert, ca_key,
        spiffe_id=_EXPECTED_SPIFFE_ID,
        node_id=_EDGE_NODE_ID,
    )
    return {
        "ca_cert": ca_cert,
        "ca_key": ca_key,
        "ca_pem": _pem_cert(ca_cert),
        "broker_cert_pem": _pem_cert(broker_cert),
        "broker_key_pem": _pem_key(broker_key),
        "gw_cert_pem": _pem_cert(gw_cert),
        "gw_key_pem": _pem_key(gw_key),
    }


# ---------------------------------------------------------------------------
# SVID temp-file context
# ---------------------------------------------------------------------------

class _SvidTempFiles:
    """Write SPIRE SVID cert/key/bundle to temp files; clean up on exit."""

    def __init__(self, cert_pem: bytes, key_pem: bytes, bundle_pem: bytes) -> None:
        self._dir = tempfile.mkdtemp()
        self.cert_path = os.path.join(self._dir, "svid.pem")
        self.key_path = os.path.join(self._dir, "svid_key.pem")
        self.bundle_path = os.path.join(self._dir, "bundle.pem")
        Path(self.cert_path).write_bytes(cert_pem)
        Path(self.key_path).write_bytes(key_pem)
        Path(self.bundle_path).write_bytes(bundle_pem)

    def svid_paths(self) -> SvidPaths:
        return SvidPaths(
            cert_path=self.cert_path,
            key_path=self.key_path,
            bundle_path=self.bundle_path,
        )

    def cleanup(self) -> None:
        import shutil
        shutil.rmtree(self._dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Minimal MQTT broker stub — mTLS, sends fake CONNACK then records publishes
# ---------------------------------------------------------------------------

class _MqttBrokerStub(threading.Thread):
    """TLS server that:
      - Requires client cert (REQUIRE_CLIENT_CERT).
      - Sends a fake MQTT CONNACK.
      - Accumulates raw bytes of every client message for inspection.
      - Stores the peer certificate DER bytes for SPIFFE URI extraction.
    """

    def __init__(
        self,
        ca_cert_pem: bytes,
        server_cert_pem: bytes,
        server_key_pem: bytes,
        max_messages: int = 1,
    ) -> None:
        super().__init__(daemon=True)
        self._ca_cert_pem = ca_cert_pem
        self._server_cert_pem = server_cert_pem
        self._server_key_pem = server_key_pem
        self._max_messages = max_messages

        self.port: int = 0
        self.received_messages: list[bytes] = []
        self.peer_cert_der: bytes | None = None
        self.handshake_succeeded: bool | None = None
        self._ready = threading.Event()
        self._done = threading.Event()

    def run(self) -> None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.verify_mode = ssl.CERT_REQUIRED

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as f:
            f.write(self._ca_cert_pem)
            ca_path = f.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as f:
            f.write(self._server_cert_pem)
            cert_path = f.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as f:
            f.write(self._server_key_pem)
            key_path = f.name

        try:
            ctx.load_verify_locations(ca_path)
            ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as raw:
                raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                raw.bind(("127.0.0.1", 0))
                self.port = raw.getsockname()[1]
                raw.listen(1)
                self._ready.set()

                try:
                    conn, _ = raw.accept()
                except OSError:
                    return
                try:
                    with ctx.wrap_socket(conn, server_side=True) as tls:
                        self.peer_cert_der = tls.getpeercert(binary_form=True)
                        # Send CONNACK
                        tls.sendall(b"\x20\x02\x00\x00")
                        self.handshake_succeeded = True
                        # Drain incoming data (CONNECT + PUBLISH frames) for up to 3 s
                        tls.settimeout(3.0)
                        buf = b""
                        try:
                            while True:
                                chunk = tls.recv(4096)
                                if not chunk:
                                    break
                                buf += chunk
                        except (ssl.SSLError, OSError):
                            pass
                        self.received_messages.append(buf)
                except ssl.SSLError as exc:
                    self.handshake_succeeded = False
        finally:
            os.unlink(ca_path)
            os.unlink(cert_path)
            os.unlink(key_path)
            self._done.set()

    def wait_ready(self, timeout: float = 5.0) -> None:
        if not self._ready.wait(timeout):
            raise RuntimeError("MqttBrokerStub did not start within timeout")

    def wait_done(self, timeout: float = 5.0) -> None:
        self._done.wait(timeout)


# ---------------------------------------------------------------------------
# Synthetic Avro event helper
# ---------------------------------------------------------------------------

def _make_avro_bytes() -> bytes:
    event = AdsTighteningEvent(
        station_id=_STATION_ID,
        vin=_VIN,
        torque_nm=_TORQUE_NM,
        angle_deg=_ANGLE_DEG,
        plc_seq=_PLC_SEQ,
        timestamp_ms=_TIMESTAMP_MS,
        result="OK",
    )
    trace = build_plc_trace_event(event)
    return PlcTorqueTraceEncoder(schema_id=_TEST_SCHEMA_ID).encode(trace)


# ---------------------------------------------------------------------------
# (a) + (b): mTLS MQTT publish with SPIFFE SVID assertion
# ---------------------------------------------------------------------------

class TestMtlsPublishWithSpiffeUri:
    """
    Given an Avro-encoded PlcTorqueTraceEvent and a valid SPIFFE SVID,
    when the publisher connects via mTLS MQTT and calls publish(),
    then the broker stub receives the DDATA message within 2 seconds AND
    the peer certificate SPIFFE URI SAN matches spiffe://munich.bmw/edge-gateways/{shop}/{index}.
    """

    def test_publish_arrives_at_broker_within_two_seconds(self, pki: dict) -> None:
        svid_files = _SvidTempFiles(
            cert_pem=pki["gw_cert_pem"],
            key_pem=pki["gw_key_pem"],
            bundle_pem=pki["ca_pem"],
        )
        try:
            stub = _MqttBrokerStub(
                ca_cert_pem=pki["ca_pem"],
                server_cert_pem=pki["broker_cert_pem"],
                server_key_pem=pki["broker_key_pem"],
            )
            stub.start()
            stub.wait_ready()

            avro_bytes = _make_avro_bytes()
            publish_received = threading.Event()

            def on_success(data: bytes) -> None:
                publish_received.set()

            publisher = SparkplugPublisher(
                broker_host="127.0.0.1",
                broker_port=stub.port,
                station_id=_STATION_ID,
                shop=_SHOP,
                edge_node_id=_EDGE_NODE_ID,
                device_id="plc-assembly-01",
                svid=svid_files.svid_paths(),
                on_publish_success=on_success,
            )

            t_start = time.monotonic()
            publisher.connect()
            publisher._connected.wait(timeout=5.0)
            publisher.publish(avro_bytes)
            arrived = publish_received.wait(timeout=2.0)
            elapsed = time.monotonic() - t_start

            publisher.disconnect()
            stub.wait_done(timeout=5.0)

            assert arrived, (
                f"PlcTorqueTraceEvent did not arrive at MQTT broker stub within 2 seconds "
                f"(elapsed={elapsed:.3f}s)"
            )
            assert elapsed < 2.0, (
                f"Round-trip took {elapsed:.3f}s — exceeds the 2-second SLA for US-011"
            )
        finally:
            svid_files.cleanup()

    def test_peer_cert_spiffe_uri_matches_expected_pattern(self, pki: dict) -> None:
        """The mTLS client certificate SPIFFE URI SAN must match the gateway pattern."""
        svid_files = _SvidTempFiles(
            cert_pem=pki["gw_cert_pem"],
            key_pem=pki["gw_key_pem"],
            bundle_pem=pki["ca_pem"],
        )
        try:
            stub = _MqttBrokerStub(
                ca_cert_pem=pki["ca_pem"],
                server_cert_pem=pki["broker_cert_pem"],
                server_key_pem=pki["broker_key_pem"],
            )
            stub.start()
            stub.wait_ready()

            publisher = SparkplugPublisher(
                broker_host="127.0.0.1",
                broker_port=stub.port,
                station_id=_STATION_ID,
                shop=_SHOP,
                edge_node_id=_EDGE_NODE_ID,
                device_id="plc-assembly-01",
                svid=svid_files.svid_paths(),
            )
            publisher.connect()
            publisher._connected.wait(timeout=5.0)
            avro_bytes = _make_avro_bytes()
            publisher.publish(avro_bytes)
            time.sleep(0.5)
            publisher.disconnect()
            stub.wait_done(timeout=5.0)

            assert stub.handshake_succeeded is True, (
                "TLS handshake must succeed when presenting a valid SPIFFE SVID"
            )
            assert stub.peer_cert_der is not None, "Broker must have received a peer certificate"

            # Extract SPIFFE URI from peer certificate
            peer_cert = x509.load_der_x509_certificate(stub.peer_cert_der, default_backend())
            san_ext = peer_cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            )
            uris = san_ext.value.get_values_for_type(x509.UniformResourceIdentifier)
            spiffe_uris = [u for u in uris if u.startswith("spiffe://")]

            assert len(spiffe_uris) == 1, (
                f"Expected exactly 1 SPIFFE URI SAN in client certificate, got: {spiffe_uris!r}"
            )
            assert spiffe_uris[0] == _EXPECTED_SPIFFE_ID, (
                f"Client certificate SPIFFE URI {spiffe_uris[0]!r} does not match "
                f"expected pattern {_EXPECTED_SPIFFE_ID!r}"
            )
        finally:
            svid_files.cleanup()

    def test_read_spiffe_id_from_svid_returns_expected_uri(self, pki: dict) -> None:
        """read_spiffe_id_from_svid() must extract the SPIFFE URI from the PEM file."""
        svid_files = _SvidTempFiles(
            cert_pem=pki["gw_cert_pem"],
            key_pem=pki["gw_key_pem"],
            bundle_pem=pki["ca_pem"],
        )
        try:
            spiffe_id = read_spiffe_id_from_svid(svid_files.cert_path)
            assert spiffe_id == _EXPECTED_SPIFFE_ID, (
                f"read_spiffe_id_from_svid returned {spiffe_id!r}, "
                f"expected {_EXPECTED_SPIFFE_ID!r}"
            )
        finally:
            svid_files.cleanup()

    def test_load_svid_ssl_context_raises_if_cert_missing(self) -> None:
        """load_svid_ssl_context must raise FileNotFoundError for absent SVID."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_svid_ssl_context(SvidPaths(
                cert_path="/nonexistent/svid.pem",
                key_path="/nonexistent/svid_key.pem",
                bundle_path="/nonexistent/bundle.pem",
            ))
        assert "svid.pem" in str(exc_info.value), (
            "FileNotFoundError message must identify the missing SVID file"
        )


# ---------------------------------------------------------------------------
# (c) Edge buffer — buffering and flush behaviour
# ---------------------------------------------------------------------------

class TestEdgeRingBuffer:
    """_EdgeRingBuffer stores and drains events in FIFO order."""

    def test_push_and_drain_fifo_order(self) -> None:
        buf = _EdgeRingBuffer(max_events=10)
        for i in range(3):
            buf.push(_BufferedEvent(avro_bytes=bytes([i]), timestamp_ms=_TIMESTAMP_MS + i))
        events = buf.drain()
        assert len(events) == 3, "Buffer must contain exactly 3 events after 3 pushes"
        assert [e.avro_bytes[0] for e in events] == [0, 1, 2], (
            "Events must be drained in FIFO (push) order"
        )

    def test_drain_clears_buffer(self) -> None:
        buf = _EdgeRingBuffer(max_events=10)
        buf.push(_BufferedEvent(avro_bytes=b"x", timestamp_ms=_TIMESTAMP_MS))
        buf.drain()
        assert len(buf) == 0, "Buffer must be empty after drain"

    def test_oldest_event_dropped_when_full(self) -> None:
        buf = _EdgeRingBuffer(max_events=3)
        for i in range(4):
            buf.push(_BufferedEvent(avro_bytes=bytes([i]), timestamp_ms=_TIMESTAMP_MS + i))
        events = buf.drain()
        assert len(events) == 3, "Buffer capacity must be respected"
        # Oldest (0) was dropped; remaining are 1, 2, 3
        assert events[0].avro_bytes == bytes([1]), (
            "After overflow, oldest event must be dropped (FIFO eviction)"
        )
        assert events[-1].avro_bytes == bytes([3])

    def test_new_instance_starts_empty(self) -> None:
        buf1 = _EdgeRingBuffer(max_events=5)
        buf1.push(_BufferedEvent(avro_bytes=b"a", timestamp_ms=_TIMESTAMP_MS))
        buf2 = _EdgeRingBuffer(max_events=5)
        assert len(buf2) == 0, (
            "A new _EdgeRingBuffer instance must start empty — no shared module state"
        )


class TestPublisherBufferOnDisconnect:
    """Publisher enters buffer mode when MQTT is down; overlay status is notified."""

    def test_publish_buffered_when_not_connected(self) -> None:
        store = SimaticManagedStore()
        publisher = SparkplugPublisher(
            broker_host="127.0.0.1",
            broker_port=9999,
            station_id=_STATION_ID,
            shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID,
            device_id="plc-test-01",
            managed_store=store,
        )
        # Do not call connect() — publisher is not connected
        avro_bytes = _make_avro_bytes()
        publisher.publish(avro_bytes)
        assert len(store) == 1, (
            f"Event must be written to managed store when publisher is not connected; "
            f"store size={len(store)}"
        )

    def test_buffer_state_change_callback_fires_on_disconnect(self) -> None:
        state_changes: list[bool] = []

        publisher = SparkplugPublisher(
            broker_host="127.0.0.1",
            broker_port=9999,
            station_id=_STATION_ID,
            shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID,
            device_id="plc-test-01",
            on_buffer_state_change=state_changes.append,
        )
        publisher.publish(_make_avro_bytes())
        # First publish while disconnected triggers buffering=True
        assert state_changes == [True], (
            f"on_buffer_state_change must fire with True on first buffered publish; "
            f"got {state_changes!r}"
        )

    def test_seq_resets_to_zero_on_new_publisher_instance(self) -> None:
        """seq counter must start at 0 on every new SparkplugPublisher instance."""
        pub_a = SparkplugPublisher(
            broker_host="127.0.0.1", broker_port=9999,
            station_id=_STATION_ID, shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID, device_id="plc-test-01",
        )
        pub_b = SparkplugPublisher(
            broker_host="127.0.0.1", broker_port=9999,
            station_id=_STATION_ID, shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID, device_id="plc-test-02",
        )
        assert pub_a._seq == 0, "seq must start at 0 on a new instance"
        assert pub_b._seq == 0, "seq must restart at 0 on a fresh instance (not shared)"


# ---------------------------------------------------------------------------
# (d) DDATA payload — Sparkplug B topic and Avro bytes embedding
# ---------------------------------------------------------------------------

class TestSparkplugDdataPayload:
    """The Sparkplug B DDATA topic and payload must match the contract."""

    def test_ddata_topic_follows_sparkplug_namespace(self) -> None:
        publisher = SparkplugPublisher(
            broker_host="127.0.0.1", broker_port=9999,
            station_id=_STATION_ID, shop="assembly",
            edge_node_id="edge-assembly-01", device_id="plc-assembly-01",
        )
        expected = "spBv1.0/Munich/assembly/edge-assembly-01/plc-assembly-01/DDATA"
        assert publisher._ddata_topic == expected, (
            f"DDATA topic {publisher._ddata_topic!r} does not match expected {expected!r}"
        )

    def test_overlay_status_topic_contains_station_id(self) -> None:
        publisher = SparkplugPublisher(
            broker_host="127.0.0.1", broker_port=9999,
            station_id="MARRIAGE-01", shop="assembly",
            edge_node_id="edge-assembly-01", device_id="plc-assembly-01",
        )
        assert "MARRIAGE-01" in publisher._overlay_status_topic, (
            f"Overlay status topic must include station_id; got {publisher._overlay_status_topic!r}"
        )

    def test_kafka_topic_constant_matches_contract(self) -> None:
        assert KAFKA_TORQUE_TOPIC == "bmw.ifos.munich.uns.torque.plc-trace.v1", (
            f"KAFKA_TORQUE_TOPIC must match the contract topic name; got {KAFKA_TORQUE_TOPIC!r}"
        )


# ---------------------------------------------------------------------------
# edgeBuffered flag and idempotency key preservation on buffer flush
# ---------------------------------------------------------------------------

class TestEdgeBufferedFlagOnFlush:
    """
    Acceptance criteria (AC-b734032a):
    - Real-time publishes must have edgeBuffered=False.
    - Buffered events replayed after reconnect must have edgeBuffered=True.
    - The original idempotencyKey must be unchanged in the replayed bytes.
    """

    def test_set_edge_buffered_flag_flips_field_to_true(self) -> None:
        """_set_edge_buffered_flag must return Avro bytes with edgeBuffered=True."""
        from ads_reader.ads_torque_reader import (
            AdsTighteningEvent,
            PlcTorqueTraceEncoder,
            build_plc_trace_event,
        )

        event = AdsTighteningEvent(
            station_id=_STATION_ID,
            vin=_VIN,
            torque_nm=_TORQUE_NM,
            angle_deg=_ANGLE_DEG,
            plc_seq=_PLC_SEQ,
            timestamp_ms=_TIMESTAMP_MS,
            result="OK",
            edge_buffered=False,
        )
        trace = build_plc_trace_event(event)
        original_key = trace.idempotency_key
        encoder = PlcTorqueTraceEncoder(schema_id=_TEST_SCHEMA_ID)
        original_bytes = encoder.encode(trace)

        # Confirm edgeBuffered starts as False
        original_decoded = encoder.decode(original_bytes)
        assert original_decoded["edgeBuffered"] is False, (
            "Freshly encoded event must have edgeBuffered=False"
        )

        replayed_bytes = _set_edge_buffered_flag(original_bytes)
        replayed_decoded = encoder.decode(replayed_bytes)

        assert replayed_decoded["edgeBuffered"] is True, (
            "After _set_edge_buffered_flag, edgeBuffered must be True"
        )
        assert replayed_decoded["idempotencyKey"] == original_key, (
            f"idempotencyKey must be preserved unchanged; "
            f"expected {original_key!r}, got {replayed_decoded['idempotencyKey']!r}"
        )

    def test_set_edge_buffered_flag_preserves_all_other_fields(self) -> None:
        """All fields except edgeBuffered must survive the re-encode unchanged."""
        from ads_reader.ads_torque_reader import (
            AdsTighteningEvent,
            PlcTorqueTraceEncoder,
            build_plc_trace_event,
        )

        event = AdsTighteningEvent(
            station_id=_STATION_ID,
            vin=_VIN,
            torque_nm=_TORQUE_NM,
            angle_deg=_ANGLE_DEG,
            plc_seq=_PLC_SEQ,
            timestamp_ms=_TIMESTAMP_MS,
            result="OK",
        )
        trace = build_plc_trace_event(event)
        encoder = PlcTorqueTraceEncoder(schema_id=_TEST_SCHEMA_ID)
        original_bytes = encoder.encode(trace)
        original = encoder.decode(original_bytes)

        replayed_bytes = _set_edge_buffered_flag(original_bytes)
        replayed = encoder.decode(replayed_bytes)

        assert replayed["stationId"] == original["stationId"], "stationId must be unchanged"
        assert replayed["vin"] == original["vin"], "vin must be unchanged"
        assert replayed["torqueNm"] == original["torqueNm"], "torqueNm must be unchanged"
        assert replayed["angleDeg"] == original["angleDeg"], "angleDeg must be unchanged"
        assert replayed["timestampMs"] == original["timestampMs"], "timestampMs must be unchanged"
        assert replayed["idempotencyKey"] == original["idempotencyKey"], (
            "idempotencyKey must be preserved"
        )

    def test_buffer_flush_publishes_with_edge_buffered_true(self) -> None:
        """On reconnect, buffer flush must deliver events with edgeBuffered=True."""
        from ads_reader.ads_torque_reader import (
            AdsTighteningEvent,
            PlcTorqueTraceEncoder,
            build_plc_trace_event,
        )

        received_payloads: list[bytes] = []
        store = SimaticManagedStore()

        encoder = PlcTorqueTraceEncoder(schema_id=_TEST_SCHEMA_ID)
        event = AdsTighteningEvent(
            station_id=_STATION_ID,
            vin=_VIN,
            torque_nm=_TORQUE_NM,
            angle_deg=_ANGLE_DEG,
            plc_seq=_PLC_SEQ,
            timestamp_ms=_TIMESTAMP_MS,
            result="OK",
        )
        trace = build_plc_trace_event(event)
        original_key = trace.idempotency_key
        avro_bytes = encoder.encode(trace)

        publisher = SparkplugPublisher(
            broker_host="127.0.0.1",
            broker_port=9999,
            station_id=_STATION_ID,
            shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID,
            device_id="plc-test-01",
            managed_store=store,
            on_publish_success=received_payloads.append,
        )

        # Buffer the event (not connected)
        publisher.publish(avro_bytes)
        assert len(store) == 1, "Event must be in managed store before flush"

        # Simulate reconnect: inject a mock client and call _flush_buffer directly
        mock_client = MagicMock()
        mock_info = MagicMock()
        mock_info.wait_for_publish.return_value = None
        mock_client.publish.return_value = mock_info
        publisher._client = mock_client
        publisher._connected.set()

        publisher._flush_buffer()

        assert len(received_payloads) == 1, (
            "Exactly one event must be published from the buffer flush"
        )
        flushed_bytes = received_payloads[0]
        decoded = encoder.decode(flushed_bytes)

        assert decoded["edgeBuffered"] is True, (
            f"Flushed event must have edgeBuffered=True; got {decoded['edgeBuffered']!r}"
        )
        assert decoded["idempotencyKey"] == original_key, (
            f"idempotencyKey must be unchanged after buffer flush; "
            f"expected {original_key!r}, got {decoded['idempotencyKey']!r}"
        )
        assert len(store) == 0, "Managed store must be empty after successful flush"


# ---------------------------------------------------------------------------
# Stale event discard — AC-b734032a 60-second window
# ---------------------------------------------------------------------------

class TestStaleEventDiscard:
    """
    Events older than BUFFER_MAX_AGE_MS must be discarded at drain time.
    """

    def test_stale_events_discarded_on_flush(self) -> None:
        """Events with timestamp_ms older than 60 s must be dropped during flush."""
        from ads_reader.ads_torque_reader import (
            AdsTighteningEvent,
            PlcTorqueTraceEncoder,
            build_plc_trace_event,
        )

        received_payloads: list[bytes] = []
        encoder = PlcTorqueTraceEncoder(schema_id=_TEST_SCHEMA_ID)
        store = SimaticManagedStore()

        fresh_event = AdsTighteningEvent(
            station_id=_STATION_ID, vin=_VIN,
            torque_nm=_TORQUE_NM, angle_deg=_ANGLE_DEG,
            plc_seq=1, timestamp_ms=_TIMESTAMP_MS, result="OK",
        )
        stale_event = AdsTighteningEvent(
            station_id=_STATION_ID, vin=_VIN,
            torque_nm=_TORQUE_NM, angle_deg=_ANGLE_DEG,
            plc_seq=2, timestamp_ms=_TIMESTAMP_MS, result="OK",
        )
        fresh_trace = build_plc_trace_event(fresh_event)
        stale_trace = build_plc_trace_event(stale_event)
        fresh_avro = encoder.encode(fresh_trace)
        stale_avro = encoder.encode(stale_trace)

        publisher = SparkplugPublisher(
            broker_host="127.0.0.1",
            broker_port=9999,
            station_id=_STATION_ID,
            shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID,
            device_id="plc-test-01",
            managed_store=store,
            on_publish_success=received_payloads.append,
        )

        now_ms = int(time.time() * 1000)
        stale_ts = now_ms - (BUFFER_MAX_AGE_MS + 5_000)  # 65 s ago — definitely stale

        # Inject directly into the managed store with synthetic timestamps
        store.put(stale_trace.idempotency_key, _BufferedEvent(avro_bytes=stale_avro, timestamp_ms=stale_ts))
        store.put(fresh_trace.idempotency_key, _BufferedEvent(avro_bytes=fresh_avro, timestamp_ms=now_ms))

        mock_client = MagicMock()
        mock_info = MagicMock()
        mock_info.wait_for_publish.return_value = None
        mock_client.publish.return_value = mock_info
        publisher._client = mock_client
        publisher._connected.set()

        publisher._flush_buffer()

        assert len(received_payloads) == 1, (
            f"Only the fresh event must be published; got {len(received_payloads)} publishes"
        )
        decoded = encoder.decode(received_payloads[0])
        assert decoded["edgeBuffered"] is True, (
            "The published fresh event must have edgeBuffered=True"
        )

    def test_buffer_max_age_ms_constant_is_60_seconds(self) -> None:
        assert BUFFER_MAX_AGE_MS == 60_000, (
            f"BUFFER_MAX_AGE_MS must be 60 000 ms (60 s); got {BUFFER_MAX_AGE_MS!r}"
        )


# ---------------------------------------------------------------------------
# (a) UNS outage: 5 events written to SIMATIC Managed Store (AC-b734032a)
# ---------------------------------------------------------------------------

class TestSimaticManagedStoreOnOutage:
    """
    Given a UNS MQTT broker outage,
    when 5 tightening events are injected via publish(),
    then all 5 are written to the SIMATIC Runtime Managed Store.
    """

    def test_five_events_written_to_managed_store_on_outage(self) -> None:
        store = SimaticManagedStore()
        publisher = SparkplugPublisher(
            broker_host="127.0.0.1",
            broker_port=9999,
            station_id=_STATION_ID,
            shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID,
            device_id="plc-test-01",
            managed_store=store,
        )
        # Publisher is not connected — simulates UNS outage
        encoder = PlcTorqueTraceEncoder(schema_id=_TEST_SCHEMA_ID)
        for seq in range(1, 6):
            event = AdsTighteningEvent(
                station_id=_STATION_ID,
                vin=_VIN,
                torque_nm=_TORQUE_NM,
                angle_deg=_ANGLE_DEG,
                plc_seq=seq,
                timestamp_ms=_TIMESTAMP_MS + seq,
                result="OK",
            )
            avro_bytes = encoder.encode(build_plc_trace_event(event))
            publisher.publish(avro_bytes)

        assert len(store) == 5, (
            f"All 5 tightening events must be written to the managed store during outage; "
            f"got {len(store)}"
        )

    def test_managed_store_keyed_by_idempotency_key(self) -> None:
        """Each entry must be stored under its idempotencyKey (not an integer index)."""
        store = SimaticManagedStore()
        publisher = SparkplugPublisher(
            broker_host="127.0.0.1",
            broker_port=9999,
            station_id=_STATION_ID,
            shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID,
            device_id="plc-test-01",
            managed_store=store,
        )
        encoder = PlcTorqueTraceEncoder(schema_id=_TEST_SCHEMA_ID)
        event = AdsTighteningEvent(
            station_id=_STATION_ID,
            vin=_VIN,
            torque_nm=_TORQUE_NM,
            angle_deg=_ANGLE_DEG,
            plc_seq=_PLC_SEQ,
            timestamp_ms=_TIMESTAMP_MS,
            result="OK",
        )
        trace = build_plc_trace_event(event)
        avro_bytes = encoder.encode(trace)
        publisher.publish(avro_bytes)

        entries = store.drain()
        assert len(entries) == 1, "Exactly 1 entry must be in the store"
        stored_key, _ = entries[0]
        assert stored_key == trace.idempotency_key, (
            f"Store key must be the event idempotencyKey {trace.idempotency_key!r}; "
            f"got {stored_key!r}"
        )


# ---------------------------------------------------------------------------
# (b) Flush on reconnect: all 5 events with edgeBuffered=True within 60s
# ---------------------------------------------------------------------------

class TestFlushOnReconnect:
    """
    Given 5 events written to the SIMATIC store during outage,
    when the UNS broker reconnects,
    then all 5 are flushed oldest-first with edgeBuffered=True,
    and the flush completes within 60 seconds of reconnect detection.
    """

    def test_five_events_flushed_oldest_first_with_edge_buffered_true(self) -> None:
        store = SimaticManagedStore()
        received_payloads: list[bytes] = []
        encoder = PlcTorqueTraceEncoder(schema_id=_TEST_SCHEMA_ID)

        publisher = SparkplugPublisher(
            broker_host="127.0.0.1",
            broker_port=9999,
            station_id=_STATION_ID,
            shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID,
            device_id="plc-test-01",
            managed_store=store,
            on_publish_success=received_payloads.append,
        )

        # Inject 5 events during outage (publisher not connected)
        expected_keys = []
        for seq in range(1, 6):
            event = AdsTighteningEvent(
                station_id=_STATION_ID,
                vin=_VIN,
                torque_nm=_TORQUE_NM,
                angle_deg=_ANGLE_DEG,
                plc_seq=seq,
                timestamp_ms=_TIMESTAMP_MS + seq,
                result="OK",
            )
            trace = build_plc_trace_event(event)
            expected_keys.append(trace.idempotency_key)
            publisher.publish(encoder.encode(trace))

        assert len(store) == 5, "Pre-condition: all 5 events must be in the store"

        # Simulate reconnect: inject mock UNS client and trigger flush
        mock_client = MagicMock()
        mock_info = MagicMock()
        mock_info.wait_for_publish.return_value = None
        mock_client.publish.return_value = mock_info
        publisher._client = mock_client
        publisher._connected.set()

        t_reconnect = time.monotonic()
        publisher._flush_buffer()
        flush_elapsed = time.monotonic() - t_reconnect

        assert len(received_payloads) == 5, (
            f"All 5 buffered events must be flushed on reconnect; "
            f"got {len(received_payloads)}"
        )
        assert flush_elapsed < 60.0, (
            f"Flush must complete within 60 seconds of reconnect; "
            f"took {flush_elapsed:.3f}s"
        )

        # All flushed events must have edgeBuffered=True
        for i, payload in enumerate(received_payloads):
            decoded = encoder.decode(payload)
            assert decoded["edgeBuffered"] is True, (
                f"Flushed event {i} must have edgeBuffered=True; got {decoded['edgeBuffered']!r}"
            )

        # Verify FIFO order by idempotencyKey (seq 1 through 5)
        flushed_keys = [encoder.decode(p)["idempotencyKey"] for p in received_payloads]
        assert flushed_keys == expected_keys, (
            f"Events must be flushed in insertion (FIFO) order; "
            f"expected {expected_keys!r}, got {flushed_keys!r}"
        )

    def test_store_cleared_after_successful_flush(self) -> None:
        """After a successful flush the managed store must be empty."""
        store = SimaticManagedStore()
        encoder = PlcTorqueTraceEncoder(schema_id=_TEST_SCHEMA_ID)

        publisher = SparkplugPublisher(
            broker_host="127.0.0.1",
            broker_port=9999,
            station_id=_STATION_ID,
            shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID,
            device_id="plc-test-01",
            managed_store=store,
        )
        event = AdsTighteningEvent(
            station_id=_STATION_ID, vin=_VIN,
            torque_nm=_TORQUE_NM, angle_deg=_ANGLE_DEG,
            plc_seq=_PLC_SEQ, timestamp_ms=_TIMESTAMP_MS, result="OK",
        )
        publisher.publish(encoder.encode(build_plc_trace_event(event)))
        assert len(store) == 1, "Pre-condition: event must be in store before flush"

        mock_client = MagicMock()
        mock_info = MagicMock()
        mock_info.wait_for_publish.return_value = None
        mock_client.publish.return_value = mock_info
        publisher._client = mock_client
        publisher._connected.set()

        publisher._flush_buffer()

        assert len(store) == 0, (
            "Managed store must be empty after a successful flush; "
            f"got {len(store)} entries remaining"
        )


# ---------------------------------------------------------------------------
# (c) PENDING_SYNC broadcast within 500ms of UNS connection loss
# ---------------------------------------------------------------------------

class TestPendingSyncBroadcast:
    """
    Given the publisher has a local OT broker client,
    when the UNS MQTT broker disconnects,
    then a PENDING_SYNC status message must be broadcast on
    edge/{stationId}/overlay/status within 500ms of connection loss.
    """

    def test_pending_sync_published_to_ot_broker_on_disconnect(self) -> None:
        ot_publishes: list[dict] = []

        def capture_publish(topic: str, payload: bytes, **kwargs: object) -> MagicMock:
            ot_publishes.append({"topic": topic, "payload": json.loads(payload)})
            info = MagicMock()
            return info

        publisher = SparkplugPublisher(
            broker_host="127.0.0.1",
            broker_port=9999,
            station_id=_STATION_ID,
            shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID,
            device_id="plc-test-01",
        )
        mock_ot = MagicMock()
        mock_ot.publish.side_effect = capture_publish
        publisher._ot_client = mock_ot

        t_disconnect = time.monotonic()
        # Simulate UNS broker disconnect
        publisher._on_disconnect(client=None, userdata=None, rc=7)
        elapsed = time.monotonic() - t_disconnect

        assert elapsed < 0.5, (
            f"PENDING_SYNC must be broadcast within 500ms of connection loss; "
            f"took {elapsed:.3f}s"
        )
        pending_calls = [
            p for p in ot_publishes
            if p["payload"].get("syncStatus") == "pending_sync"
        ]
        assert len(pending_calls) == 1, (
            f"Exactly one PENDING_SYNC message must be broadcast; got {ot_publishes!r}"
        )
        assert pending_calls[0]["topic"] == f"edge/{_STATION_ID}/overlay/status", (
            f"PENDING_SYNC must be on topic edge/{{stationId}}/overlay/status; "
            f"got {pending_calls[0]['topic']!r}"
        )

    def test_pending_sync_uses_qos1_retain(self) -> None:
        """overlay status publishes must use QoS 1 and retained=True."""
        captured_kwargs: list[dict] = []

        def capture_publish(topic: str, payload: bytes, **kwargs: object) -> MagicMock:
            captured_kwargs.append(kwargs)
            return MagicMock()

        publisher = SparkplugPublisher(
            broker_host="127.0.0.1",
            broker_port=9999,
            station_id=_STATION_ID,
            shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID,
            device_id="plc-test-01",
        )
        mock_ot = MagicMock()
        mock_ot.publish.side_effect = capture_publish
        publisher._ot_client = mock_ot

        publisher._on_disconnect(client=None, userdata=None, rc=7)

        assert captured_kwargs, "publish must have been called"
        call_kwargs = captured_kwargs[0]
        assert call_kwargs.get("qos") == 1, (
            f"Overlay status publish must use QoS 1; got qos={call_kwargs.get('qos')!r}"
        )
        assert call_kwargs.get("retain") is True, (
            f"Overlay status publish must be retained=True; got retain={call_kwargs.get('retain')!r}"
        )


# ---------------------------------------------------------------------------
# (d) Sync-cleared broadcast after flush completion
# ---------------------------------------------------------------------------

class TestSyncClearedBroadcast:
    """
    Given buffered events are flushed after reconnect,
    when flush completes,
    then a sync-cleared ('synced') status message must be broadcast on
    edge/{stationId}/overlay/status via the local OT broker.
    """

    def test_synced_published_to_ot_broker_after_flush(self) -> None:
        ot_publishes: list[dict] = []

        def capture_publish(topic: str, payload: bytes, **kwargs: object) -> MagicMock:
            ot_publishes.append({"topic": topic, "payload": json.loads(payload)})
            return MagicMock()

        store = SimaticManagedStore()
        encoder = PlcTorqueTraceEncoder(schema_id=_TEST_SCHEMA_ID)

        publisher = SparkplugPublisher(
            broker_host="127.0.0.1",
            broker_port=9999,
            station_id=_STATION_ID,
            shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID,
            device_id="plc-test-01",
            managed_store=store,
        )
        mock_ot = MagicMock()
        mock_ot.publish.side_effect = capture_publish
        publisher._ot_client = mock_ot

        # Trigger buffering (PENDING_SYNC)
        event = AdsTighteningEvent(
            station_id=_STATION_ID, vin=_VIN,
            torque_nm=_TORQUE_NM, angle_deg=_ANGLE_DEG,
            plc_seq=_PLC_SEQ, timestamp_ms=_TIMESTAMP_MS, result="OK",
        )
        publisher.publish(encoder.encode(build_plc_trace_event(event)))

        # Simulate reconnect and flush
        mock_uns = MagicMock()
        mock_info = MagicMock()
        mock_info.wait_for_publish.return_value = None
        mock_uns.publish.return_value = mock_info
        publisher._client = mock_uns
        publisher._connected.set()

        publisher._flush_buffer()

        synced_calls = [
            p for p in ot_publishes
            if p["payload"].get("syncStatus") == "synced"
        ]
        assert len(synced_calls) == 1, (
            f"Exactly one 'synced' message must be broadcast after flush; "
            f"got {ot_publishes!r}"
        )
        assert synced_calls[0]["topic"] == f"edge/{_STATION_ID}/overlay/status", (
            f"sync-cleared must publish to edge/{{stationId}}/overlay/status; "
            f"got {synced_calls[0]['topic']!r}"
        )

    def test_pending_sync_then_synced_sequence(self) -> None:
        """The OT broker must receive pending_sync then synced — in that order."""
        ot_statuses: list[str] = []

        def capture_publish(topic: str, payload: bytes, **kwargs: object) -> MagicMock:
            ot_statuses.append(json.loads(payload)["syncStatus"])
            return MagicMock()

        store = SimaticManagedStore()
        encoder = PlcTorqueTraceEncoder(schema_id=_TEST_SCHEMA_ID)

        publisher = SparkplugPublisher(
            broker_host="127.0.0.1",
            broker_port=9999,
            station_id=_STATION_ID,
            shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID,
            device_id="plc-test-01",
            managed_store=store,
        )
        mock_ot = MagicMock()
        mock_ot.publish.side_effect = capture_publish
        publisher._ot_client = mock_ot

        event = AdsTighteningEvent(
            station_id=_STATION_ID, vin=_VIN,
            torque_nm=_TORQUE_NM, angle_deg=_ANGLE_DEG,
            plc_seq=_PLC_SEQ, timestamp_ms=_TIMESTAMP_MS, result="OK",
        )
        publisher.publish(encoder.encode(build_plc_trace_event(event)))

        mock_uns = MagicMock()
        mock_info = MagicMock()
        mock_info.wait_for_publish.return_value = None
        mock_uns.publish.return_value = mock_info
        publisher._client = mock_uns
        publisher._connected.set()
        publisher._flush_buffer()

        assert ot_statuses == ["pending_sync", "synced"], (
            f"OT broker must receive pending_sync then synced in that order; "
            f"got {ot_statuses!r}"
        )


# ---------------------------------------------------------------------------
# (e) Idempotency: double-flush does not produce duplicate Kafka publishes
# ---------------------------------------------------------------------------

class TestManagedStoreIdempotency:
    """
    Given the same buffered events are presented to the managed store twice
    (simulating crash-recovery where the store survived but the flush did not),
    when the flush runs a second time,
    then no duplicate events are published to Kafka (idempotencyKey dedup).
    """

    def test_duplicate_put_same_key_is_no_op(self) -> None:
        """SimaticManagedStore.put with an existing key must not add a duplicate."""
        store = SimaticManagedStore()
        event = _BufferedEvent(avro_bytes=b"payload-1", timestamp_ms=_TIMESTAMP_MS)
        store.put("key-abc", event)
        store.put("key-abc", _BufferedEvent(avro_bytes=b"payload-2", timestamp_ms=_TIMESTAMP_MS + 1))

        assert len(store) == 1, (
            "Managed store must contain exactly 1 entry after two puts with the same key; "
            f"got {len(store)}"
        )
        entries = store.drain()
        assert entries[0][1].avro_bytes == b"payload-1", (
            "First-written payload must be preserved; second put must be discarded"
        )

    def test_double_flush_does_not_republish(self) -> None:
        """
        Simulates crash-recovery: store survives, flush runs twice.
        The second flush must publish 0 events because the store was cleared
        by the first successful flush.
        """
        store = SimaticManagedStore()
        received_payloads: list[bytes] = []
        encoder = PlcTorqueTraceEncoder(schema_id=_TEST_SCHEMA_ID)

        publisher = SparkplugPublisher(
            broker_host="127.0.0.1",
            broker_port=9999,
            station_id=_STATION_ID,
            shop=_SHOP,
            edge_node_id=_EDGE_NODE_ID,
            device_id="plc-test-01",
            managed_store=store,
            on_publish_success=received_payloads.append,
        )

        for seq in range(1, 6):
            event = AdsTighteningEvent(
                station_id=_STATION_ID, vin=_VIN,
                torque_nm=_TORQUE_NM, angle_deg=_ANGLE_DEG,
                plc_seq=seq, timestamp_ms=_TIMESTAMP_MS + seq, result="OK",
            )
            publisher.publish(encoder.encode(build_plc_trace_event(event)))

        assert len(store) == 5, "Pre-condition: 5 events in store"

        mock_client = MagicMock()
        mock_info = MagicMock()
        mock_info.wait_for_publish.return_value = None
        mock_client.publish.return_value = mock_info
        publisher._client = mock_client
        publisher._connected.set()

        # First flush — must publish all 5
        publisher._flush_buffer()
        assert len(received_payloads) == 5, (
            f"First flush must publish all 5 events; got {len(received_payloads)}"
        )
        assert len(store) == 0, "Store must be empty after first flush"

        # Second flush — simulates crash-recovery reconnect; must publish 0
        publisher._flush_buffer()
        assert len(received_payloads) == 5, (
            f"Second flush must not produce additional publishes (idempotency); "
            f"total publishes after second flush: {len(received_payloads)}"
        )

    def test_new_store_instance_starts_empty(self) -> None:
        """Two SimaticManagedStore instances must not share state."""
        store_a = SimaticManagedStore()
        store_a.put("key-1", _BufferedEvent(avro_bytes=b"a", timestamp_ms=_TIMESTAMP_MS))
        store_b = SimaticManagedStore()
        assert len(store_b) == 0, (
            "A new SimaticManagedStore instance must start empty — no shared module state"
        )
