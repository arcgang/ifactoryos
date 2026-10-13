/// POST /sequence/publish, GET /sequence/jobs, GET /sequence/jobs/{syncJobId}
use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    Json,
};
use chrono::Utc;
use serde::Deserialize;
use tracing::{error, info, instrument, warn};
use uuid::Uuid;

use crate::{
    auth::require_role,
    cursor::{parse_cursor, Cursor},
    db::{
        find_sync_job_by_idempotency_key, get_sync_job_by_id, has_inflight_sync_job,
        insert_sync_job, mark_sync_job_published, mark_sync_job_queued,
        mark_sync_job_rejected, query_sync_jobs,
    },
    kafka::{publish, TOPIC_SEQUENCE_PUBLISHED, TOPIC_SEQUENCE_PUBLISH_FAILED},
    models::{
        CursorPage, ErrorResponse, SequencePublishRequest, SequencePublishResponse,
        SequencePublishedEvent, SequencePublishFailedEvent, SyncJobListResponse, SyncJobResponse,
    },
    AppState,
};

static SEQUENCE_PUBLISH_ROLE: &str = "sequence-publish";

// ---------------------------------------------------------------------------
// Request validation helpers
// ---------------------------------------------------------------------------

fn is_valid_vin(vin: &str) -> bool {
    vin.len() == 17
        && vin.chars().all(|c| {
            matches!(c,
                'A'..='H' | 'J'..='N' | 'P'..='Z' | '0'..='9'
            )
        })
}

