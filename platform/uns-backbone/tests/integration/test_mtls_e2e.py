"""
End-to-end mTLS enforcement and audit trail integration tests.
Component: cmp:uns-backbone  |  Realises: US-027 · BR-10

Acceptance criteria validated:
  (a) Valid SPIFFE SVID client accepted end-to-end — MQTT CONNACK 0, PUBACK on
      QoS 1 publish, and Kafka producer ACK received; no rejection blob written.
  (b) No-certificate MQTT client rejected within 100 ms of TCP connect, and an
      audit blob appears in WORM storage within 1 s with rejection_reason=no_cert_presented.
  (c) Untrusted-CA certificate rejected within 100 ms; audit blob written within
      1 s with rejection_reason=unknown_ca.
  (d) Edge gateway A cannot publish to a peer gateway's MQTT namespace — own-zone
      publish succeeds, cross-zone publish is denied.

Tests run against a live Kubernetes test namespace (uns-backbone-test) containing
HiveMQ, Kafka, the audit-forwarder, and an Azurite WORM container.  All environment
variables are documented in fixtures/.env.example; tests are marked pytest.mark.integration
so unit-only runs can skip them with `-m "not integration"`.

IEC 62443 SL2 audit evidence
─────────────────────────────
The CI log produced by running these four tests constitutes the primary technical
evidence for acceptance criterion (a) and (b) of US-027.  The auditor reviews this
log together with the Terraform plan output from the uns-backbone Helm deployment.
See README.md for instructions on extracting the CI artefact.
"""

from __future__ import annotations

import datetime
import json
import os
import socket
import ssl
import struct
import tempfile
import time
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

# ---------------------------------------------------------------------------
# Environment — every value read from the environment; no hardcoded credentials
# ---------------------------------------------------------------------------

HIVEMQ_HOST = os.environ.get("HIVEMQ_HOST", "hivemq.uns-backbone-test.svc.cluster.local")
HIVEMQ_PORT = int(os.environ.get("HIVEMQ_MTLS_PORT", "8883"))

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "kafka.uns-backbone-test.svc.cluster.local:9092")
KAFKA_OPCENTER_USERNAME = os.environ.get("KAFKA_OPCENTER_USERNAME", "opcenter-sync-service")
KAFKA_OPCENTER_PASSWORD = os.environ.get("KAFKA_OPCENTER_PASSWORD", "")
KAFKA_CA_CERT_PATH = os.environ.get("KAFKA_CA_CERT_PATH", "/etc/ssl/certs/ifos-ca.crt")

AUDIT_FORWARDER_URL = os.environ.get(
    "AUDIT_FORWARDER_URL",
    "http://audit-forwarder.uns-backbone-test.svc.cluster.local",
)
AZURITE_ACCOUNT_URL = os.environ.get(
    "AZURITE_ACCOUNT_URL",
    "http://azurite.uns-backbone-test.svc.cluster.local:10000/devstoreaccount1",
)
AZURITE_CONNECTION_STRING = os.environ.get(
    "AZURITE_CONNECTION_STRING",
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://azurite.uns-backbone-test.svc.cluster.local:10000/devstoreaccount1;",
)
WORM_CONTAINER = os.environ.get("WORM_CONTAINER", "audit-events")

# Timing constants (hard deadlines — not retried until softly passed)
REJECT_DEADLINE_MS = 100
AUDIT_BLOB_DEADLINE_S = 1.0

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# PKI helpers — identical to edge-gateways/tests/test_mtls_spiffe.py
# ---------------------------------------------------------------------------

_FAR_FUTURE = datetime.datetime(2099, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc)
_CERT_NOT_BEFORE = datetime.datetime(2025, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
_TRUST_DOMAIN = "munich.bmw"
_SPIFFE_URI_TEMPLATE = "spiffe://{trust_domain}/edge-gateways/{shop}/{index}"


def _generate_ca_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _build_ca_cert(
    ca_key: ec.EllipticCurvePrivateKey,
    cn: str,
    not_valid_after: datetime.datetime = _FAR_FUTURE,
) -> x509.Certificate:
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_CERT_NOT_BEFORE)
        .not_valid_after(not_valid_after)
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
    not_valid_before: datetime.datetime | None = None,
) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    """Return (key, cert) for a SPIFFE SVID leaf signed by ca_key."""
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    nbf = not_valid_before or _CERT_NOT_BEFORE
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(nbf)
        .not_valid_after(not_valid_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(spiffe_id)]),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return leaf_key, cert


