"""Integration tests: POST /vin/{vin}/thread/events audit-write behaviour.

Verifies AC-04d1d046 (US-023):
  - An audit log entry is written within 1 second of a 2xx response.
  - The entry contains all six required fields (user_id, action_type, entity_id,
    before_hash, after_hash, utc_ts).
  - No audit entry is written when the request returns a non-2xx status.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.routers.events import _IdempotencyStore, get_idempotency_store


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _CaptureStore:
    """Captures every write_event call made by fire_audit_event."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []

    def record(
        self,
        user_id: str,
        action_type: str,
        entity_id: str,
        before_hash: str,
        after_hash: str,
        utc_ts: str,
    ) -> str:
        with self._lock:
            self._events.append(
                {
                    "user_id": user_id,
                    "action_type": action_type,
                    "entity_id": entity_id,
                    "before_hash": before_hash,
                    "after_hash": after_hash,
                    "utc_ts": utc_ts,
                }
            )
        return f"fake/{action_type}/{uuid.uuid4()}.json"

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def wait_for_event(self, *, timeout: float = 1.0) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            evts = self.events()
            if evts:
                return evts[-1]
            time.sleep(0.01)
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_VIN = "WBA1A9C50DVX12345"
_BEARER_WITH_ROLE = (
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJzdmMtdW5zLWNvbnN1bWVyIiwicm9sZXMiOlsidGhyZWFkLWluZ2VzdG9yIl0sImV4cCI6OTk5OTk5OTk5OX0"
    "."
)
_BEARER_NO_ROLE = (
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJ1c2VyLW5vcm9sZSIsInJvbGVzIjpbXSwiZXhwIjo5OTk5OTk5OTk5fQ"
    "."
)


def _make_body(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "idempotencyKey": str(uuid.uuid4()),
        "eventType": "GATE_RESULT",
        "shop": "PAINT",
        "station": "PAINT_PREP_GATE_1",
        "occurredAt": "2026-10-12T06:00:00Z",
        "dataSource": "plc-siemens-s7",
        "parameters": {"result": "PASS", "score": 98},
    }
    base.update(overrides)
    return base


@pytest.fixture()
def capture() -> _CaptureStore:
    return _CaptureStore()


@pytest.fixture()
def client(capture: _CaptureStore):
    app = create_app()

    fresh_store = _IdempotencyStore()
    app.dependency_overrides[get_idempotency_store] = lambda: fresh_store

    with patch("app.audit._write_sync", side_effect=capture.record):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


# ---------------------------------------------------------------------------
# Happy-path audit tests
# ---------------------------------------------------------------------------

def test_audit_blob_written_within_1s_on_201(client: TestClient, capture: _CaptureStore) -> None:
    """Audit event must appear within 1 second of a 201 response."""
    t0 = time.monotonic()
    resp = client.post(
        f"/vin/{_VALID_VIN}/thread/events",
        json=_make_body(),
        headers={"Authorization": f"Bearer {_BEARER_WITH_ROLE}"},
    )
    assert resp.status_code == 201, resp.text

    evt = capture.wait_for_event(timeout=1.0)
    elapsed = time.monotonic() - t0

    assert evt is not None, "Audit event was not written within 1 second"
    assert elapsed <= 1.0, f"Audit write took {elapsed:.3f}s, exceeds 1 s SLA"


def test_audit_blob_contains_all_six_required_fields(client: TestClient, capture: _CaptureStore) -> None:
    """Blob payload must contain user_id, action_type, entity_id, before_hash, after_hash, utc_ts."""
    resp = client.post(
        f"/vin/{_VALID_VIN}/thread/events",
        json=_make_body(),
        headers={"Authorization": f"Bearer {_BEARER_WITH_ROLE}"},
    )
    assert resp.status_code == 201, resp.text

    evt = capture.wait_for_event(timeout=1.0)
    assert evt is not None

    assert evt["user_id"] == "svc-uns-consumer"
    assert evt["action_type"] == "VIN_THREAD_EVENT_LINKED"
    assert evt["entity_id"] == _VALID_VIN
    assert len(evt["before_hash"]) == 64, "before_hash must be a SHA-256 hex digest"
    assert len(evt["after_hash"]) == 64, "after_hash must be a SHA-256 hex digest"
    assert evt["utc_ts"], "utc_ts must be present"


