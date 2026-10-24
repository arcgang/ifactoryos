"""Acceptance tests — US-021 · Quality Studio session events end-to-end.

Covers:
  AC1  Every tracked meaningful action (PROCESS_REPLAY_LAUNCH, DEFECT_GENOME_QUERY,
       ROOT_CAUSE_ASSIGNED, CERTIFY, CERTIFICATE_STUDIO_OPEN) is counted as an
       active user-day by the WAU calculator.  Each action in isolation produces
       WAU=1 for the week it falls in.

  AC2  A login-only session (no meaningful action) contributes 0 to WAU.  The
       compute_quality_studio_wau function — the Python mirror of the KQL saved
       queries quality_studio_paint_engineer_wau / quality_studio_quality_validator_wau
       — correctly excludes such sessions.

  AC3  A CertificateIssuedEvent published by POST /certificate/issue carries
       fabricTransactionId, enabling A7 (≥90% certs via Quality Studio)
       measurement by cross-referencing the Kafka event payload with the Fabric
       anchor count.

Seam under test: the junction between the Quality Studio frontend emitters
(apps/quality-studio/src/instrumentation/adoptionEvents.ts), the Hyperledger
Fabric API (services/hyperledger-fabric-api), and the observability-stack WAU
calculator (compute_quality_studio_wau in the existing test helper module).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# ── Precondition: WAU calculator is importable ────────────────────────────────
# If the module or the QUALITY_STUDIO_MEANINGFUL_ACTIONS set do not exist the
# import fails loudly, making the test-suite fail rather than silently passing.
from test_wau_login_only_exclusion import (
    compute_quality_studio_wau,
    QUALITY_STUDIO_MEANINGFUL_ACTIONS,
    _make_qs_event,
    _iso_to_week_key,
)

# ── Precondition: hyperledger-fabric-api is importable ───────────────────────
import sys
import os

_FABRIC_API_SRC = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..", "services", "hyperledger-fabric-api", "src",
)
if _FABRIC_API_SRC not in sys.path:
    sys.path.insert(0, _FABRIC_API_SRC)

from app.main import create_app  # type: ignore[import]
from app.fabric_client import FabricCommitResult  # type: ignore[import]

# ── Time fixtures (reuse same Gate-1 window as the existing WAU tests) ────────

_GATE1_START = datetime(2025, 11, 1, 0, 0, 0, tzinfo=timezone.utc)
_GATE1_END   = datetime(2025, 11, 28, 23, 59, 59, tzinfo=timezone.utc)

def _ts(offset_days: int, hour: int = 9) -> str:
    dt = _GATE1_START + timedelta(days=offset_days, hours=hour)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

_DAY_W45 = _ts(2)   # 2025-11-03 09:00Z — Monday W45
_DAY_W46 = _ts(9)   # 2025-11-10 09:00Z — Monday W46
_WINDOW_START = _GATE1_START.strftime("%Y-%m-%dT%H:%M:%SZ")
_WINDOW_END   = _GATE1_END.strftime("%Y-%m-%dT%H:%M:%SZ")

# ── Fabric API test plumbing (mirrors test_certificate_router.py conventions) ──

_VIN         = "WBA3A5C55FK123456"
_CERT_ID     = str(uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"))
_VALIDATOR   = str(uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002"))
_CERT_HASH   = "a" * 64
_ISSUED_AT   = "2099-01-01T06:00:00+00:00"
_FAR_FUTURE  = datetime(2099, 1, 1, 6, 0, 0, tzinfo=timezone.utc)
_FAKE_COMMIT = FabricCommitResult(transaction_id="deadbeef" * 8, block_number=42)

_BASE_PAYLOAD: dict[str, Any] = {
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
    "validatorId": _VALIDATOR,
    "issuedAt": _ISSUED_AT,
    "certificateHash": _CERT_HASH,
    "sourceSystem": "quality-studio",
}

_JWT_HEADERS = {"Authorization": "Bearer stub"}


class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class _FakeConn:
    def __init__(self) -> None:
        self.inserted_certs: list[dict] = []
        self.inserted_attempts: list[dict] = []
        self.updated_attempts: list[dict] = []

    async def fetchrow(self, query: str, *args: Any) -> None:
        return None  # no existing cert → new issuance path

    async def fetch(self, query: str, *args: Any) -> list:
        return []

    async def execute(self, query: str, *args: Any) -> None:
        q = query.lower()
        if "insert into fabric_certificate" in q:
            self.inserted_certs.append({"args": list(args)})
        elif "insert into fabric_anchor_attempt" in q:
            self.inserted_attempts.append({"args": list(args)})
        elif "update fabric_anchor_attempt" in q:
            self.updated_attempts.append({"args": list(args)})


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> "_FakeAcquire":
        return _FakeAcquire(self._conn)


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


def _make_client() -> tuple[TestClient, _FakeConn, list[dict]]:
    conn = _FakeConn()
    pool = _FakePool(conn)
    captured: list[dict] = []

    async def _fake_publish(topic: str, payload: dict) -> None:
        captured.append({"topic": topic, "payload": payload})

    app = create_app()
    from app.db import get_pool  # type: ignore[import]
    from app.auth import require_bearer, require_certificate_issuer, require_certificate_reader  # type: ignore[import]

    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[require_bearer] = lambda: {"sub": "qv-001", "roles": ["CERTIFICATE_ISSUER"]}
    app.dependency_overrides[require_certificate_issuer] = lambda: {"sub": "qv-001", "roles": ["CERTIFICATE_ISSUER"]}
    app.dependency_overrides[require_certificate_reader] = lambda: {"sub": "qv-001", "roles": ["CERTIFICATE_READER"]}

    return TestClient(app, raise_server_exceptions=False), conn, captured, _fake_publish  # type: ignore[return-value]


# =============================================================================
# AC1 — every meaningful action type is counted by the WAU calculator
# =============================================================================

class TestAC1MeaningfulActionsCountedByWAU:
    """AC1: each meaningful action type produces WAU=1 when present in isolation."""

    @pytest.mark.parametrize("action", sorted(QUALITY_STUDIO_MEANINGFUL_ACTIONS))
    def test_each_meaningful_action_produces_wau_1_for_paint_engineer(self, action: str) -> None:
        """AC1 · {action} emitted by a PAINT_ENGINEER is counted as active for that week."""
        events = [_make_qs_event("pe-001", action, _DAY_W45, role="PAINT_ENGINEER")]
        wau = compute_quality_studio_wau(events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END)
        week_key = _iso_to_week_key(_DAY_W45)
        assert wau.get(week_key, 0) == 1, (
            f"Expected WAU=1 for PAINT_ENGINEER + action={action!r}; got {wau}"
        )

    @pytest.mark.parametrize("action", sorted(QUALITY_STUDIO_MEANINGFUL_ACTIONS))
    def test_each_meaningful_action_produces_wau_1_for_quality_validator(self, action: str) -> None:
        """AC1 · {action} emitted by a QUALITY_GATE_VALIDATOR is counted as active."""
        events = [_make_qs_event("qv-001", action, _DAY_W45, role="QUALITY_GATE_VALIDATOR")]
        wau = compute_quality_studio_wau(events, "QUALITY_GATE_VALIDATOR", _WINDOW_START, _WINDOW_END)
        week_key = _iso_to_week_key(_DAY_W45)
        assert wau.get(week_key, 0) == 1, (
            f"Expected WAU=1 for QUALITY_GATE_VALIDATOR + action={action!r}; got {wau}"
        )

    def test_meaningful_action_set_contains_all_required_actions(self) -> None:
        """Precondition: the WAU calculator knows about every required action type."""
        required = {
            "PROCESS_REPLAY_LAUNCH",
            "DEFECT_GENOME_QUERY",
            "ROOT_CAUSE_ASSIGNED",
            "CERTIFY",
            "CERTIFICATE_STUDIO_OPEN",
        }
        missing = required - QUALITY_STUDIO_MEANINGFUL_ACTIONS
        assert not missing, (
            f"QUALITY_STUDIO_MEANINGFUL_ACTIONS is missing: {missing!r}"
        )


# =============================================================================
# AC2 — login-only session excluded from WAU
# =============================================================================

class TestAC2LoginOnlySessionExcluded:
    """AC2: a session where the only event is LOGIN contributes 0 to WAU."""

    def test_paint_engineer_login_only_session_wau_is_zero(self) -> None:
        """AC2 · PAINT_ENGINEER login-only session → WAU=0."""
        events = [_make_qs_event("pe-001", "LOGIN", _DAY_W45, role="PAINT_ENGINEER")]
        wau = compute_quality_studio_wau(events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END)
        assert sum(wau.values()) == 0, (
            f"Expected WAU=0 for login-only PAINT_ENGINEER session; got {wau}"
        )

    def test_quality_validator_login_only_session_wau_is_zero(self) -> None:
        """AC2 · QUALITY_GATE_VALIDATOR login-only session → WAU=0."""
        events = [_make_qs_event("qv-001", "LOGIN", _DAY_W45, role="QUALITY_GATE_VALIDATOR")]
        wau = compute_quality_studio_wau(events, "QUALITY_GATE_VALIDATOR", _WINDOW_START, _WINDOW_END)
        assert sum(wau.values()) == 0, (
            f"Expected WAU=0 for login-only QUALITY_GATE_VALIDATOR session; got {wau}"
        )

    def test_login_followed_by_meaningful_action_counts_as_active(self) -> None:
        """AC2 · LOGIN then a meaningful action in the same day → WAU=1 (not 0)."""
        events = [
            _make_qs_event("pe-001", "LOGIN", _DAY_W45, role="PAINT_ENGINEER"),
            _make_qs_event("pe-001", "CERTIFY", _DAY_W45, role="PAINT_ENGINEER"),
        ]
        wau = compute_quality_studio_wau(events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END)
        week_key = _iso_to_week_key(_DAY_W45)
        assert wau.get(week_key, 0) == 1, (
            f"Expected WAU=1 for LOGIN+CERTIFY session; got {wau}"
        )

    def test_login_in_week_45_action_in_week_46_only_week_46_counted(self) -> None:
        """AC2 · Login on W45, first meaningful action on W46 → W45=0, W46=1."""
        events = [
            _make_qs_event("pe-001", "LOGIN", _DAY_W45, role="PAINT_ENGINEER"),
            _make_qs_event("pe-001", "PROCESS_REPLAY_LAUNCH", _DAY_W46, role="PAINT_ENGINEER"),
        ]
        wau = compute_quality_studio_wau(events, "PAINT_ENGINEER", _WINDOW_START, _WINDOW_END)
        week_45 = _iso_to_week_key(_DAY_W45)
        week_46 = _iso_to_week_key(_DAY_W46)
        assert wau.get(week_45, 0) == 0, (
            f"W45 should be 0 (login-only); got {wau.get(week_45)}"
        )
        assert wau.get(week_46, 0) == 1, (
            f"W46 should be 1; got {wau.get(week_46)}"
        )

    def test_login_action_not_in_meaningful_action_set(self) -> None:
        """AC2 precondition: 'LOGIN' must be absent from QUALITY_STUDIO_MEANINGFUL_ACTIONS."""
        assert "LOGIN" not in QUALITY_STUDIO_MEANINGFUL_ACTIONS, (
            "LOGIN must NOT be a meaningful action — "
            "it should not contribute to WAU on its own."
        )


# =============================================================================
# AC3 — CertificateIssuedEvent contains fabricTransactionId
# =============================================================================

class TestAC3CertificateIssuedEventContainsFabricTransactionId:
    """AC3: POST /certificate/issue publishes a CertificateIssuedEvent that includes
    fabricTransactionId so the A7 counter can cross-reference with the Fabric anchor.
    """

    def test_issue_certificate_publishes_event_with_fabric_transaction_id(self) -> None:
        """AC3 · 201 response and published event both carry fabricTransactionId."""
        conn = _FakeConn()
        pool = _FakePool(conn)
        captured: list[dict] = []

        async def _fake_publish(topic: str, payload: dict) -> None:
            captured.append({"topic": topic, "payload": payload})

        app = create_app()
        from app.db import get_pool  # type: ignore[import]
        from app.auth import require_certificate_issuer  # type: ignore[import]

        app.dependency_overrides[get_pool] = lambda: pool
        app.dependency_overrides[require_certificate_issuer] = (
            lambda: {"sub": "qv-001", "roles": ["CERTIFICATE_ISSUER"]}
        )

        client = TestClient(app, raise_server_exceptions=False)

        with patch("app.routers.certificates.submit_transaction", new=AsyncMock(return_value=_FAKE_COMMIT)), \
             patch("app.routers.certificates.publish", new=_fake_publish):
            resp = client.post("/certificate/issue", json=_BASE_PAYLOAD, headers=_JWT_HEADERS)

        assert resp.status_code == 201, f"Expected 201; got {resp.status_code}: {resp.text}"

        # Response body carries fabricTransactionId
        body = resp.json()
        assert body["fabricTransactionId"] == _FAKE_COMMIT.transaction_id, (
            "Response body must include fabricTransactionId for A7 measurement"
        )

        # The published Kafka event must also include fabricTransactionId
        issued_events = [e for e in captured if "certificate.issued" in e["topic"]]
        assert len(issued_events) == 1, (
            f"Expected 1 CertificateIssuedEvent on the certificate.issued topic; "
            f"found {len(issued_events)}.  All captured topics: {[e['topic'] for e in captured]}"
        )
        event_payload = issued_events[0]["payload"]
        assert "fabricTransactionId" in event_payload, (
            "CertificateIssuedEvent payload must contain fabricTransactionId "
            "so the A7 counter can cross-reference with the Fabric anchor count"
        )
        assert event_payload["fabricTransactionId"] == _FAKE_COMMIT.transaction_id, (
            f"Expected fabricTransactionId={_FAKE_COMMIT.transaction_id!r}; "
            f"got {event_payload.get('fabricTransactionId')!r}"
        )

    def test_issue_certificate_event_also_carries_source_system_quality_studio(self) -> None:
        """AC3 · CertificateIssuedEvent must carry sourceSystem='quality-studio' for A7 filter."""
        conn = _FakeConn()
        pool = _FakePool(conn)
        captured: list[dict] = []

        async def _fake_publish(topic: str, payload: dict) -> None:
            captured.append({"topic": topic, "payload": payload})

        app = create_app()
        from app.db import get_pool  # type: ignore[import]
        from app.auth import require_certificate_issuer  # type: ignore[import]

        app.dependency_overrides[get_pool] = lambda: pool
        app.dependency_overrides[require_certificate_issuer] = (
            lambda: {"sub": "qv-001", "roles": ["CERTIFICATE_ISSUER"]}
        )

        client = TestClient(app, raise_server_exceptions=False)

        with patch("app.routers.certificates.submit_transaction", new=AsyncMock(return_value=_FAKE_COMMIT)), \
             patch("app.routers.certificates.publish", new=_fake_publish):
            resp = client.post("/certificate/issue", json=_BASE_PAYLOAD, headers=_JWT_HEADERS)

        assert resp.status_code == 201
        issued_events = [e for e in captured if "certificate.issued" in e["topic"]]
        assert len(issued_events) == 1
        event_payload = issued_events[0]["payload"]
        assert event_payload.get("sourceSystem") == "quality-studio", (
            "CertificateIssuedEvent must carry sourceSystem='quality-studio' "
            "so the A7 KQL query can filter certs issued via Quality Studio"
        )

    def test_issue_certificate_event_carries_vin_and_certificate_id(self) -> None:
        """AC3 · CertificateIssuedEvent must carry vin and certificateId for full traceability."""
        conn = _FakeConn()
        pool = _FakePool(conn)
        captured: list[dict] = []

        async def _fake_publish(topic: str, payload: dict) -> None:
            captured.append({"topic": topic, "payload": payload})

        app = create_app()
        from app.db import get_pool  # type: ignore[import]
        from app.auth import require_certificate_issuer  # type: ignore[import]

        app.dependency_overrides[get_pool] = lambda: pool
        app.dependency_overrides[require_certificate_issuer] = (
            lambda: {"sub": "qv-001", "roles": ["CERTIFICATE_ISSUER"]}
        )

        client = TestClient(app, raise_server_exceptions=False)

        with patch("app.routers.certificates.submit_transaction", new=AsyncMock(return_value=_FAKE_COMMIT)), \
             patch("app.routers.certificates.publish", new=_fake_publish):
            resp = client.post("/certificate/issue", json=_BASE_PAYLOAD, headers=_JWT_HEADERS)

        assert resp.status_code == 201
        issued_events = [e for e in captured if "certificate.issued" in e["topic"]]
        assert len(issued_events) == 1
        event_payload = issued_events[0]["payload"]
        assert event_payload.get("vin") == _VIN
        assert event_payload.get("certificateId") == _CERT_ID