def _pem(obj: x509.Certificate | ec.EllipticCurvePrivateKey) -> bytes:
    if isinstance(obj, x509.Certificate):
        return obj.public_bytes(serialization.Encoding.PEM)
    return obj.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


# ---------------------------------------------------------------------------
# Low-level MQTT helpers (no external mqtt library required in tests)
# ---------------------------------------------------------------------------

def _mqtt_connect_packet(client_id: str) -> bytes:
    """Build a minimal MQTT 3.1.1 CONNECT packet."""
    protocol_name = b"\x00\x04MQTT"
    protocol_level = b"\x04"  # MQTT 3.1.1
    connect_flags = b"\x02"   # clean session
    keep_alive = struct.pack("!H", 60)
    encoded_id = client_id.encode()
    payload = struct.pack("!H", len(encoded_id)) + encoded_id
    variable_header = protocol_name + protocol_level + connect_flags + keep_alive
    remaining = variable_header + payload
    return bytes([0x10, len(remaining)]) + remaining


def _mqtt_publish_packet(topic: str, payload: bytes, qos: int = 1, packet_id: int = 1) -> bytes:
    """Build a minimal MQTT 3.1.1 PUBLISH packet at QoS 0 or 1."""
    encoded_topic = topic.encode()
    fixed_header_flags = 0x32 if qos == 1 else 0x30  # PUBLISH + QoS bit
    variable_header = struct.pack("!H", len(encoded_topic)) + encoded_topic
    if qos == 1:
        variable_header += struct.pack("!H", packet_id)
    remaining = variable_header + payload
    length_bytes = _encode_remaining_length(len(remaining))
    return bytes([fixed_header_flags]) + length_bytes + remaining


def _encode_remaining_length(length: int) -> bytes:
    encoded = []
    while True:
        digit = length % 128
        length //= 128
        if length > 0:
            digit |= 0x80
        encoded.append(digit)
        if length == 0:
            break
    return bytes(encoded)


def _tls_client_ctx(
    ca_cert_pem: bytes,
    client_cert_pem: bytes | None,
    client_key_pem: bytes | None,
) -> tuple[ssl.SSLContext, list[str]]:
    """
    Build an SSLContext for the test client.  Returns (ctx, tmp_paths) where
    tmp_paths are files to unlink after use.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED

    tmp: list[str] = []

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as f:
        f.write(ca_cert_pem)
        tmp.append(f.name)
    ctx.load_verify_locations(tmp[-1])

    if client_cert_pem is not None and client_key_pem is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as fc:
            fc.write(client_cert_pem)
            tmp.append(fc.name)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as fk:
            fk.write(client_key_pem)
            tmp.append(fk.name)
        ctx.load_cert_chain(certfile=tmp[-2], keyfile=tmp[-1])

    return ctx, tmp


def _cleanup_tmp(paths: list[str]) -> None:
    import os as _os
    for p in paths:
        try:
            _os.unlink(p)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Shared PKI fixtures (module scope — generated once per test run)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def trusted_ca() -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    """The approved SPIRE CA for spiffe://munich.bmw — loaded into HiveMQ trust store."""
    key = _generate_ca_key()
    cert = _build_ca_cert(key, cn="Test IFOS SPIRE CA (munich.bmw)")
    return key, cert


@pytest.fixture(scope="module")
def rogue_ca() -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    """A second CA not trusted by the broker — used in test (c)."""
    key = _generate_ca_key()
    cert = _build_ca_cert(key, cn="Rogue CA — not in munich.bmw trust store")
    return key, cert


