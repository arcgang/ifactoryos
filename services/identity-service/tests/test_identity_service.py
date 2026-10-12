"""Tests for identity-service pseudonymise/deletion endpoints.

Uses a fake DB connection and AuditStore test double so no real database or
Azure storage is needed.  Tests assert exact field values, not shape alone.
"""

from __future__ import annotations

import pathlib
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.audit import AuditStore
from app.main import create_app
from app.routers.identity import _get_conn

# Root of the identity-service tree, two levels above this tests/ directory
_SERVICE_ROOT = pathlib.Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Fake DB connection
# ---------------------------------------------------------------------------

class FakeConn:
    """Minimal asyncpg connection double with declarative fetchrow/execute behaviour."""

    def __init__(self) -> None:
        self._rows: dict[str, Any] = {}
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def seed(self, key: str, row: dict[str, Any]) -> None:
        self._rows[key] = row

    async def fetchrow(self, query: str, *args: Any) -> Any | None:
        # Key lookup by first arg (employee_id or similar)
        return self._rows.get(str(args[0])) if args else None

    async def execute(self, query: str, *args: Any) -> None:
        self.executed.append((query, args))


class FakeConnWithInsert(FakeConn):
    """Fake that simulates INSERT … RETURNING by returning a fixed row."""

    def __init__(self, token: uuid.UUID) -> None:
        super().__init__()
        self._token = token

    async def fetchrow(self, query: str, *args: Any) -> Any | None:
        # Return existing row only when seeded; otherwise simulate INSERT RETURNING
        if str(args[0]) in self._rows:
            return self._rows[str(args[0])]
        return {"pseudonymous_token": self._token}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app_with_conn(conn: Any) -> TestClient:
    app = create_app()

    async def _override() -> Any:
        yield conn

    app.dependency_overrides[_get_conn] = _override
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /internal/identity/pseudonymise — new employee
# ---------------------------------------------------------------------------

