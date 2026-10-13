"""Business logic for cluster query, cluster list/get, and corrective action endpoints."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timezone

import asyncpg

from app.advisor_v1 import evaluate_advisor_v1
from app.cluster_state import get_recent_events, run_dbscan, score_cluster_xgboost
from app.models import (
    ClusterListResponse,
    ClusterQueryRequest,
    ClusterQueryResult,
    ClusterStatus,
    ConfidenceThresholdConfig,
    CorrectiveActionRequest,
    CorrectiveActionResult,
    CursorPage,
    DefectClass,
    DefectCluster,
    ModelFeedbackAck,
    ModelFeedbackRequest,
    RootCauseFactor,
)

logger = logging.getLogger(__name__)

_MODEL_VERSION = "1.0.0"

# In-process idempotency cache: idempotency_key → (ClusterQueryResult, expiry_epoch_s)
_idempotency_cache: dict[str, tuple[ClusterQueryResult, float]] = {}

# Confidence threshold cache: (config_dict, expiry_epoch_s) | None
_threshold_cache: tuple[dict, float] | None = None


def _now_utc_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _epoch_now() -> float:
    import time
    return time.monotonic()


async def get_confidence_config(pool: asyncpg.Pool) -> ConfidenceThresholdConfig:
    global _threshold_cache
    from app.config import get_settings

    cfg = get_settings()
    now = _epoch_now()
    if _threshold_cache is not None:
        cached_cfg, expiry = _threshold_cache
        if now < expiry:
            return ConfidenceThresholdConfig(**cached_cfg)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT low_confidence_threshold, cluster_alert_threshold,
                   cluster_alert_min_vins, cluster_alert_window_hours,
                   updated_at
            FROM confidence_threshold_config
            WHERE singleton_guard = 'singleton'
            """
        )

    if row is None:
        config_dict = {
            "low_confidence_threshold": 0.60,
            "cluster_alert_threshold": 0.80,
            "cluster_alert_min_vins": 3,
            "cluster_alert_window_hours": 4,
            "updated_at": None,
        }
    else:
        config_dict = {
            "low_confidence_threshold": float(row["low_confidence_threshold"]),
            "cluster_alert_threshold": float(row["cluster_alert_threshold"]),
            "cluster_alert_min_vins": int(row["cluster_alert_min_vins"]),
            "cluster_alert_window_hours": int(row["cluster_alert_window_hours"]),
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }

    _threshold_cache = (config_dict, now + cfg.confidence_cache_ttl_s)
    return ConfidenceThresholdConfig(**config_dict)


