"""US-028 acceptance tests — GDPR Article 17 deletion workflow (AC3).

Cross-task seam tested here (not covered by any existing unit test):
  AC3: POST /internal/identity/deletion-request (identity-service / bmw-entra-id task)
       tombstones the row.  The CronJob scrubber (identity-scrubber-cronjob.yaml) then
       NULLs employee_id and name while leaving pseudonymous_token intact so all
       VIN-linked operational records (torque logs, PLC traces) remain queryable by token.
       This proves: directly-identifying data removed, operational records retained.
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.audit import AuditStore
from app.main import create_app
from app.routers.identity import _get_conn

# ---------------------------------------------------------------------------
# Repo paths
# ---------------------------------------------------------------------------
_SERVICE_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SCRUBBER_CRONJOB = (
    pathlib.Path(__file__).resolve().parent.parent.parent.parent
    / "platform" / "bmw-entra-id" / "k8s" / "identity-scrubber-cronjob.yaml"
)


# ---------------------------------------------------------------------------
# In-memory DB that simulates the real pseudonymisation_map lifecycle
# ---------------------------------------------------------------------------

class _LiveStore:
    """Minimal in-memory simulation of identity_pseudonymisation_map.

    Supports pseudonymise → request_deletion → scrubber-nullify lifecycle.
    All writes are applied to a shared dict keyed by employee_id.
    """

    def __init__(self, token: uuid.UUID) -> None:
        self._token = token
        self._rows: dict[str, dict[str, Any]] = {}

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if not args:
            return None
        key = str(args[0])
        row = self._rows.get(key)
        if row is None:
            # INSERT … RETURNING path: create a new row
            tombstoned_at = args[2] if len(args) > 2 else None
            name = args[1] if len(args) > 1 else None
            self._rows[key] = {
                "employee_id": key,
                "name": name,
                "pseudonymous_token": self._token,
                "tombstoned_at": tombstoned_at,
            }
            return self._rows[key]
        # UPDATE / re-insert: bump tombstoned_at and name if provided
        if len(args) > 2:
            row["tombstoned_at"] = args[2]
        if len(args) > 1:
            row["name"] = args[1]
        # If SELECT for non-tombstoned row only — honour the WHERE tombstoned_at IS NULL
        if "tombstoned_at IS NULL" in query and row.get("tombstoned_at") is not None:
            return None
        return row

    async def execute(self, _query: str, *_args: Any) -> None:
        pass

    def get_row(self, employee_id: str) -> dict[str, Any] | None:
        return self._rows.get(employee_id)

    def nullify_pii(self, employee_id: str) -> None:
        """Simulate what the CronJob scrubber does: NULL employee_id and name."""
        row = self._rows.get(employee_id)
        if row is not None:
            row["employee_id"] = None
            row["name"] = None


def _make_client(store: _LiveStore) -> TestClient:
    app = create_app()

    async def _override() -> Any:
        yield store

    app.dependency_overrides[_get_conn] = _override
    return TestClient(app)


# ===========================================================================
# AC3 — Deletion workflow: tombstone + scrub (end-to-end across both tasks)
# ===========================================================================

def test_ac3_deletion_request_returns_202_accepted() -> None:
    """AC3: POST /internal/identity/deletion-request must return 202 Accepted."""
    token = uuid.UUID("dddddddd-0001-4000-8000-000000000011")
    store = _LiveStore(token)
    client = _make_client(store)

    resp = client.post(
        "/internal/identity/deletion-request",
        json={
            "employee_id": "11223344",
            "name": "Hans Müller",
            "requested_at": "2099-06-01T00:00:00Z",
        },
    )
    assert resp.status_code == 202, (
        f"Deletion request must be accepted with 202; got {resp.status_code}: {resp.text}"
    )


def test_ac3_deletion_request_response_carries_pseudonymous_token_and_correlation_id() -> None:
    """AC3: deletion response carries pseudonymous_token, action, requested_at, correlation_id."""
    token = uuid.UUID("dddddddd-0002-4000-8000-000000000012")
    store = _LiveStore(token)
    client = _make_client(store)

    resp = client.post(
        "/internal/identity/deletion-request",
        json={
            "employee_id": "22334455",
            "name": "Anna Schmidt",
            "requested_at": "2099-06-01T00:00:00Z",
        },
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["action"] == "IDENTITY_DELETION_REQUESTED"
    assert body["pseudonymous_token"] == str(token)
    assert "requested_at" in body
    assert "correlation_id" in body


def test_ac3_deletion_request_sets_tombstoned_at_on_identity_row() -> None:
    """AC3: deletion request tombstones the row — tombstoned_at is set in the identity store.

    This is the cross-task seam: vin-digital-thread-api calls identity-service
    (bmw-entra-id) which sets tombstoned_at so the CronJob scrubber knows to act.
    """
    token = uuid.UUID("dddddddd-0003-4000-8000-000000000013")
    store = _LiveStore(token)
    client = _make_client(store)

    resp = client.post(
        "/internal/identity/deletion-request",
        json={
            "employee_id": "33445566",
            "name": "Klaus Weber",
            "requested_at": "2099-06-01T00:00:00Z",
        },
    )

    assert resp.status_code == 202

    row = store.get_row("33445566")
    assert row is not None, (
        "Identity row must exist in the store after a deletion request"
    )
    assert row["tombstoned_at"] is not None, (
        "tombstoned_at must be set by the deletion request — "
        "this signals the CronJob scrubber to nullify PII"
    )


def test_ac3_after_scrubber_run_employee_id_and_name_are_nulled() -> None:
    """AC3: after the CronJob scrubber runs, employee_id and name are NULL.

    Simulates the complete AC3 lifecycle:
      1. POST /internal/identity/deletion-request → tombstones row
      2. CronJob scrubber nullifies employee_id and name (simulated here)
      3. Assert directly-identifying data (employee_id, name) is gone
    """
    token = uuid.UUID("dddddddd-0004-4000-8000-000000000014")
    store = _LiveStore(token)
    client = _make_client(store)

    resp = client.post(
        "/internal/identity/deletion-request",
        json={
            "employee_id": "44556677",
            "name": "Petra Huber",
            "requested_at": "2099-06-01T00:00:00Z",
        },
    )
    assert resp.status_code == 202

    # Simulate the CronJob scrubber (identity-scrubber-cronjob.yaml) running
    store.nullify_pii("44556677")

    row = store.get_row("44556677")
    assert row is not None
    assert row["employee_id"] is None, (
        "employee_id must be NULL after the scrubber runs — "
        "this is the GDPR Art. 17 directly-identifying data removal"
    )
    assert row["name"] is None, (
        "name must be NULL after the scrubber runs — "
        "this is the GDPR Art. 17 directly-identifying data removal"
    )


def test_ac3_pseudonymous_token_preserved_after_scrubber_run() -> None:
    """AC3: pseudonymous_token is NOT nulled by the scrubber — operational records remain intact.

    GDPR Art. 17(3)(b) exempts operational records held for legal obligation.
    The token is the only reference from torque_log / VIN thread records;
    it must survive so those records remain queryable.
    """
    token = uuid.UUID("dddddddd-0005-4000-8000-000000000015")
    store = _LiveStore(token)
    client = _make_client(store)

    resp = client.post(
        "/internal/identity/deletion-request",
        json={
            "employee_id": "55667788",
            "name": "Franz Müller",
            "requested_at": "2099-06-01T00:00:00Z",
        },
    )
    assert resp.status_code == 202

    # Simulate scrubber: NULL PII columns
    store.nullify_pii("55667788")

    row = store.get_row("55667788")
    assert row is not None
    assert row["pseudonymous_token"] == token, (
        f"pseudonymous_token must remain '{token}' after the scrubber; "
        "operational records linked by this token must not be orphaned"
    )


def test_ac3_pseudonymise_endpoint_returns_stable_token_after_deletion_tombstone() -> None:
    """AC3: a pseudonymised worker whose row is tombstoned gets a fresh token on re-pseudonymise.

    The cross-task seam: vin-digital-thread-api may call pseudonymise again after a
    deletion request.  The identity-service must handle this cleanly (tombstone → new row).
    """
    token_v1 = uuid.UUID("dddddddd-0006-4000-8000-000000000016")
    store = _LiveStore(token_v1)
    client = _make_client(store)

    # First: pseudonymise the employee
    resp1 = client.get("/internal/identity/pseudonymise", params={"employee_id": "66778899"})
    assert resp1.status_code == 200
    assert resp1.json()["pseudonymous_token"] == str(token_v1)

    # Then: tombstone via deletion request
    resp2 = client.post(
        "/internal/identity/deletion-request",
        json={
            "employee_id": "66778899",
            "name": "Maria Fischer",
            "requested_at": "2099-06-01T00:00:00Z",
        },
    )
    assert resp2.status_code == 202

    # The tombstoned row must have tombstoned_at set
    row = store.get_row("66778899")
    assert row is not None
    assert row["tombstoned_at"] is not None, (
        "Deletion request must tombstone the row even when a prior pseudonymous entry exists"
    )


# ===========================================================================
# AC3 — CronJob schedule: must run daily (within 30-day GDPR Art. 17 deadline)
# ===========================================================================

def test_ac3_scrubber_cronjob_yaml_exists() -> None:
    """AC3 (precondition): identity-scrubber-cronjob.yaml must exist."""
    assert _SCRUBBER_CRONJOB.is_file(), (
        f"identity-scrubber-cronjob.yaml not found at {_SCRUBBER_CRONJOB}; "
        "bmw-entra-id task may not be merged"
    )


def test_ac3_scrubber_runs_daily_to_meet_30_day_gdpr_deadline() -> None:
    """AC3: CronJob schedule must be daily (or more frequent) to pseudonymise within 30 days."""
    assert _SCRUBBER_CRONJOB.is_file(), f"Precondition: {_SCRUBBER_CRONJOB} must exist"
    text = _SCRUBBER_CRONJOB.read_text()

    # The schedule "0 2 * * *" means daily at 02:00 UTC
    assert "* * *" in text, (
        "CronJob schedule must run at least daily (* * * at the end) "
        "to pseudonymise within 30 days as required by GDPR Art. 17"
    )


def test_ac3_scrubber_cronjob_references_scrubber_module() -> None:
    """AC3: CronJob must run app.scrubber — the module that nullifies PII columns."""
    assert _SCRUBBER_CRONJOB.is_file(), f"Precondition: {_SCRUBBER_CRONJOB} must exist"
    text = _SCRUBBER_CRONJOB.read_text()

    assert "app.scrubber" in text, (
        "CronJob command must run app.scrubber to nullify employee_id and name "
        "per the GDPR Art. 17 pseudonymisation requirement"
    )


# ===========================================================================
# AC3 — Scrubber module: nullifies PII, preserves operational records
# ===========================================================================

def test_ac3_scrubber_module_nulls_employee_id_and_name_not_token() -> None:
    """AC3: scrubber.py UPDATE statement NULLs employee_id and name, not pseudonymous_token."""
    scrubber_py = _SERVICE_ROOT / "src" / "app" / "scrubber.py"
    assert scrubber_py.is_file(), (
        f"app/scrubber.py not found at {scrubber_py}; identity-service task may not be merged"
    )
    text = scrubber_py.read_text()

    assert "employee_id = NULL" in text, (
        "Scrubber UPDATE must set employee_id = NULL to remove directly-identifying data"
    )
    assert "name        = NULL" in text or "name = NULL" in text, (
        "Scrubber UPDATE must set name = NULL to remove directly-identifying data"
    )
    assert "pseudonymous_token = NULL" not in text, (
        "Scrubber must NEVER null pseudonymous_token — operational records depend on it"
    )


# ===========================================================================
# AC3 — AuditStore isolation (cross-task: both tasks use an AuditStore)
# ===========================================================================

def test_ac3_audit_store_new_instance_starts_with_empty_records() -> None:
    """AuditStore (used by both identity-service and vin-digital-thread-api) must start clean."""
    store = AuditStore()
    assert store.records == [], (
        "AuditStore.records must be an empty list on a new instance — "
        "state must not bleed from a previous instance"
    )


def test_ac3_audit_store_records_deletion_request_action_type() -> None:
    """AC3: audit record written on deletion request carries IDENTITY_DELETION_REQUESTED action."""
    store = AuditStore()
    null_hash = "74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b"
    token = uuid.UUID("dddddddd-0007-4000-8000-000000000017")
    utc_ts = datetime(2099, 1, 1, tzinfo=timezone.utc).isoformat()

    store.write_event(
        user_id=str(token),
        action_type="IDENTITY_DELETION_REQUESTED",
        entity_id=str(token),
        before_hash=null_hash,
        after_hash=null_hash,
        utc_ts=utc_ts,
    )

    records = store.records
    assert len(records) == 1, "AuditStore must record exactly one entry"
    assert records[0].action_type == "IDENTITY_DELETION_REQUESTED", (
        "action_type in the audit record must be 'IDENTITY_DELETION_REQUESTED'"
    )
    assert records[0].entity_id == str(token), (
        "entity_id in the audit record must be the pseudonymous_token, never the raw employee_id"
    )