fn validate_publish_request(req: &SequencePublishRequest) -> Result<(), String> {
    if req.source != "planning-cockpit" {
        return Err("source must be 'planning-cockpit'".to_string());
    }
    if req.vin_sequence.is_empty() {
        return Err("vinSequence must not be empty".to_string());
    }
    for item in &req.vin_sequence {
        if !is_valid_vin(&item.vin) {
            return Err(format!(
                "invalid VIN '{}': must be 17 chars matching [A-HJ-NPR-Z0-9]",
                item.vin
            ));
        }
        if item.position < 1 {
            return Err(format!("position {} is invalid: must be >= 1", item.position));
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// POST /sequence/publish
// ---------------------------------------------------------------------------

#[instrument(skip(state, req), fields(sequence_plan_id = %req.sequence_plan_id))]
pub async fn publish_sequence(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
    Json(req): Json<SequencePublishRequest>,
) -> Result<
    (StatusCode, Json<SequencePublishResponse>),
    (StatusCode, Json<ErrorResponse>),
> {
    let correlation_id = Uuid::new_v4();

    // --- JWT auth: require sequence-publish role ---
    let token = extract_bearer(&headers).ok_or_else(|| {
        (
            StatusCode::UNAUTHORIZED,
            Json(ErrorResponse {
                code: "MISSING_TOKEN".to_string(),
                message: "Authorization header missing or malformed".to_string(),
                correlation_id,
            }),
        )
    })?;

    require_role(token, SEQUENCE_PUBLISH_ROLE, &state.settings).map_err(|e| {
        (
            StatusCode::FORBIDDEN,
            Json(ErrorResponse {
                code: "FORBIDDEN".to_string(),
                message: e.to_string(),
                correlation_id,
            }),
        )
    })?;

    // --- Request validation ---
    validate_publish_request(&req).map_err(|msg| {
        (
            StatusCode::BAD_REQUEST,
            Json(ErrorResponse {
                code: "INVALID_REQUEST".to_string(),
                message: msg,
                correlation_id,
            }),
        )
    })?;

    let client = state.pool.get().await.map_err(|e| {
        error!(error = %e, "db pool acquire failed");
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse {
                code: "DB_UNAVAILABLE".to_string(),
                message: "Database unavailable".to_string(),
                correlation_id,
            }),
        )
    })?;

    // --- Idempotency check ---
    if let Some(idem_key) = req.idempotency_key {
        if let Some(row) = find_sync_job_by_idempotency_key(&client, idem_key)
            .await
            .map_err(|e| {
                error!(error = %e, "idempotency lookup failed");
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(ErrorResponse {
                        code: "DB_ERROR".to_string(),
                        message: "Database error".to_string(),
                        correlation_id,
                    }),
                )
            })?
        {
            let cached = sync_job_row_to_publish_response(&row);
            return Ok((StatusCode::OK, Json(cached)));
        }
    }

    // --- Conflict check: in-flight job for same sequence_plan_id ---
    let in_flight = has_inflight_sync_job(&client, req.sequence_plan_id)
        .await
        .map_err(|e| {
            error!(error = %e, "conflict check failed");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(ErrorResponse {
                    code: "DB_ERROR".to_string(),
                    message: "Database error".to_string(),
                    correlation_id,
                }),
            )
        })?;

    if in_flight {
        return Err((
            StatusCode::CONFLICT,
            Json(ErrorResponse {
                code: "PUBLISH_IN_PROGRESS".to_string(),
                message: format!(
                    "A publish is already in progress for sequencePlanId {}",
                    req.sequence_plan_id
                ),
                correlation_id,
            }),
        ));
    }

    // --- Create sync_job row (initial status: queued; update on outcome) ---
    let sync_job_id = insert_sync_job(
        &client,
        req.sequence_plan_id,
        req.planner_user_id.as_deref(),
        "queued",
        req.idempotency_key,
    )
    .await
    .map_err(|e| {
        error!(error = %e, "insert sync_job failed");
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse {
                code: "DB_ERROR".to_string(),
                message: "Failed to create sync job".to_string(),
                correlation_id,
            }),
        )
    })?;

    info!(%sync_job_id, sequence_plan_id = %req.sequence_plan_id, "sync_job created");

    // --- Call OpCenter ---
    let opcenter_result = call_opcenter(&state, &req, correlation_id).await;

    match opcenter_result {
        Ok(opcenter_sequence_id) => {
            // Happy path: OpCenter accepted the sequence.
            let published_at = Utc::now();

            // Write to immutable audit log (fire-and-forget failure tolerance)
            let audit_event_id = write_audit_log(
                &state,
                sync_job_id,
                req.sequence_plan_id,
                req.planner_user_id.as_deref(),
                &opcenter_sequence_id,
                published_at,
                correlation_id,
            )
            .await
            .map_err(|e| warn!(error = %e, "audit log write failed — continuing"))
            .ok();

            // Update DB
            mark_sync_job_published(
                &client,
                sync_job_id,
                &opcenter_sequence_id,
                audit_event_id,
                published_at,
            )
            .await
            .unwrap_or_else(|e| warn!(error = %e, "mark_sync_job_published failed"));

            // Publish Kafka event
            let event = SequencePublishedEvent {
                event_id: Uuid::new_v4(),
                event_type: "opcenter.sequence.published".to_string(),
                sync_job_id,
                sequence_plan_id: req.sequence_plan_id,
                opcenter_sequence_id: opcenter_sequence_id.clone(),
                source: "planning-cockpit".to_string(),
                published_at: published_at.to_rfc3339(),
                correlation_id,
            };
            if let Err(e) = publish(
                &state.producer,
                TOPIC_SEQUENCE_PUBLISHED,
                &sync_job_id.to_string(),
                &event,
                correlation_id,
            )
            .await
            {
                warn!(error = %e, "sequence.published Kafka publish failed");
            }

            info!(%sync_job_id, %opcenter_sequence_id, "sequence published to OpCenter");

            Ok((
                StatusCode::ACCEPTED,
                Json(SequencePublishResponse {
                    sync_job_id,
                    opcenter_sequence_id: Some(opcenter_sequence_id),
                    status: "published".to_string(),
                    source: "planning-cockpit".to_string(),
                    published_at: Some(published_at.to_rfc3339()),
                    audit_event_id,
                    retry_scheduled_at: None,
                }),
            ))
        }

        Err(OpcenterError::Unavailable(msg)) => {
            // OpCenter unreachable or 5xx — queue for retry.
            let next_retry_at = Utc::now() + chrono::Duration::seconds(
                state.settings.retry_interval_secs as i64,
            );
            mark_sync_job_queued(&client, sync_job_id, next_retry_at, Some(&msg))
                .await
                .unwrap_or_else(|e| warn!(error = %e, "mark_sync_job_queued failed"));

            // Publish failure event
            publish_failed_event(
                &state,
                sync_job_id,
                req.sequence_plan_id,
                "queued",
                Some(&msg),
                Some(next_retry_at),
                correlation_id,
            )
            .await;

            info!(%sync_job_id, "OpCenter unavailable — sequence queued for retry");

            Ok((
                StatusCode::ACCEPTED,
                Json(SequencePublishResponse {
                    sync_job_id,
                    opcenter_sequence_id: None,
                    status: "queued".to_string(),
                    source: "planning-cockpit".to_string(),
                    published_at: None,
                    audit_event_id: None,
                    retry_scheduled_at: Some(next_retry_at.to_rfc3339()),
                }),
            ))
        }

        Err(OpcenterError::Rejected(msg)) => {
            // OpCenter 422 — rejected (e.g. unsigned/invalid VIN).
            mark_sync_job_rejected(&client, sync_job_id, &msg)
                .await
                .unwrap_or_else(|e| warn!(error = %e, "mark_sync_job_rejected failed"));

            // Audit log for rejection
            let _ = write_rejection_audit_log(
                &state,
                sync_job_id,
                req.sequence_plan_id,
                req.planner_user_id.as_deref(),
                &msg,
                correlation_id,
            )
            .await;

            publish_failed_event(
                &state,
                sync_job_id,
                req.sequence_plan_id,
                "rejected_by_opcenter",
                Some(&msg),
                None,
                correlation_id,
            )
            .await;

            error!(%sync_job_id, error = %msg, "OpCenter rejected the sequence");

            Err((
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(ErrorResponse {
                    code: "OPCENTER_REJECTED".to_string(),
                    message: msg,
                    correlation_id,
                }),
            ))
        }
    }
}