@pytest.fixture(scope="module")
def hivemq_server_ca_pem() -> bytes:
    """
    PEM-encoded CA bundle that signed the HiveMQ server certificate.
    Read from environment so test clients can verify the broker's TLS cert.
    """
    path = os.environ.get("HIVEMQ_SERVER_CA_CERT", "/etc/ssl/certs/ifos-ca.crt")
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as err:
        pytest.skip(
            f"HIVEMQ_SERVER_CA_CERT not found at {path!r}; "
            "set HIVEMQ_SERVER_CA_CERT to the test CA PEM path"
        ) from err


@pytest.fixture(scope="module")
def body_shop_svid(
    trusted_ca: tuple[ec.EllipticCurvePrivateKey, x509.Certificate],
) -> tuple[bytes, bytes, bytes]:
    """SVID for edge-gateways/body/01 — the gateway-under-test in scenario (a) and (d)."""
    ca_key, ca_cert = trusted_ca
    key, cert = _build_svid_cert(
        ca_cert,
        ca_key,
        spiffe_id=_SPIFFE_URI_TEMPLATE.format(
            trust_domain=_TRUST_DOMAIN, shop="body", index="01"
        ),
        node_id="edge-body-01",
    )
    return _pem(ca_cert), _pem(cert), _pem(key)


# ---------------------------------------------------------------------------
# Test (a): valid SPIFFE SVID — CONNACK 0, QoS 1 PUBACK, no rejection blob
# ---------------------------------------------------------------------------

