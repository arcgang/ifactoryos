"""Azure Blob WORM writer with sub-1-second latency enforcement.

Uses Azure Workload Identity (Pod MSI via azure-workload-identity webhook) — no
storage account key or connection string is accepted.  One BlobServiceClient is
shared for the process lifetime; it is constructed once via get_writer() in main.py.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Protocol

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from config import settings
from models import RejectionEvent

logger = logging.getLogger(__name__)

_WRITE_LIMIT_S = 1.0


class BlobWriterProtocol(Protocol):
    def write_rejection(self, source: str, event: RejectionEvent) -> str: ...


class AzureBlobWriter:
    """Production writer — authenticates via Workload Identity (DefaultAzureCredential)."""

    def __init__(self) -> None:
        account_url = f"https://{settings.audit_storage_account}.blob.core.windows.net"
        self._client = BlobServiceClient(
            account_url=account_url,
            credential=DefaultAzureCredential(),
        )
        self._container = settings.audit_container_name

    def write_rejection(self, source: str, event: RejectionEvent) -> str:
        """Write one rejection event blob; return the blob path.

        source must be 'mqtt' or 'kafka'.
        Logs a warning if the write exceeds 950 ms; the caller receives a 503
        if the write takes longer than 1 second.
        """
        date_part = event.timestamp[:10]  # YYYY-MM-DD from ISO-8601
        blob_path = f"{source}/{date_part}/{event.correlation_id}.json"
        payload = json.dumps(event.model_dump()).encode()

        container_client = self._client.get_container_client(self._container)
        blob_client = container_client.get_blob_client(blob_path)

        t0 = time.monotonic()
        blob_client.upload_blob(data=payload, overwrite=False)
        elapsed_s = time.monotonic() - t0

        warn_threshold_s = settings.write_warn_ms / 1000.0
        if elapsed_s >= warn_threshold_s:
            logger.warning(
                "blob write latency approaching limit",
                extra={
                    "elapsed_ms": round(elapsed_s * 1000),
                    "blob_path": blob_path,
                    "source": source,
                    "correlation_id": event.correlation_id,
                },
            )

        if elapsed_s > _WRITE_LIMIT_S:
            raise WriteLatencyExceeded(
                f"blob write took {elapsed_s:.3f}s, exceeds 1 s SLA; path={blob_path}"
            )

        return blob_path


class WriteLatencyExceeded(RuntimeError):
    """Raised when a blob write exceeds the 1-second SLA."""


class MockBlobWriter:
    """Test double — records writes in an instance field; never touches Azure."""

    def __init__(self) -> None:
        self._writes: list[tuple[str, RejectionEvent]] = []

    def write_rejection(self, source: str, event: RejectionEvent) -> str:
        self._writes.append((source, event))
        date_part = event.timestamp[:10]
        return f"{source}/{date_part}/{event.correlation_id}.json"

    @property
    def writes(self) -> list[tuple[str, RejectionEvent]]:
        return list(self._writes)
