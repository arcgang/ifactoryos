use deadpool_postgres::{Config as PoolConfig, Pool, Runtime};
use tokio_postgres::NoTls;

use crate::config::Settings;

pub type DbPool = Pool;

/// Create a shared connection pool. Called once at startup.
pub fn create_pool(settings: &Settings) -> anyhow::Result<DbPool> {
    let mut cfg = PoolConfig::new();
    cfg.url = Some(settings.database_url.clone());
    cfg.pool = Some(deadpool_postgres::PoolConfig {
        max_size: settings.db_max_connections as usize,
        ..Default::default()
    });
    let pool = cfg.create_pool(Some(Runtime::Tokio1), NoTls)?;
    Ok(pool)
}

/// Run all pending migrations at startup (idempotent — uses IF NOT EXISTS throughout).
pub async fn run_migrations(pool: &DbPool) -> anyhow::Result<()> {
    let client = pool.get().await?;
    let migrations: &[&str] = &[
        include_str!("../migrations/20261012_001_oee_snapshot.sql"),
        include_str!("../migrations/20261012_002_andon_alert.sql"),
        include_str!("../migrations/20261012_003_sync_jobs.sql"),
    ];
    for sql in migrations {
        client.batch_execute(sql).await?;
    }
    Ok(())
}

/// Insert an OEE snapshot row, skipping duplicates on opcenter_event_id.
/// Returns the generated oee_snapshot_id on insert; None when the row was deduped.
pub async fn insert_oee_snapshot(
    client: &deadpool_postgres::Client,
    line_id: &str,
    oee_percent: rust_decimal::Decimal,
    availability: rust_decimal::Decimal,
    performance: rust_decimal::Decimal,
    quality: rust_decimal::Decimal,
    measured_at: chrono::DateTime<chrono::Utc>,
    opcenter_event_id: Option<&str>,
    correlation_id: uuid::Uuid,
) -> anyhow::Result<Option<uuid::Uuid>> {
    let row = client
        .query_opt(
            "INSERT INTO oee_snapshot \
                (line_id, oee_percent, availability, performance, quality, \
                 measured_at, opcenter_event_id, correlation_id) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8) \
             ON CONFLICT (opcenter_event_id) WHERE opcenter_event_id IS NOT NULL \
             DO NOTHING \
             RETURNING oee_snapshot_id",
            &[
                &line_id,
                &oee_percent,
                &availability,
                &performance,
                &quality,
                &measured_at,
                &opcenter_event_id,
                &correlation_id,
            ],
        )
        .await?;
    Ok(row.map(|r| r.get(0)))
}

/// Insert an Andon alert row, skipping duplicates on opcenter_event_id.
/// Returns the generated alert_id on insert; None when the row was deduped.
pub async fn insert_andon_alert(
    client: &deadpool_postgres::Client,
    opcenter_event_id: Option<&str>,
    station_id: &str,
    line_id: &str,
    alert_type: &str,
    predicted_failure_type: &str,
    minutes_to_predicted_failure: Option<i32>,
    recommended_action: &str,
    detected_at: chrono::DateTime<chrono::Utc>,
    correlation_id: uuid::Uuid,
) -> anyhow::Result<Option<uuid::Uuid>> {
    let row = client
        .query_opt(
            "INSERT INTO andon_alert \
                (opcenter_event_id, station_id, line_id, alert_type, \
                 predicted_failure_type, minutes_to_predicted_failure, \
                 recommended_action, status, detected_at, correlation_id) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, 'open', $8, $9) \
             ON CONFLICT (opcenter_event_id) WHERE opcenter_event_id IS NOT NULL \
             DO NOTHING \
             RETURNING alert_id",
            &[
                &opcenter_event_id,
                &station_id,
                &line_id,
                &alert_type,
                &predicted_failure_type,
                &minutes_to_predicted_failure,
                &recommended_action,
                &detected_at,
                &correlation_id,
            ],
        )
        .await?;
    Ok(row.map(|r| r.get(0)))
}

