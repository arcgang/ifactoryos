"""Targeted mid-week re-sequence service.

Validates a ResequenceRequest against an active constraint_alert, resolves
downstream-dependent VINs from the current week's locked sequence, runs a
Monte Carlo (or heuristic fallback) on that subset, and publishes
ResequenceTriggeredEvent to Kafka and an audit log entry.

Public API
----------
run_resequence(request, pool, kserve_client, user_id)  → OptimizeResponse
get_alert_for_resequence(alert_id, pool)               → dict | None
list_active_alerts(pool, cursor, limit)                → AlertListResponse
acknowledge_alert_db(alert_id, acknowledged_by, pool)  → ConstraintAlertRecord
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import asyncpg

from app.alerts import AlertNotFoundError
from app.models import (
    AlertListResponse,
    AlertStatus,
    ConstraintAlertRecord,
    ConstraintType,
    CursorPage,
    OptimizeResponse,
    RankedVin,
    ResequenceRequest,
    ResultType,
    VinInput,
)
from app.optimizer import KServeClient, _aggregate_monte_carlo_results, apply_heuristic

logger = logging.getLogger(__name__)

_RESEQUENCE_TIMEOUT_S = 45.0
_CONFIDENCE_FALLBACK_THRESHOLD = 0.70
_PAGE_DEFAULT = 50
_PAGE_MAX = 200

_RESEQUENCE_SOURCE = "planning-cockpit"


# ---------------------------------------------------------------------------
# Alert DB helpers
# ---------------------------------------------------------------------------


def _row_to_alert_record(row: asyncpg.Record) -> ConstraintAlertRecord:
    def _ts(val: object) -> str | None:
        if val is None:
            return None
        if isinstance(val, datetime):
            return val.isoformat()
        return str(val)

    affected_raw = row["affected_vins"]
    if isinstance(affected_raw, str):
        affected_vins: list[str] = json.loads(affected_raw)
    else:
        affected_vins = list(affected_raw) if affected_raw else []

    return ConstraintAlertRecord(
        alertId=row["alert_id"],
        constraintType=ConstraintType(row["constraint_type"]),
        currentValue=float(row["current_value"]),
        thresholdValue=float(row["threshold_value"]),
        affectedVins=affected_vins,
        status=AlertStatus(row["status"]),
        detectedAt=_ts(row["detected_at"]) or "",
        acknowledgedAt=_ts(row["acknowledged_at"]),
        acknowledgedBy=row["acknowledged_by"],
        resolvedAt=_ts(row["resolved_at"]),
    )


async def get_alert_for_resequence(
    alert_id: uuid.UUID,
    pool: asyncpg.Pool,
) -> ConstraintAlertRecord | None:
    """Fetch a constraint_alert row; return None if absent."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT alert_id, constraint_type, current_value, threshold_value,
                   affected_vins, status, detected_at, acknowledged_at,
                   acknowledged_by, resolved_at
            FROM constraint_alert
            WHERE alert_id = $1::uuid
            """,
            str(alert_id),
        )
    if row is None:
        return None
    return _row_to_alert_record(row)


async def list_active_alerts(
    pool: asyncpg.Pool,
    cursor: str | None,
    limit: int | None,
    status_filter: str | None = "active",
) -> AlertListResponse:
    """List constraint_alert rows, optionally filtered by status, cursor-paginated.

    Pass status_filter="all" or None to return all statuses.
    """
    import base64

    page_limit = min(limit or _PAGE_DEFAULT, _PAGE_MAX)
    # "all" means no status filter — treat as None
    effective_status: str | None = None if (status_filter is None or status_filter == "all") else status_filter

    def _encode_cursor(alert_id: str) -> str:
        return base64.urlsafe_b64encode(alert_id.encode()).decode()

    def _decode_cursor(cur: str) -> str:
        return base64.urlsafe_b64decode(cur.encode()).decode()

    async with pool.acquire() as conn:
        if effective_status is not None and cursor is not None:
            after_id = _decode_cursor(cursor)
            rows = await conn.fetch(
                """
                SELECT alert_id, constraint_type, current_value, threshold_value,
                       affected_vins, status, detected_at, acknowledged_at,
                       acknowledged_by, resolved_at
                FROM constraint_alert
                WHERE status = $1
                  AND alert_id > $2::uuid
                ORDER BY alert_id
                LIMIT $3
                """,
                effective_status,
                after_id,
                page_limit + 1,
            )
        elif effective_status is not None:
            rows = await conn.fetch(
                """
                SELECT alert_id, constraint_type, current_value, threshold_value,
                       affected_vins, status, detected_at, acknowledged_at,
                       acknowledged_by, resolved_at
                FROM constraint_alert
                WHERE status = $1
                ORDER BY alert_id
                LIMIT $2
                """,
                effective_status,
                page_limit + 1,
            )
        elif cursor is not None:
            after_id = _decode_cursor(cursor)
            rows = await conn.fetch(
                """
                SELECT alert_id, constraint_type, current_value, threshold_value,
                       affected_vins, status, detected_at, acknowledged_at,
                       acknowledged_by, resolved_at
                FROM constraint_alert
                WHERE alert_id > $1::uuid
                ORDER BY alert_id
                LIMIT $2
                """,
                after_id,
                page_limit + 1,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT alert_id, constraint_type, current_value, threshold_value,
                       affected_vins, status, detected_at, acknowledged_at,
                       acknowledged_by, resolved_at
                FROM constraint_alert
                ORDER BY alert_id
                LIMIT $1
                """,
                page_limit + 1,
            )

    has_more = len(rows) > page_limit
    page_rows = rows[:page_limit]
    next_cursor = (
        _encode_cursor(str(page_rows[-1]["alert_id"])) if has_more else None
    )

    return AlertListResponse(
        data=[_row_to_alert_record(r) for r in page_rows],
        page=CursorPage(next_cursor=next_cursor, limit=page_limit),
    )


