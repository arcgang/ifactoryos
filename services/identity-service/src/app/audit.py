"""Fire-and-forget audit writes to the immutable audit log (Azure Blob WORM).

Writes IDENTITY_DELETION_REQUESTED and IDENTITY_PSEUDONYMISED events using the
shared audit_client SDK.  All writes are dispatched on a daemon thread so the
HTTP response is never blocked.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any


NULL_HASH = hashlib.sha256(b"null").hexdigest()


def sha256_hex(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def fire_audit_event(
    *,
    user_id: str,
    action_type: str,
    entity_id: str,
    after_hash: str,
    utc_ts: str,
    correlation_id: str | None = None,
    component: str = "identity-service",
) -> None:
    """Schedule an audit write on a daemon thread; never blocks the caller."""

    def _write() -> None:
        try:
            from audit_client import AuditClient

            client = AuditClient()
            client.write_event(
                user_id=user_id,
                action_type=action_type,
                entity_id=entity_id,
                before_hash=NULL_HASH,
                after_hash=after_hash,
                utc_ts=utc_ts,
                correlation_id=correlation_id,
                component=component,
                result="success",
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[audit] write_event failed: action={action_type} entity={entity_id} err={exc}",
                file=sys.stderr,
            )

    t = threading.Thread(target=_write, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Test double — mirrors AuditStore in line-command-api/audit.py
# ---------------------------------------------------------------------------

@dataclass
class AuditRecord:
    user_id: str
    action_type: str
    entity_id: str
    before_hash: str
    after_hash: str
    utc_ts: str
    written_at: float


class AuditStore:
    """Test double that records write_event calls in an instance field."""

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
        *,
        correlation_id: str | None = None,
        session_id: str | None = None,
        component: str | None = None,
        result: str | None = None,
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
