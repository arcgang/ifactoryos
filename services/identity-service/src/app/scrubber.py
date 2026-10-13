"""GDPR Article 17 scrubber — run as a CronJob, never as part of the HTTP service.

Selects rows from identity_pseudonymisation_map where tombstoned_at IS NOT NULL
and tombstoned_at <= NOW(), then NULLs employee_id and name while leaving
pseudonymous_token intact.  Logs each completion to the immutable audit log
(action = IDENTITY_PSEUDONYMISED).

Exit code 0 on success, 1 on any unrecoverable error.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

import asyncpg

from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NULL_HASH = hashlib.sha256(b"null").hexdigest()


async def _run() -> None:
    pool = await asyncpg.create_pool(settings.identity_db_url, min_size=1, max_size=3)

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, pseudonymous_token
                FROM identity_pseudonymisation_map
                WHERE tombstoned_at IS NOT NULL
                  AND tombstoned_at <= NOW()
                  AND employee_id IS NOT NULL
                """
            )

            if not rows:
                logger.info("scrubber: no rows to scrub")
                return

            logger.info("scrubber: scrubbing %d rows", len(rows))

            for row in rows:
                token = str(row["pseudonymous_token"])
                async with conn.transaction():
                    await conn.execute(
                        """
                        UPDATE identity_pseudonymisation_map
                        SET employee_id = NULL,
                            name        = NULL
                        WHERE id = $1
                        """,
                        row["id"],
                    )

                # Fire audit event outside the transaction (fire-and-forget)
                _fire_audit(token)

                logger.info("scrubber: pseudonymised token=%s", token)

    finally:
        await pool.close()


def _fire_audit(token: str) -> None:
    utc_ts = datetime.now(timezone.utc).isoformat()
    after_hash = hashlib.sha256(
        json.dumps(
            {"action": "IDENTITY_PSEUDONYMISED", "pseudonymous_token": token},
            sort_keys=True,
        ).encode()
    ).hexdigest()

    try:
        from audit_client import AuditClient

        AuditClient().write_event(
            user_id=token,
            action_type="IDENTITY_PSEUDONYMISED",
            entity_id=token,
            before_hash=NULL_HASH,
            after_hash=after_hash,
            utc_ts=utc_ts,
            correlation_id=str(uuid.uuid4()),
            component="identity-scrubber",
            result="success",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("scrubber: audit write failed for token=%s err=%s", token, exc)


if __name__ == "__main__":
    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        logger.error("scrubber: fatal error: %s", exc)
        sys.exit(1)
