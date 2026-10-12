"""Immutable audit write service for line-command-api.

Provides fire-and-forget audit writes for VIN digital record mutations.
Each call to fire_audit_write schedules a background thread so the HTTP
response is never blocked by the blob upload.

The AuditStore test double captures every write call in an in-process list
so tests can assert on payload content and timing without hitting Azure.

SHA-256 null sentinel: sha256("null") =
  74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b
Used when a before_state or after_state has no meaningful content.
"""

from __future__ import annotations

import hashlib
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


# SHA-256 of the string "null" — canonical empty-state sentinel.
NULL_HASH = hashlib.sha256(b"null").hexdigest()


def sha256_hex(value: str) -> str:
    """Return the lowercase SHA-256 hex digest of a UTF-8 encoded string."""
    return hashlib.sha256(value.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Protocol — any object with write_event(…) satisfies it
# ---------------------------------------------------------------------------

class AuditWriterProtocol(Protocol):
    def write_event(
        self,
        user_id: str,
        action_type: str,
        entity_id: str,
        before_hash: str,
        after_hash: str,
        utc_ts: str,
    ) -> str: ...


# ---------------------------------------------------------------------------
# In-memory audit store — test double, not shipped to production
# ---------------------------------------------------------------------------

@dataclass
class AuditRecord:
    user_id: str
    action_type: str
    entity_id: str
    before_hash: str
    after_hash: str
    utc_ts: str
    written_at: float  # monotonic timestamp (seconds) of the write call


class AuditStore:
    """Test double that records write_event calls in an instance field.

    Each instantiation starts with an empty records list; state does not bleed
    between instances.
    """

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def write_event(
        self,
        user_id: str,
        action_type: str,
        entity_id: str,
        before_hash: str,
        after_hash: str,
        utc_ts: str,
    ) -> str:
        self._records.append(
            AuditRecord(
                user_id=user_id,
                action_type=action_type,
                entity_id=entity_id,
                before_hash=before_hash,
                after_hash=after_hash,
                utc_ts=utc_ts,
                written_at=time.monotonic(),
            )
        )
        return f"{utc_ts}/{entity_id}/{uuid.uuid4()}.json"

    @property
    def records(self) -> list[AuditRecord]:
        return list(self._records)


# ---------------------------------------------------------------------------
# Fire-and-forget dispatch
# ---------------------------------------------------------------------------

def fire_audit_write(
    writer: AuditWriterProtocol,
    user_id: str,
    action_type: str,
    entity_id: str,
    before_hash: str,
    after_hash: str,
) -> None:
    """Schedule an audit write on a daemon thread; never blocks the caller.

    The write must complete within 1 second of being fired — the AuditClient
    SDK enforces this internally and raises WriteLatencyExceeded on breach.
    Exceptions from the background thread are swallowed after logging to
    stderr so that a transient Azure write failure never surfaces as an HTTP
    error to the caller.
    """
    utc_ts = datetime.now(tz=timezone.utc).isoformat()

    def _write() -> None:
        try:
            writer.write_event(
                user_id=user_id,
                action_type=action_type,
                entity_id=entity_id,
                before_hash=before_hash,
                after_hash=after_hash,
                utc_ts=utc_ts,
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[audit] write_event failed: action={action_type} entity={entity_id} err={exc}",
                file=sys.stderr,
            )

    t = threading.Thread(target=_write, daemon=True)
    t.start()