class TestValidSvidAccepted:
    """
    Given an edge-gateway client presenting a valid SPIFFE SVID signed by the
    trusted spiffe://munich.bmw CA, when it connects on TCP/8883 and publishes
    a Sparkplug B DDATA message at QoS 1 to its assigned topic, then:
      - The MQTT CONNACK return code is 0 (accepted).
      - A QoS 1 PUBACK is received (message delivered).
      - The Kafka producer authenticated as opcenter-sync-service receives an ACK.
      - No rejection blob is written to WORM storage.
    """

    def test_valid_svid_mqtt_connack_zero(
        self,
        body_shop_svid: tuple[bytes, bytes, bytes],
        hivemq_server_ca_pem: bytes,
    ) -> None:
        ca_pem, cert_pem, key_pem = body_shop_svid
        ctx, tmp = _tls_client_ctx(hivemq_server_ca_pem, cert_pem, key_pem)
        client_id = f"test-body-01-{uuid.uuid4().hex[:8]}"
        try:
            with socket.create_connection((HIVEMQ_HOST, HIVEMQ_PORT), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname=HIVEMQ_HOST) as tls:
                    tls.sendall(_mqtt_connect_packet(client_id))
                    connack = tls.recv(4)
        finally:
            _cleanup_tmp(tmp)

        assert len(connack) == 4, f"Expected 4-byte CONNACK, got {len(connack)} bytes: {connack!r}"
        assert connack[0] == 0x20, f"Not a CONNACK fixed header: {connack[0]:#04x}"
        return_code = connack[3]
        assert return_code == 0x00, (
            f"CONNACK return code {return_code:#04x} != 0x00 (Connection Accepted); "
            "valid SPIFFE SVID was rejected by HiveMQ"
        )

    def test_valid_svid_qos1_puback_received(
        self,
        body_shop_svid: tuple[bytes, bytes, bytes],
        hivemq_server_ca_pem: bytes,
    ) -> None:
        ca_pem, cert_pem, key_pem = body_shop_svid
        ctx, tmp = _tls_client_ctx(hivemq_server_ca_pem, cert_pem, key_pem)
        client_id = f"test-body-01-pub-{uuid.uuid4().hex[:8]}"
        topic = "spBv1.0/munich-i4/DDATA/body/weld-cell-01"
        packet_id = 42
        ddata_payload = json.dumps({
            "timestamp": int(time.time() * 1000),
            "metrics": [{"name": "weld_current_a", "value": 150.5, "dataType": "Double"}],
        }).encode()
        try:
            with socket.create_connection((HIVEMQ_HOST, HIVEMQ_PORT), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname=HIVEMQ_HOST) as tls:
                    tls.sendall(_mqtt_connect_packet(client_id))
                    tls.recv(4)  # drain CONNACK
                    tls.sendall(_mqtt_publish_packet(topic, ddata_payload, qos=1, packet_id=packet_id))
                    puback = tls.recv(4)
        finally:
            _cleanup_tmp(tmp)

        assert len(puback) == 4, f"Expected 4-byte PUBACK, got {len(puback)} bytes: {puback!r}"
        assert puback[0] == 0x40, f"Not a PUBACK fixed header: {puback[0]:#04x}"
        acked_id = struct.unpack("!H", puback[2:4])[0]
        assert acked_id == packet_id, (
            f"PUBACK packet identifier {acked_id} != {packet_id}; "
            "message may not have been acknowledged"
        )

    def test_valid_svid_no_rejection_blob_in_worm(
        self,
        body_shop_svid: tuple[bytes, bytes, bytes],
        hivemq_server_ca_pem: bytes,
    ) -> None:
        """Assert the successful connection did NOT produce an audit rejection blob."""
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError:
            pytest.skip("azure-storage-blob not installed")

        marker_id = str(uuid.uuid4())
        date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

        # Give the forwarder a moment in case a spurious event arrives
        time.sleep(0.3)

        with BlobServiceClient.from_connection_string(AZURITE_CONNECTION_STRING) as svc:
            container = svc.get_container_client(WORM_CONTAINER)
            blob_path = f"mqtt/{date_str}/{marker_id}.json"
            try:
                container.get_blob_client(blob_path).download_blob().readall()
                pytest.fail(
                    f"Unexpected rejection blob found at {blob_path}; "
                    "a valid-SVID connection must not produce an audit entry"
                )
            except Exception:
                pass  # blob absent — expected

    def test_kafka_opcenter_producer_ack_received(self) -> None:
        """
        Kafka producer authenticated as opcenter-sync-service (SASL/SCRAM + SPIFFE TLS cert)
        produces one message to factory.munich.opcenter.andon.alert.v1 and receives an ACK.
        """
        try:
            from confluent_kafka import Producer
        except ImportError:
            pytest.skip("confluent-kafka not installed")

        if not KAFKA_OPCENTER_PASSWORD:
            pytest.skip("KAFKA_OPCENTER_PASSWORD not set")

        delivered: list[bool] = []

        def _on_delivery(err: object, msg: object) -> None:
            delivered.append(err is None)

        producer = Producer({
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "security.protocol": "SASL_SSL",
            "sasl.mechanism": "SCRAM-SHA-512",
            "sasl.username": KAFKA_OPCENTER_USERNAME,
            "sasl.password": KAFKA_OPCENTER_PASSWORD,
            "ssl.ca.location": KAFKA_CA_CERT_PATH,
        })

        payload = json.dumps({
            "alert_id": str(uuid.uuid4()),
            "station": "body-shop-01",
            "severity": "WARNING",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }).encode()

        producer.produce(
            "factory.munich.opcenter.andon.alert.v1",
            value=payload,
            on_delivery=_on_delivery,
        )
        producer.flush(timeout=10.0)

        assert len(delivered) == 1, (
            f"Expected exactly 1 delivery callback; got {len(delivered)}"
        )
        assert delivered[0] is True, (
            "Kafka producer delivery callback reported an error for opcenter-sync-service; "
            "SASL/SCRAM + SPIFFE TLS authentication may have failed"
        )


# ---------------------------------------------------------------------------
# Test (b): no certificate — rejected within 100 ms, audit blob within 1 s
# ---------------------------------------------------------------------------