/// Update kafka_offset on an oee_snapshot row after successful Kafka produce.
pub async fn set_oee_kafka_offset(
    client: &deadpool_postgres::Client,
    oee_snapshot_id: uuid::Uuid,
    offset: i64,
) -> anyhow::Result<()> {
    client
        .execute(
            "UPDATE oee_snapshot SET kafka_offset = $1, updated_at = NOW() \
             WHERE oee_snapshot_id = $2",
            &[&offset, &oee_snapshot_id],
        )
        .await?;
    Ok(())
}

/// Update kafka_offset on an andon_alert row after successful Kafka produce.
pub async fn set_andon_kafka_offset(
    client: &deadpool_postgres::Client,
    alert_id: uuid::Uuid,
    offset: i64,
) -> anyhow::Result<()> {
    client
        .execute(
            "UPDATE andon_alert SET kafka_offset = $1, updated_at = NOW() \
             WHERE alert_id = $2",
            &[&offset, &alert_id],
        )
        .await?;
    Ok(())
}

/// Query OEE snapshots — one per line (latest measured_at), keyset-paginated.
/// All filters are passed as bound parameters (no interpolation).
pub async fn query_latest_oee_snapshots(
    client: &deadpool_postgres::Client,
    line_id_filter: Option<&str>,
    max_age_seconds: Option<i32>,
    after_cursor_ts: Option<chrono::DateTime<chrono::Utc>>,
    after_cursor_id: Option<uuid::Uuid>,
    limit: i64,
) -> anyhow::Result<Vec<tokio_postgres::Row>> {
    let sentinel_ts = chrono::DateTime::<chrono::Utc>::MAX_UTC;
    let sentinel_id = uuid::Uuid::max();
    let ts = after_cursor_ts.unwrap_or(sentinel_ts);
    let id = after_cursor_id.unwrap_or(sentinel_id);

    let rows = match (line_id_filter, max_age_seconds) {
        (Some(lid), Some(max_age)) => {
            let max_age_i64 = max_age as i64;
            client
                .query(
                    "SELECT oee_snapshot_id, line_id, oee_percent, availability, \
                            performance, quality, measured_at, received_at, \
                            opcenter_event_id, kafka_offset, correlation_id, \
                            created_at, updated_at \
                     FROM ( \
                         SELECT DISTINCT ON (line_id) \
                                oee_snapshot_id, line_id, oee_percent, availability, \
                                performance, quality, measured_at, received_at, \
                                opcenter_event_id, kafka_offset, correlation_id, \
                                created_at, updated_at \
                         FROM oee_snapshot \
                         WHERE line_id = $1 \
                           AND received_at >= NOW() - ($2 * INTERVAL '1 second') \
                         ORDER BY line_id, measured_at DESC \
                     ) sub \
                     WHERE (created_at, oee_snapshot_id) < ($3, $4) \
                     ORDER BY created_at DESC, oee_snapshot_id DESC \
                     LIMIT $5",
                    &[&lid, &max_age_i64, &ts, &id, &limit],
                )
                .await?
        }
        (Some(lid), None) => {
            client
                .query(
                    "SELECT oee_snapshot_id, line_id, oee_percent, availability, \
                            performance, quality, measured_at, received_at, \
                            opcenter_event_id, kafka_offset, correlation_id, \
                            created_at, updated_at \
                     FROM ( \
                         SELECT DISTINCT ON (line_id) \
                                oee_snapshot_id, line_id, oee_percent, availability, \
                                performance, quality, measured_at, received_at, \
                                opcenter_event_id, kafka_offset, correlation_id, \
                                created_at, updated_at \
                         FROM oee_snapshot \
                         WHERE line_id = $1 \
                         ORDER BY line_id, measured_at DESC \
                     ) sub \
                     WHERE (created_at, oee_snapshot_id) < ($2, $3) \
                     ORDER BY created_at DESC, oee_snapshot_id DESC \
                     LIMIT $4",
                    &[&lid, &ts, &id, &limit],
                )
                .await?
        }
        (None, Some(max_age)) => {
            let max_age_i64 = max_age as i64;
            client
                .query(
                    "SELECT oee_snapshot_id, line_id, oee_percent, availability, \
                            performance, quality, measured_at, received_at, \
                            opcenter_event_id, kafka_offset, correlation_id, \
                            created_at, updated_at \
                     FROM ( \
                         SELECT DISTINCT ON (line_id) \
                                oee_snapshot_id, line_id, oee_percent, availability, \
                                performance, quality, measured_at, received_at, \
                                opcenter_event_id, kafka_offset, correlation_id, \
                                created_at, updated_at \
                         FROM oee_snapshot \
                         WHERE received_at >= NOW() - ($1 * INTERVAL '1 second') \
                         ORDER BY line_id, measured_at DESC \
                     ) sub \
                     WHERE (created_at, oee_snapshot_id) < ($2, $3) \
                     ORDER BY created_at DESC, oee_snapshot_id DESC \
                     LIMIT $4",
                    &[&max_age_i64, &ts, &id, &limit],
                )
                .await?
        }
        (None, None) => {
            client
                .query(
                    "SELECT oee_snapshot_id, line_id, oee_percent, availability, \
                            performance, quality, measured_at, received_at, \
                            opcenter_event_id, kafka_offset, correlation_id, \
                            created_at, updated_at \
                     FROM ( \
                         SELECT DISTINCT ON (line_id) \
                                oee_snapshot_id, line_id, oee_percent, availability, \
                                performance, quality, measured_at, received_at, \
                                opcenter_event_id, kafka_offset, correlation_id, \
                                created_at, updated_at \
                         FROM oee_snapshot \
                         ORDER BY line_id, measured_at DESC \
                     ) sub \
                     WHERE (created_at, oee_snapshot_id) < ($1, $2) \
                     ORDER BY created_at DESC, oee_snapshot_id DESC \
                     LIMIT $3",
                    &[&ts, &id, &limit],
                )
                .await?
        }
    };
    Ok(rows)
}