async def acknowledge_alert_db(
    alert_id: uuid.UUID,
    acknowledged_by: str,
    pool: asyncpg.Pool,
    now: datetime | None = None,
) -> ConstraintAlertRecord:
    """Set status='acknowledged' on a constraint_alert row and return the updated record.

    Raises AlertNotFoundError when no row matches alert_id.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE constraint_alert
               SET status          = 'acknowledged',
                   acknowledged_at = $1,
                   acknowledged_by = $2
             WHERE alert_id = $3::uuid
            RETURNING alert_id, constraint_type, current_value, threshold_value,
                      affected_vins, status, detected_at, acknowledged_at,
                      acknowledged_by, resolved_at
            """,
            now,
            acknowledged_by,
            str(alert_id),
        )
    if row is None:
        raise AlertNotFoundError(f"Alert {alert_id!r} not found")
    return _row_to_alert_record(row)


# ---------------------------------------------------------------------------
# VIN resolution — downstream dependencies from locked sequence
# ---------------------------------------------------------------------------


async def _resolve_downstream_vins(
    pool: asyncpg.Pool,
    affected_vin_strs: list[str],
) -> list[VinInput]:
    """Build VinInput list for affected VINs, expanded with any adjacent VINs
    that are positional neighbours in the current week's locked sequence."""
    if not affected_vin_strs:
        return []

    affected_set = set(affected_vin_strs)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT ranked_sequence, constraint_snapshot
            FROM optimizer_run
            WHERE created_at >= (date_trunc('week', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
            ORDER BY completed_at DESC
            LIMIT 1
            """,
        )

    if row is None:
        # No locked run — use affected VINs only with default attributes
        return [VinInput(vin=v) for v in affected_vin_strs]

    try:
        ranked: list[dict] = json.loads(row["ranked_sequence"])
    except (json.JSONDecodeError, TypeError):
        return [VinInput(vin=v) for v in affected_vin_strs]

    # Build position map and add immediate downstream neighbour (+1) for each affected VIN
    position_map: dict[str, int] = {}
    for item in ranked:
        vin_str = item.get("vin", "")
        pos = item.get("position", 0)
        if vin_str and isinstance(pos, int):
            position_map[vin_str] = pos

    downstream_positions: set[int] = set()
    for v in affected_vin_strs:
        pos = position_map.get(v)
        if pos is not None:
            downstream_positions.add(pos)
            downstream_positions.add(pos + 1)  # immediate downstream dependency

    expanded: set[str] = set(affected_vin_strs)
    for item in ranked:
        vin_str = item.get("vin", "")
        pos = item.get("position", 0)
        if isinstance(pos, int) and pos in downstream_positions and vin_str:
            expanded.add(vin_str)

    # Build VinInput objects; propagate vipPriority / regulatoryHold from ranked sequence
    meta_map: dict[str, dict] = {
        item.get("vin", ""): item for item in ranked if item.get("vin")
    }
    result: list[VinInput] = []
    for vin_str in sorted(expanded):
        meta = meta_map.get(vin_str, {})
        result.append(
            VinInput(
                vin=vin_str,
                vipPriority=bool(meta.get("vipPriority", False)),
                regulatoryHold=bool(meta.get("regulatoryHold", False)),
            )
        )
    return result


# ---------------------------------------------------------------------------
# Idempotency — deduplicate within 10-minute TTL
# ---------------------------------------------------------------------------


async def _load_cached_resequence(
    pool: asyncpg.Pool,
    idempotency_key: uuid.UUID,
    ttl_seconds: int = 600,
) -> OptimizeResponse | None:
    from app.optimizer import _load_cached_run

    return await _load_cached_run(pool, idempotency_key, ttl_seconds)


# ---------------------------------------------------------------------------
# Publish ResequenceTriggeredEvent
# ---------------------------------------------------------------------------


async def _publish_resequence_event(
    response: OptimizeResponse,
    alert_id: uuid.UUID,
    affected_vins: list[str],
) -> None:
    """Publish ResequenceTriggeredEvent to Kafka — best-effort."""
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
                "runId": str(response.runId),
                "alertId": str(alert_id),
                "weekIso": response.weekIso,
                "resultType": response.resultType.value,
                "affectedVins": affected_vins,
                "rankedSequence": [rv.model_dump(mode="json") for rv in response.rankedSequence],
                "confidenceScore": response.confidenceScore,
                "durationMs": response.durationMs,
                "completedAt": response.completedAt,
                "source": _RESEQUENCE_SOURCE,
                "my26ConfigVersion": response.my26ConfigVersion,
            }
            await asyncio.wait_for(
                producer.send(
                    cfg.resequence_triggered_topic,
                    value=json.dumps(event_payload).encode(),
                ),
                timeout=5.0,
            )
            logger.info(
                "Published ResequenceTriggeredEvent runId=%s alertId=%s affectedVins=%d",
                response.runId,
                alert_id,
                len(affected_vins),
            )
        finally:
            await producer.stop()
    except ImportError as exc:
        logger.exception("aiokafka not available for resequence publish: %s", exc)
    except Exception as exc:  # noqa: BLE001 — fire-and-forget
        logger.exception("Failed to publish ResequenceTriggeredEvent runId=%s: %s", response.runId, exc)


# ---------------------------------------------------------------------------
# Audit log write
# ---------------------------------------------------------------------------


async def _write_resequence_audit(
    response: OptimizeResponse,
    alert_id: uuid.UUID,
    user_id: str,
    affected_vins: list[str],
) -> None:
    """POST re-sequence audit event — best-effort, 1 s timeout."""
    try:
        import httpx

        from app.config import get_settings

        cfg = get_settings()
        payload = {
            "action": "resequence_publish",
            "userId": user_id,
            "runId": str(response.runId),
            "alertId": str(alert_id),
            "resultType": response.resultType.value,
            "confidenceScore": response.confidenceScore,
            "durationMs": response.durationMs,
            "affectedVins": affected_vins,
            "source": _RESEQUENCE_SOURCE,
        }
        async with httpx.AsyncClient(timeout=cfg.audit_log_timeout_s) as client:
            await client.post(cfg.audit_log_url, json=payload)
    except Exception as exc:  # noqa: BLE001 — fire-and-forget
        logger.exception("Failed to write resequence audit event runId=%s: %s", response.runId, exc)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


async def run_resequence(
    request: ResequenceRequest,
    pool: asyncpg.Pool,
    kserve_client: KServeClient,
    user_id: str = "unknown",
) -> OptimizeResponse:
    """Run a targeted re-sequence for the affected VINs from a constraint_alert."""
    from app.config import get_settings
    from app.optimizer import _store_run

    cfg = get_settings()

    # Idempotency: return cached result for same key within TTL
    if request.idempotencyKey is not None:
        cached = await _load_cached_resequence(pool, request.idempotencyKey, cfg.idempotency_cache_ttl_s)
        if cached is not None:
            logger.info(
                "Returning cached resequence result for idempotencyKey=%s runId=%s",
                request.idempotencyKey,
                cached.runId,
            )
            return cached

    # Validate that the alert exists and is active
    alert = await get_alert_for_resequence(request.alertId, pool)
    if alert is None:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "ALERT_NOT_FOUND",
                "message": f"Constraint alert {request.alertId} not found",
                "correlation_id": str(uuid.uuid4()),
            },
        )
    if alert.status != AlertStatus.active:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "ALERT_NOT_ACTIVE",
                "message": f"Constraint alert {request.alertId} is not active (status={alert.status.value})",
                "correlation_id": str(uuid.uuid4()),
            },
        )

    # Resolve downstream-dependent VINs from the current week's locked sequence
    affected_vin_strs = [vi.vin for vi in request.affectedVins]
    vins_for_run = await _resolve_downstream_vins(pool, affected_vin_strs)

    # Fall back to the request's own affectedVins if resolution returned nothing useful
    if not vins_for_run:
        vins_for_run = list(request.affectedVins)

    # Determine ISO week from the constraint snapshot timestamp
    try:
        snap_dt = datetime.fromisoformat(request.constraintSnapshot.snapshotTimestamp)
        year, week, _ = snap_dt.isocalendar()
        week_iso = f"{year}-W{week:02d}"
    except (ValueError, AttributeError):
        week_iso = datetime.now(tz=timezone.utc).strftime("%G-W%V")

    start_ms = time.monotonic()
    result_type = ResultType.monte_carlo
    ranked: list[RankedVin] = []
    confidence: float = 0.0

    use_heuristic = False
    fallback_reason = ""
    try:
        all_results: list[list[dict]] = []
        tasks = [
            asyncio.create_task(
                kserve_client.predict(vins_for_run, request.constraintSnapshot, i)
            )
            for i in range(50)
        ]
        done_set, pending_set = await asyncio.wait(tasks, timeout=_RESEQUENCE_TIMEOUT_S)
        for t in pending_set:
            t.cancel()
        if pending_set:
            logger.warning(
                "KServe: %d/50 resequence runs timed out, triggering heuristic fallback",
                len(pending_set),
            )
            use_heuristic = True
            fallback_reason = "KServe timeout"
        else:
            for task in done_set:
                task_exc = task.exception()
                if task_exc is not None:
                    logger.warning("KServe resequence run raised %s, triggering heuristic fallback", task_exc)
                    use_heuristic = True
                    fallback_reason = f"KServe error: {task_exc}"
                    break
                all_results.append(task.result())

        if not use_heuristic:
            ranked, confidence = _aggregate_monte_carlo_results(all_results, vins_for_run)
            if confidence < _CONFIDENCE_FALLBACK_THRESHOLD:
                use_heuristic = True
                fallback_reason = f"confidence {confidence:.2f} below threshold"

    except (asyncio.TimeoutError, OSError, ConnectionError) as exc:
        logger.warning("KServe resequence call failed with %s, triggering heuristic fallback", exc)
        use_heuristic = True
        fallback_reason = str(exc)

    if use_heuristic:
        logger.info("Applying heuristic fallback for resequence: %s", fallback_reason)
        result_type = ResultType.heuristic_fallback
        ranked = apply_heuristic(vins_for_run, request.constraintSnapshot)
        confidence = 0.5

    duration_ms = int((time.monotonic() - start_ms) * 1000)
    completed_at = datetime.now(tz=timezone.utc).isoformat()

    response = OptimizeResponse(
        runId=uuid.uuid4(),
        weekIso=week_iso,
        resultType=result_type,
        rankedSequence=ranked,
        confidenceScore=confidence,
        durationMs=duration_ms,
        my26ConfigVersion=None,
        completedAt=completed_at,
        constraintSnapshotTimestamp=request.constraintSnapshot.snapshotTimestamp,
    )

    constraint_snapshot_json = json.dumps(request.constraintSnapshot.model_dump(mode="json"))
    await _store_run(pool, response, request.idempotencyKey, constraint_snapshot_json)

    asyncio.create_task(
        _publish_resequence_event(response, request.alertId, affected_vin_strs)
    )
    asyncio.create_task(
        _write_resequence_audit(response, request.alertId, user_id, affected_vin_strs)
    )

    return response
