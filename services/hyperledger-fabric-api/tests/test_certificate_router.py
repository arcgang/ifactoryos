"""Tests for POST /certificate/issue, GET /certificate/{id}/verify, GET /certificates.

All DB, Kafka, and Fabric I/O replaced with in-process fakes; no external services needed.

POST /certificate/issue scenarios:
  - 201 returned for a valid new certificate
  - 200 returned (idempotent) for a duplicate certificateId + matching hash
  - 409 returned for duplicate certificateId with different hash
  - 403 returned when caller lacks CERTIFICATE_ISSUER role
  - 401 returned when Authorization header is missing
  - 422 returned for missing required field
  - 422 returned for certificateHash failing SHA-256 pattern
  - 504 returned when Fabric times out

GET /certificate/{certificateId}/verify scenarios:
  - 200 verified=true when ledger hash matches stored hash
  - 200 verified=false and event emitted when ledger hash mismatches
  - 404 returned when certificateId not found
  - 403 returned when caller lacks reader role

GET /certificates scenarios:
  - 200 with paginated data
  - 200 empty list when no rows
  - 400 for malformed cursor
  - 401 when Authorization header is missing

FakeConn instance isolation:
  - Two FakeConn instances have independent state
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VIN = "WBA3A5C55FK123456"
_CERT_ID = str(uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"))
_VALIDATOR_ID = str(uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002"))
_SHIFT_ID = str(uuid.UUID("cccccccc-0000-0000-0000-000000000003"))
_CERT_HASH = "a" * 64  # 64-char hex string
_ISSUED_AT = "2099-01-01T06:00:00+00:00"
_FAR_FUTURE_ANCHORED = datetime(2099, 1, 1, 6, 0, 0, tzinfo=timezone.utc)

_BASE_PAYLOAD: dict = {
    "certificateId": _CERT_ID,
    "vin": _VIN,
    "gateResultSnapshot": {
        "gateId": "GATE-3",
        "stationResults": [
            {"stationCode": "DIM", "result": "PASS"},
            {"stationCode": "LEAK", "result": "PASS"},
        ],
    },
    "healthScore": 95.0,
    "validatorId": _VALIDATOR_ID,
    "issuedAt": _ISSUED_AT,
    "certificateHash": _CERT_HASH,
    "shiftId": _SHIFT_ID,
    "sourceSystem": "quality-studio",
}

_JWT_HEADERS = {"Authorization": "Bearer stub"}


def _issuer_payload() -> dict:
    return {"sub": "test-user", "roles": ["CERTIFICATE_ISSUER"]}


def _reader_payload() -> dict:
    return {"sub": "test-reader", "roles": ["CERTIFICATE_READER"]}


def _no_role_payload() -> dict:
    return {"sub": "test-noop", "roles": []}


# ---------------------------------------------------------------------------
# Fake DB infrastructure
# ---------------------------------------------------------------------------

class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class FakeConn:
    """Fake asyncpg connection — all mutable state in instance fields only."""

    def __init__(
        self,
        existing_cert: dict | None = None,
        cert_rows: list[dict] | None = None,
    ) -> None:
        self._existing_cert = existing_cert
        self._cert_rows: list[dict] = cert_rows or []

        self.inserted_certs: list[dict] = []
        self.inserted_attempts: list[dict] = []
        self.updated_attempts: list[dict] = []

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        q = query.lower()
        if "fabric_certificate" in q and "certificate_id" in q:
            if self._existing_cert and str(args[0]) == self._existing_cert.get("certificate_id"):
                return _FakeRecord(self._existing_cert)
            return None
        return None

    async def fetch(self, query: str, *args: Any) -> list[_FakeRecord]:
        return [_FakeRecord(r) for r in self._cert_rows]

    async def execute(self, query: str, *args: Any) -> None:
        q = query.lower()
        if "insert into fabric_certificate" in q:
            self.inserted_certs.append({"args": list(args)})
        elif "insert into fabric_anchor_attempt" in q:
            self.inserted_attempts.append({"args": list(args)})
        elif "update fabric_anchor_attempt" in q:
            self.updated_attempts.append({"query": query.strip(), "args": list(args)})


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> "_FakeAcquireCtx":
        return _FakeAcquireCtx(self._conn)


class _FakeAcquireCtx:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Fake Fabric commit result
# ---------------------------------------------------------------------------

from app.fabric_client import FabricCommitResult, FabricTimeoutError, FabricPeerError

_FAKE_COMMIT = FabricCommitResult(
    transaction_id="deadbeef" * 8,
    block_number=42,
)


# ---------------------------------------------------------------------------
# Test client factory
# ---------------------------------------------------------------------------

def _make_client(
    jwt_payload_fn=_issuer_payload,
    existing_cert: dict | None = None,
    cert_rows: list[dict] | None = None,
) -> tuple[TestClient, FakeConn]:
    conn = FakeConn(existing_cert=existing_cert, cert_rows=cert_rows)
    pool = FakePool(conn)
    app = create_app()

    from app.db import get_pool
    from app.auth import require_bearer, require_certificate_issuer, require_certificate_reader

    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[require_bearer] = lambda: jwt_payload_fn()
    app.dependency_overrides[require_certificate_issuer] = lambda: jwt_payload_fn()
    app.dependency_overrides[require_certificate_reader] = lambda: jwt_payload_fn()

    return TestClient(app, raise_server_exceptions=False), conn


# ---------------------------------------------------------------------------
# POST /certificate/issue
# ---------------------------------------------------------------------------

class TestIssueCertificate:
    def test_201_for_valid_new_certificate(self):
        client, conn = _make_client()
        with patch("app.routers.certificates.submit_transaction", new=AsyncMock(return_value=_FAKE_COMMIT)), \
             patch("app.routers.certificates.publish", new=AsyncMock()):
            resp = client.post("/certificate/issue", json=_BASE_PAYLOAD, headers=_JWT_HEADERS)
        assert resp.status_code == 201
        body = resp.json()
        assert body["certificateId"] == _CERT_ID
        assert body["vin"] == _VIN
        assert body["fabricTransactionId"] == _FAKE_COMMIT.transaction_id
        assert body["fabricBlockNumber"] == _FAKE_COMMIT.block_number
        assert body["certificateHash"] == _CERT_HASH
        assert body["idempotent"] is False
        assert len(conn.inserted_certs) == 1
        assert len(conn.inserted_attempts) == 1

    def test_200_idempotent_for_duplicate_cert_id_same_hash(self):
        existing = {
            "certificate_id": _CERT_ID,
            "vin": _VIN,
            "fabric_transaction_id": "ff" * 32,
            "fabric_block_number": 10,
            "anchored_at": _FAR_FUTURE_ANCHORED,
            "certificate_hash": _CERT_HASH,
        }
        client, conn = _make_client(existing_cert=existing)
        with patch("app.routers.certificates.submit_transaction", new=AsyncMock(return_value=_FAKE_COMMIT)), \
             patch("app.routers.certificates.publish", new=AsyncMock()):
            resp = client.post("/certificate/issue", json=_BASE_PAYLOAD, headers=_JWT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["certificateId"] == _CERT_ID
        assert body["idempotent"] is True
        # No new cert or attempt was inserted for idempotent replay
        assert len(conn.inserted_certs) == 0
        assert len(conn.inserted_attempts) == 0

    def test_409_for_duplicate_cert_id_different_hash(self):
        different_hash = "b" * 64
        existing = {
            "certificate_id": _CERT_ID,
            "vin": _VIN,
            "fabric_transaction_id": "ff" * 32,
            "fabric_block_number": 10,
            "anchored_at": _FAR_FUTURE_ANCHORED,
            "certificate_hash": different_hash,
        }
        client, _ = _make_client(existing_cert=existing)
        resp = client.post("/certificate/issue", json=_BASE_PAYLOAD, headers=_JWT_HEADERS)
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "CERTIFICATE_HASH_MISMATCH"
        assert "correlation_id" in body

    def test_401_when_authorization_header_missing(self):
        app = create_app()
        from app.db import get_pool
        app.dependency_overrides[get_pool] = lambda: FakePool(FakeConn())
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post("/certificate/issue", json=_BASE_PAYLOAD)
        assert resp.status_code == 401

    def test_403_when_caller_lacks_issuer_role(self):
        from fastapi import HTTPException as _HTTPException

        def _raise_403():
            raise _HTTPException(
                status_code=403,
                detail={
                    "code": "FORBIDDEN",
                    "message": "Caller requires CERTIFICATE_ISSUER role",
                    "correlation_id": str(uuid.uuid4()),
                },
            )

        app = create_app()
        from app.db import get_pool
        from app.auth import require_certificate_issuer
        app.dependency_overrides[get_pool] = lambda: FakePool(FakeConn())
        app.dependency_overrides[require_certificate_issuer] = _raise_403
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post("/certificate/issue", json=_BASE_PAYLOAD, headers=_JWT_HEADERS)
        assert resp.status_code == 403

    def test_422_for_missing_required_field(self):
        client, _ = _make_client()
        payload = {k: v for k, v in _BASE_PAYLOAD.items() if k != "certificateHash"}
        resp = client.post("/certificate/issue", json=payload, headers=_JWT_HEADERS)
        assert resp.status_code == 422

    def test_422_for_invalid_certificate_hash_pattern(self):
        client, _ = _make_client()
        payload = {**_BASE_PAYLOAD, "certificateHash": "not-a-valid-hash"}
        resp = client.post("/certificate/issue", json=payload, headers=_JWT_HEADERS)
        assert resp.status_code == 422

    def test_422_for_invalid_vin_length(self):
        client, _ = _make_client()
        payload = {**_BASE_PAYLOAD, "vin": "TOOSHORT"}
        resp = client.post("/certificate/issue", json=payload, headers=_JWT_HEADERS)
        assert resp.status_code == 422

    def test_504_when_fabric_times_out(self):
        client, conn = _make_client()
        with patch(
            "app.routers.certificates.submit_transaction",
            new=AsyncMock(side_effect=FabricTimeoutError("timed out")),
        ), patch("app.routers.certificates.publish", new=AsyncMock()):
            resp = client.post("/certificate/issue", json=_BASE_PAYLOAD, headers=_JWT_HEADERS)
        assert resp.status_code == 504
        body = resp.json()
        assert body["code"] == "FABRIC_TIMEOUT"
        # Attempt should be updated to timeout status
        assert any("timeout" in str(a) for a in conn.updated_attempts)

    def test_502_when_fabric_peer_error(self):
        client, conn = _make_client()
        with patch(
            "app.routers.certificates.submit_transaction",
            new=AsyncMock(side_effect=FabricPeerError("peer down")),
        ), patch("app.routers.certificates.publish", new=AsyncMock()):
            resp = client.post("/certificate/issue", json=_BASE_PAYLOAD, headers=_JWT_HEADERS)
        assert resp.status_code == 502
        body = resp.json()
        assert body["code"] == "FABRIC_PEER_ERROR"

    def test_event_includes_fabric_transaction_hash(self):
        """AC-77d7d8c2: emitted event includes fabricTransactionId for A7 measurement."""
        client, _ = _make_client()
        captured: list[dict] = []

        async def _fake_publish(topic: str, payload: dict) -> None:
            captured.append({"topic": topic, "payload": payload})

        with patch("app.routers.certificates.submit_transaction", new=AsyncMock(return_value=_FAKE_COMMIT)), \
             patch("app.routers.certificates.publish", new=_fake_publish):
            resp = client.post("/certificate/issue", json=_BASE_PAYLOAD, headers=_JWT_HEADERS)

        assert resp.status_code == 201
        issued_events = [e for e in captured if "certificate.issued" in e["topic"]]
        assert len(issued_events) == 1
        event_payload = issued_events[0]["payload"]
        assert event_payload["fabricTransactionId"] == _FAKE_COMMIT.transaction_id
        assert event_payload["certificateId"] == _CERT_ID
        assert event_payload["vin"] == _VIN
        assert event_payload["sourceSystem"] == "quality-studio"
        assert event_payload["shiftId"] == _SHIFT_ID

    def test_source_system_defaults_to_quality_studio(self):
        client, _ = _make_client()
        payload = {k: v for k, v in _BASE_PAYLOAD.items() if k != "sourceSystem"}
        with patch("app.routers.certificates.submit_transaction", new=AsyncMock(return_value=_FAKE_COMMIT)), \
             patch("app.routers.certificates.publish", new=AsyncMock()):
            resp = client.post("/certificate/issue", json=payload, headers=_JWT_HEADERS)
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# GET /certificate/{certificateId}/verify
# ---------------------------------------------------------------------------

class TestVerifyCertificate:
    def _make_verify_client(self, existing_cert: dict | None = None, jwt_fn=_reader_payload):
        conn = FakeConn(existing_cert=existing_cert)
        pool = FakePool(conn)
        app = create_app()
        from app.db import get_pool
        from app.auth import require_certificate_reader
        app.dependency_overrides[get_pool] = lambda: pool
        app.dependency_overrides[require_certificate_reader] = lambda: jwt_fn()
        return TestClient(app, raise_server_exceptions=False), conn

    def test_200_verified_true_when_hash_matches(self):
        existing = {
            "certificate_id": _CERT_ID,
            "vin": _VIN,
            "fabric_transaction_id": "deadbeef" * 8,
            "certificate_hash": _CERT_HASH,
        }
        client, _ = self._make_verify_client(existing_cert=existing)
        with patch("app.routers.certificates.query_transaction_hash", new=AsyncMock(return_value=_CERT_HASH)), \
             patch("app.routers.certificates.publish", new=AsyncMock()):
            resp = client.get(f"/certificate/{_CERT_ID}/verify", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["verified"] is True
        assert body["certificateId"] == _CERT_ID
        assert body["fabricTransactionId"] == "deadbeef" * 8

    def test_200_verified_false_and_event_emitted_on_hash_mismatch(self):
        existing = {
            "certificate_id": _CERT_ID,
            "vin": _VIN,
            "fabric_transaction_id": "deadbeef" * 8,
            "certificate_hash": _CERT_HASH,
        }
        client, _ = self._make_verify_client(existing_cert=existing)
        captured: list[dict] = []

        async def _fake_publish(topic: str, payload: dict) -> None:
            captured.append({"topic": topic, "payload": payload})

        with patch("app.routers.certificates.query_transaction_hash", new=AsyncMock(return_value="c" * 64)), \
             patch("app.routers.certificates.publish", new=_fake_publish):
            resp = client.get(f"/certificate/{_CERT_ID}/verify", headers=_JWT_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert body["verified"] is False
        failed_events = [e for e in captured if "verify-failed" in e["topic"]]
        assert len(failed_events) == 1
        assert failed_events[0]["payload"]["certificateId"] == _CERT_ID

    def test_404_when_certificate_not_found(self):
        client, _ = self._make_verify_client(existing_cert=None)
        resp = client.get(f"/certificate/{_CERT_ID}/verify", headers=_JWT_HEADERS)
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "CERTIFICATE_NOT_FOUND"

    def test_403_when_caller_lacks_reader_role(self):
        existing = {
            "certificate_id": _CERT_ID,
            "vin": _VIN,
            "fabric_transaction_id": "deadbeef" * 8,
            "certificate_hash": _CERT_HASH,
        }
        from fastapi import HTTPException

        def _raise_403():
            raise HTTPException(
                status_code=403,
                detail={"code": "FORBIDDEN", "message": "no role", "correlation_id": str(uuid.uuid4())},
            )

        conn = FakeConn(existing_cert=existing)
        pool = FakePool(conn)
        app = create_app()
        from app.db import get_pool
        from app.auth import require_certificate_reader
        app.dependency_overrides[get_pool] = lambda: pool
        app.dependency_overrides[require_certificate_reader] = _raise_403
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.get(f"/certificate/{_CERT_ID}/verify", headers=_JWT_HEADERS)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /certificates
# ---------------------------------------------------------------------------

class TestListCertificates:
    def _make_list_client(self, cert_rows: list[dict] | None = None):
        conn = FakeConn(cert_rows=cert_rows)
        pool = FakePool(conn)
        app = create_app()
        from app.db import get_pool
        from app.auth import require_certificate_reader
        app.dependency_overrides[get_pool] = lambda: pool
        app.dependency_overrides[require_certificate_reader] = lambda: _reader_payload()
        return TestClient(app, raise_server_exceptions=False), conn

    def test_200_with_certificate_rows(self):
        rows = [
            {
                "certificate_id": _CERT_ID,
                "vin": _VIN,
                "fabric_transaction_id": "ff" * 32,
                "fabric_block_number": 5,
                "anchored_at": _FAR_FUTURE_ANCHORED,
                "certificate_hash": _CERT_HASH,
            }
        ]
        client, _ = self._make_list_client(cert_rows=rows)
        resp = client.get(f"/certificates?shiftId={_SHIFT_ID}", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["certificateId"] == _CERT_ID
        assert body["page"]["next_cursor"] is None

    def test_200_empty_list_when_no_rows(self):
        client, _ = self._make_list_client(cert_rows=[])
        resp = client.get("/certificates", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["page"]["next_cursor"] is None

    def test_400_for_malformed_cursor(self):
        client, _ = self._make_list_client()
        resp = client.get("/certificates?cursor=!!!invalid!!!", headers=_JWT_HEADERS)
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "INVALID_CURSOR"

    def test_401_when_authorization_header_missing(self):
        app = create_app()
        from app.db import get_pool
        app.dependency_overrides[get_pool] = lambda: FakePool(FakeConn())
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.get("/certificates")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# FakeConn instance isolation
# ---------------------------------------------------------------------------

class TestFakeConnIsolation:
    def test_two_instances_have_independent_state(self):
        import asyncio

        conn_a = FakeConn()
        conn_b = FakeConn()

        asyncio.run(conn_a.execute("INSERT INTO fabric_certificate", "x"))
        assert len(conn_a.inserted_certs) == 1
        assert len(conn_b.inserted_certs) == 0