// ---------------------------------------------------------------------------
// GET /sequence/jobs
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct SyncJobListQuery {
    pub cursor: Option<String>,
    pub limit: Option<i64>,
    pub status: Option<String>,
}

const VALID_STATUSES: &[&str] = &[
    "published",
    "queued",
    "retrying",
    "failed",
    "rejected_by_opcenter",
];

pub async fn list_sync_jobs(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
    Query(params): Query<SyncJobListQuery>,
) -> Result<Json<SyncJobListResponse>, (StatusCode, Json<ErrorResponse>)> {
    let correlation_id = Uuid::new_v4();

    let token = extract_bearer(&headers).ok_or_else(|| {
        (
            StatusCode::UNAUTHORIZED,
            Json(ErrorResponse {
                code: "MISSING_TOKEN".to_string(),
                message: "Authorization header missing or malformed".to_string(),
                correlation_id,
            }),
        )
    })?;

    // Any authenticated caller may list jobs (no role restriction beyond valid JWT)
    require_role(token, SEQUENCE_PUBLISH_ROLE, &state.settings).map_err(|_| {
        (
            StatusCode::UNAUTHORIZED,
            Json(ErrorResponse {
                code: "INVALID_TOKEN".to_string(),
                message: "Invalid or expired token".to_string(),
                correlation_id,
            }),
        )
    })?;

    if let Some(s) = &params.status {
        if !VALID_STATUSES.contains(&s.as_str()) {
            return Err((
                StatusCode::BAD_REQUEST,
                Json(ErrorResponse {
                    code: "STATUS_INVALID".to_string(),
                    message: format!("invalid status '{s}'; expected one of: {}", VALID_STATUSES.join(", ")),
                    correlation_id,
                }),
            ));
        }
    }

    let limit = params.limit.unwrap_or(50).clamp(1, 200);
    let (cursor_ts, cursor_id) = parse_cursor(params.cursor.as_deref()).map_err(|_| {
        (
            StatusCode::BAD_REQUEST,
            Json(ErrorResponse {
                code: "CURSOR_INVALID".to_string(),
                message: "Invalid pagination cursor".to_string(),
                correlation_id,
            }),
        )
    })?;

    let client = state.pool.get().await.map_err(|e| {
        error!(error = %e, "db pool acquire failed");
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse {
                code: "DB_UNAVAILABLE".to_string(),
                message: "Database unavailable".to_string(),
                correlation_id,
            }),
        )
    })?;

    let rows = query_sync_jobs(
        &client,
        params.status.as_deref(),
        cursor_ts,
        cursor_id,
        limit + 1,
    )
    .await
    .map_err(|e| {
        error!(error = %e, "query_sync_jobs failed");
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse {
                code: "QUERY_FAILED".to_string(),
                message: "Failed to list sync jobs".to_string(),
                correlation_id,
            }),
        )
    })?;

    let has_more = rows.len() as i64 > limit;
    let page: Vec<_> = rows.into_iter().take(limit as usize).collect();

    let data: Vec<SyncJobResponse> = page.iter().map(row_to_sync_job_response).collect();

    let next_cursor = if has_more {
        page.last().map(|row| {
            let ts: chrono::DateTime<chrono::Utc> = row.get("created_at");
            let id: Uuid = row.get("sync_job_id");
            Cursor::encode(ts, id)
        })
    } else {
        None
    };

    Ok(Json(SyncJobListResponse {
        data,
        page: CursorPage {
            next_cursor,
            limit,
        },
    }))
}

// ---------------------------------------------------------------------------
// GET /sequence/jobs/{syncJobId}
// ---------------------------------------------------------------------------

