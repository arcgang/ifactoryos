"""Audit forwarder — HTTP server receiving broker rejection events and writing
them to Azure Blob WORM storage within 1 second.

Endpoints:
  POST /rejections/mqtt   — events forwarded from HiveMQ
  POST /rejections/kafka  — events forwarded from the Kafka log-tailer sidecar
"""

from __future__ import annotations

import logging
import time

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError

from blob_writer import AzureBlobWriter, BlobWriterProtocol, WriteLatencyExceeded
from models import RejectionEvent

logging.basicConfig(
    format="%(message)s",
    level=logging.INFO,
)
logger = structlog.get_logger(__name__)

app = FastAPI(title="audit-forwarder", version="1.0.0")

# Lazily initialised — None until first request or override_writer() call.
# Tests call override_writer() before the first request so AzureBlobWriter is
# never constructed in the test process.
_writer: BlobWriterProtocol | None = None


def get_writer() -> BlobWriterProtocol:
    global _writer
    if _writer is None:
        _writer = AzureBlobWriter()
    return _writer


def override_writer(writer: BlobWriterProtocol) -> None:
    """Replace the writer at test time."""
    global _writer
    _writer = writer


async def _handle_rejection(
    source: str,
    request: Request,
    writer: BlobWriterProtocol,
) -> Response:
    raw = await request.body()
    try:
        event = RejectionEvent.model_validate_json(raw)
    except (ValidationError, ValueError) as exc:
        logger.warning(
            "rejected malformed payload",
            source=source,
            error=str(exc),
        )
        raise HTTPException(
            status_code=400,
            detail={"detail": f"Invalid payload: {exc}", "status_code": 400},
        ) from exc

    t0 = time.monotonic()
    try:
        blob_path = writer.write_rejection(source, event)
    except WriteLatencyExceeded as exc:
        logger.error(
            "blob write latency exceeded",
            source=source,
            correlation_id=event.correlation_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=503,
            detail={"detail": "WORM write exceeded 1 s SLA", "status_code": 503},
        ) from exc
    except Exception as exc:  # noqa: BLE001 — azure-storage-blob raises varied SDK exceptions
        logger.error(
            "blob write failed",
            source=source,
            correlation_id=event.correlation_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail={"detail": "Transient Azure Blob write failure", "status_code": 500},
        ) from exc

    elapsed_ms = round((time.monotonic() - t0) * 1000)
    logger.info(
        "rejection event written",
        source=source,
        blob_path=blob_path,
        correlation_id=event.correlation_id,
        elapsed_ms=elapsed_ms,
    )
    return Response(status_code=204)


@app.post("/rejections/mqtt", status_code=204, response_model=None)
async def receive_mqtt_rejection(
    request: Request,
    writer: BlobWriterProtocol = Depends(get_writer),
) -> Response:
    return await _handle_rejection("mqtt", request, writer)


@app.post("/rejections/kafka", status_code=204, response_model=None)
async def receive_kafka_rejection(
    request: Request,
    writer: BlobWriterProtocol = Depends(get_writer),
) -> Response:
    return await _handle_rejection("kafka", request, writer)


@app.get("/healthz", status_code=200)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