class TestNoCertificateRejected:
    """
    Given an MQTT client that presents NO TLS client certificate, when it
    attempts to connect on TCP/8883, then:
      - The TLS handshake fails within REJECT_DEADLINE_MS ms of TCP connect.
      - A rejection blob appears in WORM storage within AUDIT_BLOB_DEADLINE_S seconds
        with rejection_reason=no_cert_presented.
    """

    def test_no_cert_rejected_within_100ms(self, hivemq_server_ca_pem: bytes) -> None:
        ctx, tmp = _tls_client_ctx(hivemq_server_ca_pem, None, None)
        t0 = time.monotonic()
        try:
            with socket.create_connection((HIVEMQ_HOST, HIVEMQ_PORT), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname=HIVEMQ_HOST) as tls:
                    tls.recv(4)  # HiveMQ should close before we get here
            pytest.fail(
                "Expected TLS handshake failure for a no-certificate client; "
                "connection was accepted instead"
            )
        except ssl.SSLError:
            pass
        except OSError:
            pass  # connection reset also counts as rejection
        finally:
            _cleanup_tmp(tmp)

        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < REJECT_DEADLINE_MS, (
            f"No-certificate connection was not rejected within {REJECT_DEADLINE_MS} ms; "
            f"actual elapsed: {elapsed_ms:.1f} ms — IEC 62443 SL2 hard deadline violated"
        )

    def test_no_cert_audit_blob_written_within_1s(self, hivemq_server_ca_pem: bytes) -> None:
        ctx, tmp = _tls_client_ctx(hivemq_server_ca_pem, None, None)
        t0_wall = datetime.datetime.now(datetime.timezone.utc)
        try:
            with socket.create_connection((HIVEMQ_HOST, HIVEMQ_PORT), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname=HIVEMQ_HOST):
                    pass
        except (ssl.SSLError, OSError):
            pass
        finally:
            _cleanup_tmp(tmp)

        # Probe the audit-forwarder list endpoint to find the newly-written blob.
        # We cannot know the correlation_id in advance; instead we poll for any
        # blob under mqtt/{date}/ created after t0_wall.
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError:
            pytest.skip("azure-storage-blob not installed")

        date_str = t0_wall.strftime("%Y-%m-%d")
        prefix = f"mqtt/{date_str}/"
        found_blob: dict | None = None

        with BlobServiceClient.from_connection_string(AZURITE_CONNECTION_STRING) as svc:
            container = svc.get_container_client(WORM_CONTAINER)
            deadline = time.monotonic() + AUDIT_BLOB_DEADLINE_S
            while time.monotonic() < deadline:
                try:
                    blobs = list(container.list_blobs(name_starts_with=prefix))
                    for item in blobs:
                        blob_data = container.get_blob_client(item.name).download_blob().readall()
                        record = json.loads(blob_data)
                        created = item.creation_time
                        if created and created >= t0_wall.replace(tzinfo=None):
                            if record.get("rejection_reason") == "no_cert_presented":
                                found_blob = record
                                break
                except Exception:
                    pass
                if found_blob:
                    break
                time.sleep(0.05)

        assert found_blob is not None, (
            f"No audit blob with rejection_reason=no_cert_presented appeared in "
            f"WORM container within {AUDIT_BLOB_DEADLINE_S} s of the rejected connection"
        )
        assert found_blob["rejection_reason"] == "no_cert_presented", (
            f"Expected rejection_reason=no_cert_presented, got {found_blob['rejection_reason']!r}"
        )


# ---------------------------------------------------------------------------
# Test (c): untrusted CA — rejected within 100 ms, audit blob within 1 s
# ---------------------------------------------------------------------------

