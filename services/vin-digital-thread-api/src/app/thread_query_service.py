"""Business logic for read-path thread endpoints.

All Neo4j reads use read replicas via bolt+routing.
Completeness cache is backed by PostgreSQL/Citus vin_thread_completeness_cache.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections import defaultdict
from typing import Any

import asyncpg

from app.models import (
    BoothParameterTimeSeries,
    BoothSample,
    ClimateTimeSeries,
    ClimateSample,
    CursorPage,
    PrepStageSample,
    PrepStageTimeSeries,
    ProcessReplayResponse,
    StageCompletenessDetail,
    ThreadCompletenessResponse,
    ThreadEvent,
    ThreadSnapshotResponse,
    VinStatus,
    VinThreadResponse,
)

logger = logging.getLogger(__name__)

_VIN_RE_STR = r"^[A-HJ-NPR-Z0-9]{17}$"
_COMPLETENESS_THRESHOLD = 95.0
_NEO4J_READ_TIMEOUT_S = 9.0

# ---------------------------------------------------------------------------
# Cursor helpers — (occurredAt, eventId) tuple, base64-encoded
# ---------------------------------------------------------------------------


def _encode_cursor(occurred_at: str, event_id: str) -> str:
    raw = json.dumps({"ts": occurred_at, "id": event_id})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    """Return (occurred_at_iso, event_id_str). Raises ValueError on bad input."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        parsed = json.loads(raw)
        return parsed["ts"], parsed["id"]
    except (KeyError, ValueError, json.JSONDecodeError) as err:
        raise ValueError(f"Invalid cursor: {cursor!r}") from err


# ---------------------------------------------------------------------------
# Stage configuration (loaded once at import time)
# ---------------------------------------------------------------------------


def _load_stage_config() -> list[dict[str, Any]]:
    import os
    import yaml  # type: ignore[import]

    config_path = os.path.join(os.path.dirname(__file__), "paint_prep_stage_config.yaml")
    with open(config_path) as fh:
        data = yaml.safe_load(fh)
    return data["stages"]


_STAGE_CONFIG: list[dict[str, Any]] = []


def get_stage_config() -> list[dict[str, Any]]:
    global _STAGE_CONFIG
    if not _STAGE_CONFIG:
        _STAGE_CONFIG = _load_stage_config()
    return _STAGE_CONFIG


# ---------------------------------------------------------------------------
# Neo4j driver helper
# ---------------------------------------------------------------------------


async def _neo4j_driver():  # type: ignore[return]
    from neo4j import AsyncGraphDatabase
    from app.config import get_settings

    cfg = get_settings()
    return AsyncGraphDatabase.driver(
        cfg.neo4j_uri,
        auth=(cfg.neo4j_user, cfg.neo4j_password),
    )


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread
# ---------------------------------------------------------------------------


