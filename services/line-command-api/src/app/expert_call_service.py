"""Expert call service — Works Council mode selection, DB writes, VDT audit logging.

All database and HTTP I/O is here; the router stays thin.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB connection protocol — allows test injection without asyncpg import
# ---------------------------------------------------------------------------

class DBConnection(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Any | None: ...
    async def execute(self, query: str, *args: Any) -> None: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...

    def transaction(self) -> Any: ...


# ---------------------------------------------------------------------------
# Works Council config lookup
# ---------------------------------------------------------------------------

async def get_video_approved(conn: DBConnection, plant_id: str) -> bool:
    """Return True if video streaming is Works-Council-approved for the given plant."""
    row = await conn.fetchrow(
        """
        SELECT video_streaming_approved
        FROM works_council_config
        WHERE plant_id = $1
        """,
        plant_id,
    )
    if row is None:
        return False
    return bool(row["video_streaming_approved"])


# ---------------------------------------------------------------------------
# Initiate expert call — insert with idempotency
# ---------------------------------------------------------------------------

async def initiate_expert_call(
    conn: DBConnection,
    vin: str,
    station_id: str,
    session_id: str | None,
    idempotency_key: str,
    initiated_by_user_id: str,
    call_mode: str,
) -> dict[str, Any]:
    """Insert an expert_call row and return {id, call_mode, created_at}.

    On idempotency_key conflict, returns the existing row unchanged.
    call_mode must be 'video' or 'voice'.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO expert_call
            (vin, station_id, ar_session_id, call_mode, status,
             idempotency_key, initiated_by_user_id)
        VALUES ($1, $2, $3::uuid, $4, 'active', $5::uuid, $6)
        ON CONFLICT (idempotency_key) DO UPDATE
            SET idempotency_key = EXCLUDED.idempotency_key  -- no-op to allow RETURNING
        RETURNING id, call_mode, created_at
        """,
        vin,
        station_id,
        session_id,
        call_mode,
        idempotency_key,
        initiated_by_user_id,
    )
    return dict(row)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# End expert call — UPDATE + VDT audit in one transaction, then Kafka
# ---------------------------------------------------------------------------

async def end_expert_call(
    conn: DBConnection,
    call_id: str,
    expert_user_id: str,
    duration_seconds: int,
    vdt_base_url: str,
    vdt_timeout_seconds: float,
) -> dict[str, Any]:
    """End the call, post to VDT, and return the result dict.

    The DB UPDATE and VDT call run inside a single asyncpg transaction so
    that a VDT failure automatically rolls back the UPDATE (spec: "if the
    vin-digital-thread-api call fails, roll back the UPDATE and return 502").
    The Kafka publish happens *after* the transaction commits — the caller is
    responsible for it.

    Returns {call_id, vin, station_id, call_mode, expert_user_id,
             duration_seconds, audit_entry_id, ended_at}.
    Raises HTTPException 404 if call not found or already in a terminal state.
    Raises HTTPException 502 if VDT call fails (transaction already rolled back).
    """
    ended_at = datetime.now(timezone.utc)

    # Read the call row outside the transaction — cheap guard before acquiring the lock.
    call_row = await conn.fetchrow(
        """
        SELECT id, vin, station_id, call_mode, status
        FROM expert_call
        WHERE id = $1::uuid
        """,
        call_id,
    )

    if call_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CALL_NOT_FOUND",
                "message": f"Expert call {call_id} not found",
                "correlation_id": str(uuid.uuid4()),
            },
        )

    # Guard against any terminal state, not just 'ended' (convention: check ALL terminals)
    if call_row["status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CALL_ALREADY_ENDED",
                "message": f"Expert call {call_id} is not active",
                "correlation_id": str(uuid.uuid4()),
            },
        )

    vin = call_row["vin"]
    station_id = call_row["station_id"]
    call_mode = call_row["call_mode"]

    # VDT call + DB UPDATE inside one transaction.
    # asyncpg rolls back automatically if any exception is raised inside the block.
    async with conn.transaction():
        # Atomic UPDATE — guards concurrent end attempts with WHERE status='active'
        updated = await conn.fetchrow(
            """
            UPDATE expert_call
            SET status           = 'ended',
                ended_at         = $2,
                duration_seconds = $3,
                expert_user_id   = $4,
                updated_at       = $2
            WHERE id = $1::uuid
              AND status = 'active'
            RETURNING id, ended_at
            """,
            call_id,
            ended_at,
            duration_seconds,
            expert_user_id,
        )

        if updated is None:
            # Lost the race — another concurrent end request won
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "CALL_NOT_FOUND",
                    "message": f"Expert call {call_id} was not active",
                    "correlation_id": str(uuid.uuid4()),
                },
            )

        # POST to VDT inside the transaction so a failure triggers rollback
        audit_entry_id = await _post_vdt_audit(
            vdt_base_url=vdt_base_url,
            vdt_timeout_seconds=vdt_timeout_seconds,
            vin=vin,
            station_id=station_id,
            call_mode=call_mode,
            expert_user_id=expert_user_id,
            duration_seconds=duration_seconds,
            call_id=call_id,
        )

        # Store the audit entry ID on the row while still in the transaction
        await conn.execute(
            """
            UPDATE expert_call
            SET audit_entry_id = $2::uuid,
                updated_at     = $3
            WHERE id = $1::uuid
            """,
            call_id,
            audit_entry_id,
            ended_at,
        )

    return {
        "call_id": call_id,
        "vin": vin,
        "station_id": station_id,
        "call_mode": call_mode,
        "expert_user_id": expert_user_id,
        "duration_seconds": duration_seconds,
        "audit_entry_id": audit_entry_id,
        "ended_at": updated["ended_at"],
    }


async def _post_vdt_audit(
    vdt_base_url: str,
    vdt_timeout_seconds: float,
    vin: str,
    station_id: str,
    call_mode: str,
    expert_user_id: str,
    duration_seconds: int,
    call_id: str,
) -> str:
    """POST the call audit entry to vin-digital-thread-api.

    Returns the audit entry ID (UUID string).
    Raises HTTPException 502 on any failure — asyncpg will roll back the
    enclosing transaction when this propagates.
    """
    url = f"{vdt_base_url}/api/v1/vin/{vin}/thread/events"
    payload = {
        "eventType": "EXPERT_CALL_END",
        "callId": call_id,
        "stationId": station_id,
        "callMode": call_mode,
        "expertUserId": expert_user_id,
        "callDurationSeconds": duration_seconds,
        "biometricDataIncluded": False,
        "gazeDataIncluded": False,
    }
    try:
        async with httpx.AsyncClient(timeout=vdt_timeout_seconds) as client:
            resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("eventId") or data.get("id") or uuid.uuid4())
    except Exception as exc:
        logger.error(
            "VDT audit write failed for expert_call call_id=%s vin=%s: %s",
            call_id,
            vin,
            exc,
            extra={"call_id": call_id, "vin": vin},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "VDT_AUDIT_FAILED",
                "message": "Failed to write audit entry to VIN digital thread",
                "correlation_id": str(uuid.uuid4()),
            },
        ) from exc