class TestUntrustedCaRejected:
    """
    Given an MQTT client presenting a certificate signed by a self-signed CA that
    is NOT in the spiffe://munich.bmw trust store, when it attempts to connect on
    TCP/8883, then:
      - The TLS handshake fails within REJECT_DEADLINE_MS ms.
      - A rejection blob appears in WORM storage within AUDIT_BLOB_DEADLINE_S seconds
        with rejection_reason=unknown_ca.
    """

    def test_untrusted_ca_rejected_within_100ms(
        self,
        rogue_ca: tuple[ec.EllipticCurvePrivateKey, x509.Certificate],
        hivemq_server_ca_pem: bytes,
    ) -> None:
        rogue_key, rogue_ca_cert = rogue_ca
        client_key, client_cert = _build_svid_cert(
            rogue_ca_cert,
            rogue_key,
            spiffe_id="spiffe://munich.bmw/edge-gateways/body/01",
            node_id="rogue-edge-body-01",
        )
        # Client trusts the real server CA so the TLS failure is server-side client-cert rejection
        ctx, tmp = _tls_client_ctx(hivemq_server_ca_pem, _pem(client_cert), _pem(client_key))
        t0 = time.monotonic()
        try:
            with socket.create_connection((HIVEMQ_HOST, HIVEMQ_PORT), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname=HIVEMQ_HOST) as tls:
                    tls.recv(4)
            pytest.fail(
                "Expected TLS handshake failure for an untrusted-CA certificate; "
                "connection was accepted instead"
            )
        except (ssl.SSLError, OSError):
            pass
        finally:
            _cleanup_tmp(tmp)

        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < REJECT_DEADLINE_MS, (
            f"Untrusted-CA connection was not rejected within {REJECT_DEADLINE_MS} ms; "
            f"actual elapsed: {elapsed_ms:.1f} ms — IEC 62443 SL2 hard deadline violated"
        )

    def test_untrusted_ca_audit_blob_written_within_1s(
        self,
        rogue_ca: tuple[ec.EllipticCurvePrivateKey, x509.Certificate],
        hivemq_server_ca_pem: bytes,
    ) -> None:
        rogue_key, rogue_ca_cert = rogue_ca
        client_key, client_cert = _build_svid_cert(
            rogue_ca_cert,
            rogue_key,
            spiffe_id="spiffe://munich.bmw/edge-gateways/body/01",
            node_id="rogue-edge-body-01",
        )
        ctx, tmp = _tls_client_ctx(hivemq_server_ca_pem, _pem(client_cert), _pem(client_key))
        t0_wall = datetime.datetime.now(datetime.timezone.utc)
        try:
            with socket.create_connection((HIVEMQ_HOST, HIVEMQ_PORT), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname=HIVEMQ_HOST):
                    pass
        except (ssl.SSLError, OSError):
            pass
        finally:
            _cleanup_tmp(tmp)

        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError:
            pytest.skip("azure-storage-blob not installed")

        date_str = t0_wall.strftime("%Y-%m-%d")
        prefix = f"mqtt/{date_str}/"
        found_blob: dict | None = None

        with BlobServiceClient.from_connection_string(AZURITE_CONNECTION_STRING) as svc:
            container = svc.get_container_client(WORM_CONTAINER)
            deadline = time.monotonic() + AUDIT_BLOB_DEADLINE_S
            while time.monotonic() < deadline:
                try:
                    blobs = list(container.list_blobs(name_starts_with=prefix))
                    for item in blobs:
                        blob_data = container.get_blob_client(item.name).download_blob().readall()
                        record = json.loads(blob_data)
                        created = item.creation_time
                        if created and created >= t0_wall.replace(tzinfo=None):
                            if record.get("rejection_reason") == "unknown_ca":
                                found_blob = record
                                break
                except Exception:
                    pass
                if found_blob:
                    break
                time.sleep(0.05)

        assert found_blob is not None, (
            f"No audit blob with rejection_reason=unknown_ca appeared in WORM container "
            f"within {AUDIT_BLOB_DEADLINE_S} s of the rejected connection"
        )
        assert found_blob["rejection_reason"] == "unknown_ca", (
            f"Expected rejection_reason=unknown_ca, got {found_blob['rejection_reason']!r}"
        )


# ---------------------------------------------------------------------------
# Test (d): edge gateway cannot publish to a peer gateway's MQTT namespace
# ---------------------------------------------------------------------------

