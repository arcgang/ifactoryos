"""Acceptance tests for US-018 — Digital Birth Certificate end-to-end.

Covers the seam between hyperledger-fabric-api (task 2) and the data contract
that quality-studio (task 3) depends on.  Unit tests for each task live
elsewhere; these tests prove the three acceptance criteria hold across the
integration boundary.

AC1 — Certificate contains VIN, gate results snapshot, health score,
       certifying validator ID, timestamp, and a Fabric TX hash.
AC2 — Certificate hash is retrievable from Fabric and matches the content,
       confirming tamper-evidence.
AC3 — List endpoint returns per-record Fabric anchor hashes so the frontend
       can build the Shift Summary PDF.
"""

from __future__ import annotations

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
# Shared constants
# ---------------------------------------------------------------------------

_CERT_ID = "550e8400-e29b-41d4-a716-446655440000"
_VIN = "WBA3A5C55FK123456"
_HASH = "a3f2c1d4e5b6a7f8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
_ALT_HASH = "b3f2c1d4e5b6a7f8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b3"
_VALIDATOR_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_SHIFT_ID = "d4e5f6a7-b8c9-0d1e-2f3a-4b5c6d7e8f9a"
_ISSUED_AT = "2026-10-13T07:55:00Z"
_ANCHORED_AT = "2026-10-13T08:00:00+00:00"

_ISSUER_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbIkNFUlRJRklDQVRFX0lTU1VFUiJdfQ.sig"
)
_READER_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbIkNFUlRJRklDQVRFX1JFQURFUiJdfQ.sig"
)