async def run_cluster_query(
    request: ClusterQueryRequest,
    pool: asyncpg.Pool,
    caller_id: str,
) -> ClusterQueryResult:
    from app.config import get_settings
    from app.kafka_producer import publish

    cfg = get_settings()
    now = _epoch_now()

    # Idempotency check (in-process cache + DB fallback)
    if request.idempotency_key is not None:
        key_str = str(request.idempotency_key)
        if key_str in _idempotency_cache:
            cached_result, expiry = _idempotency_cache[key_str]
            if now < expiry:
                return cached_result
        # Check DB
        cached = await _load_cached_query(pool, request.idempotency_key, cfg.idempotency_cache_ttl_s)
        if cached is not None:
            return cached

    conf_cfg = await get_confidence_config(pool)

    # Run DBSCAN on rolling window
    recent_events = get_recent_events(
        request.defect_class.value,
        request.time_window_hours,
        request.vin_filter,
    )
    clusters = run_dbscan(recent_events)

    # Pick largest cluster for this query
    if clusters:
        signature, cluster_events = max(clusters.items(), key=lambda kv: len(kv[1]))
    else:
        cluster_events = []
        signature = f"no_cluster:{request.defect_class.value}"

    top_factors_raw, model_confidence = score_cluster_xgboost(
        cluster_events, request.defect_class.value, _MODEL_VERSION
    )

    vins = list({e.get("vin", "") for e in cluster_events if e.get("vin")})

    # Ensure at least 1 factor is present
    if not top_factors_raw:
        top_factors_raw = [{
            "rank": 1,
            "factor_name": "insufficient_data",
            "factor_category": "booth_parameter",
            "probability_score": 0.0,
            "supporting_vin_count": 0,
        }]

    top_factors = [RootCauseFactor(**f) for f in top_factors_raw]

    low_confidence = model_confidence < conf_cfg.low_confidence_threshold

    advisor_fallback = None
    if low_confidence:
        # Use most representative event for advisor v1 context
        sample_event = cluster_events[0] if cluster_events else {}
        advisor_fallback = evaluate_advisor_v1(request.defect_class, sample_event)

    cluster_id = uuid.uuid5(
        uuid.NAMESPACE_DNS,
        f"{request.defect_class.value}:{signature}",
    )
    query_id = uuid.uuid4()
    queried_at = _now_utc_iso()

    result = ClusterQueryResult(
        query_id=query_id,
        cluster_id=cluster_id,
        queried_at=queried_at,
        defect_class=request.defect_class,
        model_confidence=model_confidence,
        low_confidence=low_confidence,
        top_factors=top_factors,
        affected_vin_count=len(vins),
        affected_vins=vins,
        model_version=_MODEL_VERSION,
        advisor_v1_fallback=advisor_fallback,
    )

    # Ensure the parent defect_cluster row exists before inserting the query result (FK)
    most_recent_vin = vins[-1] if vins else "UNKNOWN"
    process_replay_url = f"{cfg.quality_studio_base_url}/process-replay/{most_recent_vin}"
    await _upsert_defect_cluster_for_query(
        pool=pool,
        cluster_id=cluster_id,
        defect_class=request.defect_class.value,
        confidence_score=model_confidence,
        affected_vin_count=len(vins),
        root_cause_signature=signature,
        most_recent_vin=most_recent_vin,
        process_replay_url=process_replay_url,
        top_factors_json=json.dumps([f.model_dump(mode="json") for f in top_factors]),
        detected_at=queried_at,
    )

    # Persist to DB
    await _persist_cluster_query_result(pool, result, caller_id, request.idempotency_key)

    # Cache idempotency
    if request.idempotency_key is not None:
        key_str = str(request.idempotency_key)
        _idempotency_cache[key_str] = (result, now + cfg.idempotency_cache_ttl_s)

    # Publish event (fire-and-forget)
    event_payload = {
        "queryId": str(query_id),
        "clusterId": str(cluster_id),
        "defectClass": request.defect_class.value,
        "modelConfidence": model_confidence,
        "lowConfidence": low_confidence,
        "topFactors": [f.model_dump(mode="json") for f in top_factors],
        "affectedVinCount": len(vins),
        "queriedAt": queried_at,
        "modelVersion": _MODEL_VERSION,
        "callerId": caller_id,
    }
    asyncio.create_task(publish(cfg.cluster_query_result_topic, event_payload))

    return result


async def _upsert_defect_cluster_for_query(
    pool: asyncpg.Pool,
    cluster_id: uuid.UUID,
    defect_class: str,
    confidence_score: float,
    affected_vin_count: int,
    root_cause_signature: str,
    most_recent_vin: str,
    process_replay_url: str,
    top_factors_json: str,
    detected_at: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO defect_cluster (
                cluster_id, defect_class, status, root_cause_signature,
                confidence_score, affected_vin_count, most_recent_vin,
                detected_at, process_replay_url, top_factors, model_version,
                created_at, updated_at, data_region
            ) VALUES (
                $1::uuid, $2, 'open', $3, $4, $5, $6,
                $7::timestamptz, $8, $9::jsonb, $10,
                now(), now(), 'eu-de-west-central'
            )
            ON CONFLICT (cluster_id) DO UPDATE SET
                confidence_score = EXCLUDED.confidence_score,
                affected_vin_count = EXCLUDED.affected_vin_count,
                most_recent_vin = EXCLUDED.most_recent_vin,
                top_factors = EXCLUDED.top_factors,
                updated_at = now()
            """,
            str(cluster_id),
            defect_class,
            root_cause_signature,
            confidence_score,
            affected_vin_count,
            most_recent_vin,
            detected_at,
            process_replay_url,
            top_factors_json,
            _MODEL_VERSION,
        )


async def _load_cached_query(
    pool: asyncpg.Pool,
    idempotency_key: uuid.UUID,
    ttl_s: int,
) -> ClusterQueryResult | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT query_id, cluster_id, defect_class, model_confidence,
                   low_confidence, top_factors, affected_vin_count,
                   affected_vins, model_version, advisor_v1_fallback, queried_at
            FROM cluster_query_result
            WHERE idempotency_key = $1
              AND queried_at > now() - ($2 * interval '1 second')
            ORDER BY queried_at DESC
            LIMIT 1
            """,
            str(idempotency_key),
            ttl_s,
        )
    if row is None:
        return None
    return _row_to_cluster_query_result(row)


