"""Identity pseudonymisation and GDPR Article 17 deletion service logic.

Database I/O is isolated here; routers remain thin.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID


# ---------------------------------------------------------------------------
# DB connection protocol — allows test injection without asyncpg import
# ---------------------------------------------------------------------------

class DBConnection(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Any | None: ...
    async def execute(self, query: str, *args: Any) -> None: ...


# ---------------------------------------------------------------------------
# Pseudonymise lookup / create
# ---------------------------------------------------------------------------

async def pseudonymise(
    conn: DBConnection,
    employee_id: str,
) -> tuple[UUID, bool]:
    """Return (pseudonymous_token, created).

    If a non-tombstoned row already exists for employee_id, return its token
    (created=False).  Otherwise INSERT a new row and return the generated token
    (created=True).
    """
    existing = await conn.fetchrow(
        """
        SELECT pseudonymous_token
        FROM identity_pseudonymisation_map
        WHERE employee_id = $1
          AND tombstoned_at IS NULL
        """,
        employee_id,
    )
    if existing is not None:
        return UUID(str(existing["pseudonymous_token"])), False

    row = await conn.fetchrow(
        """
        INSERT INTO identity_pseudonymisation_map (employee_id, name, created_at)
        VALUES ($1, NULL, NOW())
        ON CONFLICT (employee_id) DO UPDATE
          SET tombstoned_at = NULL
        RETURNING pseudonymous_token
        """,
        employee_id,
    )
    token = UUID(str(row["pseudonymous_token"]))
    return token, True


# ---------------------------------------------------------------------------
# Deletion request — tombstone + enqueue for scrubber
# ---------------------------------------------------------------------------

async def request_deletion(
    conn: DBConnection,
    employee_id: str,
    name: str,
    requested_at: datetime,
) -> UUID:
    """Upsert the mapping row and set tombstoned_at to mark it for scrubbing.

    Returns the pseudonymous_token so callers can include it in the audit event.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO identity_pseudonymisation_map
            (employee_id, name, created_at, tombstoned_at)
        VALUES ($1, $2, NOW(), $3)
        ON CONFLICT (employee_id) DO UPDATE
            SET name          = EXCLUDED.name,
                tombstoned_at = EXCLUDED.tombstoned_at
        RETURNING pseudonymous_token
        """,
        employee_id,
        name,
        requested_at.astimezone(timezone.utc),
    )
    return UUID(str(row["pseudonymous_token"]))
