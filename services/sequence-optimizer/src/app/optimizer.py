"""Sequence optimisation service — Monte Carlo via KServe and heuristic fallback.

Implements the two execution paths required by US-002:
  1. 50-run Monte Carlo via KServe (resultType=monte_carlo)
  2. Rule-based heuristic (resultType=heuristic_fallback) when KServe times out
     or returns a non-2xx error.

Public API
----------
run_optimize(request, pool, kserve_client, user_id)  → OptimizeResponse
get_run(run_id, pool)                                → OptimizeResponse | None
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Protocol

import asyncpg

from app.models import (
    ConstraintSnapshot,
    OptimizeRequest,
    OptimizeResponse,
    RankedVin,
    ResultType,
    VinInput,
)

logger = logging.getLogger(__name__)

_OPTIMIZER_TIMEOUT_S = 45.0
_PAINT_CHANGE_PENALTY = 200
_VIP_BONUS = 500
_REGULATORY_BASE = 0
_REGULATORY_NORMAL_BASE = 1000
_KITTING_THRESHOLD = 80.0
_KITTING_PENALTY_FACTOR = 5
_MAINTENANCE_PENALTY = 300
_BODY_WIP_HIGH_RATIO = 0.9
_BODY_WIP_CAPACITY = 1000  # assumed max capacity when not explicit
_BODY_WIP_PENALTY = 100
_CONFIDENCE_FALLBACK_THRESHOLD = 0.70

# ---------------------------------------------------------------------------
# Protocol for the KServe client (allows clean test substitution)
# ---------------------------------------------------------------------------


class KServeClient(Protocol):
    async def predict(
        self,
        vins: list[VinInput],
        constraint_snapshot: ConstraintSnapshot,
        run_index: int,
    ) -> list[dict]:
        """Run one Monte Carlo iteration; returns ranked list of dicts."""
        ...


# ---------------------------------------------------------------------------
# Heuristic fallback scorer
# ---------------------------------------------------------------------------


def _paint_code_changed(prev_paint: str | None, curr_paint: str | None) -> bool:
    if prev_paint is None or curr_paint is None:
        return False
    return prev_paint != curr_paint


def _maintenance_overlap(vin_index: int, windows: list) -> bool:
    """Approximate: odd-indexed VINs overlap the first maintenance window if any."""
    return bool(windows) and (vin_index % 2 == 1)


def score_vin_heuristic(
    vin_input: VinInput,
    prev_paint_code: str | None,
    constraint_snapshot: ConstraintSnapshot,
    vin_index: int,
    paint_overrides: dict[str, int] | None = None,
) -> float:
    """Deterministic constraint score per the settled formula.

    S = (regulatoryHold ? 0 : 1000)
      + (vipPriority ? 500 : 0)
      - (paintCodeChangeover ? changeover_penalty : 0)
      - max(0, kittingReadinessPct < 80 ? (80 - kittingReadinessPct) * 5 : 0)
      - (maintenanceWindowOverlap ? 300 : 0)
      - (bodyWipQueueDepth > 0.9 * capacity ? 100 : 0)

    paint_overrides maps paintCode → changeoverPenaltyMinutes (MY26 config overrides).
    """
    s: float = _REGULATORY_NORMAL_BASE if not vin_input.regulatoryHold else _REGULATORY_BASE
    if vin_input.vipPriority:
        s += _VIP_BONUS
    if _paint_code_changed(prev_paint_code, vin_input.paintCode):
        if paint_overrides and vin_input.paintCode in paint_overrides:
            penalty = float(paint_overrides[vin_input.paintCode])
        else:
            penalty = float(_PAINT_CHANGE_PENALTY)
        s -= penalty
    kitting = constraint_snapshot.kittingReadinessPct
    if kitting < _KITTING_THRESHOLD:
        s -= (_KITTING_THRESHOLD - kitting) * _KITTING_PENALTY_FACTOR
    if _maintenance_overlap(vin_index, constraint_snapshot.maintenanceWindows):
        s -= _MAINTENANCE_PENALTY
    if constraint_snapshot.bodyWipQueueDepth > _BODY_WIP_HIGH_RATIO * _BODY_WIP_CAPACITY:
        s -= _BODY_WIP_PENALTY
    return s


def apply_heuristic(
    vins: list[VinInput],
    constraint_snapshot: ConstraintSnapshot,
    paint_overrides: dict[str, int] | None = None,
) -> list[RankedVin]:
    """Apply deterministic heuristic; regulatory-hold VINs go to the end."""
    scored: list[tuple[float, int, VinInput]] = []
    prev_paint: str | None = None
    for idx, vin_input in enumerate(vins):
        s = score_vin_heuristic(vin_input, prev_paint, constraint_snapshot, idx, paint_overrides)
        scored.append((s, idx, vin_input))
        prev_paint = vin_input.paintCode

    # Sort: regulatory holds to end; within each group descending score, ties by original order.
    normal = sorted(
        [(s, orig_idx, v) for s, orig_idx, v in scored if not v.regulatoryHold],
        key=lambda t: (-t[0], t[1]),
    )
    holds = sorted(
        [(s, orig_idx, v) for s, orig_idx, v in scored if v.regulatoryHold],
        key=lambda t: (-t[0], t[1]),
    )
    ordered = normal + holds

    return [
        RankedVin(
            vin=v.vin,
            position=pos + 1,
            constraintScore=s,
            vipPriority=v.vipPriority,
            regulatoryHold=v.regulatoryHold,
        )
        for pos, (s, _orig, v) in enumerate(ordered)
    ]


# ---------------------------------------------------------------------------
# Monte Carlo aggregation
# ---------------------------------------------------------------------------


def _aggregate_monte_carlo_results(
    all_run_results: list[list[dict]],
    vins: list[VinInput],
) -> tuple[list[RankedVin], float]:
    """Aggregate position votes across Monte Carlo runs; return ranked sequence + confidence."""
    # Accumulate position sum per VIN (lower = better across all runs).
    position_sums: dict[str, float] = {v.vin: 0.0 for v in vins}
    position_counts: dict[str, int] = {v.vin: 0 for v in vins}
    vin_meta: dict[str, VinInput] = {v.vin: v for v in vins}

    for run_result in all_run_results:
        for item in run_result:
            vin = item.get("vin", "")
            pos = item.get("position", 0)
            if vin in position_sums:
                position_sums[vin] += pos
                position_counts[vin] += 1

    # Average position; VINs missing from KServe output go last.
    avg_positions: list[tuple[float, str]] = []
    for vin_str, total in position_sums.items():
        count = position_counts[vin_str]
        avg = total / count if count > 0 else float("inf")
        avg_positions.append((avg, vin_str))

    avg_positions.sort(key=lambda t: (t[0], t[1]))

    # Confidence: 1 - (std-dev of rank variance / max possible variance).
    # Simplified: ratio of agreement among runs for top-ranked VIN.
    n_runs = len(all_run_results)
    n_vins = len(vins)
    if n_runs > 0 and n_vins > 0 and avg_positions:
        top_vin = avg_positions[0][1]
        top_count = sum(
            1 for run in all_run_results
            if run and run[0].get("vin") == top_vin
        )
        confidence = top_count / n_runs
    else:
        confidence = 0.0

    ranked: list[RankedVin] = []
    for pos, (_, vin_str) in enumerate(avg_positions):
        meta = vin_meta.get(vin_str)
        # Try to get constraint score from last run
        score = 0.0
        for run in reversed(all_run_results):
            for item in run:
                if item.get("vin") == vin_str:
                    score = float(item.get("constraintScore", 0.0))
                    break
            else:
                continue
            break
        ranked.append(
            RankedVin(
                vin=vin_str,
                position=pos + 1,
                constraintScore=score,
                vipPriority=meta.vipPriority if meta else None,
                regulatoryHold=meta.regulatoryHold if meta else None,
            )
        )

    return ranked, confidence


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


async def _store_run(
    pool: asyncpg.Pool,
    response: OptimizeResponse,
    idempotency_key: uuid.UUID | None,
    constraint_snapshot_json: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO optimizer_run (
                run_id, week_iso, result_type, ranked_sequence,
                confidence_score, duration_ms, my26_config_version,
                constraint_snapshot, idempotency_key, completed_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            response.runId,
            response.weekIso,
            response.resultType.value,
            json.dumps([rv.model_dump(mode="json") for rv in response.rankedSequence]),
            response.confidenceScore,
            response.durationMs,
            uuid.UUID(response.my26ConfigVersion) if response.my26ConfigVersion else None,
            constraint_snapshot_json,
            idempotency_key,
            datetime.fromisoformat(response.completedAt),
        )


async def _load_cached_run(
    pool: asyncpg.Pool,
    idempotency_key: uuid.UUID,
    ttl_seconds: int,
) -> OptimizeResponse | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT run_id, week_iso, result_type, ranked_sequence,
                   confidence_score, duration_ms, my26_config_version,
                   constraint_snapshot, completed_at
            FROM optimizer_run
            WHERE idempotency_key = $1
              AND created_at > (now() - ($2 || ' seconds')::interval)
            LIMIT 1
            """,
            idempotency_key,
            str(ttl_seconds),
        )
    if row is None:
        return None
    return _row_to_response(row)


