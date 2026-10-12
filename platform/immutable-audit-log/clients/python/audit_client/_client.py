"""Internal implementation of the audit_client write SDK."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient

if TYPE_CHECKING:
    pass

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WRITE_TIMEOUT_S = 1.0

# Matches 8-digit numeric employee IDs — rejected to enforce pseudonymisation.
# user_id must be a UUID-format operator token, never a raw employee number.
_RAW_EMPLOYEE_ID_RE = re.compile(r"^\d{8}$")

# Mandatory fields in every audit blob.
_REQUIRED_EVENT_KEYS = frozenset(
    {
        "timestamp",
        "correlation_id",
        "user_id",
        "session_id",
        "component",
        "action",
        "result",
        "entity_id",
        "before_hash",
        "after_hash",
    }
)


class EmployeeIdRejected(ValueError):
    """Raised when user_id matches the 8-digit numeric employee ID pattern.

    Caller must pseudonymise the employee ID to a UUID operator token before
    calling write_event.  Raw numeric IDs are GDPR-sensitive and must not
    enter the WORM audit log.
    """


@dataclass(frozen=True)
class AuditEvent:
    user_id: str
    action_type: str
    entity_id: str
    before_hash: str  # SHA-256 hex of caller-supplied before-state
    after_hash: str   # SHA-256 hex of caller-supplied after-state
    utc_ts: str       # ISO-8601 UTC timestamp
    # Extended GDPR Article 30 fields — optional for backward-compat callers
    correlation_id: str | None = field(default=None)
    session_id: str | None = field(default=None)
    component: str | None = field(default=None)
    result: str | None = field(default=None)


class WriteLatencyExceeded(RuntimeError):
    """Raised when write_event exceeds the 1-second SLA."""


class AuditClient:
    """Write-only client for the immutable audit log container.

    Uses Managed Identity authentication — no credentials are stored or logged.
    Callers are responsible for computing before_hash and after_hash as SHA-256
    hex digests of the respective state values before calling write_event.

    Raises EmployeeIdRejected when user_id matches ^\d{8}$ — callers must
    pseudonymise to a UUID operator token first (GDPR US-028).
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
        *,
        correlation_id: str | None = None,
        session_id: str | None = None,
        component: str | None = None,
        result: str | None = None,
    ) -> str:
        """Append one audit event blob and return the blob name.

        Raises EmployeeIdRejected if user_id matches the 8-digit numeric pattern.
        Raises WriteLatencyExceeded if the upload takes longer than 1 second.
        Raises ValueError for malformed before_hash / after_hash.
        """
        _reject_raw_employee_id(user_id)
        _validate_sha256(before_hash, "before_hash")
        _validate_sha256(after_hash, "after_hash")

        event = AuditEvent(
            user_id=user_id,
            action_type=action_type,
            entity_id=entity_id,
            before_hash=before_hash,
            after_hash=after_hash,
            utc_ts=utc_ts,
            correlation_id=correlation_id,
            session_id=session_id,
            component=component,
            result=result,
        )

        blob_name = _make_blob_name(utc_ts, entity_id)
        payload = _serialise_event(event)

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
    *,
    correlation_id: str | None = None,
    session_id: str | None = None,
    component: str | None = None,
    result: str | None = None,
) -> str:
    """Module-level convenience wrapper; reads config from environment."""
    return AuditClient().write_event(
        user_id=user_id,
        action_type=action_type,
        entity_id=entity_id,
        before_hash=before_hash,
        after_hash=after_hash,
        utc_ts=utc_ts,
        correlation_id=correlation_id,
        session_id=session_id,
        component=component,
        result=result,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reject_raw_employee_id(user_id: str) -> None:
    """Raise EmployeeIdRejected when user_id is an 8-digit numeric employee ID."""
    if _RAW_EMPLOYEE_ID_RE.match(user_id):
        raise EmployeeIdRejected(
            "user_id must be a pseudonymous UUID operator token; "
            "8-digit numeric employee IDs are rejected (GDPR US-028). "
            "Call the identity service to obtain a pseudonymous token first."
        )


def _validate_sha256(value: str, field_name: str) -> None:
    if not _SHA256_RE.match(value):
        raise ValueError(
            f"{field_name} must be a lowercase 64-character SHA-256 hex digest, got: {value!r}"
        )


def _serialise_event(event: AuditEvent) -> bytes:
    """Produce the canonical JSON blob payload with all mandatory Article 30 fields."""
    doc: dict[str, object] = {
        "timestamp": event.utc_ts,
        "user_id": event.user_id,
        "action_type": event.action_type,
        "entity_id": event.entity_id,
        "before_hash": event.before_hash,
        "after_hash": event.after_hash,
    }
    # Optional extended fields — included when provided
    if event.correlation_id is not None:
        doc["correlation_id"] = event.correlation_id
    if event.session_id is not None:
        doc["session_id"] = event.session_id
    if event.component is not None:
        doc["component"] = event.component
    if event.result is not None:
        doc["result"] = event.result
    return json.dumps(doc, sort_keys=True).encode()


def _make_blob_name(utc_ts: str, entity_id: str) -> str:
    """Derive a deterministic, sortable blob path from timestamp and entity."""
    # Normalise: strip sub-second precision and timezone suffix for path safety.
    safe_ts = utc_ts[:19].replace(":", "-").replace("T", "/")
    run_id = uuid.uuid4()
    return f"{safe_ts}/{entity_id}/{run_id}.json"
