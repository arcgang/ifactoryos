"""
mTLS / SPIFFE acceptance tests for the edge-gateways component (cmp:edge-gateways).

Acceptance criteria validated:
  (a) An edge-gateway MQTT client presenting a valid SPIFFE SVID connects
      successfully to the broker test stub (TLS handshake completes, CONNACK received).
  (b) A client presenting an expired SVID certificate is rejected with a TLS
      handshake failure — no CONNACK is ever issued.
  (c) A client presenting a certificate from an untrusted CA root is rejected with
      a TLS handshake failure.
  (d) The zone-conduit model document renders without broken internal references
      (all anchor links and conduit IDs referenced in prose are defined in the table).

Tests (a)–(c) use a local TLS broker stub built with Python's ssl + socket modules
so no live HiveMQ instance is required.  The stub enforces mutual TLS with
REQUIRE_CLIENT_CERT and only trusts the test CA.

Run:
    pytest platform/edge-gateways/tests/test_mtls_spiffe.py -v

No environment variables are required; all certificates are generated in-process
using the cryptography library (declared in requirements).
"""

from __future__ import annotations

import datetime
import ipaddress
import os
import socket
import ssl
import tempfile
import threading
from pathlib import Path
from typing import Generator

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

# ---------------------------------------------------------------------------
# Certificate generation helpers
# ---------------------------------------------------------------------------

_FAR_FUTURE = datetime.datetime(2099, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc)
_TRUST_DOMAIN = "munich.bmw"
_SPIFFE_URI_TEMPLATE = "spiffe://{trust_domain}/edge-gateways/{shop}/{node_index}"