/// Query Andon alerts with optional filters, keyset-paginated by detected_at DESC.
/// All filter values are bound parameters.
pub async fn query_andon_alerts(
    client: &deadpool_postgres::Client,
    status_filter: Option<&str>,
    line_id_filter: Option<&str>,
    station_id_filter: Option<&str>,
    after_cursor_ts: Option<chrono::DateTime<chrono::Utc>>,
    after_cursor_id: Option<uuid::Uuid>,
    limit: i64,
) -> anyhow::Result<Vec<tokio_postgres::Row>> {
    let sentinel_ts = chrono::DateTime::<chrono::Utc>::MAX_UTC;
    let sentinel_id = uuid::Uuid::max();
    let ts = after_cursor_ts.unwrap_or(sentinel_ts);
    let id = after_cursor_id.unwrap_or(sentinel_id);

    let rows = client
        .query(
            "SELECT alert_id, opcenter_event_id, station_id, line_id, alert_type, \
                    predicted_failure_type, minutes_to_predicted_failure, \
                    recommended_action, status, detected_at, received_at, \
                    acknowledged_at, resolved_at, kafka_offset, \
                    correlation_id, created_at, updated_at \
             FROM andon_alert \
             WHERE ($1::TEXT IS NULL OR status = $1) \
               AND ($2::TEXT IS NULL OR line_id = $2) \
               AND ($3::TEXT IS NULL OR station_id = $3) \
               AND (detected_at, alert_id) < ($4, $5) \
             ORDER BY detected_at DESC, alert_id DESC \
             LIMIT $6",
            &[
                &status_filter,
                &line_id_filter,
                &station_id_filter,
                &ts,
                &id,
                &limit,
            ],
        )
        .await?;
    Ok(rows)
}

