"""Internal implementation of the audit_client write SDK."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient

if TYPE_CHECKING:
    pass

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WRITE_TIMEOUT_S = 1.0

# Matches the six required fields in the spec.
_REQUIRED_EVENT_KEYS = frozenset(
    {"user_id", "action_type", "entity_id", "before_hash", "after_hash", "utc_ts"}
)


@dataclass(frozen=True)
class AuditEvent:
    user_id: str
    action_type: str
    entity_id: str
    before_hash: str  # SHA-256 hex of caller-supplied before-state
    after_hash: str   # SHA-256 hex of caller-supplied after-state
    utc_ts: str       # ISO-8601 UTC timestamp


class WriteLatencyExceeded(RuntimeError):
    """Raised when write_event exceeds the 1-second SLA."""


class AuditClient:
    """Write-only client for the immutable audit log container.

    Uses Managed Identity authentication — no credentials are stored or logged.
    Callers are responsible for computing before_hash and after_hash as SHA-256
    hex digests of the respective state values before calling write_event.
    """

    def __init__(
        self,
        storage_account: str | None = None,
        container_name: str | None = None,
    ) -> None:
        self._account = storage_account or os.environ["AUDIT_STORAGE_ACCOUNT"]
        self._container = container_name or os.environ.get(
            "AUDIT_CONTAINER_NAME", "audit-events"
        )
        account_url = f"https://{self._account}.blob.core.windows.net"
        self._blob_service = BlobServiceClient(
            account_url=account_url,
            credential=ManagedIdentityCredential(),
        )

    def write_event(
        self,
        user_id: str,
        action_type: str,
        entity_id: str,
        before_hash: str,
        after_hash: str,
        utc_ts: str,
    ) -> str:
        """Append one audit event blob and return the blob name.

        Raises WriteLatencyExceeded if the upload takes longer than 1 second.
        Raises ValueError for malformed before_hash / after_hash.
        """
        _validate_sha256(before_hash, "before_hash")
        _validate_sha256(after_hash, "after_hash")

        event = AuditEvent(
            user_id=user_id,
            action_type=action_type,
            entity_id=entity_id,
            before_hash=before_hash,
            after_hash=after_hash,
            utc_ts=utc_ts,
        )

        blob_name = _make_blob_name(utc_ts, entity_id)
        payload = json.dumps(
            {
                "user_id": event.user_id,
                "action_type": event.action_type,
                "entity_id": event.entity_id,
                "before_hash": event.before_hash,
                "after_hash": event.after_hash,
                "utc_ts": event.utc_ts,
            }
        ).encode()

        container_client = self._blob_service.get_container_client(self._container)
        blob_client = container_client.get_blob_client(blob_name)

        t0 = time.monotonic()
        blob_client.upload_blob(
            data=payload,
            overwrite=False,  # append-only — WORM semantics enforced at client layer too
        )
        elapsed = time.monotonic() - t0

        if elapsed > _WRITE_TIMEOUT_S:
            raise WriteLatencyExceeded(
                f"write_event took {elapsed:.3f}s, exceeds 1 s SLA"
            )

        return blob_name


def write_event(
    user_id: str,
    action_type: str,
    entity_id: str,
    before_hash: str,
    after_hash: str,
    utc_ts: str,
) -> str:
    """Module-level convenience wrapper; reads config from environment."""
    return AuditClient().write_event(
        user_id=user_id,
        action_type=action_type,
        entity_id=entity_id,
        before_hash=before_hash,
        after_hash=after_hash,
        utc_ts=utc_ts,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_sha256(value: str, field: str) -> None:
    if not _SHA256_RE.match(value):
        raise ValueError(
            f"{field} must be a lowercase 64-character SHA-256 hex digest, got: {value!r}"
        )


def _make_blob_name(utc_ts: str, entity_id: str) -> str:
    """Derive a deterministic, sortable blob path from timestamp and entity."""
    # Normalise: strip sub-second precision and timezone suffix for path safety.
    safe_ts = utc_ts[:19].replace(":", "-").replace("T", "/")
    run_id = uuid.uuid4()
    return f"{safe_ts}/{entity_id}/{run_id}.json"