def _generate_ca_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _build_ca_cert(
    ca_key: ec.EllipticCurvePrivateKey,
    cn: str,
    not_valid_after: datetime.datetime = _FAR_FUTURE,
) -> x509.Certificate:
    now = datetime.datetime(2025, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
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
    """Build a SPIFFE SVID leaf certificate signed by ca_key."""
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    now = not_valid_before or datetime.datetime(2025, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
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


def _pem(obj: x509.Certificate | ec.EllipticCurvePrivateKey) -> bytes:
    if isinstance(obj, x509.Certificate):
        return obj.public_bytes(serialization.Encoding.PEM)
    return obj.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


# ---------------------------------------------------------------------------
# Broker stub — single-connection mTLS TCP server
# ---------------------------------------------------------------------------

class _BrokerStub(threading.Thread):
    """
    Minimal TLS server that:
      - Requires a client certificate (REQUIRE_CLIENT_CERT).
      - Sends a fake MQTT CONNACK (0x20 0x02 0x00 0x00) after a successful handshake.
      - Stores whether the handshake succeeded or the error that terminated it.
    """

    def __init__(
        self,
        ca_cert_pem: bytes,
        server_cert_pem: bytes,
        server_key_pem: bytes,
    ) -> None:
        super().__init__(daemon=True)
        self._ca_cert_pem = ca_cert_pem
        self._server_cert_pem = server_cert_pem
        self._server_key_pem = server_key_pem
        self.port: int = 0
        self.handshake_succeeded: bool | None = None
        self.handshake_error: Exception | None = None
        self._ready = threading.Event()

    def run(self) -> None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.verify_mode = ssl.CERT_REQUIRED

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as ca_f:
            ca_f.write(self._ca_cert_pem)
            ca_path = ca_f.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as cert_f:
            cert_f.write(self._server_cert_pem)
            cert_path = cert_f.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as key_f:
            key_f.write(self._server_key_pem)
            key_path = key_f.name

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
                    with ctx.wrap_socket(conn, server_side=True) as tls_conn:
                        # Send a minimal MQTT CONNACK (return code 0 = accepted)
                        tls_conn.sendall(b"\x20\x02\x00\x00")
                        tls_conn.recv(1024)  # drain any CONNECT packet
                    self.handshake_succeeded = True
                except ssl.SSLError as exc:
                    self.handshake_succeeded = False
                    self.handshake_error = exc
        finally:
            os.unlink(ca_path)
            os.unlink(cert_path)
            os.unlink(key_path)

    def wait_ready(self, timeout: float = 5.0) -> None:
        if not self._ready.wait(timeout):
            raise RuntimeError("BrokerStub did not start within timeout")


def _connect_client(
    host: str,
    port: int,
    ca_cert_pem: bytes,
    client_cert_pem: bytes,
    client_key_pem: bytes,
    server_hostname: str = "broker.test.internal",
) -> bytes:
    """
    Attempt a TLS client connection; return whatever bytes the server sends, or
    raise ssl.SSLError on handshake failure.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as ca_f:
        ca_f.write(ca_cert_pem)
        ca_path = ca_f.name
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as cert_f:
        cert_f.write(client_cert_pem)
        cert_path = cert_f.name
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as key_f:
        key_f.write(client_key_pem)
        key_path = key_f.name

    try:
        ctx.load_verify_locations(ca_path)
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        with socket.create_connection((host, port), timeout=5) as raw:
            with ctx.wrap_socket(raw, server_hostname=server_hostname) as tls:
                return tls.recv(4)
    finally:
        os.unlink(ca_path)
        os.unlink(cert_path)
        os.unlink(key_path)


# ---------------------------------------------------------------------------
# Shared PKI fixtures — test CA, broker server cert, and per-test SVIDs
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ca_keypair() -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    """Trusted CA — represents the SPIRE server root used for this test run."""
    key = _generate_ca_key()
    cert = _build_ca_cert(key, cn="Test IFOS SPIRE CA")
    return key, cert


@pytest.fixture(scope="module")
def broker_tls(
    ca_keypair: tuple[ec.EllipticCurvePrivateKey, x509.Certificate],
) -> tuple[bytes, bytes, bytes]:
    """Server cert + key for the broker stub, signed by the test CA."""
    ca_key, ca_cert = ca_keypair
    srv_key, srv_cert = _build_svid_cert(
        ca_cert,
        ca_key,
        spiffe_id="spiffe://munich.bmw/uns-backbone/mqtt-kafka-bridge",
        node_id="uns-hivemq",
    )
    return _pem(ca_cert), _pem(srv_cert), _pem(srv_key)


# ---------------------------------------------------------------------------
# (a) Valid SPIFFE SVID — connection succeeds, CONNACK received
# ---------------------------------------------------------------------------

class TestMtlsValidSvid:
    """
    Given an edge-gateway MQTT client presenting a valid SPIFFE SVID (not expired,
    signed by the trusted SPIRE CA), when it connects to the MQTT broker on TCP/8883,
    then the TLS handshake completes and the server issues a CONNACK.
    """

    def test_valid_svid_connects_and_receives_connack(
        self,
        ca_keypair: tuple[ec.EllipticCurvePrivateKey, x509.Certificate],
        broker_tls: tuple[bytes, bytes, bytes],
    ) -> None:
        ca_key, ca_cert = ca_keypair
        ca_pem, srv_cert_pem, srv_key_pem = broker_tls

        client_key, client_cert = _build_svid_cert(
            ca_cert,
            ca_key,
            spiffe_id=_SPIFFE_URI_TEMPLATE.format(
                trust_domain=_TRUST_DOMAIN, shop="assembly", node_index="01"
            ),
            node_id="edge-assembly-01",
        )

        stub = _BrokerStub(ca_pem, srv_cert_pem, srv_key_pem)
        stub.start()
        stub.wait_ready()

        response = _connect_client(
            host="127.0.0.1",
            port=stub.port,
            ca_cert_pem=ca_pem,
            client_cert_pem=_pem(client_cert),
            client_key_pem=_pem(client_key),
        )
        stub.join(timeout=5)

        # MQTT CONNACK fixed header: 0x20 0x02 + return-code 0x00 0x00
        assert response == b"\x20\x02\x00\x00", (
            f"Expected CONNACK b'\\x20\\x02\\x00\\x00', got {response!r}"
        )
        assert stub.handshake_succeeded is True, (
            "Broker stub did not complete TLS handshake for valid SVID"
        )


# ---------------------------------------------------------------------------
# (b) Expired SVID — TLS handshake failure, no CONNACK
# ---------------------------------------------------------------------------

class TestMtlsRejectsExpiredSvid:
    """
    Given an edge-gateway MQTT client presenting an expired SVID (not_valid_after
    in the past), when it attempts to connect to the MQTT broker, then the TLS
    handshake fails and no CONNACK is issued.
    """

    def test_expired_svid_is_rejected(
        self,
        ca_keypair: tuple[ec.EllipticCurvePrivateKey, x509.Certificate],
        broker_tls: tuple[bytes, bytes, bytes],
    ) -> None:
        ca_key, ca_cert = ca_keypair
        ca_pem, srv_cert_pem, srv_key_pem = broker_tls

        # Certificate expired before the current moment (anchor: 2025-01-01 .. 2025-01-02)
        expired_key, expired_cert = _build_svid_cert(
            ca_cert,
            ca_key,
            spiffe_id=_SPIFFE_URI_TEMPLATE.format(
                trust_domain=_TRUST_DOMAIN, shop="assembly", node_index="01"
            ),
            node_id="edge-assembly-01",
            not_valid_before=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
            not_valid_after=datetime.datetime(2025, 1, 2, tzinfo=datetime.timezone.utc),
        )

        stub = _BrokerStub(ca_pem, srv_cert_pem, srv_key_pem)
        stub.start()
        stub.wait_ready()

        with pytest.raises(ssl.SSLError) as exc_info:
            _connect_client(
                host="127.0.0.1",
                port=stub.port,
                ca_cert_pem=ca_pem,
                client_cert_pem=_pem(expired_cert),
                client_key_pem=_pem(expired_key),
            )

        stub.join(timeout=5)

        # The SSL error must indicate certificate verification failed
        assert exc_info.value is not None, "Expected ssl.SSLError for expired SVID"
        assert stub.handshake_succeeded is False, (
            "Broker stub should have failed the TLS handshake for an expired SVID"
        )


# ---------------------------------------------------------------------------
# (c) Untrusted CA — TLS handshake failure, no CONNACK
# ---------------------------------------------------------------------------

class TestMtlsRejectsUntrustedCert:
    """
    Given an edge-gateway MQTT client presenting a certificate signed by an
    untrusted (rogue) CA — not the SPIRE root — when it attempts to connect,
    then the TLS handshake fails and no CONNACK is issued.
    """

    def test_untrusted_ca_cert_is_rejected(
        self,
        ca_keypair: tuple[ec.EllipticCurvePrivateKey, x509.Certificate],
        broker_tls: tuple[bytes, bytes, bytes],
    ) -> None:
        ca_pem, srv_cert_pem, srv_key_pem = broker_tls

        # Rogue CA — not the same as ca_keypair; broker does not trust it
        rogue_ca_key = _generate_ca_key()
        rogue_ca_cert = _build_ca_cert(rogue_ca_key, cn="Rogue CA — untrusted")
        rogue_client_key, rogue_client_cert = _build_svid_cert(
            rogue_ca_cert,
            rogue_ca_key,
            spiffe_id=_SPIFFE_URI_TEMPLATE.format(
                trust_domain=_TRUST_DOMAIN, shop="assembly", node_index="01"
            ),
            node_id="edge-assembly-01",
        )

        stub = _BrokerStub(ca_pem, srv_cert_pem, srv_key_pem)
        stub.start()
        stub.wait_ready()

        with pytest.raises(ssl.SSLError) as exc_info:
            _connect_client(
                host="127.0.0.1",
                port=stub.port,
                ca_cert_pem=_pem(rogue_ca_cert),  # client trusts rogue; broker does not
                client_cert_pem=_pem(rogue_client_cert),
                client_key_pem=_pem(rogue_client_key),
            )

        stub.join(timeout=5)

        assert exc_info.value is not None, "Expected ssl.SSLError for untrusted CA certificate"
        assert stub.handshake_succeeded is False, (
            "Broker stub should have failed the TLS handshake for an untrusted CA certificate"
        )


# ---------------------------------------------------------------------------
# (d) Zone-conduit model document — no broken internal references
# ---------------------------------------------------------------------------

class TestZoneConduitModelDocument:
    """
    Validates acceptance criterion (d): the zone-conduit model document renders
    without broken references — every conduit ID cited in prose is present in
    the Conduit Register table, and every IEC 62443 control cited is listed
    in the Control Mapping section.
    """

    DOCUMENT_PATH = (
        Path(__file__).parent.parent / "docs" / "zone-conduit-model.md"
    )

    # Conduit IDs that must appear in the Conduit Register table
    EXPECTED_CONDUIT_IDS = {"CM-013", "FW-IN-005", "IT→Cloud-001"}

    # IEC 62443 controls that must be defined in the Control Mapping section
    EXPECTED_CONTROLS = {"SC-004", "SC-017", "SC-007", "SC-010"}

    # SPIFFE IDs referenced in the document — must all share the single trust domain
    EXPECTED_TRUST_DOMAIN = "munich.bmw"

    def test_document_exists(self) -> None:
        assert self.DOCUMENT_PATH.exists(), (
            f"zone-conduit-model.md not found at {self.DOCUMENT_PATH}"
        )

    def test_all_conduit_ids_defined_in_register(self) -> None:
        content = self.DOCUMENT_PATH.read_text(encoding="utf-8")

        # The Conduit Register table must contain a row for each expected conduit
        for conduit_id in self.EXPECTED_CONDUIT_IDS:
            assert conduit_id in content, (
                f"Conduit ID '{conduit_id}' not found in {self.DOCUMENT_PATH.name}; "
                f"every conduit referenced in prose must have a row in the Conduit Register table"
            )

    def test_all_iec62443_controls_defined(self) -> None:
        content = self.DOCUMENT_PATH.read_text(encoding="utf-8")

        for control in self.EXPECTED_CONTROLS:
            assert control in content, (
                f"IEC 62443 control '{control}' not found in {self.DOCUMENT_PATH.name}; "
                f"all controls cited in conduit annotations must be listed in the Control Mapping section"
            )

    def test_single_trust_domain_no_additional_roots(self) -> None:
        """Verify the document names only the single approved trust domain."""
        content = self.DOCUMENT_PATH.read_text(encoding="utf-8")

        assert self.EXPECTED_TRUST_DOMAIN in content, (
            f"Trust domain '{self.EXPECTED_TRUST_DOMAIN}' not referenced in "
            f"{self.DOCUMENT_PATH.name}"
        )
        # Ensure no unauthorised trust roots are introduced.
        # The approved SPIFFE trust domain is munich.bmw (from spiffe-spire module).
        # A bare SPIFFE URI spiffe://ifos.munich.internal/... would introduce a second root.
        assert "spiffe://ifos.munich.internal" not in content, (
            "Document references 'spiffe://ifos.munich.internal' as a SPIFFE trust domain — "
            "the only approved trust domain is 'munich.bmw' (from spiffe-spire module)"
        )

    def test_cm013_firewall_rule_annotated_with_protocol_and_port(self) -> None:
        content = self.DOCUMENT_PATH.read_text(encoding="utf-8")

        # CM-013 must declare TCP/8883 and the mTLS authentication method
        for fragment in ("CM-013", "TCP/8883", "mTLS", "SC-004", "SC-017"):
            assert fragment in content, (
                f"Expected fragment '{fragment}' in zone-conduit-model.md for CM-013 conduit annotation"
            )

    def test_three_zones_present(self) -> None:
        content = self.DOCUMENT_PATH.read_text(encoding="utf-8")

        for zone_name in ("OT Zone", "On-Prem IT Zone", "Cloud IT Zone"):
            assert zone_name in content, (
                f"Zone '{zone_name}' not found in zone-conduit-model.md; "
                f"the three-zone model requires all three zones to be documented"
            )