async def get_vin_thread(
    vin: str,
    cursor: str | None,
    limit: int,
    shop_filter: list[str] | None,
    from_ts: str | None,
    to_ts: str | None,
    pool: asyncpg.Pool,
) -> VinThreadResponse:
    from app.config import get_settings

    cfg = get_settings()
    timeout = cfg.neo4j_read_timeout_s

    # Parse cursor
    cursor_ts: str | None = None
    cursor_id: str | None = None
    if cursor:
        cursor_ts, cursor_id = _decode_cursor(cursor)

    # Build Cypher — keyset pagination on (occurredAt, eventId)
    # Shops and time window filtered in query.
    where_clauses: list[str] = []
    if cursor_ts and cursor_id:
        where_clauses.append(
            "(e.occurredAt > $cursor_ts OR (e.occurredAt = $cursor_ts AND e.eventId > $cursor_id))"
        )
    if from_ts:
        where_clauses.append("e.occurredAt >= $from_ts")
    if to_ts:
        where_clauses.append("e.occurredAt <= $to_ts")
    if shop_filter:
        where_clauses.append("e.shop IN $shops")

    where_str = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    cypher = f"""
        MATCH (v:VIN {{vin: $vin}})-[:HAS_EVENT]->(e)
        {where_str}
        RETURN e
        ORDER BY e.occurredAt ASC, e.eventId ASC
        LIMIT $limit
    """

    params: dict[str, Any] = {
        "vin": vin,
        "limit": limit + 1,  # fetch one extra to detect next page
    }
    if cursor_ts:
        params["cursor_ts"] = cursor_ts
    if cursor_id:
        params["cursor_id"] = cursor_id
    if from_ts:
        params["from_ts"] = from_ts
    if to_ts:
        params["to_ts"] = to_ts
    if shop_filter:
        params["shops"] = shop_filter

    events: list[dict[str, Any]] = []
    vin_exists = False

    try:
        driver = await _neo4j_driver()
        async with driver:
            async with driver.session() as session:
                # Check VIN exists
                vin_check = await asyncio.wait_for(
                    session.run("MATCH (v:VIN {vin: $vin}) RETURN v LIMIT 1", vin=vin),
                    timeout=timeout,
                )
                vin_records = await vin_check.data()
                vin_exists = len(vin_records) > 0

                if vin_exists:
                    result = await asyncio.wait_for(
                        session.run(cypher, **params),
                        timeout=timeout,
                    )
                    rows = await result.data()
                    for row in rows:
                        e = row["e"]
                        # parameters stored as JSON string in Neo4j
                        raw_params = e.get("parameters", "{}")
                        parsed_params = json.loads(raw_params) if isinstance(raw_params, str) else raw_params
                        events.append({
                            "eventId": e.get("eventId", ""),
                            "vin": vin,
                            "eventType": e.get("eventType", ""),
                            "shop": e.get("shop", ""),
                            "station": e.get("station", ""),
                            "occurredAt": e.get("occurredAt", ""),
                            "dataSource": e.get("dataSource", ""),
                            "parameters": parsed_params,
                            "createdAt": e.get("linkedAt"),
                            "stageNumber": e.get("stageNumber"),
                            "tunnelPositionMm": e.get("tunnelPositionMm"),
                        })
    except asyncio.TimeoutError as err:
        raise Neo4jUnavailableError() from err
    except Exception as err:
        logger.exception("Neo4j read failed for vin=%s", vin)
        raise Neo4jReadError() from err

    if not vin_exists:
        raise VinNotFoundError(vin)

    # Detect next page
    has_next = len(events) > limit
    if has_next:
        events = events[:limit]

    next_cursor: str | None = None
    if has_next and events:
        last = events[-1]
        next_cursor = _encode_cursor(last["occurredAt"], last["eventId"])

    # Read VIN status from completeness cache
    vin_status = await _read_vin_status(vin, pool)

    thread_events = [ThreadEvent(**ev) for ev in events]

    return VinThreadResponse(
        vin=vin,
        data=thread_events,
        page=CursorPage(next_cursor=next_cursor, limit=limit),
        vinStatus=vin_status,
    )


async def _read_vin_status(vin: str, pool: asyncpg.Pool) -> VinStatus:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT vin_status FROM vin_thread_completeness_cache WHERE vin = $1",
                vin,
            )
        if row is None:
            return VinStatus.UNKNOWN
        return VinStatus(row["vin_status"])
    except Exception:
        logger.exception("Failed to read vin_status for vin=%s", vin)
        return VinStatus.UNKNOWN


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread/replay
# ---------------------------------------------------------------------------