pub async fn get_sync_job(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
    Path(sync_job_id): Path<Uuid>,
) -> Result<Json<SyncJobResponse>, (StatusCode, Json<ErrorResponse>)> {
    let correlation_id = Uuid::new_v4();

    let token = extract_bearer(&headers).ok_or_else(|| {
        (
            StatusCode::UNAUTHORIZED,
            Json(ErrorResponse {
                code: "MISSING_TOKEN".to_string(),
                message: "Authorization header missing or malformed".to_string(),
                correlation_id,
            }),
        )
    })?;

    require_role(token, SEQUENCE_PUBLISH_ROLE, &state.settings).map_err(|_| {
        (
            StatusCode::UNAUTHORIZED,
            Json(ErrorResponse {
                code: "INVALID_TOKEN".to_string(),
                message: "Invalid or expired token".to_string(),
                correlation_id,
            }),
        )
    })?;

    let client = state.pool.get().await.map_err(|e| {
        error!(error = %e, "db pool acquire failed");
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse {
                code: "DB_UNAVAILABLE".to_string(),
                message: "Database unavailable".to_string(),
                correlation_id,
            }),
        )
    })?;

    let row = get_sync_job_by_id(&client, sync_job_id)
        .await
        .map_err(|e| {
            error!(error = %e, "get_sync_job_by_id failed");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(ErrorResponse {
                    code: "QUERY_FAILED".to_string(),
                    message: "Failed to fetch sync job".to_string(),
                    correlation_id,
                }),
            )
        })?
        .ok_or_else(|| {
            (
                StatusCode::NOT_FOUND,
                Json(ErrorResponse {
                    code: "SYNC_JOB_NOT_FOUND".to_string(),
                    message: format!("Sync job {sync_job_id} not found"),
                    correlation_id,
                }),
            )
        })?;

    Ok(Json(row_to_sync_job_response(&row)))
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

fn extract_bearer(headers: &axum::http::HeaderMap) -> Option<&str> {
    headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
}

fn row_to_sync_job_response(row: &tokio_postgres::Row) -> SyncJobResponse {
    let created_at: chrono::DateTime<chrono::Utc> = row.get("created_at");
    let updated_at: chrono::DateTime<chrono::Utc> = row.get("updated_at");
    let last_attempt_at: Option<chrono::DateTime<chrono::Utc>> = row.get("last_attempt_at");
    let next_retry_at: Option<chrono::DateTime<chrono::Utc>> = row.get("next_retry_at");

    SyncJobResponse {
        sync_job_id: row.get("sync_job_id"),
        sequence_plan_id: row.get("sequence_plan_id"),
        planner_user_id: row.get("planner_user_id"),
        status: row.get("status"),
        source: row.try_get("source").ok(),
        opcenter_sequence_id: row.get("opcenter_sequence_id"),
        created_at: created_at.to_rfc3339(),
        updated_at: updated_at.to_rfc3339(),
        last_attempt_at: last_attempt_at.map(|t| t.to_rfc3339()),
        next_retry_at: next_retry_at.map(|t| t.to_rfc3339()),
        attempt_count: row.get("attempt_count"),
        opcenter_error: row.get("opcenter_error"),
    }
}

/// Convert a sync_jobs row to a SequencePublishResponse for idempotency replay.
fn sync_job_row_to_publish_response(row: &tokio_postgres::Row) -> SequencePublishResponse {
    let published_at: Option<chrono::DateTime<chrono::Utc>> = row.get("published_at");
    let next_retry_at: Option<chrono::DateTime<chrono::Utc>> = row.get("next_retry_at");

    SequencePublishResponse {
        sync_job_id: row.get("sync_job_id"),
        opcenter_sequence_id: row.get("opcenter_sequence_id"),
        status: row.get("status"),
        source: "planning-cockpit".to_string(),
        published_at: published_at.map(|t| t.to_rfc3339()),
        audit_event_id: row.get("audit_event_id"),
        retry_scheduled_at: next_retry_at.map(|t| t.to_rfc3339()),
    }
}

// ---------------------------------------------------------------------------
// OpCenter call
// ---------------------------------------------------------------------------

enum OpcenterError {
    Unavailable(String),
    Rejected(String),
}