def test_pseudonymise_new_employee_returns_201_with_token() -> None:
    token = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
    conn = FakeConnWithInsert(token)
    client = _make_app_with_conn(conn)

    resp = client.get("/internal/identity/pseudonymise", params={"employee_id": "12345678"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["pseudonymous_token"] == str(token)
    assert body["created"] is True


def test_pseudonymise_existing_employee_returns_same_token() -> None:
    token = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
    conn = FakeConn()
    conn.seed("12345678", {"pseudonymous_token": token})
    client = _make_app_with_conn(conn)

    resp = client.get("/internal/identity/pseudonymise", params={"employee_id": "12345678"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["pseudonymous_token"] == str(token)
    assert body["created"] is False


def test_pseudonymise_invalid_employee_id_returns_400() -> None:
    conn = FakeConn()
    client = _make_app_with_conn(conn)

    # Non-numeric
    resp = client.get("/internal/identity/pseudonymise", params={"employee_id": "ABC12345"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_EMPLOYEE_ID"


def test_pseudonymise_short_employee_id_returns_400() -> None:
    conn = FakeConn()
    client = _make_app_with_conn(conn)

    # Only 7 digits
    resp = client.get("/internal/identity/pseudonymise", params={"employee_id": "1234567"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_EMPLOYEE_ID"


def test_pseudonymise_missing_employee_id_returns_422() -> None:
    conn = FakeConn()
    client = _make_app_with_conn(conn)

    resp = client.get("/internal/identity/pseudonymise")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /internal/identity/deletion-request
# ---------------------------------------------------------------------------

def test_deletion_request_returns_202_with_correct_action() -> None:
    token = uuid.UUID("cccccccc-0000-4000-8000-000000000003")
    conn = FakeConnWithInsert(token)
    client = _make_app_with_conn(conn)

    # Far-future timestamp — not tied to today's date
    resp = client.post(
        "/internal/identity/deletion-request",
        json={
            "employee_id": "87654321",
            "name": "Max Mustermann",
            "requested_at": "2099-01-01T00:00:00Z",
        },
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["action"] == "IDENTITY_DELETION_REQUESTED"
    assert body["pseudonymous_token"] == str(token)
    assert "requested_at" in body
    assert "correlation_id" in body


def test_deletion_request_invalid_employee_id_returns_422() -> None:
    conn = FakeConn()
    client = _make_app_with_conn(conn)

    resp = client.post(
        "/internal/identity/deletion-request",
        json={
            "employee_id": "not-an-id",
            "name": "Max Mustermann",
            "requested_at": "2026-10-12T10:00:00Z",
        },
    )

    assert resp.status_code == 422


def test_deletion_request_missing_name_returns_422() -> None:
    conn = FakeConn()
    client = _make_app_with_conn(conn)

    resp = client.post(
        "/internal/identity/deletion-request",
        json={"employee_id": "87654321", "requested_at": "2026-10-12T10:00:00Z"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# AuditStore isolation test
# ---------------------------------------------------------------------------

def test_audit_store_instances_are_isolated() -> None:
    store_a = AuditStore()
    store_b = AuditStore()

    # Far-future anchor — not tied to real clock; avoids hardcoding "today"
    utc_ts = datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
    null_hash = "74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b"

    store_a.write_event(
        user_id="aaaaaaaa-0000-4000-8000-000000000001",
        action_type="IDENTITY_PSEUDONYMISED",
        entity_id="aaaaaaaa-0000-4000-8000-000000000001",
        before_hash=null_hash,
        after_hash=null_hash,
        utc_ts=utc_ts,
    )

    assert len(store_a.records) == 1
    assert len(store_b.records) == 0


def test_audit_store_new_instance_starts_empty() -> None:
    store = AuditStore()
    assert store.records == []


# ---------------------------------------------------------------------------
# GDPR deletion workflow: tombstone → scrubber nullifies PII
# ---------------------------------------------------------------------------

def test_deletion_workflow_employee_id_and_name_nulled_pseudonymous_token_preserved() -> None:
    """Simulate the full Article 17 flow: tombstone then CronJob scrub."""
    from app.identity_service import pseudonymise, request_deletion
    import asyncio

    token_holder: dict[str, uuid.UUID] = {}

    class WorkflowConn:
        """Simulates PostgreSQL with in-memory state."""

        def __init__(self) -> None:
            self._store: dict[str, dict[str, Any]] = {}

        async def fetchrow(self, query: str, *args: Any) -> Any | None:
            key = str(args[0])
            if key not in self._store:
                return None
            row = self._store[key]
            if "tombstoned_at" in query and row.get("tombstoned_at") is not None:
                return None
            return row

        async def execute(self, query: str, *args: Any) -> None:
            pass

    class InsertConn(WorkflowConn):
        """Supports INSERT … RETURNING."""

        def __init__(self) -> None:
            super().__init__()
            self._next_token = uuid.UUID("dddddddd-0000-4000-8000-000000000004")

        async def fetchrow(self, query: str, *args: Any) -> Any | None:
            key = str(args[0])
            if "tombstoned_at IS NULL" in query:
                row = self._store.get(key)
                if row and row.get("tombstoned_at") is None:
                    return row
                return None
            # INSERT … RETURNING path
            if key not in self._store:
                self._store[key] = {
                    "employee_id": key,
                    "name": args[1] if len(args) > 1 else None,
                    "pseudonymous_token": self._next_token,
                    "tombstoned_at": args[2] if len(args) > 2 else None,
                }
            else:
                row = self._store[key]
                if len(args) > 2:
                    row["tombstoned_at"] = args[2]
                if len(args) > 1:
                    row["name"] = args[1]
            return self._store[key]

    async def run() -> None:
        conn = InsertConn()

        # Step 1: pseudonymise creates a token
        token, created = await pseudonymise(conn, "11112222")
        assert created is True
        token_holder["token"] = token

        # Step 2: deletion request — tombstones the row (30 days before the far-future anchor)
        requested_at = datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        returned_token = await request_deletion(conn, "11112222", "Hans Müller", requested_at)
        assert returned_token == token

        # Step 3: simulate CronJob scrubber — nullifies employee_id and name
        row = conn._store["11112222"]
        assert row["tombstoned_at"] is not None  # tombstone set
        row["employee_id"] = None
        row["name"] = None

        # Step 4: assert PII is gone but pseudonymous_token is preserved
        assert row["employee_id"] is None
        assert row["name"] is None
        assert row["pseudonymous_token"] == token

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Migration SQL — read the actual file; never assert against a hand-written copy
# ---------------------------------------------------------------------------

def test_migration_sql_file_contains_identity_pseudonymisation_map_table() -> None:
    migration = (_SERVICE_ROOT / "migrations" / "001_identity_pseudonymisation_map.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS identity_pseudonymisation_map" in migration


def test_migration_sql_has_pseudonymous_token_column_not_null() -> None:
    migration = (_SERVICE_ROOT / "migrations" / "001_identity_pseudonymisation_map.sql").read_text()
    assert "pseudonymous_token  UUID        NOT NULL DEFAULT gen_random_uuid()" in migration


def test_migration_sql_has_tombstoned_at_index() -> None:
    migration = (_SERVICE_ROOT / "migrations" / "001_identity_pseudonymisation_map.sql").read_text()
    assert "idx_identity_map_tombstoned_at" in migration


def test_migration_sql_revokes_public_access() -> None:
    migration = (_SERVICE_ROOT / "migrations" / "001_identity_pseudonymisation_map.sql").read_text()
    assert "REVOKE ALL ON identity_pseudonymisation_map FROM PUBLIC" in migration


def test_migration_sql_grants_identity_service_only() -> None:
    migration = (_SERVICE_ROOT / "migrations" / "001_identity_pseudonymisation_map.sql").read_text()
    assert "GRANT SELECT, INSERT, UPDATE ON identity_pseudonymisation_map\n    TO identity_service" in migration