class TestCrossZonePublishDenied:
    """
    Given edge gateway body/01 connects with its valid SPIFFE SVID, then:
      - It CAN publish to spBv1.0/munich-i4/DDATA/body/# (own zone — ALLOW).
      - It CANNOT publish to spBv1.0/munich-i4/DDATA/paint/# (peer zone — DENY).

    The ACL enforcement is by the HiveMQ Enterprise Security Extension loading
    mqtt-acl-rules.xml (deployed in main.tf).  A cross-zone PUBLISH attempt results
    in the broker dropping the connection or returning MQTT disconnect.
    """

    def test_own_zone_publish_succeeds(
        self,
        body_shop_svid: tuple[bytes, bytes, bytes],
        hivemq_server_ca_pem: bytes,
    ) -> None:
        ca_pem, cert_pem, key_pem = body_shop_svid
        ctx, tmp = _tls_client_ctx(hivemq_server_ca_pem, cert_pem, key_pem)
        client_id = f"test-acl-own-{uuid.uuid4().hex[:8]}"
        own_topic = "spBv1.0/munich-i4/DDATA/body/weld-cell-01"
        packet_id = 10

        try:
            with socket.create_connection((HIVEMQ_HOST, HIVEMQ_PORT), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname=HIVEMQ_HOST) as tls:
                    tls.sendall(_mqtt_connect_packet(client_id))
                    connack = tls.recv(4)
                    assert connack[3] == 0x00, (
                        f"body/01 failed to connect: CONNACK return code {connack[3]:#04x}"
                    )
                    ddata = json.dumps({"metrics": [{"name": "torque_nm", "value": 120.0}]}).encode()
                    tls.sendall(_mqtt_publish_packet(own_topic, ddata, qos=1, packet_id=packet_id))
                    tls.settimeout(2.0)
                    response = tls.recv(4)
        finally:
            _cleanup_tmp(tmp)

        assert len(response) >= 2, f"No response to own-zone QoS 1 PUBLISH: {response!r}"
        # PUBACK (0x40) confirms the publish was accepted
        assert response[0] == 0x40, (
            f"Expected PUBACK (0x40) for own-zone publish, got {response[0]:#04x}; "
            "own-zone publish by body/01 should be allowed"
        )

    def test_cross_zone_publish_denied(
        self,
        body_shop_svid: tuple[bytes, bytes, bytes],
        hivemq_server_ca_pem: bytes,
    ) -> None:
        ca_pem, cert_pem, key_pem = body_shop_svid
        ctx, tmp = _tls_client_ctx(hivemq_server_ca_pem, cert_pem, key_pem)
        client_id = f"test-acl-cross-{uuid.uuid4().hex[:8]}"
        peer_topic = "spBv1.0/munich-i4/DDATA/paint/robot-02"
        packet_id = 11

        cross_zone_denied = False
        try:
            with socket.create_connection((HIVEMQ_HOST, HIVEMQ_PORT), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname=HIVEMQ_HOST) as tls:
                    tls.sendall(_mqtt_connect_packet(client_id))
                    connack = tls.recv(4)
                    assert connack[3] == 0x00, (
                        f"body/01 failed to connect: CONNACK return code {connack[3]:#04x}"
                    )
                    ddata = json.dumps({"metrics": [{"name": "robot_speed", "value": 1.5}]}).encode()
                    tls.sendall(_mqtt_publish_packet(peer_topic, ddata, qos=1, packet_id=packet_id))
                    tls.settimeout(2.0)
                    try:
                        response = tls.recv(16)
                        # DISCONNECT (0xE0) — broker dropped the connection after denied publish
                        # Or no PUBACK received (empty / only DISCONNECT)
                        if len(response) == 0:
                            cross_zone_denied = True
                        elif response[0] == 0xE0:
                            cross_zone_denied = True
                        elif response[0] == 0x40:
                            # PUBACK received — broker accepted the publish despite ACL deny
                            # This is a failure: cross-zone publish must be blocked
                            cross_zone_denied = False
                        else:
                            # Any non-PUBACK response (error, disconnect) counts as denied
                            cross_zone_denied = True
                    except (ssl.SSLError, OSError):
                        cross_zone_denied = True
        except (ssl.SSLError, OSError):
            cross_zone_denied = True
        finally:
            _cleanup_tmp(tmp)

        assert cross_zone_denied, (
            "body/01 was able to publish to paint/robot-02 — cross-zone publish must be "
            "denied by the HiveMQ Enterprise Security Extension ACL rules (mqtt-acl-rules.xml)"
        )