async def get_process_replay(
    vin: str,
    cursor: str | None,
    limit: int,
    pool: asyncpg.Pool,
) -> ProcessReplayResponse:
    from app.config import get_settings

    cfg = get_settings()
    timeout = cfg.neo4j_read_timeout_s

    cursor_ts: str | None = None
    cursor_id: str | None = None
    if cursor:
        cursor_ts, cursor_id = _decode_cursor(cursor)

    # Fetch completeness (cache-first, 30s TTL)
    completeness = await _get_completeness_cached(vin, pool)

    # Cypher scoped to paint event types only (performance: smaller result set)
    paint_types = ["PAINT_PREP_STAGE", "BOOTH_PARAMETER", "CLIMATE_DATA"]

    where_clauses = ["e.eventType IN $event_types"]
    if cursor_ts and cursor_id:
        where_clauses.append(
            "(e.occurredAt > $cursor_ts OR (e.occurredAt = $cursor_ts AND e.eventId > $cursor_id))"
        )

    cypher = f"""
        MATCH (v:VIN {{vin: $vin}})-[:HAS_EVENT]->(e)
        WHERE {" AND ".join(where_clauses)}
        RETURN e
        ORDER BY e.occurredAt ASC, e.eventId ASC
        LIMIT $limit
    """

    params: dict[str, Any] = {
        "vin": vin,
        "event_types": paint_types,
        "limit": limit + 1,
    }
    if cursor_ts:
        params["cursor_ts"] = cursor_ts
    if cursor_id:
        params["cursor_id"] = cursor_id

    raw_events: list[dict[str, Any]] = []
    vin_exists = False

    try:
        driver = await _neo4j_driver()
        async with driver:
            async with driver.session() as session:
                vin_check = await asyncio.wait_for(
                    session.run("MATCH (v:VIN {vin: $vin}) RETURN v LIMIT 1", vin=vin),
                    timeout=timeout,
                )
                vin_records = await vin_check.data()
                vin_exists = len(vin_records) > 0

                if vin_exists:
                    result = await asyncio.wait_for(
                        session.run(cypher, **params),
                        timeout=timeout,
                    )
                    rows = await result.data()
                    for row in rows:
                        e = row["e"]
                        raw_params = e.get("parameters", "{}")
                        parsed_params = json.loads(raw_params) if isinstance(raw_params, str) else raw_params
                        raw_events.append({
                            "eventId": e.get("eventId", ""),
                            "eventType": e.get("eventType", ""),
                            "occurredAt": e.get("occurredAt", ""),
                            "parameters": parsed_params,
                            "stageNumber": e.get("stageNumber"),
                            "tunnelPositionMm": e.get("tunnelPositionMm"),
                            "boothId": e.get("boothId"),
                        })
    except asyncio.TimeoutError as err:
        raise Neo4jUnavailableError() from err
    except Exception as err:
        logger.exception("Neo4j replay read failed for vin=%s", vin)
        raise Neo4jReadError() from err

    if not vin_exists:
        raise VinNotFoundError(vin)

    # Detect next page
    has_next = len(raw_events) > limit
    if has_next:
        raw_events = raw_events[:limit]

    next_cursor: str | None = None
    if has_next and raw_events:
        last = raw_events[-1]
        next_cursor = _encode_cursor(last["occurredAt"], last["eventId"])

    # Assemble response by event type
    prep_stage_map: dict[int, list[PrepStageSample]] = defaultdict(list)
    booth_map: dict[str, list[BoothSample]] = defaultdict(list)
    climate_samples: list[ClimateSample] = []
    all_ts: list[str] = []

    for ev in raw_events:
        ts = ev["occurredAt"]
        all_ts.append(ts)
        etype = ev["eventType"]
        params_ev = ev["parameters"]

        if etype == "PAINT_PREP_STAGE":
            stage_num = ev.get("stageNumber")
            if stage_num is not None:
                prep_stage_map[int(stage_num)].append(
                    PrepStageSample(
                        ts=ts,
                        parameters=params_ev,
                        tunnelPositionMm=ev.get("tunnelPositionMm"),
                    )
                )
        elif etype == "BOOTH_PARAMETER":
            booth_id = ev.get("boothId") or params_ev.get("boothId", "BOOTH_UNKNOWN")
            booth_map[str(booth_id)].append(BoothSample(ts=ts, parameters=params_ev))
        elif etype == "CLIMATE_DATA":
            climate_samples.append(ClimateSample(ts=ts, parameters=params_ev))

    # Build 14 prep stage time-series (OQ-6: missing stage → available=False, samples=[])
    stages_cfg = get_stage_config()
    prep_stages: list[PrepStageTimeSeries] = []
    for stage_cfg in stages_cfg:
        num = stage_cfg["number"]
        samples = prep_stage_map.get(num, [])
        prep_stages.append(
            PrepStageTimeSeries(
                stageNumber=num,
                stageName=stage_cfg["name"],
                available=len(samples) > 0,
                samples=sorted(samples, key=lambda s: s.ts),
            )
        )

    booth_params: list[BoothParameterTimeSeries] = [
        BoothParameterTimeSeries(
            boothId=bid,
            available=True,
            samples=sorted(bsamples, key=lambda s: s.ts),
        )
        for bid, bsamples in sorted(booth_map.items())
    ]

    climate = ClimateTimeSeries(
        available=len(climate_samples) > 0,
        samples=sorted(climate_samples, key=lambda s: s.ts),
    )

    replay_start = min(all_ts) if all_ts else None
    replay_end = max(all_ts) if all_ts else None

    overall_pct = float(completeness["overall_completeness_pct"]) if completeness else 0.0

    return ProcessReplayResponse(
        vin=vin,
        overallCompletenessPercent=overall_pct,
        prepStages=prep_stages,
        boothParameters=booth_params,
        climateData=climate,
        page=CursorPage(next_cursor=next_cursor, limit=limit),
        replayWindowStart=replay_start,
        replayWindowEnd=replay_end,
    )


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread/snapshot
# ---------------------------------------------------------------------------


