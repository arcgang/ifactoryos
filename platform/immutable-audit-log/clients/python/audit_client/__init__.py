"""audit_client — append-only write SDK for the immutable audit log.

Writes structured audit events to Azure Blob Storage (WORM) using Managed
Identity.  before_hash / after_hash must be SHA-256 hex digests of the
caller-supplied state; this module does not hash raw values.

Environment variables required:
  AUDIT_STORAGE_ACCOUNT  — Azure Storage account name (no .blob.core.windows.net)
  AUDIT_CONTAINER_NAME   — Blob container name (default: audit-events)
"""

from audit_client._client import AuditClient, EmployeeIdRejected, write_event

__all__ = ["AuditClient", "EmployeeIdRejected", "write_event"]