// ---------------------------------------------------------------------------
// sync_jobs — sequence publish tracking (US-004)
// ---------------------------------------------------------------------------

/// Insert a new sync_job row. Returns the generated sync_job_id.
pub async fn insert_sync_job(
    client: &deadpool_postgres::Client,
    sequence_plan_id: uuid::Uuid,
    planner_user_id: Option<&str>,
    status: &str,
    idempotency_key: Option<uuid::Uuid>,
) -> anyhow::Result<uuid::Uuid> {
    let row = client
        .query_one(
            "INSERT INTO sync_jobs \
                (sequence_plan_id, planner_user_id, status, idempotency_key) \
             VALUES ($1, $2, $3, $4) \
             RETURNING sync_job_id",
            &[&sequence_plan_id, &planner_user_id, &status, &idempotency_key],
        )
        .await?;
    Ok(row.get(0))
}

/// Look up an existing sync_job by idempotency_key. Returns the row if found.
pub async fn find_sync_job_by_idempotency_key(
    client: &deadpool_postgres::Client,
    idempotency_key: uuid::Uuid,
) -> anyhow::Result<Option<tokio_postgres::Row>> {
    let row = client
        .query_opt(
            "SELECT sync_job_id, sequence_plan_id, planner_user_id, status, source, \
                    opcenter_sequence_id, created_at, updated_at, last_attempt_at, \
                    next_retry_at, attempt_count, opcenter_error, published_at, audit_event_id \
             FROM sync_jobs \
             WHERE idempotency_key = $1",
            &[&idempotency_key],
        )
        .await?;
    Ok(row)
}

/// Check for an in-flight job for the same sequence_plan_id.
/// Returns true when a job with status 'queued' or 'retrying' already exists.
pub async fn has_inflight_sync_job(
    client: &deadpool_postgres::Client,
    sequence_plan_id: uuid::Uuid,
) -> anyhow::Result<bool> {
    let row = client
        .query_one(
            "SELECT COUNT(*) FROM sync_jobs \
             WHERE sequence_plan_id = $1 \
               AND status IN ('queued', 'retrying')",
            &[&sequence_plan_id],
        )
        .await?;
    let count: i64 = row.get(0);
    Ok(count > 0)
}

/// Update a sync_job to status=published after OpCenter confirms.
pub async fn mark_sync_job_published(
    client: &deadpool_postgres::Client,
    sync_job_id: uuid::Uuid,
    opcenter_sequence_id: &str,
    audit_event_id: Option<uuid::Uuid>,
    published_at: chrono::DateTime<chrono::Utc>,
) -> anyhow::Result<()> {
    client
        .execute(
            "UPDATE sync_jobs \
             SET status = 'published', \
                 opcenter_sequence_id = $2, \
                 audit_event_id = $3, \
                 published_at = $4, \
                 updated_at = NOW() \
             WHERE sync_job_id = $1",
            &[&sync_job_id, &opcenter_sequence_id, &audit_event_id, &published_at],
        )
        .await?;
    Ok(())
}

/// Update a sync_job to status=queued with a scheduled retry time.
pub async fn mark_sync_job_queued(
    client: &deadpool_postgres::Client,
    sync_job_id: uuid::Uuid,
    next_retry_at: chrono::DateTime<chrono::Utc>,
    opcenter_error: Option<&str>,
) -> anyhow::Result<()> {
    client
        .execute(
            "UPDATE sync_jobs \
             SET status = 'queued', \
                 next_retry_at = $2, \
                 opcenter_error = $3, \
                 last_attempt_at = NOW(), \
                 updated_at = NOW() \
             WHERE sync_job_id = $1",
            &[&sync_job_id, &next_retry_at, &opcenter_error],
        )
        .await?;
    Ok(())
}