async def get_thread_snapshot(
    vin: str,
    at_ts: str,
    pool: asyncpg.Pool,
) -> ThreadSnapshotResponse:
    from app.config import get_settings

    cfg = get_settings()
    timeout = cfg.neo4j_read_timeout_s

    # Fetch last-known-value for all parameters at or before at_ts
    cypher = """
        MATCH (v:VIN {vin: $vin})-[:HAS_EVENT]->(e)
        WHERE e.occurredAt <= $at_ts
        RETURN e
        ORDER BY e.occurredAt DESC
        LIMIT 1
    """

    vin_exists = False
    snapshot_event: dict[str, Any] | None = None

    try:
        driver = await _neo4j_driver()
        async with driver:
            async with driver.session() as session:
                vin_check = await asyncio.wait_for(
                    session.run("MATCH (v:VIN {vin: $vin}) RETURN v LIMIT 1", vin=vin),
                    timeout=timeout,
                )
                vin_records = await vin_check.data()
                vin_exists = len(vin_records) > 0

                if vin_exists:
                    result = await asyncio.wait_for(
                        session.run(cypher, vin=vin, at_ts=at_ts),
                        timeout=timeout,
                    )
                    rows = await result.data()
                    if rows:
                        e = rows[0]["e"]
                        raw_params = e.get("parameters", "{}")
                        parsed_params = json.loads(raw_params) if isinstance(raw_params, str) else raw_params
                        snapshot_event = {
                            "eventType": e.get("eventType", ""),
                            "occurredAt": e.get("occurredAt", ""),
                            "stageNumber": e.get("stageNumber"),
                            "stageName": None,
                            "tunnelPositionMm": e.get("tunnelPositionMm"),
                            "parameters": parsed_params,
                        }
    except asyncio.TimeoutError as err:
        raise Neo4jUnavailableError() from err
    except Exception as err:
        logger.exception("Neo4j snapshot read failed for vin=%s", vin)
        raise Neo4jReadError() from err

    if not vin_exists:
        raise VinNotFoundError(vin)

    if snapshot_event is None:
        # No events at or before at_ts — data gap
        return ThreadSnapshotResponse(
            vin=vin,
            atTs=at_ts,
            stageName=None,
            stageNumber=None,
            tunnelPositionMm=None,
            parameters={},
            dataAvailable=False,
        )

    # Resolve stage name from config
    stage_number: int | None = snapshot_event.get("stageNumber")
    stage_name: str | None = None
    if stage_number is not None:
        for s in get_stage_config():
            if s["number"] == stage_number:
                stage_name = s["name"]
                break

    # data_available=False when atTs is ahead of latest event (data gap)
    data_available = snapshot_event is not None

    return ThreadSnapshotResponse(
        vin=vin,
        atTs=at_ts,
        stageName=stage_name,
        stageNumber=stage_number,
        tunnelPositionMm=snapshot_event.get("tunnelPositionMm"),
        parameters=snapshot_event.get("parameters", {}),
        dataAvailable=data_available,
    )


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread/completeness
# ---------------------------------------------------------------------------


