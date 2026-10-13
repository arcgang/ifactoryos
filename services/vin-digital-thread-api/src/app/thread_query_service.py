"""Business logic for GET /vin/{vin}/thread and GET /vin/{vin}/thread/completeness.

Read queries route to Neo4j read replicas via bolt+routing. Completeness data is
cached in vin_thread_completeness_cache (PG) with a 30-second TTL; on cache miss
the per-stage counts are re-queried from Neo4j and the cache is refreshed.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import asyncpg
import yaml

from app.models import (
    CursorPage,
    EventType,
    StageCompletenessDetail,
    ThreadCompletenessResponse,
    ThreadEvent,
    VinStatus,
    VinThreadResponse,
)

logger = logging.getLogger(__name__)

_COMPLETENESS_THRESHOLD = 95.0
_SHOP_ORDER = ["BODY", "PAINT", "ASSEMBLY", "FINAL"]

# Shops that represent distinct manufacturing stages — crossings between these
# emit synthetic SHOP_BOUNDARY_TRANSITION events if missing from the thread.
_KNOWN_SHOPS: frozenset[str] = frozenset({"BODY", "PAINT", "ASSEMBLY", "FINAL"})


# ---------------------------------------------------------------------------
# Paint prep stage config
# ---------------------------------------------------------------------------

def _load_stage_config(config_path: str) -> list[dict[str, Any]]:
    """Load and return the list of stage definitions from the YAML config file."""
    resolved = config_path if os.path.isabs(config_path) else os.path.join(
        os.path.dirname(__file__), "..", "..", "..", config_path
    )
    with open(resolved, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data["stages"]


_stage_config_cache: list[dict[str, Any]] | None = None


def get_stage_config() -> list[dict[str, Any]]:
    global _stage_config_cache
    if _stage_config_cache is None:
        from app.config import get_settings
        cfg = get_settings()
        _stage_config_cache = _load_stage_config(cfg.paint_prep_stage_config_path)
    return _stage_config_cache


# ---------------------------------------------------------------------------
# Cursor encoding
# ---------------------------------------------------------------------------

def _encode_cursor(occurred_at: str, event_id: str) -> str:
    token = json.dumps({"oa": occurred_at, "id": event_id})
    return base64.urlsafe_b64encode(token.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    """Return (occurredAt, eventId) from an opaque cursor string."""
    try:
        token = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return token["oa"], token["id"]
    except (KeyError, ValueError, TypeError, UnicodeDecodeError) as err:
        raise CursorDecodeError(f"Invalid pagination cursor: {cursor!r}") from err


class CursorDecodeError(ValueError):
    pass


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread
# ---------------------------------------------------------------------------

async def query_vin_thread(
    vin: str,
    *,
    cursor: str | None,
    limit: int,
    shop_filter: list[str] | None,
    from_ts: str | None,
    to_ts: str | None,
) -> VinThreadResponse:
    """Execute Cypher query and return paginated VinThreadResponse."""
    from neo4j import AsyncGraphDatabase
    from app.config import get_settings

    cfg = get_settings()

    cursor_ts: str | None = None
    cursor_id: str | None = None
    if cursor:
        cursor_ts, cursor_id = _decode_cursor(cursor)

    cypher = _build_thread_cypher(
        has_cursor=cursor is not None,
        has_shop_filter=bool(shop_filter),
        has_from_ts=from_ts is not None,
        has_to_ts=to_ts is not None,
    )

    params: dict[str, Any] = {
        "vin": vin,
        "limit": limit + 1,  # fetch one extra to detect next page
    }
    if cursor_ts is not None:
        params["cursor_ts"] = cursor_ts
        params["cursor_id"] = cursor_id
    if shop_filter:
        params["shops"] = shop_filter
    if from_ts:
        params["from_ts"] = from_ts
    if to_ts:
        params["to_ts"] = to_ts

    async with AsyncGraphDatabase.driver(
        cfg.neo4j_uri,
        auth=(cfg.neo4j_user, cfg.neo4j_password),
    ) as driver:
        async with driver.session() as session:
            result = await session.run(cypher, **params)
            records = await result.data()

        # Check VIN exists — if no events come back and no VIN node, raise 404.
        if not records:
            async with driver.session() as session:
                vin_check = await session.run(
                    "MATCH (v:VIN {vin: $vin}) RETURN v.vin AS vin LIMIT 1",
                    vin=vin,
                )
                vin_rows = await vin_check.data()
            if not vin_rows:
                raise VinNotFoundError(vin)

        # Determine next cursor.
        has_more = len(records) > limit
        page_records = records[:limit]

        next_cursor: str | None = None
        if has_more and page_records:
            last = page_records[-1]
            next_cursor = _encode_cursor(last["occurredAt"], last["eventId"])

        events = [_record_to_thread_event(vin, r) for r in page_records]

        # Inject synthetic SHOP_BOUNDARY_TRANSITION events at shop crossings.
        events = _inject_shop_boundary_transitions(events)

        # Determine VIN status from the event list.
        vin_status = _derive_vin_status(events)

    return VinThreadResponse(
        vin=vin,
        data=events,
        page=CursorPage(next_cursor=next_cursor, limit=limit),
        vinStatus=vin_status,
    )


def _build_thread_cypher(
    *,
    has_cursor: bool,
    has_shop_filter: bool,
    has_from_ts: bool,
    has_to_ts: bool,
) -> str:
    match_clause = "MATCH (v:VIN {vin: $vin})-[:HAS_EVENT]->(e:ManufacturingEvent)"
    where_parts: list[str] = []

    if has_cursor:
        where_parts.append(
            "(e.occurredAt > $cursor_ts OR (e.occurredAt = $cursor_ts AND e.eventId > $cursor_id))"
        )
    if has_shop_filter:
        where_parts.append("e.shop IN $shops")
    if has_from_ts:
        where_parts.append("e.occurredAt >= $from_ts")
    if has_to_ts:
        where_parts.append("e.occurredAt <= $to_ts")

    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    return (
        f"{match_clause} {where_clause} "
        "RETURN e.eventId AS eventId, e.eventType AS eventType, e.shop AS shop, "
        "e.station AS station, e.occurredAt AS occurredAt, e.dataSource AS dataSource, "
        "e.parameters AS parameters, e.linkedAt AS createdAt, "
        "e.stageNumber AS stageNumber, e.tunnelPositionMm AS tunnelPositionMm "
        "ORDER BY e.occurredAt ASC, e.eventId ASC "
        "LIMIT $limit"
    )


def _record_to_thread_event(vin: str, record: dict[str, Any]) -> ThreadEvent:
    raw_params = record.get("parameters") or "{}"
    if isinstance(raw_params, str):
        try:
            params = json.loads(raw_params)
        except (ValueError, TypeError):
            params = {}
    else:
        params = raw_params

    return ThreadEvent(
        eventId=record["eventId"],
        vin=vin,
        eventType=EventType(record["eventType"]),
        shop=record.get("shop", ""),
        station=record.get("station", ""),
        occurredAt=record.get("occurredAt", ""),
        dataSource=record.get("dataSource", ""),
        parameters=params,
        createdAt=record.get("createdAt"),
        stageNumber=record.get("stageNumber"),
        tunnelPositionMm=record.get("tunnelPositionMm"),
    )


def _inject_shop_boundary_transitions(events: list[ThreadEvent]) -> list[ThreadEvent]:
    """Insert synthetic SHOP_BOUNDARY_TRANSITION events where shop crossings lack one."""
    if not events:
        return events

    result: list[ThreadEvent] = []
    prev_shop: str | None = None

    for event in events:
        current_shop = event.shop
        if (
            prev_shop is not None
            and current_shop != prev_shop
            and current_shop in _KNOWN_SHOPS
            and prev_shop in _KNOWN_SHOPS
            and event.eventType != EventType.SHOP_BOUNDARY_TRANSITION
        ):
            # Check whether the immediately preceding real event was already a transition.
            if not result or result[-1].eventType != EventType.SHOP_BOUNDARY_TRANSITION:
                synthetic = ThreadEvent(
                    eventId=f"synthetic-{prev_shop}-{current_shop}-{event.occurredAt}",
                    vin=event.vin,
                    eventType=EventType.SHOP_BOUNDARY_TRANSITION,
                    shop=current_shop,
                    station="BOUNDARY",
                    occurredAt=event.occurredAt,
                    dataSource="SYSTEM_INFERRED",
                    parameters={
                        "from_shop": prev_shop,
                        "to_shop": current_shop,
                    },
                    createdAt=event.occurredAt,
                )
                result.append(synthetic)
        result.append(event)
        if event.eventType != EventType.SHOP_BOUNDARY_TRANSITION:
            prev_shop = current_shop

    return result


def _derive_vin_status(events: list[ThreadEvent]) -> VinStatus:
    """Return FINAL_GATE_COMPLETE if any QUALITY_CERTIFICATION event exists; else IN_PROGRESS."""
    for event in events:
        if event.eventType == EventType.QUALITY_CERTIFICATION:
            return VinStatus.FINAL_GATE_COMPLETE
    return VinStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# GET /vin/{vin}/thread/completeness
# ---------------------------------------------------------------------------

async def query_vin_completeness(
    vin: str,
    pool: asyncpg.Pool,
) -> ThreadCompletenessResponse:
    """Return completeness metrics, using PG cache when fresh enough."""
    from app.config import get_settings
    cfg = get_settings()

    # Try cache first.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT overall_completeness_pct, meets_threshold, stage_completeness,
                   vin_status, updated_at
            FROM vin_thread_completeness_cache
            WHERE vin = $1
            """,
            vin,
        )

    if row is not None:
        updated_at = row["updated_at"]
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_s = (datetime.now(tz=timezone.utc) - updated_at).total_seconds()
        if age_s <= cfg.completeness_cache_ttl_s:
            return _build_completeness_response(vin, row)

    # Cache miss or stale — query Neo4j for per-stage actual counts.
    stage_counts = await _query_stage_counts_from_neo4j(vin)

    # Compute completeness from config + actual counts.
    stages_config = get_stage_config()
    overall_pct, stage_details = _compute_completeness(stages_config, stage_counts)
    meets = overall_pct >= _COMPLETENESS_THRESHOLD

    # Upsert cache.
    stage_json = json.dumps([s.model_dump() for s in stage_details])
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO vin_thread_completeness_cache
                (vin, overall_completeness_pct, meets_threshold, stage_completeness, updated_at)
            VALUES ($1, $2, $3, $4::jsonb, now())
            ON CONFLICT (vin) DO UPDATE
                SET overall_completeness_pct = EXCLUDED.overall_completeness_pct,
                    meets_threshold          = EXCLUDED.meets_threshold,
                    stage_completeness       = EXCLUDED.stage_completeness,
                    updated_at               = now()
            """,
            vin,
            overall_pct,
            meets,
            stage_json,
        )

    return ThreadCompletenessResponse(
        vin=vin,
        overallCompletenessPercent=overall_pct,
        meetsThreshold=meets,
        stages=stage_details,
    )


def _build_completeness_response(vin: str, row: Any) -> ThreadCompletenessResponse:
    """Reconstruct ThreadCompletenessResponse from a PG cache row."""
    raw = row["stage_completeness"]
    if isinstance(raw, str):
        stage_list = json.loads(raw)
    else:
        stage_list = raw or []

    stages = [StageCompletenessDetail(**s) for s in stage_list]
    overall_pct = float(row["overall_completeness_pct"])
    meets = bool(row["meets_threshold"])

    return ThreadCompletenessResponse(
        vin=vin,
        overallCompletenessPercent=overall_pct,
        meetsThreshold=meets,
        stages=stages,
    )


async def _query_stage_counts_from_neo4j(vin: str) -> dict[int, dict[str, Any]]:
    """Return {stageNumber: {actualSampleCount, presentParameters}} from Neo4j."""
    from neo4j import AsyncGraphDatabase
    from app.config import get_settings

    cfg = get_settings()
    async with AsyncGraphDatabase.driver(
        cfg.neo4j_uri,
        auth=(cfg.neo4j_user, cfg.neo4j_password),
    ) as driver:
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (v:VIN {vin: $vin})-[:HAS_EVENT]->(e:ManufacturingEvent)
                WHERE e.eventType = 'PAINT_PREP_STAGE' AND e.stageNumber IS NOT NULL
                RETURN e.stageNumber AS stageNumber,
                       count(e) AS sampleCount,
                       collect(e.parameters) AS parametersList
                """,
                vin=vin,
            )
            rows = await result.data()

    stage_counts: dict[int, dict[str, Any]] = {}
    for row in rows:
        stage_num = int(row["stageNumber"])
        params_list = row.get("parametersList") or []
        present_keys: set[str] = set()
        for raw_params in params_list:
            if isinstance(raw_params, str):
                try:
                    p = json.loads(raw_params)
                except (ValueError, TypeError):
                    p = {}
            else:
                p = raw_params or {}
            present_keys.update(p.keys())

        stage_counts[stage_num] = {
            "actualSampleCount": int(row["sampleCount"]),
            "presentParameters": present_keys,
        }

    return stage_counts