def _row_to_cluster_query_result(row: dict) -> ClusterQueryResult:
    from app.advisor_v1 import AdvisorV1Result as _AV1

    top_factors_raw = json.loads(row["top_factors"]) if isinstance(row["top_factors"], str) else row["top_factors"]
    top_factors = [RootCauseFactor(**f) for f in (top_factors_raw or [])]

    advisor_raw = row.get("advisor_v1_fallback")
    advisor = None
    if advisor_raw:
        raw = json.loads(advisor_raw) if isinstance(advisor_raw, str) else advisor_raw
        advisor = _AV1(**raw) if raw else None

    affected_vins_raw = row.get("affected_vins")
    affected_vins = None
    if affected_vins_raw:
        affected_vins = json.loads(affected_vins_raw) if isinstance(affected_vins_raw, str) else affected_vins_raw

    return ClusterQueryResult(
        query_id=row["query_id"],
        cluster_id=row["cluster_id"],
        queried_at=row["queried_at"].isoformat() if hasattr(row["queried_at"], "isoformat") else str(row["queried_at"]),
        defect_class=DefectClass(row["defect_class"]),
        model_confidence=float(row["model_confidence"]),
        low_confidence=bool(row["low_confidence"]),
        top_factors=top_factors,
        affected_vin_count=int(row["affected_vin_count"]),
        affected_vins=affected_vins,
        model_version=row.get("model_version"),
        advisor_v1_fallback=advisor,
    )


async def _persist_cluster_query_result(
    pool: asyncpg.Pool,
    result: ClusterQueryResult,
    caller_id: str,
    idempotency_key: uuid.UUID | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO cluster_query_result (
                query_id, cluster_id, defect_class, model_confidence,
                low_confidence, top_factors, affected_vin_count,
                affected_vins, model_version, advisor_v1_fallback,
                idempotency_key, queried_at, caller_id, data_region
            ) VALUES (
                $1::uuid, $2::uuid, $3, $4, $5,
                $6::jsonb, $7, $8::jsonb, $9, $10::jsonb,
                $11, $12::timestamptz, $13, 'eu-de-west-central'
            )
            ON CONFLICT DO NOTHING
            """,
            str(result.query_id),
            str(result.cluster_id),
            result.defect_class.value,
            result.model_confidence,
            result.low_confidence,
            json.dumps([f.model_dump(mode="json") for f in result.top_factors]),
            result.affected_vin_count,
            json.dumps(result.affected_vins) if result.affected_vins else json.dumps([]),
            result.model_version,
            json.dumps(result.advisor_v1_fallback.model_dump(mode="json")) if result.advisor_v1_fallback else None,
            str(idempotency_key) if idempotency_key else None,
            result.queried_at,
            caller_id,
        )


def _encode_cursor(created_at: str, cluster_id: str) -> str:
    raw = json.dumps({"ca": created_at, "id": cluster_id})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, str] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        obj = json.loads(raw)
        return obj["ca"], obj["id"]
    except Exception:  # noqa: BLE001
        return None


async def list_clusters(
    pool: asyncpg.Pool,
    defect_class: DefectClass | None,
    status: ClusterStatus | None,
    cursor: str | None,
    limit: int,
) -> ClusterListResponse:
    conditions = ["1=1"]
    params: list = []
    idx = 1

    if defect_class is not None:
        conditions.append(f"defect_class = ${idx}")
        params.append(defect_class.value)
        idx += 1

    if status is not None:
        conditions.append(f"status = ${idx}")
        params.append(status.value)
        idx += 1

    if cursor is not None:
        decoded = _decode_cursor(cursor)
        if decoded:
            ca, cid = decoded
            conditions.append(f"(created_at, cluster_id::text) < (${idx}::timestamptz, ${idx + 1})")
            params.append(ca)
            params.append(cid)
            idx += 2

    where_clause = " AND ".join(conditions)
    params.append(limit + 1)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT cluster_id, defect_class, status, root_cause_signature,
                   confidence_score, affected_vin_count, most_recent_vin,
                   detected_at, created_at, updated_at, top_factors,
                   process_replay_url, corrective_action_jira_id
            FROM defect_cluster
            WHERE {where_clause}
            ORDER BY created_at DESC, cluster_id DESC
            LIMIT ${idx}
            """,
            *params,
        )

    has_more = len(rows) > limit
    page_rows = rows[:limit]

    clusters = [_row_to_defect_cluster(r) for r in page_rows]

    next_cursor = None
    if has_more and clusters:
        last = clusters[-1]
        next_cursor = _encode_cursor(last.created_at, str(last.cluster_id))

    return ClusterListResponse(
        data=clusters,
        page=CursorPage(next_cursor=next_cursor, limit=limit),
    )


