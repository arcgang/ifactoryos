#!/usr/bin/env python3
"""
Generate the test SPIFFE CA for spiffe://munich.bmw used by the integration tests.

Usage:
    python generate_test_ca.py [--out-dir <dir>]

Outputs (written to --out-dir, default: ./pki):
    ca.pem          — PEM-encoded test CA certificate (loaded into HiveMQ trust store)
    ca.key.pem      — PEM-encoded test CA private key  (NOT committed; gitignored)
    server.pem      — PEM-encoded HiveMQ server leaf certificate
    server.key.pem  — PEM-encoded HiveMQ server private key  (NOT committed)

These files are generated once per test environment.  They are intentionally
short-lived (not_valid_after = 30 days from generation) so that a stale fixture
cannot accumulate in a CI environment.

NEVER commit ca.key.pem or server.key.pem.  The .gitignore in this directory
excludes *.key.pem.
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

_TRUST_DOMAIN = "munich.bmw"
_VALIDITY_DAYS = 30


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def _generate_ca(cn: str) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = _now_utc()
    not_after = now + datetime.timedelta(days=_VALIDITY_DAYS)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _generate_leaf(
    ca_key: ec.EllipticCurvePrivateKey,
    ca_cert: x509.Certificate,
    cn: str,
    spiffe_id: str,
    dns_names: list[str],
) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    now = _now_utc()
    not_after = now + datetime.timedelta(days=_VALIDITY_DAYS)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    san_entries: list[x509.GeneralName] = [x509.UniformResourceIdentifier(spiffe_id)]
    for name in dns_names:
        san_entries.append(x509.DNSName(name))
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(san_entries),
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--out-dir", default="./pki", help="Output directory (created if absent)")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Trusted CA — represents spiffe://munich.bmw SPIRE CA in the test environment
    ca_key, ca_cert = _generate_ca(cn=f"Test IFOS SPIRE CA ({_TRUST_DOMAIN})")
    (out / "ca.pem").write_bytes(_pem_cert(ca_cert))
    (out / "ca.key.pem").write_bytes(_pem_key(ca_key))
    os.chmod(out / "ca.key.pem", 0o600)
    print(f"[OK] CA certificate: {out / 'ca.pem'}")

    # HiveMQ server leaf certificate (presented by the broker to MQTT clients)
    srv_key, srv_cert = _generate_leaf(
        ca_key,
        ca_cert,
        cn="hivemq.uns-backbone-test.svc.cluster.local",
        spiffe_id=f"spiffe://{_TRUST_DOMAIN}/uns-backbone/mqtt-broker",
        dns_names=[
            "hivemq.uns-backbone-test.svc.cluster.local",
            "hivemq",
            "localhost",
        ],
    )
    (out / "server.pem").write_bytes(_pem_cert(srv_cert))
    (out / "server.key.pem").write_bytes(_pem_key(srv_key))
    os.chmod(out / "server.key.pem", 0o600)
    print(f"[OK] Server certificate: {out / 'server.pem'}")

    # Per-gateway SVID examples (body/01 is the gateway used in scenario a and d)
    for shop, index in [("body", "01"), ("paint", "01")]:
        gw_key, gw_cert = _generate_leaf(
            ca_key,
            ca_cert,
            cn=f"edge-{shop}-{index}",
            spiffe_id=f"spiffe://{_TRUST_DOMAIN}/edge-gateways/{shop}/{index}",
            dns_names=[f"edge-{shop}-{index}.edge-gateways.ifos.munich.internal"],
        )
        (out / f"edge-{shop}-{index}.pem").write_bytes(_pem_cert(gw_cert))
        (out / f"edge-{shop}-{index}.key.pem").write_bytes(_pem_key(gw_key))
        os.chmod(out / f"edge-{shop}-{index}.key.pem", 0o600)
        print(f"[OK] Gateway {shop}/{index}: {out / f'edge-{shop}-{index}.pem'}")

    print("\nCA and server certificates written.")
    print("Load ca.pem into HiveMQ trust store before running the integration tests.")
    print("NEVER commit *.key.pem files.")


if __name__ == "__main__":
    main()
