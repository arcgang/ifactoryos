"""Tests for the hyperledger-fabric-api certificate endpoints.

Uses in-process fakes for PostgreSQL (asyncpg), Kafka, and the Fabric client
so the test suite runs without external services. Each fake is instantiated per
test to guarantee instance isolation.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CERT_ID = "550e8400-e29b-41d4-a716-446655440000"
_VIN = "WBA3A5C55FK123456"
_HASH = "a3f2c1d4e5b6a7f8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
_ALT_HASH = "b3f2c1d4e5b6a7f8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b3"
_VALIDATOR_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_SHIFT_ID = "d4e5f6a7-b8c9-0d1e-2f3a-4b5c6d7e8f9a"

_ISSUER_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbIkNFUlRJRklDQVRFX0lTU1VFUiJdfQ.sig"
)
_READER_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbIkNFUlRJRklDQVRFX1JFQURFUiJdfQ.sig"
)
_NO_ROLE_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbImFub3RoZXItcm9sZSJdfQ.sig"
)

_ANCHORED_AT = "2026-10-13T08:00:00+00:00"
_ISSUED_AT = "2026-10-13T07:55:00Z"

_VALID_ISSUE_BODY: dict = {
    "certificateId": _CERT_ID,
    "vin": _VIN,
    "gateResultSnapshot": {
        "gateId": "GATE-3",
        "stationResults": [
            {"stationCode": "DIM", "result": "PASS"},
            {"stationCode": "LEAK", "result": "PASS"},
        ],
    },
    "healthScore": 98.5,
    "validatorId": _VALIDATOR_ID,
    "issuedAt": _ISSUED_AT,
    "certificateHash": _HASH,
    "shiftId": _SHIFT_ID,
    "sourceSystem": "quality-studio",
}


# ---------------------------------------------------------------------------
# Fake Fabric client
# ---------------------------------------------------------------------------


class FakeFabricAnchorResult:
    def __init__(self) -> None:
        self.transaction_id = "fabric-tx-" + str(uuid.uuid4()).replace("-", "")
        self.block_number = 42
        self.anchored_at = _ANCHORED_AT


class FakeFabricCertificateData:
    def __init__(self, certificate_hash: str) -> None:
        self.certificate_id = _CERT_ID
        self.vin = _VIN
        self.certificate_hash = certificate_hash
        self.validator_id = _VALIDATOR_ID
        self.issued_at = _ISSUED_AT
        self.transaction_id = "fabric-tx-abc123"
        self.block_number = 42
        self.anchored_at = _ANCHORED_AT


class FakeFabricClient:
    """Fake Fabric client — each instance starts with clean state."""

    def __init__(
        self,
        *,
        existing_hash: str | None = None,
        conflict_hash: str | None = None,
        unavailable: bool = False,
        timeout: bool = False,
        ledger_hash: str | None = None,
    ) -> None:
        self._existing_hash = existing_hash
        self._conflict_hash = conflict_hash
        self._unavailable = unavailable
        self._timeout = timeout
        self._ledger_hash = ledger_hash or _HASH
        self.issue_calls: list[dict] = []
        self.get_calls: list[str] = []

    async def issue_certificate(
        self,
        certificate_id: str,
        vin: str,
        certificate_hash: str,
        validator_id: str,
        issued_at: str,
    ) -> tuple[FakeFabricAnchorResult, bool]:
        from app.fabric_client import (
            FabricHashConflictError,
            FabricTimeoutError,
            FabricUnavailableError,
        )

        self.issue_calls.append({"certificate_id": certificate_id, "hash": certificate_hash})

        if self._unavailable:
            raise FabricUnavailableError("peer unavailable")
        if self._timeout:
            raise FabricTimeoutError("commit timed out")
        if self._conflict_hash and certificate_hash == self._conflict_hash:
            raise FabricHashConflictError("hash conflict")

        idempotent = self._existing_hash is not None and self._existing_hash == certificate_hash
        return FakeFabricAnchorResult(), idempotent

    async def get_certificate(self, certificate_id: str) -> FakeFabricCertificateData:
        from app.fabric_client import CertificateNotFoundError, FabricUnavailableError

        self.get_calls.append(certificate_id)

        if self._unavailable:
            raise FabricUnavailableError("peer unavailable")
        if certificate_id == "not-found-id":
            raise CertificateNotFoundError("certificate not found")
        return FakeFabricCertificateData(self._ledger_hash)


# ---------------------------------------------------------------------------
# Fake asyncpg pool
# ---------------------------------------------------------------------------


class FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


class FakeConnection:
    def __init__(self, store: dict) -> None:
        self._store = store

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(_self):
                return _self

            async def __aexit__(_self, *_):
                pass

        return _Tx()

    async def fetchrow(self, query: str, *args: Any) -> FakeRecord | None:
        cert_id = str(args[0])
        existing = self._store.get("existing_certs", {})
        if cert_id in existing:
            return FakeRecord(existing[cert_id])
        return None

    async def fetch(self, query: str, *args: Any) -> list[FakeRecord]:
        rows = self._store.get("cert_rows", [])
        limit = args[-1] if args else 50
        return [FakeRecord(r) for r in rows[:limit]]

    async def execute(self, query: str, *args: Any) -> None:
        q = query.upper()
        if "INSERT INTO FABRIC_CERTIFICATE" in q:
            self._store["last_cert_insert"] = args
        elif "INSERT INTO FABRIC_ANCHOR_ATTEMPT" in q:
            self._store["last_attempt_insert"] = args


class FakePool:
    def __init__(self, store: dict | None = None) -> None:
        self._store: dict = store or {}
        self._acquire_count: int = 0

    def acquire(self):
        store = self._store

        class _CM:
            async def __aenter__(_self) -> FakeConnection:
                return FakeConnection(store)

            async def __aexit__(_self, *_) -> None:
                pass

        self._acquire_count += 1
        return _CM()


# ---------------------------------------------------------------------------
# Fake Kafka producer
# ---------------------------------------------------------------------------


class FakeKafkaProducer:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_and_wait(self, topic: str, payload: dict) -> None:
        self.messages.append({"topic": topic, "payload": payload})


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_existing_cert_row() -> dict:
    return {
        "certificate_id": _CERT_ID,
        "vin": _VIN,
        "shift_id": _SHIFT_ID,
        "gate_result_snapshot": json.dumps({
            "gateId": "GATE-3",
            "stationResults": [
                {"stationCode": "DIM", "result": "PASS"},
                {"stationCode": "LEAK", "result": "PASS"},
            ],
        }),
        "health_score": 98.5,
        "validator_id": _VALIDATOR_ID,
        "issued_at": datetime.fromisoformat(_ISSUED_AT.replace("Z", "+00:00")),
        "certificate_hash": _HASH,
        "fabric_transaction_id": "fabric-tx-abc123",
        "fabric_block_number": 42,
        "anchored_at": datetime.fromisoformat(_ANCHORED_AT),
        "source_system": "quality-studio",
    }


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    import app.config as config_mod

    settings = MagicMock()
    settings.oidc_issuer_url = "http://localhost:8090/default"
    settings.oidc_client_id = "local"
    settings.citus_url = "postgresql://localhost/test"
    settings.kafka_bootstrap_servers = "localhost:9092"
    settings.certificate_issuer_role = "CERTIFICATE_ISSUER"
    settings.certificate_reader_role = "CERTIFICATE_READER"
    settings.fabric_commit_timeout_s = 55.0

    monkeypatch.setattr(config_mod, "_settings", settings)


# ---------------------------------------------------------------------------
# Fixtures: default (clean state)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def client() -> AsyncGenerator[tuple[AsyncClient, FakePool, FakeFabricClient, FakeKafkaProducer], None]:
    pool = FakePool()
    fabric = FakeFabricClient()
    kafka = FakeKafkaProducer()

    import app.db as db_mod
    import app.fabric_client as fabric_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    application = create_app()

    async def _get_pool():
        return pool

    async def _get_producer():
        return kafka

    with (
        patch.object(db_mod, "get_pool", _get_pool),
        patch.object(fabric_mod, "get_fabric_client", lambda: fabric),
        patch.object(kafka_mod, "get_producer", _get_producer),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as ac:
            yield ac, pool, fabric, kafka


@pytest_asyncio.fixture()
async def client_with_existing() -> AsyncGenerator[tuple[AsyncClient, FakePool, FakeFabricClient, FakeKafkaProducer], None]:
    """Pool already contains the certificate record (idempotent replay path)."""
    store = {"existing_certs": {_CERT_ID: _make_existing_cert_row()}}
    pool = FakePool(store=store)
    fabric = FakeFabricClient(existing_hash=_HASH)
    kafka = FakeKafkaProducer()

    import app.db as db_mod
    import app.fabric_client as fabric_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    application = create_app()

    async def _get_pool():
        return pool

    async def _get_producer():
        return kafka

    with (
        patch.object(db_mod, "get_pool", _get_pool),
        patch.object(fabric_mod, "get_fabric_client", lambda: fabric),
        patch.object(kafka_mod, "get_producer", _get_producer),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as ac:
            yield ac, pool, fabric, kafka


@pytest_asyncio.fixture()
async def client_fabric_unavailable() -> AsyncGenerator[AsyncClient, None]:
    pool = FakePool()
    fabric = FakeFabricClient(unavailable=True)
    kafka = FakeKafkaProducer()

    import app.db as db_mod
    import app.fabric_client as fabric_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    application = create_app()

    async def _get_pool():
        return pool

    async def _get_producer():
        return kafka

    with (
        patch.object(db_mod, "get_pool", _get_pool),
        patch.object(fabric_mod, "get_fabric_client", lambda: fabric),
        patch.object(kafka_mod, "get_producer", _get_producer),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as ac:
            yield ac


@pytest_asyncio.fixture()
async def client_fabric_timeout() -> AsyncGenerator[AsyncClient, None]:
    pool = FakePool()
    fabric = FakeFabricClient(timeout=True)
    kafka = FakeKafkaProducer()

    import app.db as db_mod
    import app.fabric_client as fabric_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    application = create_app()

    async def _get_pool():
        return pool

    async def _get_producer():
        return kafka

    with (
        patch.object(db_mod, "get_pool", _get_pool),
        patch.object(fabric_mod, "get_fabric_client", lambda: fabric),
        patch.object(kafka_mod, "get_producer", _get_producer),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as ac:
            yield ac


@pytest_asyncio.fixture()
async def client_with_cert_rows() -> AsyncGenerator[tuple[AsyncClient, FakePool], None]:
    row = _make_existing_cert_row()
    store = {
        "cert_rows": [row],
        "existing_certs": {_CERT_ID: row},
    }
    pool = FakePool(store=store)
    fabric = FakeFabricClient()
    kafka = FakeKafkaProducer()

    import app.db as db_mod
    import app.fabric_client as fabric_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    application = create_app()

    async def _get_pool():
        return pool

    async def _get_producer():
        return kafka

    with (
        patch.object(db_mod, "get_pool", _get_pool),
        patch.object(fabric_mod, "get_fabric_client", lambda: fabric),
        patch.object(kafka_mod, "get_producer", _get_producer),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as ac:
            yield ac, pool


# ---------------------------------------------------------------------------
# Tests: POST /certificate/issue — 201 new issuance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_new_certificate_returns_201(
    client: tuple,
) -> None:
    ac, pool, fabric, kafka = client
    resp = await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_issue_new_certificate_response_fields(
    client: tuple,
) -> None:
    ac, *_ = client
    resp = await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    body = resp.json()
    assert "certificateId" in body
    assert "vin" in body
    assert "fabricTransactionId" in body
    assert "fabricBlockNumber" in body
    assert "anchoredAt" in body
    assert "certificateHash" in body


@pytest.mark.asyncio
async def test_issue_new_certificate_vin_matches_request(
    client: tuple,
) -> None:
    ac, *_ = client
    resp = await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert resp.json()["vin"] == _VIN


@pytest.mark.asyncio
async def test_issue_new_certificate_hash_matches_request(
    client: tuple,
) -> None:
    ac, *_ = client
    resp = await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert resp.json()["certificateHash"] == _HASH


@pytest.mark.asyncio
async def test_issue_new_certificate_emits_kafka_event(
    client: tuple,
) -> None:
    ac, pool, fabric, kafka = client
    await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert len(kafka.messages) == 1
    assert kafka.messages[0]["topic"] == "bmw.ifos.munich.fabric.certificate.issued.v1"


@pytest.mark.asyncio
async def test_issue_new_certificate_kafka_event_contains_cert_id(
    client: tuple,
) -> None:
    ac, pool, fabric, kafka = client
    await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    payload = kafka.messages[0]["payload"]
    assert payload["certificateId"] == _CERT_ID


# ---------------------------------------------------------------------------
# Tests: POST /certificate/issue — 200 idempotent replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_idempotent_replay_returns_200(
    client_with_existing: tuple,
) -> None:
    ac, *_ = client_with_existing
    resp = await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_issue_idempotent_replay_returns_idempotent_true(
    client_with_existing: tuple,
) -> None:
    ac, *_ = client_with_existing
    resp = await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert resp.json()["idempotent"] is True


@pytest.mark.asyncio
async def test_issue_idempotent_replay_no_fabric_call(
    client_with_existing: tuple,
) -> None:
    ac, pool, fabric, kafka = client_with_existing
    await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert len(fabric.issue_calls) == 0


# ---------------------------------------------------------------------------
# Tests: POST /certificate/issue — 409 hash conflict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_hash_conflict_returns_409(client: tuple) -> None:
    ac, pool, fabric, kafka = client
    # Pre-populate with a different hash
    pool._store["existing_certs"] = {
        _CERT_ID: {**_make_existing_cert_row(), "certificate_hash": _ALT_HASH}
    }
    resp = await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_issue_hash_conflict_response_has_error_envelope(client: tuple) -> None:
    ac, pool, fabric, kafka = client
    pool._store["existing_certs"] = {
        _CERT_ID: {**_make_existing_cert_row(), "certificate_hash": _ALT_HASH}
    }
    resp = await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    body = resp.json()
    assert "code" in body
    assert "message" in body
    assert "correlation_id" in body


# ---------------------------------------------------------------------------
# Tests: POST /certificate/issue — 502 Fabric unavailable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_fabric_unavailable_returns_502(
    client_fabric_unavailable: AsyncClient,
) -> None:
    resp = await client_fabric_unavailable.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Tests: POST /certificate/issue — 504 Fabric timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_fabric_timeout_returns_504(
    client_fabric_timeout: AsyncClient,
) -> None:
    resp = await client_fabric_timeout.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert resp.status_code == 504


# ---------------------------------------------------------------------------
# Tests: POST /certificate/issue — 401 / 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_missing_bearer_returns_401(client: tuple) -> None:
    ac, *_ = client
    resp = await ac.post("/certificate/issue", json=_VALID_ISSUE_BODY)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_issue_missing_issuer_role_returns_403(client: tuple) -> None:
    ac, *_ = client
    resp = await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _NO_ROLE_BEARER},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests: POST /certificate/issue — 400 validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_invalid_hash_returns_400(client: tuple) -> None:
    ac, *_ = client
    body = {**_VALID_ISSUE_BODY, "certificateHash": "not-a-hash"}
    resp = await ac.post(
        "/certificate/issue",
        json=body,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_issue_missing_required_field_returns_400(client: tuple) -> None:
    ac, *_ = client
    body = dict(_VALID_ISSUE_BODY)
    del body["vin"]
    resp = await ac.post(
        "/certificate/issue",
        json=body,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: GET /certificates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_certificates_returns_200(client_with_cert_rows: tuple) -> None:
    ac, _ = client_with_cert_rows
    resp = await ac.get(
        "/certificates",
        headers={"Authorization": _READER_BEARER},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_certificates_response_has_data_and_page(
    client_with_cert_rows: tuple,
) -> None:
    ac, _ = client_with_cert_rows
    resp = await ac.get(
        "/certificates",
        headers={"Authorization": _READER_BEARER},
    )
    body = resp.json()
    assert "data" in body
    assert "page" in body
    assert "next_cursor" in body["page"]
    assert "limit" in body["page"]


@pytest.mark.asyncio
async def test_list_certificates_returns_one_record(
    client_with_cert_rows: tuple,
) -> None:
    ac, _ = client_with_cert_rows
    resp = await ac.get(
        "/certificates",
        headers={"Authorization": _READER_BEARER},
    )
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["vin"] == _VIN


@pytest.mark.asyncio
async def test_list_certificates_reader_role_required(client: tuple) -> None:
    ac, *_ = client
    resp = await ac.get("/certificates", headers={"Authorization": _NO_ROLE_BEARER})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_certificates_missing_bearer_returns_401(client: tuple) -> None:
    ac, *_ = client
    resp = await ac.get("/certificates")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests: GET /certificate/{certificateId}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_certificate_found_returns_200(
    client_with_cert_rows: tuple,
) -> None:
    ac, _ = client_with_cert_rows
    resp = await ac.get(
        f"/certificate/{_CERT_ID}",
        headers={"Authorization": _READER_BEARER},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_certificate_found_returns_correct_vin(
    client_with_cert_rows: tuple,
) -> None:
    ac, _ = client_with_cert_rows
    resp = await ac.get(
        f"/certificate/{_CERT_ID}",
        headers={"Authorization": _READER_BEARER},
    )
    assert resp.json()["vin"] == _VIN


@pytest.mark.asyncio
async def test_get_certificate_found_has_all_required_fields(
    client_with_cert_rows: tuple,
) -> None:
    ac, _ = client_with_cert_rows
    resp = await ac.get(
        f"/certificate/{_CERT_ID}",
        headers={"Authorization": _READER_BEARER},
    )
    body = resp.json()
    for field in (
        "certificateId", "vin", "gateResultSnapshot", "healthScore",
        "validatorId", "issuedAt", "certificateHash",
        "fabricTransactionId", "fabricBlockNumber", "anchoredAt", "sourceSystem",
    ):
        assert field in body, f"Missing required field: {field}"


@pytest.mark.asyncio
async def test_get_certificate_not_found_returns_404(client: tuple) -> None:
    ac, *_ = client
    resp = await ac.get(
        "/certificate/non-existent-id",
        headers={"Authorization": _READER_BEARER},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_certificate_missing_bearer_returns_401(client: tuple) -> None:
    ac, *_ = client
    resp = await ac.get(f"/certificate/{_CERT_ID}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_certificate_wrong_role_returns_403(client: tuple) -> None:
    ac, *_ = client
    resp = await ac.get(
        f"/certificate/{_CERT_ID}",
        headers={"Authorization": _NO_ROLE_BEARER},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests: GET /certificate/{certificateId}/verify
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def client_with_verify() -> AsyncGenerator[tuple[AsyncClient, FakeFabricClient], None]:
    row = _make_existing_cert_row()
    store = {
        "existing_certs": {_CERT_ID: row},
        "cert_rows": [row],
    }
    pool = FakePool(store=store)
    fabric = FakeFabricClient(ledger_hash=_HASH)
    kafka = FakeKafkaProducer()

    import app.db as db_mod
    import app.fabric_client as fabric_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    application = create_app()

    async def _get_pool():
        return pool

    async def _get_producer():
        return kafka

    with (
        patch.object(db_mod, "get_pool", _get_pool),
        patch.object(fabric_mod, "get_fabric_client", lambda: fabric),
        patch.object(kafka_mod, "get_producer", _get_producer),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as ac:
            yield ac, fabric


@pytest.mark.asyncio
async def test_verify_certificate_matching_hash_returns_200(
    client_with_verify: tuple,
) -> None:
    ac, _ = client_with_verify
    resp = await ac.get(
        f"/certificate/{_CERT_ID}/verify",
        headers={"Authorization": _READER_BEARER},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_verify_certificate_matching_hash_verified_true(
    client_with_verify: tuple,
) -> None:
    ac, _ = client_with_verify
    resp = await ac.get(
        f"/certificate/{_CERT_ID}/verify",
        headers={"Authorization": _READER_BEARER},
    )
    body = resp.json()
    assert body["verified"] is True
    assert body["storedHash"] == _HASH
    assert body["fabricHash"] == _HASH


@pytest.mark.asyncio
async def test_verify_certificate_response_has_required_fields(
    client_with_verify: tuple,
) -> None:
    ac, _ = client_with_verify
    resp = await ac.get(
        f"/certificate/{_CERT_ID}/verify",
        headers={"Authorization": _READER_BEARER},
    )
    body = resp.json()
    for field in ("certificateId", "verified", "storedHash", "fabricTransactionId"):
        assert field in body, f"Missing required field: {field}"


@pytest.mark.asyncio
async def test_verify_certificate_not_found_returns_404(client: tuple) -> None:
    ac, *_ = client
    resp = await ac.get(
        "/certificate/not-found-id/verify",
        headers={"Authorization": _READER_BEARER},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_verify_certificate_missing_bearer_returns_401(client: tuple) -> None:
    ac, *_ = client
    resp = await ac.get(f"/certificate/{_CERT_ID}/verify")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_verify_certificate_wrong_role_returns_403(client: tuple) -> None:
    ac, *_ = client
    resp = await ac.get(
        f"/certificate/{_CERT_ID}/verify",
        headers={"Authorization": _NO_ROLE_BEARER},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests: verify with hash mismatch
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def client_with_mismatch() -> AsyncGenerator[tuple[AsyncClient, FakeKafkaProducer], None]:
    row = _make_existing_cert_row()
    store = {
        "existing_certs": {_CERT_ID: row},
        "cert_rows": [row],
    }
    pool = FakePool(store=store)
    fabric = FakeFabricClient(ledger_hash=_ALT_HASH)
    kafka = FakeKafkaProducer()

    import app.db as db_mod
    import app.fabric_client as fabric_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    application = create_app()

    async def _get_pool():
        return pool

    async def _get_producer():
        return kafka

    with (
        patch.object(db_mod, "get_pool", _get_pool),
        patch.object(fabric_mod, "get_fabric_client", lambda: fabric),
        patch.object(kafka_mod, "get_producer", _get_producer),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as ac:
            yield ac, kafka


@pytest.mark.asyncio
async def test_verify_mismatch_returns_verified_false(
    client_with_mismatch: tuple,
) -> None:
    ac, kafka = client_with_mismatch
    resp = await ac.get(
        f"/certificate/{_CERT_ID}/verify",
        headers={"Authorization": _READER_BEARER},
    )
    body = resp.json()
    assert body["verified"] is False
    assert body["storedHash"] == _HASH
    assert body["fabricHash"] == _ALT_HASH


@pytest.mark.asyncio
async def test_verify_mismatch_emits_verify_failed_event(
    client_with_mismatch: tuple,
) -> None:
    ac, kafka = client_with_mismatch
    await ac.get(
        f"/certificate/{_CERT_ID}/verify",
        headers={"Authorization": _READER_BEARER},
    )
    assert len(kafka.messages) == 1
    assert kafka.messages[0]["topic"] == "bmw.ifos.munich.fabric.certificate.verify-failed.v1"


# ---------------------------------------------------------------------------
# Tests: error envelope shape on every error response
# ---------------------------------------------------------------------------

_ERROR_FIELDS = {"code", "message", "correlation_id"}


@pytest.mark.asyncio
async def test_401_issue_response_has_error_envelope(client: tuple) -> None:
    ac, *_ = client
    resp = await ac.post("/certificate/issue", json=_VALID_ISSUE_BODY)
    assert resp.status_code == 401
    body = resp.json()
    for field in _ERROR_FIELDS:
        assert field in body, f"Missing {field!r} in 401 envelope"


@pytest.mark.asyncio
async def test_403_issue_response_has_error_envelope(client: tuple) -> None:
    ac, *_ = client
    resp = await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _NO_ROLE_BEARER},
    )
    assert resp.status_code == 403
    body = resp.json()
    for field in _ERROR_FIELDS:
        assert field in body, f"Missing {field!r} in 403 envelope"


# ---------------------------------------------------------------------------
# Tests: instance isolation
# ---------------------------------------------------------------------------


def test_fake_pool_instance_isolation() -> None:
    pool_a = FakePool()
    pool_b = FakePool()
    pool_a._store["key"] = {"value": 1}
    assert "key" not in pool_b._store


def test_fake_pool_acquire_count_restarts_per_instance() -> None:
    pool_a = FakePool()
    pool_a.acquire()
    pool_b = FakePool()
    assert pool_b._acquire_count == 0, "New instance must start with _acquire_count=0"


def test_fake_kafka_instance_isolation() -> None:
    k1 = FakeKafkaProducer()
    k2 = FakeKafkaProducer()
    asyncio.run(k1.send_and_wait("topic", {"msg": 1}))
    assert len(k2.messages) == 0, "New instance must start with empty messages list"


def test_fake_fabric_instance_isolation() -> None:
    f1 = FakeFabricClient()
    f2 = FakeFabricClient()
    f1.issue_calls.append({"certificate_id": "x"})
    assert len(f2.issue_calls) == 0, "New instance must start with empty issue_calls"