async def _load_run_by_id(
    pool: asyncpg.Pool,
    run_id: uuid.UUID,
) -> OptimizeResponse | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT run_id, week_iso, result_type, ranked_sequence,
                   confidence_score, duration_ms, my26_config_version,
                   constraint_snapshot, completed_at
            FROM optimizer_run
            WHERE run_id = $1
            LIMIT 1
            """,
            run_id,
        )
    if row is None:
        return None
    return _row_to_response(row)


def _row_to_response(row: asyncpg.Record) -> OptimizeResponse:
    ranked_raw = json.loads(row["ranked_sequence"])
    ranked = [RankedVin(**item) for item in ranked_raw]
    snapshot_raw = json.loads(row["constraint_snapshot"])
    snapshot_ts = snapshot_raw.get("snapshotTimestamp")
    my26 = str(row["my26_config_version"]) if row["my26_config_version"] else None
    completed_at = row["completed_at"]
    if isinstance(completed_at, datetime):
        completed_at_str = completed_at.isoformat()
    else:
        completed_at_str = str(completed_at)
    return OptimizeResponse(
        runId=row["run_id"],
        weekIso=row["week_iso"],
        resultType=ResultType(row["result_type"]),
        rankedSequence=ranked,
        confidenceScore=row["confidence_score"],
        durationMs=row["duration_ms"],
        my26ConfigVersion=my26,
        completedAt=completed_at_str,
        constraintSnapshotTimestamp=snapshot_ts,
    )


# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------


async def _publish_optimized_event(response: OptimizeResponse) -> None:
    """Publish SequenceOptimizedEvent to Kafka — best-effort, logged on failure."""
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
                "weekIso": response.weekIso,
                "resultType": response.resultType.value,
                "rankedSequence": [rv.model_dump(mode="json") for rv in response.rankedSequence],
                "confidenceScore": response.confidenceScore,
                "durationMs": response.durationMs,
                "completedAt": response.completedAt,
                "my26ConfigVersion": response.my26ConfigVersion,
                "constraintSnapshotTimestamp": response.constraintSnapshotTimestamp,
            }
            await asyncio.wait_for(
                producer.send(
                    cfg.sequence_optimized_topic,
                    value=json.dumps(event_payload).encode(),
                ),
                timeout=5.0,
            )
            logger.info(
                "Published SequenceOptimizedEvent runId=%s resultType=%s",
                response.runId,
                response.resultType.value,
            )
        finally:
            await producer.stop()
    except ImportError as exc:
        logger.exception("aiokafka not available for publish runId=%s: %s", response.runId, exc)
    except Exception as exc:  # noqa: BLE001 — fire-and-forget: must not raise into the event loop
        logger.exception("Failed to publish SequenceOptimizedEvent runId=%s: %s", response.runId, exc)


# ---------------------------------------------------------------------------
# Audit log write
# ---------------------------------------------------------------------------


async def _write_audit_event(
    response: OptimizeResponse,
    user_id: str,
) -> None:
    """POST audit event to immutable audit log — best-effort, 1 s timeout."""
    try:
        import httpx

        from app.config import get_settings

        cfg = get_settings()
        payload = {
            "userId": user_id,
            "runId": str(response.runId),
            "resultType": response.resultType.value,
            "confidenceScore": response.confidenceScore,
            "durationMs": response.durationMs,
        }
        async with httpx.AsyncClient(timeout=cfg.audit_log_timeout_s) as client:
            await client.post(cfg.audit_log_url, json=payload)
    except ImportError as exc:
        logger.exception("httpx not available for audit write runId=%s: %s", response.runId, exc)
    except Exception as exc:  # noqa: BLE001 — fire-and-forget: must not raise into the event loop
        logger.exception("Failed to write audit event for runId=%s: %s", response.runId, exc)


# ---------------------------------------------------------------------------
# Span emission
# ---------------------------------------------------------------------------


def _emit_otel_span(name: str, attributes: dict) -> None:
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("sequence-optimizer")
        with tracer.start_as_current_span(name) as span:
            for k, v in attributes.items():
                span.set_attribute(k, v)
    except ImportError:
        logger.debug("OTel span %s skipped (SDK not installed)", name)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


async def run_optimize(
    request: OptimizeRequest,
    pool: asyncpg.Pool,
    kserve_client: KServeClient,
    user_id: str = "unknown",
    my26_overrides: dict | None = None,
) -> OptimizeResponse:
    """Run optimisation (Monte Carlo or heuristic fallback) and persist the result."""
    from app.config import get_settings

    cfg = get_settings()

    # Idempotency: return cached result if same key was used within TTL.
    if request.idempotencyKey is not None:
        cached = await _load_cached_run(pool, request.idempotencyKey, cfg.idempotency_cache_ttl_s)
        if cached is not None:
            logger.info(
                "Returning cached result for idempotencyKey=%s runId=%s",
                request.idempotencyKey,
                cached.runId,
            )
            return cached

    start_ms = time.monotonic()
    result_type = ResultType.monte_carlo
    ranked: list[RankedVin] = []
    confidence: float = 0.0

    use_heuristic = False
    fallback_reason: str = ""

    # Apply MY26 paint changeover penalties to the heuristic scorer if overrides present
    paint_overrides: dict[str, int] = (
        my26_overrides.get("paint_changeover_penalties", {}) if my26_overrides else {}
    )

    try:
        all_results: list[list[dict]] = []
        tasks = [
            asyncio.create_task(
                kserve_client.predict(request.vins, request.constraintSnapshot, i)
            )
            for i in range(request.monteCarloRuns)
        ]
        done_set, pending_set = await asyncio.wait(
            tasks,
            timeout=_OPTIMIZER_TIMEOUT_S,
        )
        for t in pending_set:
            t.cancel()
        if pending_set:
            logger.warning(
                "KServe: %d/%d runs timed out, triggering heuristic fallback",
                len(pending_set),
                request.monteCarloRuns,
            )
            use_heuristic = True
            fallback_reason = "KServe timeout"
        else:
            for task in done_set:
                task_exc = task.exception()
                if task_exc is not None:
                    logger.warning("KServe run raised %s, triggering heuristic fallback", task_exc)
                    use_heuristic = True
                    fallback_reason = f"KServe error: {task_exc}"
                    break
                all_results.append(task.result())

        if not use_heuristic:
            ranked, confidence = _aggregate_monte_carlo_results(all_results, request.vins)
            if confidence < _CONFIDENCE_FALLBACK_THRESHOLD:
                logger.warning(
                    "Monte Carlo confidence=%.2f below threshold=%.2f, switching to heuristic",
                    confidence,
                    _CONFIDENCE_FALLBACK_THRESHOLD,
                )
                use_heuristic = True
                fallback_reason = f"confidence {confidence:.2f} below threshold"

    except (asyncio.TimeoutError, OSError, ConnectionError) as exc:
        logger.warning("KServe call failed with %s, triggering heuristic fallback", exc)
        use_heuristic = True
        fallback_reason = str(exc)

    if use_heuristic:
        logger.info("Applying heuristic fallback: %s", fallback_reason)
        result_type = ResultType.heuristic_fallback
        ranked = apply_heuristic(request.vins, request.constraintSnapshot, paint_overrides)
        confidence = 0.5

    duration_ms = int((time.monotonic() - start_ms) * 1000)
    completed_at = datetime.now(tz=timezone.utc).isoformat()

    response = OptimizeResponse(
        runId=uuid.uuid4(),
        weekIso=request.weekIso,
        resultType=result_type,
        rankedSequence=ranked,
        confidenceScore=confidence,
        durationMs=duration_ms,
        my26ConfigVersion=request.my26ConfigVersion,
        completedAt=completed_at,
        constraintSnapshotTimestamp=request.constraintSnapshot.snapshotTimestamp,
    )

    constraint_snapshot_json = json.dumps(request.constraintSnapshot.model_dump(mode="json"))
    await _store_run(pool, response, request.idempotencyKey, constraint_snapshot_json)

    _emit_otel_span(
        "optimizer.run",
        {
            "run_id": str(response.runId),
            "result_type": result_type.value,
            "confidence_score": confidence,
            "duration_ms": duration_ms,
        },
    )

    asyncio.create_task(_publish_optimized_event(response))
    asyncio.create_task(_write_audit_event(response, user_id))

    return response


async def get_run(run_id: uuid.UUID, pool: asyncpg.Pool) -> OptimizeResponse | None:
    """Retrieve a previously persisted optimisation run by its UUID."""
    return await _load_run_by_id(pool, run_id)