def _compute_completeness(
    stages_config: list[dict[str, Any]],
    stage_counts: dict[int, dict[str, Any]],
) -> tuple[float, list[StageCompletenessDetail]]:
    """Return (overallCompletenessPercent, stage_details_list)."""
    total_expected = 0
    total_actual = 0
    stage_details: list[StageCompletenessDetail] = []

    for stage_cfg in stages_config:
        stage_num = int(stage_cfg["stageNumber"])
        expected_count = int(stage_cfg["expectedSampleCount"])
        expected_params: list[str] = stage_cfg.get("expectedParameters", [])

        actual_data = stage_counts.get(stage_num, {})
        actual_count = int(actual_data.get("actualSampleCount", 0))
        present_params: set[str] = actual_data.get("presentParameters", set())

        missing_params = [p for p in expected_params if p not in present_params]
        available = actual_count > 0

        if expected_count > 0:
            stage_pct = min(100.0, (actual_count / expected_count) * 100.0)
        else:
            stage_pct = 100.0

        total_expected += expected_count
        total_actual += min(actual_count, expected_count)

        stage_details.append(
            StageCompletenessDetail(
                stageNumber=stage_num,
                stageName=stage_cfg["stageName"],
                available=available,
                completenessPercent=round(stage_pct, 2),
                expectedSampleCount=expected_count,
                actualSampleCount=actual_count,
                missingParameters=missing_params if missing_params else None,
            )
        )

    overall_pct = (
        round((total_actual / total_expected) * 100.0, 2)
        if total_expected > 0
        else 0.0
    )
    return overall_pct, stage_details


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class VinNotFoundError(Exception):
    def __init__(self, vin: str) -> None:
        super().__init__(f"VIN not found in digital thread graph: {vin!r}")
        self.vin = vin