def test_user_id_sourced_from_jwt_sub(client: TestClient, capture: _CaptureStore) -> None:
    """user_id in the audit entry must equal the JWT sub claim."""
    resp = client.post(
        f"/vin/{_VALID_VIN}/thread/events",
        json=_make_body(),
        headers={"Authorization": f"Bearer {_BEARER_WITH_ROLE}"},
    )
    assert resp.status_code == 201, resp.text

    evt = capture.wait_for_event(timeout=1.0)
    assert evt is not None
    assert evt["user_id"] == "svc-uns-consumer"


# ---------------------------------------------------------------------------
# Non-2xx — no audit write
# ---------------------------------------------------------------------------

def test_no_audit_blob_on_invalid_vin(client: TestClient, capture: _CaptureStore) -> None:
    """400 from malformed VIN must not trigger an audit write."""
    resp = client.post(
        "/vin/INVALID_VIN_12345/thread/events",
        json=_make_body(),
        headers={"Authorization": f"Bearer {_BEARER_WITH_ROLE}"},
    )
    assert resp.status_code == 400, resp.text

    time.sleep(0.1)
    assert capture.events() == [], "No audit event should be written on 400"


def test_no_audit_blob_on_missing_token(client: TestClient, capture: _CaptureStore) -> None:
    """401 from missing Bearer token must not trigger an audit write."""
    resp = client.post(
        f"/vin/{_VALID_VIN}/thread/events",
        json=_make_body(),
    )
    assert resp.status_code == 401, resp.text

    time.sleep(0.1)
    assert capture.events() == [], "No audit event should be written on 401"


def test_no_audit_blob_on_missing_role(client: TestClient, capture: _CaptureStore) -> None:
    """403 from lacking thread-ingestor role must not trigger an audit write."""
    resp = client.post(
        f"/vin/{_VALID_VIN}/thread/events",
        json=_make_body(),
        headers={"Authorization": f"Bearer {_BEARER_NO_ROLE}"},
    )
    assert resp.status_code == 403, resp.text

    time.sleep(0.1)
    assert capture.events() == [], "No audit event should be written on 403"


def test_no_audit_blob_on_duplicate_idempotency_key(client: TestClient, capture: _CaptureStore) -> None:
    """409 from duplicate idempotency key must not trigger a second audit write."""
    body = _make_body()
    headers = {"Authorization": f"Bearer {_BEARER_WITH_ROLE}"}

    resp1 = client.post(f"/vin/{_VALID_VIN}/thread/events", json=body, headers=headers)
    assert resp1.status_code == 201, resp1.text
    _ = capture.wait_for_event(timeout=1.0)

    # Second request with the same idempotencyKey
    count_before = len(capture.events())
    resp2 = client.post(f"/vin/{_VALID_VIN}/thread/events", json=body, headers=headers)
    assert resp2.status_code == 409, resp2.text

    time.sleep(0.1)
    assert len(capture.events()) == count_before, "Duplicate 409 must not write a second audit entry"


def test_no_audit_blob_on_validation_error(client: TestClient, capture: _CaptureStore) -> None:
    """422 from schema validation failure must not trigger an audit write."""
    bad_body = _make_body(eventType="UNKNOWN_EVENT_TYPE")
    resp = client.post(
        f"/vin/{_VALID_VIN}/thread/events",
        json=bad_body,
        headers={"Authorization": f"Bearer {_BEARER_WITH_ROLE}"},
    )
    assert resp.status_code == 422, resp.text

    time.sleep(0.1)
    assert capture.events() == [], "No audit event should be written on 422"


# ---------------------------------------------------------------------------
# Error response shape
# ---------------------------------------------------------------------------

def test_error_response_has_required_fields_on_400(client: TestClient, capture: _CaptureStore) -> None:
    resp = client.post(
        "/vin/BADINPUT0000000xx/thread/events",
        json=_make_body(),
        headers={"Authorization": f"Bearer {_BEARER_WITH_ROLE}"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "code" in body["detail"]
    assert "message" in body["detail"]
    assert "correlation_id" in body["detail"]


def test_201_response_shape(client: TestClient, capture: _CaptureStore) -> None:
    resp = client.post(
        f"/vin/{_VALID_VIN}/thread/events",
        json=_make_body(),
        headers={"Authorization": f"Bearer {_BEARER_WITH_ROLE}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["vin"] == _VALID_VIN
    assert "eventId" in body
    assert "linkedAt" in body