/// Update a sync_job to status=rejected_by_opcenter (terminal).
pub async fn mark_sync_job_rejected(
    client: &deadpool_postgres::Client,
    sync_job_id: uuid::Uuid,
    opcenter_error: &str,
) -> anyhow::Result<()> {
    client
        .execute(
            "UPDATE sync_jobs \
             SET status = 'rejected_by_opcenter', \
                 opcenter_error = $2, \
                 last_attempt_at = NOW(), \
                 updated_at = NOW() \
             WHERE sync_job_id = $1",
            &[&sync_job_id, &opcenter_error],
        )
        .await?;
    Ok(())
}

/// Atomically pick up retry-eligible jobs, set status=retrying, return them.
/// Uses a CTE with FOR UPDATE SKIP LOCKED to avoid worker thundering-herd.
pub async fn claim_retry_jobs(
    client: &deadpool_postgres::Client,
    now: chrono::DateTime<chrono::Utc>,
    batch_size: i64,
) -> anyhow::Result<Vec<tokio_postgres::Row>> {
    let rows = client
        .query(
            "WITH candidates AS ( \
                 SELECT sync_job_id FROM sync_jobs \
                 WHERE status IN ('queued', 'retrying') \
                   AND next_retry_at <= $1 \
                 ORDER BY next_retry_at \
                 LIMIT $2 \
                 FOR UPDATE SKIP LOCKED \
             ) \
             UPDATE sync_jobs \
             SET status = 'retrying', \
                 attempt_count = attempt_count + 1, \
                 last_attempt_at = NOW(), \
                 updated_at = NOW() \
             FROM candidates \
             WHERE sync_jobs.sync_job_id = candidates.sync_job_id \
             RETURNING sync_jobs.sync_job_id, sync_jobs.sequence_plan_id, \
                       sync_jobs.planner_user_id, sync_jobs.attempt_count, \
                       sync_jobs.idempotency_key",
            &[&now, &batch_size],
        )
        .await?;
    Ok(rows)
}

/// Fetch a single sync_job by ID.
pub async fn get_sync_job_by_id(
    client: &deadpool_postgres::Client,
    sync_job_id: uuid::Uuid,
) -> anyhow::Result<Option<tokio_postgres::Row>> {
    let row = client
        .query_opt(
            "SELECT sync_job_id, sequence_plan_id, planner_user_id, status, source, \
                    opcenter_sequence_id, created_at, updated_at, last_attempt_at, \
                    next_retry_at, attempt_count, opcenter_error, published_at, audit_event_id \
             FROM sync_jobs \
             WHERE sync_job_id = $1",
            &[&sync_job_id],
        )
        .await?;
    Ok(row)
}

/// List sync_jobs with optional status filter, keyset-paginated by created_at DESC.
pub async fn query_sync_jobs(
    client: &deadpool_postgres::Client,
    status_filter: Option<&str>,
    after_cursor_ts: Option<chrono::DateTime<chrono::Utc>>,
    after_cursor_id: Option<uuid::Uuid>,
    limit: i64,
) -> anyhow::Result<Vec<tokio_postgres::Row>> {
    let sentinel_ts = chrono::DateTime::<chrono::Utc>::MAX_UTC;
    let sentinel_id = uuid::Uuid::max();
    let ts = after_cursor_ts.unwrap_or(sentinel_ts);
    let id = after_cursor_id.unwrap_or(sentinel_id);

    let rows = client
        .query(
            "SELECT sync_job_id, sequence_plan_id, planner_user_id, status, source, \
                    opcenter_sequence_id, created_at, updated_at, last_attempt_at, \
                    next_retry_at, attempt_count, opcenter_error, published_at, audit_event_id \
             FROM sync_jobs \
             WHERE ($1::TEXT IS NULL OR status = $1) \
               AND (created_at, sync_job_id) < ($2, $3) \
             ORDER BY created_at DESC, sync_job_id DESC \
             LIMIT $4",
            &[&status_filter, &ts, &id, &limit],
        )
        .await?;
    Ok(rows)
}