_VALID_ISSUE_BODY: dict = {
    "certificateId": _CERT_ID,
    "vin": _VIN,
    "gateResultSnapshot": {
        "gateId": "GATE-3",
        "stationResults": [
            {"stationCode": "DIM", "result": "PASS"},
            {"stationCode": "LEAK", "result": "PASS"},
            {"stationCode": "ELEC", "result": "PASS"},
            {"stationCode": "NVH", "result": "CONDITIONAL"},
            {"stationCode": "ADAS", "result": "PASS"},
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
# Fake Fabric anchor result and client
# ---------------------------------------------------------------------------


class _FakeAnchorResult:
    def __init__(self) -> None:
        self.transaction_id = "fabric-tx-" + uuid.uuid4().hex[:12]
        self.block_number = 42
        self.anchored_at = _ANCHORED_AT


class _FakeFabricCertData:
    def __init__(self, certificate_hash: str) -> None:
        self.certificate_id = _CERT_ID
        self.vin = _VIN
        self.certificate_hash = certificate_hash
        self.validator_id = _VALIDATOR_ID
        self.issued_at = _ISSUED_AT
        self.transaction_id = "fabric-tx-abc123"
        self.block_number = 42
        self.anchored_at = _ANCHORED_AT


class _FakeFabricClient:
    """Minimal fake Fabric client for acceptance tests."""

    def __init__(self, *, ledger_hash: str = _HASH, unavailable: bool = False) -> None:
        self._ledger_hash = ledger_hash
        self._unavailable = unavailable
        self.issue_calls: list[dict] = []
        self.get_calls: list[str] = []

    async def issue_certificate(
        self,
        certificate_id: str,
        vin: str,
        certificate_hash: str,
        validator_id: str,
        issued_at: str,
    ) -> tuple[_FakeAnchorResult, bool]:
        from app.fabric_client import FabricUnavailableError

        self.issue_calls.append({"certificate_id": certificate_id, "hash": certificate_hash})
        if self._unavailable:
            raise FabricUnavailableError("peer unavailable")
        return _FakeAnchorResult(), False

    async def get_certificate(self, certificate_id: str) -> _FakeFabricCertData:
        from app.fabric_client import FabricUnavailableError

        self.get_calls.append(certificate_id)
        if self._unavailable:
            raise FabricUnavailableError("peer unavailable")
        return _FakeFabricCertData(self._ledger_hash)


# ---------------------------------------------------------------------------
# Fake Kafka producer
# ---------------------------------------------------------------------------


class _FakeKafkaProducer:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_and_wait(self, topic: str, payload: dict) -> None:
        self.messages.append({"topic": topic, "payload": payload})


# ---------------------------------------------------------------------------
# CapturingFakePool — persists INSERT data for subsequent SELECTs
#
# The standard FakePool used in unit tests does not persist data written via
# execute(), so it cannot be used for issue → get / verify round-trip tests.
# This pool stores fabric_certificate rows when INSERT is called and returns
# them on fetchrow / fetch, allowing end-to-end flows within one test.
# ---------------------------------------------------------------------------


class _CapturingFakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


def _parse_dt(v: str | datetime) -> datetime:
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


class _CapturingFakeConnection:
    def __init__(self, store: dict) -> None:
        self._store = store

    def transaction(self):
        class _Tx:
            async def __aenter__(_self) -> "_Tx":
                return _self

            async def __aexit__(_self, *_) -> None:
                pass

        return _Tx()

    async def fetchrow(self, query: str, *args: Any) -> _CapturingFakeRecord | None:
        cert_id = str(args[0])
        row = self._store.get("certs", {}).get(cert_id)
        return _CapturingFakeRecord(row) if row is not None else None

    async def fetch(self, query: str, *args: Any) -> list[_CapturingFakeRecord]:
        rows = list(self._store.get("certs", {}).values())
        limit = int(args[-1]) if args else 50
        return [_CapturingFakeRecord(r) for r in rows[:limit]]

    async def execute(self, query: str, *args: Any) -> None:
        if "INSERT INTO FABRIC_CERTIFICATE" in query.upper():
            # Positional order from certificate_service.issue_certificate:
            # cert_id, vin, shift_id, snapshot_json, health_score,
            # validator_id, issued_at, cert_hash, tx_id, block_number,
            # anchored_at, source_system
            (
                cert_id, vin, shift_id, snapshot_json, health_score,
                validator_id, issued_at, cert_hash, tx_id, block_number,
                anchored_at, source_system,
            ) = args
            self._store.setdefault("certs", {})[str(cert_id)] = {
                "certificate_id": str(cert_id),
                "vin": str(vin),
                "shift_id": str(shift_id) if shift_id else None,
                "gate_result_snapshot": snapshot_json,
                "health_score": float(health_score),
                "validator_id": str(validator_id),
                "issued_at": _parse_dt(issued_at),
                "certificate_hash": str(cert_hash),
                "fabric_transaction_id": str(tx_id),
                "fabric_block_number": int(block_number),
                "anchored_at": _parse_dt(anchored_at),
                "source_system": str(source_system),
            }


class _CapturingFakePool:
    def __init__(self) -> None:
        self._store: dict = {}
        self._acquire_count: int = 0

    def acquire(self) -> Any:
        store = self._store

        class _CM:
            async def __aenter__(_self) -> _CapturingFakeConnection:
                return _CapturingFakeConnection(store)

            async def __aexit__(_self, *_) -> None:
                pass

        self._acquire_count += 1
        return _CM()


# ---------------------------------------------------------------------------
# Shared settings patch (required by every test in this service)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
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
# Fixture: capturing client — supports full issue → get/verify round-trips
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def capturing_client() -> AsyncGenerator[
    tuple[AsyncClient, _CapturingFakePool, _FakeFabricClient, _FakeKafkaProducer],
    None,
]:
    pool = _CapturingFakePool()
    fabric = _FakeFabricClient(ledger_hash=_HASH)
    kafka = _FakeKafkaProducer()

    import app.db as db_mod
    import app.fabric_client as fabric_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    application = create_app()

    async def _get_pool() -> _CapturingFakePool:
        return pool  # type: ignore[return-value]

    async def _get_producer() -> _FakeKafkaProducer:
        return kafka  # type: ignore[return-value]

    with (
        patch.object(db_mod, "get_pool", _get_pool),
        patch.object(fabric_mod, "get_fabric_client", lambda: fabric),
        patch.object(kafka_mod, "get_producer", _get_producer),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as ac:
            yield ac, pool, fabric, kafka


# ---------------------------------------------------------------------------
# Fixture: capturing client with a mismatching Fabric ledger hash (tamper)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def capturing_client_tampered() -> AsyncGenerator[
    tuple[AsyncClient, _CapturingFakePool, _FakeFabricClient, _FakeKafkaProducer],
    None,
]:
    pool = _CapturingFakePool()
    fabric = _FakeFabricClient(ledger_hash=_ALT_HASH)
    kafka = _FakeKafkaProducer()

    import app.db as db_mod
    import app.fabric_client as fabric_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    application = create_app()

    async def _get_pool() -> _CapturingFakePool:
        return pool  # type: ignore[return-value]

    async def _get_producer() -> _FakeKafkaProducer:
        return kafka  # type: ignore[return-value]

    with (
        patch.object(db_mod, "get_pool", _get_pool),
        patch.object(fabric_mod, "get_fabric_client", lambda: fabric),
        patch.object(kafka_mod, "get_producer", _get_producer),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as ac:
            yield ac, pool, fabric, kafka


# ---------------------------------------------------------------------------
# Fixture: pre-seeded list client for AC3 shift-list tests
# ---------------------------------------------------------------------------


def _make_cert_row_for_shift() -> dict:
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
        "fabric_transaction_id": "fabric-tx-anchor-001",
        "fabric_block_number": 42,
        "anchored_at": datetime.fromisoformat(_ANCHORED_AT),
        "source_system": "quality-studio",
    }


class _SimpleFakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


class _SimpleFakeConnection:
    def __init__(self, store: dict) -> None:
        self._store = store

    def transaction(self):
        class _Tx:
            async def __aenter__(self_): return self_
            async def __aexit__(self_, *_): pass
        return _Tx()

    async def fetchrow(self, query: str, *args: Any) -> _SimpleFakeRecord | None:
        cert_id = str(args[0])
        existing = self._store.get("existing_certs", {})
        return _SimpleFakeRecord(existing[cert_id]) if cert_id in existing else None

    async def fetch(self, query: str, *args: Any) -> list[_SimpleFakeRecord]:
        rows = self._store.get("cert_rows", [])
        limit = int(args[-1]) if args else 50
        return [_SimpleFakeRecord(r) for r in rows[:limit]]

    async def execute(self, query: str, *args: Any) -> None:
        pass


class _SimpleFakePool:
    def __init__(self, store: dict | None = None) -> None:
        self._store: dict = store or {}

    def acquire(self) -> Any:
        store = self._store

        class _CM:
            async def __aenter__(_self) -> _SimpleFakeConnection:
                return _SimpleFakeConnection(store)
            async def __aexit__(_self, *_) -> None:
                pass

        return _CM()


@pytest_asyncio.fixture()
async def list_client() -> AsyncGenerator[AsyncClient, None]:
    row = _make_cert_row_for_shift()
    store = {"cert_rows": [row], "existing_certs": {_CERT_ID: row}}
    pool = _SimpleFakePool(store=store)
    fabric = _FakeFabricClient()
    kafka = _FakeKafkaProducer()

    import app.db as db_mod
    import app.fabric_client as fabric_mod
    import app.kafka_producer as kafka_mod
    from app.main import create_app

    application = create_app()

    async def _get_pool() -> _SimpleFakePool:
        return pool  # type: ignore[return-value]

    async def _get_producer() -> _FakeKafkaProducer:
        return kafka  # type: ignore[return-value]

    with (
        patch.object(db_mod, "get_pool", _get_pool),
        patch.object(fabric_mod, "get_fabric_client", lambda: fabric),
        patch.object(kafka_mod, "get_producer", _get_producer),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as ac:
            yield ac


# ===========================================================================
# AC1 — Certificate contains VIN, gate snapshot, health score, validator ID,
#        timestamp, and Fabric TX hash
# ===========================================================================


@pytest.mark.asyncio
async def test_ac1_issue_response_contains_vin_and_fabric_tx_hash(
    capturing_client: tuple,
) -> None:
    """AC1: POST /certificate/issue response includes VIN and Fabric TX hash."""
    # Precondition: the endpoint is registered and the app is importable
    ac, pool, fabric, kafka = capturing_client
    assert ac is not None, "Application client must be available"

    resp = await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    body = resp.json()
    assert body["vin"] == _VIN, "Issue response must include the VIN"
    assert body["certificateHash"] == _HASH, "Issue response must include the certificate hash"
    assert body["fabricTransactionId"], "Issue response must include a non-empty Fabric TX ID"
    assert body["fabricBlockNumber"] == 42, "Issue response must include the Fabric block number"
    assert body["anchoredAt"], "Issue response must include anchoredAt timestamp"


@pytest.mark.asyncio
async def test_ac1_get_certificate_after_issue_has_all_required_fields(
    capturing_client: tuple,
) -> None:
    """AC1: GET /certificate/{id} after issue returns VIN, gate snapshot, health
    score, validator ID, issuedAt, certificateHash, and fabricTransactionId —
    all required by the acceptance criterion.
    """
    ac, pool, fabric, kafka = capturing_client

    # Issue
    issue_resp = await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert issue_resp.status_code == 201

    issued_tx_id = issue_resp.json()["fabricTransactionId"]

    # Retrieve the certificate — this crosses the issue→get boundary
    get_resp = await ac.get(
        f"/certificate/{_CERT_ID}",
        headers={"Authorization": _READER_BEARER},
    )
    assert get_resp.status_code == 200, (
        f"GET /certificate must return 200 after issue; got {get_resp.status_code}"
    )

    body = get_resp.json()

    # AC1 required fields
    assert body["vin"] == _VIN, "VIN must be preserved through issue→get"
    assert body["healthScore"] == 98.5, "healthScore must be preserved through issue→get"
    assert body["validatorId"] == _VALIDATOR_ID, "validatorId must be preserved through issue→get"
    assert body["issuedAt"], "issuedAt must be present"
    assert body["certificateHash"] == _HASH, "certificateHash must equal the issued hash"
    assert body["fabricTransactionId"] == issued_tx_id, (
        "fabricTransactionId from GET must match the TX ID returned by issue"
    )

    # Gate results snapshot must be preserved intact
    snapshot = body.get("gateResultSnapshot")
    assert snapshot is not None, "gateResultSnapshot must be present in GET response"
    assert snapshot["gateId"] == "GATE-3", "gateResultSnapshot.gateId must be preserved"
    station_codes = [s["stationCode"] for s in snapshot["stationResults"]]
    assert set(station_codes) == {"DIM", "LEAK", "ELEC", "NVH", "ADAS"}, (
        "All five station results must be preserved in gateResultSnapshot"
    )


@pytest.mark.asyncio
async def test_ac1_kafka_event_emitted_on_issue(
    capturing_client: tuple,
) -> None:
    """AC1: A Kafka event is published when the certificate is issued, enabling
    downstream audit and EU Battery Passport compliance hooks.
    """
    ac, pool, fabric, kafka = capturing_client

    await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )

    assert len(kafka.messages) == 1, "Exactly one Kafka event must be emitted on issue"
    event = kafka.messages[0]
    assert event["topic"] == "bmw.ifos.munich.fabric.certificate.issued.v1"
    payload = event["payload"]
    assert payload["vin"] == _VIN
    assert payload["certificateHash"] == _HASH
    assert payload["fabricTransactionId"], "Kafka event must carry the Fabric TX ID"
    assert payload["validatorId"] == _VALIDATOR_ID


# ===========================================================================
# AC2 — Certificate hash is retrievable from Fabric and matches content
# ===========================================================================


@pytest.mark.asyncio
async def test_ac2_issued_hash_matches_fabric_anchor_on_verify(
    capturing_client: tuple,
) -> None:
    """AC2: After issuing, GET /certificate/{id}/verify confirms the stored hash
    equals the Fabric ledger hash and reports verified=true — tamper-evidence
    intact.
    """
    ac, pool, fabric, kafka = capturing_client

    # Issue the certificate — note the hash
    issue_resp = await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert issue_resp.status_code == 201
    issued_hash = issue_resp.json()["certificateHash"]

    # Query Fabric tamper-evidence verification — seam between backend and Fabric
    verify_resp = await ac.get(
        f"/certificate/{_CERT_ID}/verify",
        headers={"Authorization": _READER_BEARER},
    )
    assert verify_resp.status_code == 200, (
        f"Verify must return 200 when hash matches; got {verify_resp.status_code}"
    )

    body = verify_resp.json()
    assert body["verified"] is True, "verified must be True when hash matches ledger"
    assert body["storedHash"] == issued_hash, (
        "storedHash in verify response must equal the hash submitted at issue time"
    )
    assert body["fabricHash"] == issued_hash, (
        "fabricHash from ledger must match the issued certificateHash"
    )
    assert body["fabricTransactionId"], "fabricTransactionId must be present in verify response"


@pytest.mark.asyncio
async def test_ac2_tamper_detected_when_fabric_hash_differs(
    capturing_client_tampered: tuple,
) -> None:
    """AC2: When the Fabric ledger returns a different hash, verify reports
    verified=false — tamper detected — and emits a verification-failed event.
    """
    ac, pool, fabric, kafka = capturing_client_tampered

    # Issue with _HASH; Fabric will return _ALT_HASH (simulating tamper)
    issue_resp = await ac.post(
        "/certificate/issue",
        json=_VALID_ISSUE_BODY,
        headers={"Authorization": _ISSUER_BEARER},
    )
    assert issue_resp.status_code == 201
    issued_hash = issue_resp.json()["certificateHash"]

    verify_resp = await ac.get(
        f"/certificate/{_CERT_ID}/verify",
        headers={"Authorization": _READER_BEARER},
    )
    assert verify_resp.status_code == 200

    body = verify_resp.json()
    assert body["verified"] is False, "verified must be False when ledger hash differs"
    assert body["storedHash"] == issued_hash, "storedHash must reflect the DB value"
    assert body["fabricHash"] == _ALT_HASH, "fabricHash must reflect the tampered ledger value"
    assert body["storedHash"] != body["fabricHash"], (
        "storedHash and fabricHash must differ when tamper is detected"
    )

    # Tamper event must be emitted (one issue event + one verify-failed event)
    tamper_events = [
        m for m in kafka.messages
        if m["topic"] == "bmw.ifos.munich.fabric.certificate.verify-failed.v1"
    ]
    assert len(tamper_events) == 1, (
        "A verify-failed Kafka event must be emitted when tamper is detected"
    )
    tamper_payload = tamper_events[0]["payload"]
    assert tamper_payload["certificateId"] == _CERT_ID
    assert tamper_payload["storedHash"] == issued_hash
    assert tamper_payload["fabricHash"] == _ALT_HASH


# ===========================================================================
# AC3 — List endpoint returns per-record Fabric anchor hashes so frontend PDF
#        can include VINs, timestamps, and Fabric TX IDs
# ===========================================================================


@pytest.mark.asyncio
async def test_ac3_list_certificates_per_record_includes_fabric_tx_id(
    list_client: AsyncClient,
) -> None:
    """AC3: GET /certificates returns each record's fabricTransactionId — this
    field is used by the Export Shift Summary PDF; its absence would produce a
    PDF missing the Fabric anchor column.
    """
    # Precondition: pre-seeded cert row must include fabric_transaction_id
    resp = await list_client.get(
        "/certificates",
        headers={"Authorization": _READER_BEARER},
    )
    assert resp.status_code == 200

    body = resp.json()
    assert len(body["data"]) >= 1, "At least one certificate row must be present"

    for record in body["data"]:
        assert "fabricTransactionId" in record, (
            "Each record in GET /certificates must expose fabricTransactionId"
        )
        assert record["fabricTransactionId"], (
            "fabricTransactionId must be non-empty for an anchored certificate"
        )
        assert "vin" in record, "Each record must include vin"
        assert "issuedAt" in record, "Each record must include issuedAt"


@pytest.mark.asyncio
async def test_ac3_list_certificates_shiftid_filter_returns_shift_certs(
    list_client: AsyncClient,
) -> None:
    """AC3: GET /certificates?shiftId=X accepts the shiftId query parameter
    (required by the ExportShiftSummaryButton pagination flow).
    """
    resp = await list_client.get(
        f"/certificates?shiftId={_SHIFT_ID}",
        headers={"Authorization": _READER_BEARER},
    )
    assert resp.status_code == 200, (
        f"shiftId filter must be accepted; got {resp.status_code}"
    )

    body = resp.json()
    assert "data" in body
    assert "page" in body
    assert "next_cursor" in body["page"]


@pytest.mark.asyncio
async def test_ac3_list_certificates_response_has_cursor_pagination(
    list_client: AsyncClient,
) -> None:
    """AC3: List response includes pagination envelope so the frontend can
    exhaust all pages when building the shift PDF.
    """
    resp = await list_client.get(
        "/certificates?limit=200",
        headers={"Authorization": _READER_BEARER},
    )
    assert resp.status_code == 200

    page = resp.json()["page"]
    assert "next_cursor" in page, "page envelope must include next_cursor for pagination"
    assert "limit" in page, "page envelope must include limit"