async def get_thread_completeness(
    vin: str,
    pool: asyncpg.Pool,
) -> ThreadCompletenessResponse:
    from app.config import get_settings

    cfg = get_settings()

    # Check VIN exists in Neo4j first
    vin_exists = await _check_vin_exists(vin, cfg.neo4j_read_timeout_s)
    if not vin_exists:
        raise VinNotFoundError(vin)

    cached = await _get_completeness_cached(vin, pool)
    if cached is not None:
        return _build_completeness_response(vin, cached)

    # Cache miss — compute from Neo4j and UPSERT
    computed = await _compute_completeness_from_neo4j(vin, cfg.neo4j_read_timeout_s)
    await _upsert_completeness_cache(vin, computed, pool)
    return _build_completeness_response(vin, computed)


async def _check_vin_exists(vin: str, timeout: float) -> bool:
    try:
        driver = await _neo4j_driver()
        async with driver:
            async with driver.session() as session:
                result = await asyncio.wait_for(
                    session.run("MATCH (v:VIN {vin: $vin}) RETURN v LIMIT 1", vin=vin),
                    timeout=timeout,
                )
                records = await result.data()
                return len(records) > 0
    except asyncio.TimeoutError as err:
        raise Neo4jUnavailableError() from err
    except Exception as err:
        logger.exception("Neo4j vin-exists check failed for vin=%s", vin)
        raise Neo4jReadError() from err


