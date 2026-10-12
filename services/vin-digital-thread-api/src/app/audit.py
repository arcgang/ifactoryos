"""Fire-and-forget audit write helper for VIN digital record mutations.

Wraps audit_client.write_event so that:
- The write runs in a thread pool to keep the async handler non-blocking.
- Failures are logged but never propagated to the caller (the HTTP response
  must not be blocked by an audit-log write).
- The write completes (or the WriteLatencyExceeded exception fires) within the
  SDK's 1-second SLA window; that window begins inside write_event itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _sha256_of(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


_NULL_HASH = _sha256_of("null")


def sha256_hex(data: object) -> str:
    """Return SHA-256 hex of the canonical JSON-serialised form of *data*."""
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _write_sync(
    user_id: str,
    action_type: str,
    entity_id: str,
    before_hash: str,
    after_hash: str,
    utc_ts: str,
) -> None:
    """Synchronous write; called from a thread pool so it won't block the event loop."""
    # Import here so that test doubles injected via os.environ are respected at call time.
    from audit_client import write_event  # type: ignore[import]

    write_event(
        user_id=user_id,
        action_type=action_type,
        entity_id=entity_id,
        before_hash=before_hash,
        after_hash=after_hash,
        utc_ts=utc_ts,
    )


async def fire_audit_event(
    *,
    user_id: str,
    action_type: str,
    entity_id: str,
    before_hash: str = _NULL_HASH,
    after_hash: str,
    utc_ts: str | None = None,
) -> None:
    """Schedule an audit write on the default executor and never raise to the caller.

    before_hash defaults to the SHA-256 of the string literal 'null', matching the
    contract requirement for mutations that have no meaningful pre-state.
    """
    if utc_ts is None:
        utc_ts = datetime.now(timezone.utc).isoformat()

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            _write_sync,
            user_id,
            action_type,
            entity_id,
            before_hash,
            after_hash,
            utc_ts,
        )
    except Exception:
        logger.exception(
            "audit write failed",
            extra={
                "action_type": action_type,
                "entity_id": entity_id,
                "user_id": user_id,
            },
        )