async fn call_opcenter(
    state: &AppState,
    req: &SequencePublishRequest,
    correlation_id: Uuid,
) -> Result<String, OpcenterError> {
    let http = reqwest::Client::builder()
        .timeout(state.settings.http_timeout())
        .build()
        .map_err(|e| OpcenterError::Unavailable(e.to_string()))?;

    let url = format!("{}/opcenter/sequence", state.settings.opcenter_rest_url);

    let body = serde_json::json!({
        "sequencePlanId": req.sequence_plan_id,
        "vinSequence": req.vin_sequence,
        "source": req.source,
        "lockedAt": req.locked_at.to_rfc3339(),
        "plannerUserId": req.planner_user_id,
        "correlationId": correlation_id,
    });

    let response = http
        .post(&url)
        .header("X-API-Key", &state.settings.planning_cockpit_api_key)
        .header("X-Correlation-Id", correlation_id.to_string())
        .json(&body)
        .send()
        .await
        .map_err(|e| OpcenterError::Unavailable(e.to_string()))?;

    let status = response.status();

    if status.is_success() {
        let json: serde_json::Value = response
            .json()
            .await
            .map_err(|e| OpcenterError::Unavailable(format!("parse response: {e}")))?;
        let seq_id = json["sequenceId"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        Ok(seq_id)
    } else if status.as_u16() == 422 {
        let text = response
            .text()
            .await
            .unwrap_or_else(|_| "OpCenter rejected the sequence".to_string());
        Err(OpcenterError::Rejected(text))
    } else {
        let text = response
            .text()
            .await
            .unwrap_or_else(|_| format!("OpCenter returned HTTP {status}"));
        Err(OpcenterError::Unavailable(text))
    }
}

// ---------------------------------------------------------------------------
// Audit log helpers
// ---------------------------------------------------------------------------

async fn write_audit_log(
    state: &AppState,
    sync_job_id: Uuid,
    sequence_plan_id: Uuid,
    planner_user_id: Option<&str>,
    opcenter_sequence_id: &str,
    published_at: chrono::DateTime<chrono::Utc>,
    correlation_id: Uuid,
) -> anyhow::Result<Uuid> {
    let http = reqwest::Client::builder()
        .timeout(state.settings.http_timeout())
        .build()?;

    let payload = serde_json::json!({
        "action": "sequence_published",
        "source": "planning-cockpit",
        "syncJobId": sync_job_id,
        "sequencePlanId": sequence_plan_id,
        "plannerUserId": planner_user_id,
        "opcenterSequenceId": opcenter_sequence_id,
        "publishedAt": published_at.to_rfc3339(),
        "correlationId": correlation_id,
    });

    let resp = http
        .post(&state.settings.audit_log_url)
        .header("X-Correlation-Id", correlation_id.to_string())
        .json(&payload)
        .send()
        .await?;

    let body: serde_json::Value = resp.json().await?;
    let audit_event_id = body["auditEventId"]
        .as_str()
        .and_then(|s| Uuid::parse_str(s).ok())
        .ok_or_else(|| anyhow::anyhow!("audit log response missing auditEventId"))?;

    Ok(audit_event_id)
}

async fn write_rejection_audit_log(
    state: &AppState,
    sync_job_id: Uuid,
    sequence_plan_id: Uuid,
    planner_user_id: Option<&str>,
    opcenter_error: &str,
    correlation_id: Uuid,
) -> anyhow::Result<()> {
    let http = reqwest::Client::builder()
        .timeout(state.settings.http_timeout())
        .build()?;

    let payload = serde_json::json!({
        "action": "sequence_rejected_by_opcenter",
        "source": "planning-cockpit",
        "syncJobId": sync_job_id,
        "sequencePlanId": sequence_plan_id,
        "plannerUserId": planner_user_id,
        "opcenterError": opcenter_error,
        "correlationId": correlation_id,
    });

    http.post(&state.settings.audit_log_url)
        .header("X-Correlation-Id", correlation_id.to_string())
        .json(&payload)
        .send()
        .await?;

    Ok(())
}

async fn publish_failed_event(
    state: &AppState,
    sync_job_id: Uuid,
    sequence_plan_id: Uuid,
    status: &str,
    opcenter_error: Option<&str>,
    retry_scheduled_at: Option<chrono::DateTime<chrono::Utc>>,
    correlation_id: Uuid,
) {
    let event = SequencePublishFailedEvent {
        event_id: Uuid::new_v4(),
        event_type: "opcenter.sequence.publish_failed".to_string(),
        sync_job_id,
        sequence_plan_id,
        status: status.to_string(),
        opcenter_error: opcenter_error.map(|s| s.to_string()),
        retry_scheduled_at: retry_scheduled_at.map(|t| t.to_rfc3339()),
        correlation_id,
    };

    if let Err(e) = publish(
        &state.producer,
        TOPIC_SEQUENCE_PUBLISH_FAILED,
        &sync_job_id.to_string(),
        &event,
        correlation_id,
    )
    .await
    {
        warn!(error = %e, "sequence.publish_failed Kafka publish failed");
    }
}