async def _get_completeness_cached(vin: str, pool: asyncpg.Pool) -> dict[str, Any] | None:
    from app.config import get_settings

    cfg = get_settings()
    ttl = cfg.completeness_cache_ttl_s
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT overall_completeness_pct, meets_threshold, stage_completeness,
                       vin_status, updated_at
                FROM vin_thread_completeness_cache
                WHERE vin = $1
                  AND updated_at > NOW() - MAKE_INTERVAL(secs => $2)
                """,
                vin,
                int(ttl),
            )
        if row is None:
            return None
        stage_json = row["stage_completeness"]
        if isinstance(stage_json, str):
            stages = json.loads(stage_json)
        else:
            stages = stage_json if stage_json is not None else []
        return {
            "overall_completeness_pct": float(row["overall_completeness_pct"]),
            "meets_threshold": bool(row["meets_threshold"]),
            "stages": stages,
            "vin_status": str(row["vin_status"]),
        }
    except asyncpg.PostgresError:
        logger.exception("Failed to read completeness cache for vin=%s", vin)
        return None


async def _compute_completeness_from_neo4j(vin: str, timeout: float) -> dict[str, Any]:
    """Count PAINT_PREP_STAGE events per stage against expected sample counts."""
    cypher = """
        MATCH (v:VIN {vin: $vin})-[:HAS_EVENT]->(e)
        WHERE e.eventType = 'PAINT_PREP_STAGE' AND e.stageNumber IS NOT NULL
        RETURN e.stageNumber AS stageNumber, count(e) AS cnt
    """
    stage_counts: dict[int, int] = {}

    try:
        driver = await _neo4j_driver()
        async with driver:
            async with driver.session() as session:
                result = await asyncio.wait_for(
                    session.run(cypher, vin=vin),
                    timeout=timeout,
                )
                rows = await result.data()
                for row in rows:
                    sn = row["stageNumber"]
                    if sn is not None:
                        stage_counts[int(sn)] = int(row["cnt"])
    except asyncio.TimeoutError as err:
        raise Neo4jUnavailableError() from err
    except Exception as err:
        logger.exception("Neo4j completeness compute failed for vin=%s", vin)
        raise Neo4jReadError() from err

    stages_cfg = get_stage_config()
    stage_details: list[dict[str, Any]] = []
    total_expected = 0
    total_actual = 0

    for s in stages_cfg:
        num = s["number"]
        expected = s["expected_samples"]
        actual = stage_counts.get(num, 0)
        total_expected += expected
        total_actual += actual
        pct = min(100.0, (actual / expected * 100.0)) if expected > 0 else 0.0
        # Determine missing parameters by checking which param keys were never seen
        # (simplified: we flag all expected params as missing when actual==0)
        missing: list[str] = s["expected_parameters"] if actual == 0 else []
        stage_details.append({
            "stageNumber": num,
            "stageName": s["name"],
            "available": actual > 0,
            "completenessPercent": round(pct, 2),
            "expectedSampleCount": expected,
            "actualSampleCount": actual,
            "missingParameters": missing,
        })

    overall_pct = (
        min(100.0, total_actual / total_expected * 100.0) if total_expected > 0 else 0.0
    )

    return {
        "overall_completeness_pct": round(overall_pct, 2),
        "meets_threshold": overall_pct >= _COMPLETENESS_THRESHOLD,
        "stages": stage_details,
        "vin_status": "IN_PROGRESS",
    }


async def _upsert_completeness_cache(
    vin: str, data: dict[str, Any], pool: asyncpg.Pool
) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO vin_thread_completeness_cache
                    (vin, overall_completeness_pct, meets_threshold, stage_completeness, updated_at)
                VALUES ($1, $2, $3, $4::jsonb, NOW())
                ON CONFLICT (vin) DO UPDATE
                    SET overall_completeness_pct = EXCLUDED.overall_completeness_pct,
                        meets_threshold = EXCLUDED.meets_threshold,
                        stage_completeness = EXCLUDED.stage_completeness,
                        updated_at = EXCLUDED.updated_at
                """,
                vin,
                data["overall_completeness_pct"],
                data["meets_threshold"],
                json.dumps(data["stages"]),
            )
    except asyncpg.PostgresError:
        logger.exception("Failed to upsert completeness cache for vin=%s", vin)


def _build_completeness_response(vin: str, data: dict[str, Any]) -> ThreadCompletenessResponse:
    stages = [
        StageCompletenessDetail(
            stageNumber=s["stageNumber"],
            stageName=s["stageName"],
            available=s["available"],
            completenessPercent=s["completenessPercent"],
            expectedSampleCount=s["expectedSampleCount"],
            actualSampleCount=s["actualSampleCount"],
            missingParameters=s.get("missingParameters"),
        )
        for s in data["stages"]
    ]
    return ThreadCompletenessResponse(
        vin=vin,
        overallCompletenessPercent=data["overall_completeness_pct"],
        meetsThreshold=data["meets_threshold"],
        stages=stages,
    )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class VinNotFoundError(Exception):
    def __init__(self, vin: str) -> None:
        super().__init__(f"VIN not found: {vin!r}")
        self.vin = vin


class Neo4jUnavailableError(Exception):
    pass


class Neo4jReadError(Exception):
    pass