async def get_cluster(pool: asyncpg.Pool, cluster_id: uuid.UUID) -> DefectCluster | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT cluster_id, defect_class, status, root_cause_signature,
                   confidence_score, affected_vin_count, most_recent_vin,
                   detected_at, created_at, updated_at, top_factors,
                   process_replay_url, corrective_action_jira_id
            FROM defect_cluster
            WHERE cluster_id = $1::uuid
            """,
            str(cluster_id),
        )
    if row is None:
        return None
    return _row_to_defect_cluster(row)


def _row_to_defect_cluster(row: dict) -> DefectCluster:
    top_factors_raw = row.get("top_factors")
    top_factors = None
    if top_factors_raw:
        raw = json.loads(top_factors_raw) if isinstance(top_factors_raw, str) else top_factors_raw
        if raw:
            top_factors = [RootCauseFactor(**f) for f in raw]

    def _iso(v: object) -> str:
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)

    return DefectCluster(
        cluster_id=row["cluster_id"],
        defect_class=DefectClass(row["defect_class"]),
        status=ClusterStatus(row["status"]),
        root_cause_signature=row["root_cause_signature"],
        confidence_score=float(row["confidence_score"]),
        affected_vin_count=int(row["affected_vin_count"]),
        most_recent_vin=row["most_recent_vin"],
        detected_at=_iso(row["detected_at"]),
        created_at=_iso(row["created_at"]),
        updated_at=_iso(row["updated_at"]),
        top_factors=top_factors,
        process_replay_url=row.get("process_replay_url"),
        corrective_action_jira_id=row.get("corrective_action_jira_id"),
    )


async def create_corrective_action(
    cluster_id: uuid.UUID,
    request: CorrectiveActionRequest,
    pool: asyncpg.Pool,
    caller_id: str,
) -> CorrectiveActionResult:
    from app.config import get_settings
    from app.kafka_producer import publish

    cfg = get_settings()

    # Load cluster — 404 if not found
    cluster = await get_cluster(pool, cluster_id)
    if cluster is None:
        from fastapi import HTTPException, status as http_status
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CLUSTER_NOT_FOUND",
                "message": f"Cluster {cluster_id} not found",
                "correlation_id": str(uuid.uuid4()),
            },
        )

    # 409 if already has a Jira ticket
    if cluster.corrective_action_jira_id is not None:
        from fastapi import HTTPException, status as http_status
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail={
                "code": "CORRECTIVE_ACTION_EXISTS",
                "message": f"Corrective action already exists for cluster {cluster_id}: {cluster.corrective_action_jira_id}",
                "correlation_id": str(uuid.uuid4()),
            },
        )

    # Resolve the assigned factor
    assigned_factor = None
    if cluster.top_factors:
        for f in cluster.top_factors:
            if f.rank == request.assigned_root_cause_factor_rank:
                assigned_factor = f
                break

    # Build Jira issue payload
    factor_desc = (
        f"Factor rank {request.assigned_root_cause_factor_rank}: "
        f"{assigned_factor.factor_name} (probability={assigned_factor.probability_score:.2%})"
        if assigned_factor
        else f"Factor rank {request.assigned_root_cause_factor_rank}"
    )
    summary = f"DefectGenome corrective action – {cluster.defect_class.value} cluster {cluster_id}"
    description = (
        f"*Cluster ID:* {cluster_id}\n"
        f"*Defect class:* {cluster.defect_class.value}\n"
        f"*Root-cause probability:* {factor_desc}\n"
        f"*Affected VINs:* {cluster.affected_vin_count}\n"
        f"*Root-cause signature:* {cluster.root_cause_signature}\n"
        f"*Confidence score:* {cluster.confidence_score:.2%}\n"
        f"*Recommended prep stage adjustment:* see DefectGenome root-cause analysis\n"
    )
    if request.additional_notes:
        description += f"\n*Engineer notes:* {request.additional_notes}\n"
    description += f"\n_Confirmed by: {request.confirmed_by_user_id}_"

    jira_payload = {
        "fields": {
            "summary": summary,
            "description": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": description}]}
            ]},
            "issuetype": {"name": "Task"},
            "project": {"key": "DEFECTGENOME"},
        }
    }

    # Call Jira REST API v3
    jira_ticket_id, jira_ticket_url = await _call_jira_create_issue(cfg, jira_payload)

    created_at = _now_utc_iso()

    # Atomically store the jira_ticket_id on the cluster
    async with pool.acquire() as conn:
        async with conn.transaction():
            updated = await conn.fetchval(
                """
                UPDATE defect_cluster
                SET corrective_action_jira_id = $1,
                    status = 'in_progress',
                    updated_at = now()
                WHERE cluster_id = $2::uuid
                  AND corrective_action_jira_id IS NULL
                RETURNING cluster_id
                """,
                jira_ticket_id,
                str(cluster_id),
            )
            if updated is None:
                # Race: another request already created a ticket
                current_row = await conn.fetchrow(
                    "SELECT corrective_action_jira_id FROM defect_cluster WHERE cluster_id = $1::uuid",
                    str(cluster_id),
                )
                existing_id = current_row["corrective_action_jira_id"] if current_row else jira_ticket_id
                from fastapi import HTTPException, status as http_status
                raise HTTPException(
                    status_code=http_status.HTTP_409_CONFLICT,
                    detail={
                        "code": "CORRECTIVE_ACTION_EXISTS",
                        "message": f"Corrective action already exists: {existing_id}",
                        "correlation_id": str(uuid.uuid4()),
                    },
                )

    # Publish event
    event_payload = {
        "clusterId": str(cluster_id),
        "defectClass": cluster.defect_class.value,
        "jiraTicketId": jira_ticket_id,
        "jiraTicketUrl": jira_ticket_url,
        "assignedRootCauseFactorRank": request.assigned_root_cause_factor_rank,
        "confirmedByUserId": request.confirmed_by_user_id,
        "createdAt": created_at,
        "callerId": caller_id,
    }
    asyncio.create_task(publish(cfg.corrective_action_topic, event_payload))

    return CorrectiveActionResult(
        jira_ticket_id=jira_ticket_id,
        jira_ticket_url=jira_ticket_url,
        cluster_id=cluster_id,
        created_at=created_at,
        prepopulated_summary=summary,
    )


async def _call_jira_create_issue(cfg: object, payload: dict) -> tuple[str, str]:
    """POST to Jira REST API v3 using OAuth 2.0 client credentials from Vault."""
    import httpx

    from app.config import Settings

    assert isinstance(cfg, Settings)

    # Obtain access token
    token = await _get_jira_token(cfg)

    async with httpx.AsyncClient(timeout=cfg.jira_timeout_s) as client:
        resp = await client.post(
            f"{cfg.jira_base_url}/rest/api/3/issue",
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    ticket_id: str = data["key"]
    ticket_url = f"{cfg.jira_base_url}/browse/{ticket_id}"
    return ticket_id, ticket_url


_jira_token_cache: tuple[str, float] | None = None


async def _get_jira_token(cfg: object) -> str:
    global _jira_token_cache
    import time

    import httpx

    from app.config import Settings

    assert isinstance(cfg, Settings)

    now = time.monotonic()
    if _jira_token_cache is not None:
        token, expiry = _jira_token_cache
        if now < expiry:
            return token

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            cfg.jira_token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": cfg.jira_client_id,
                "client_secret": cfg.jira_client_secret,
                "scope": "write:jira-work read:jira-work",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    access_token: str = data["access_token"]
    expires_in: int = data.get("expires_in", 3600)
    _jira_token_cache = (access_token, now + expires_in - 60)
    return access_token


async def submit_model_feedback(
    request: ModelFeedbackRequest,
    pool: asyncpg.Pool,
    caller_id: str,
) -> ModelFeedbackAck:
    feedback_id = uuid.uuid4()
    accepted_at = _now_utc_iso()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO model_feedback (
                feedback_id, cluster_id, query_id, engineer_user_id,
                accepted_factor_rank, defect_class, notes, override_root_cause,
                caller_id, accepted_at, data_region
            ) VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8,
                $9, $10::timestamptz, 'eu-de-west-central'
            )
            """,
            str(feedback_id),
            str(request.cluster_id),
            str(request.query_id),
            request.engineer_user_id,
            request.accepted_factor_rank,
            request.defect_class.value,
            request.notes,
            request.override_root_cause,
            caller_id,
            accepted_at,
        )

    return ModelFeedbackAck(feedback_id=feedback_id, accepted_at=accepted_at)
