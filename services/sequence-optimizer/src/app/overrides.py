"""Override log persistence — create, list, fetch, and PDF export."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from typing import Any

import asyncpg

from app.models import CursorPage, OverrideListResponse, OverrideRecord, OverrideRequest

logger = logging.getLogger(__name__)


async def create_override(
    request: OverrideRequest,
    planner_id: str,
    pool: asyncpg.Pool,
) -> OverrideRecord:
    override_id = uuid.uuid4()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO override_log
                (override_id, run_id, vin, original_position, new_position,
                 justification, planner_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING override_id, run_id, vin, original_position, new_position,
                      justification, planner_id, created_at
            """,
            override_id,
            request.runId,
            request.vin,
            request.originalPosition,
            request.newPosition,
            request.justification,
            planner_id,
        )
    return _row_to_record(row)


async def get_override(
    override_id: uuid.UUID,
    pool: asyncpg.Pool,
) -> OverrideRecord | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT override_id, run_id, vin, original_position, new_position,
                   justification, planner_id, created_at
            FROM override_log
            WHERE override_id = $1
            """,
            override_id,
        )
    return _row_to_record(row) if row else None


async def list_overrides(
    pool: asyncpg.Pool,
    week_iso: str | None = None,
    run_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> OverrideListResponse:
    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if week_iso is not None:
        # ISO week range scan: created_at must fall within the week boundaries
        # Use date_trunc / generate_series approach via a simple cast filter
        conditions.append(f"to_char(created_at AT TIME ZONE 'UTC', 'IYYY-\"W\"IW') = ${idx}")
        params.append(week_iso)
        idx += 1

    if run_id is not None:
        conditions.append(f"run_id = ${idx}")
        params.append(run_id)
        idx += 1

    if cursor is not None:
        try:
            decoded = base64.b64decode(cursor.encode()).decode()
            parts = decoded.split("|")
            cursor_ts = parts[0]
            cursor_id = parts[1]
            conditions.append(
                f"(created_at, override_id::text) < (${idx}::timestamptz, ${idx + 1})"
            )
            params.extend([cursor_ts, cursor_id])
            idx += 2
        except (ValueError, IndexError, UnicodeDecodeError) as exc:
            logger.debug("Malformed pagination cursor ignored: %s", exc)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    fetch_limit = limit + 1
    params.append(fetch_limit)

    query = f"""
        SELECT override_id, run_id, vin, original_position, new_position,
               justification, planner_id, created_at
        FROM override_log
        {where_clause}
        ORDER BY created_at DESC, override_id DESC
        LIMIT ${idx}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    has_next = len(rows) > limit
    page_rows = rows[:limit]

    next_cursor: str | None = None
    if has_next and page_rows:
        last = page_rows[-1]
        raw = f"{last['created_at'].isoformat()}|{last['override_id']}"
        next_cursor = base64.b64encode(raw.encode()).decode()

    return OverrideListResponse(
        data=[_row_to_record(r) for r in page_rows],
        page=CursorPage(next_cursor=next_cursor, limit=limit),
    )


async def publish_override_event(record: OverrideRecord) -> None:
    """Publish SequenceOverrideAppliedEvent to Kafka — best-effort, logged on failure."""
    try:
        import aiokafka

        from app.config import get_settings

        cfg = get_settings()
        producer = aiokafka.AIOKafkaProducer(
            bootstrap_servers=cfg.kafka_bootstrap_servers,
            request_timeout_ms=5000,
        )
        await producer.start()
        try:
            event_payload = {
                "overrideId": str(record.overrideId),
                "runId": str(record.runId),
                "vin": record.vin,
                "originalPosition": record.originalPosition,
                "newPosition": record.newPosition,
                "plannerId": record.plannerId,
                "createdAt": record.createdAt,
            }
            await asyncio.wait_for(
                producer.send(
                    cfg.sequence_override_applied_topic,
                    value=json.dumps(event_payload).encode(),
                ),
                timeout=5.0,
            )
            logger.info(
                "Published SequenceOverrideAppliedEvent overrideId=%s vin=%s",
                record.overrideId,
                record.vin,
            )
        finally:
            await producer.stop()
    except ImportError as exc:
        logger.exception(
            "aiokafka not available for override event overrideId=%s: %s", record.overrideId, exc
        )
    except Exception as exc:  # noqa: BLE001 — fire-and-forget: must not raise
        logger.exception(
            "Failed to publish SequenceOverrideAppliedEvent overrideId=%s: %s",
            record.overrideId,
            exc,
        )


def build_override_pdf(records: list[OverrideRecord], week_iso: str) -> bytes:
    """Generate a minimal PDF binary for the override log export.

    Uses raw PDF construction — no external PDF library dependency.
    The output is a valid single-page PDF readable by any PDF viewer.
    """
    lines = [f"Override Log — {week_iso}", ""]
    for r in records:
        lines.append(
            f"Override {r.overrideId}: VIN {r.vin} "
            f"pos {r.originalPosition} -> {r.newPosition} "
            f"by {r.plannerId} at {r.createdAt}"
        )
        lines.append(f"  Justification: {r.justification}")
        lines.append("")

    body_text = "\n".join(lines)
    escaped = body_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream = f"BT /F1 10 Tf 40 800 Td 12 TL ({escaped}) Tj ET".encode("latin-1", errors="replace")

    objects: list[bytes] = []

    # obj 1: catalog
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    # obj 2: pages
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    # obj 3: page
    objects.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R "
        b"/MediaBox [0 0 612 792] /Contents 4 0 R /Resources "
        b"<< /Font << /F1 5 0 R >> >> >>\nendobj\n"
    )
    # obj 4: content stream
    stream_obj = (
        f"4 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode()
        + stream
        + b"\nendstream\nendobj\n"
    )
    objects.append(stream_obj)
    # obj 5: font
    objects.append(
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    )

    header = b"%PDF-1.4\n"
    body_parts = [header]
    offsets: list[int] = []
    pos = len(header)
    for obj in objects:
        offsets.append(pos)
        body_parts.append(obj)
        pos += len(obj)

    xref_offset = pos
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n"

    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    )

    return b"".join(body_parts) + xref.encode() + trailer.encode()


def _row_to_record(row: Any) -> OverrideRecord:
    return OverrideRecord(
        overrideId=row["override_id"],
        runId=row["run_id"],
        vin=row["vin"],
        originalPosition=row["original_position"],
        newPosition=row["new_position"],
        justification=row["justification"],
        plannerId=row["planner_id"],
        createdAt=row["created_at"].isoformat(),
    )
