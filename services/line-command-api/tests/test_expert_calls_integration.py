"""Integration test: full expert-call flow against a real Postgres instance
and a mock vin-digital-thread-api.

Skipped when CITUS_URL is not set or the DB is unreachable, so the CI unit-test
job is unaffected. Run with:

    CITUS_URL=postgresql://postgres:local@localhost:5434/app pytest \
        tests/test_expert_calls_integration.py -v

The test:
 1. Applies the migration DDL for works_council_config and expert_call.
 2. Inserts a MUNICH row with video_streaming_approved=FALSE.
 3. Calls POST /ar/expert-calls — expects 201 and callMode=voice.
 4. Calls POST /ar/expert-calls/{callId}/end with a mocked VDT — expects 200.
 5. Verifies the expert_call row is status=ended and has the audit_entry_id set.
"""

from __future__ import annotations

import os
import uuid
from datetime import timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SKIP = not bool(os.getenv("CITUS_URL"))
_SKIP_REASON = "CITUS_URL not set; skipping integration test"

_VIN = "WBA3A5C55FK654321"
_STATION = "FINAL-01"
_SESSION_ID = str(uuid.uuid4())
_EXPERT_USER = "expert-integration-001"
_AUDIT_ENTRY_ID = str(uuid.uuid4())

_BEARER_ASSOCIATE = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTEyMyIsInJvbGVzIjpbImlmb3MtYXNzb2NpYXRlIl19"
    ".sig"
)


def _mock_vdt_success(audit_entry_id: str) -> Any:
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"eventId": audit_entry_id}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


@pytest.mark.skipif(_SKIP, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_full_expert_call_flow_against_postgres():
    import asyncpg
    from httpx import ASGITransport, AsyncClient

    from app.audit import AuditStore
    from app.main import create_app
    from app.routers.expert_calls import get_audit_writer, get_pool

    citus_url = os.environ["CITUS_URL"]

    pool = await asyncpg.create_pool(citus_url, min_size=1, max_size=3)

    # Apply the two tables (idempotent)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS works_council_config (
                id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                plant_id                    TEXT        NOT NULL UNIQUE,
                video_streaming_approved    BOOLEAN     NOT NULL DEFAULT FALSE,
                approved_by_user_id         TEXT,
                approved_at                 TIMESTAMPTZ,
                created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        # Ensure ar_session table exists (created by earlier migration)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ar_session (
                id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                vin             TEXT        NOT NULL,
                station_id      TEXT        NOT NULL,
                device_type     TEXT        NOT NULL,
                user_id         TEXT        NOT NULL,
                idempotency_key UUID        NOT NULL,
                event_id        UUID        NOT NULL DEFAULT gen_random_uuid(),
                started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_ar_session_idempotency_it UNIQUE (idempotency_key)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS expert_call (
                id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                vin                 TEXT        NOT NULL,
                station_id          TEXT        NOT NULL,
                ar_session_id       UUID,
                call_mode           TEXT        NOT NULL CHECK (call_mode IN ('video', 'voice')),
                expert_user_id      TEXT,
                duration_seconds    INTEGER     CHECK (duration_seconds >= 0),
                status              TEXT        NOT NULL DEFAULT 'active'
                                        CHECK (status IN ('active', 'ended')),
                idempotency_key     UUID        NOT NULL,
                audit_entry_id      UUID,
                initiated_by_user_id TEXT       NOT NULL,
                started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ended_at            TIMESTAMPTZ,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_expert_call_idempotency_it UNIQUE (idempotency_key)
            )
            """
        )
        # Seed MUNICH row — voice-only
        await conn.execute(
            """
            INSERT INTO works_council_config (plant_id, video_streaming_approved)
            VALUES ($1, FALSE)
            ON CONFLICT (plant_id) DO UPDATE SET video_streaming_approved = FALSE
            """,
            "MUNICH",
        )

    app = create_app()
    fake_writer = AuditStore()

    class _RealPool:
        def acquire(self):
            return pool.acquire()

    real_pool = _RealPool()
    app.dependency_overrides[get_pool] = lambda: real_pool
    app.dependency_overrides[get_audit_writer] = lambda: fake_writer

    with (
        patch(
            "app.expert_call_service.httpx.AsyncClient",
            return_value=_mock_vdt_success(_AUDIT_ENTRY_ID),
        ),
        patch("app.routers.expert_calls.publish", new=AsyncMock()),
        patch("app.kafka_producer._producer", new=None),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Initiate call
            r1 = await client.post(
                "/ar/expert-calls",
                json={"vin": _VIN, "stationId": _STATION, "sessionId": _SESSION_ID},
                headers={"Authorization": _BEARER_ASSOCIATE},
            )
            assert r1.status_code == 201, f"initiate failed: {r1.text}"
            call_body = r1.json()
            assert call_body["callMode"] == "voice"
            call_id = call_body["callId"]

            # 2. End call
            r2 = await client.post(
                f"/ar/expert-calls/{call_id}/end",
                json={"expertUserId": _EXPERT_USER, "durationSeconds": 90},
                headers={"Authorization": _BEARER_ASSOCIATE},
            )
            assert r2.status_code == 200, f"end failed: {r2.text}"
            end_body = r2.json()
            assert end_body["callId"] == call_id
            assert end_body["auditEntryId"] == _AUDIT_ENTRY_ID

    # 3. Verify DB state
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, duration_seconds, expert_user_id, audit_entry_id "
            "FROM expert_call WHERE id = $1::uuid",
            call_id,
        )
        assert row is not None
        assert row["status"] == "ended"
        assert row["duration_seconds"] == 90
        assert row["expert_user_id"] == _EXPERT_USER
        assert str(row["audit_entry_id"]) == _AUDIT_ENTRY_ID

    await pool.close()
