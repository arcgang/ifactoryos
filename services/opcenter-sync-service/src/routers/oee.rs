use axum::{
    extract::{Query, State},
    http::StatusCode,
    Json,
};
use chrono::Utc;
use rust_decimal::prelude::ToPrimitive;
use serde::Deserialize;
use tracing::error;
use uuid::Uuid;

use crate::{
    cursor::{parse_cursor, Cursor},
    db::query_latest_oee_snapshots,
    models::{CursorPage, ErrorResponse, OeeSnapshotListResponse, OeeSnapshotResponse},
    AppState,
};

#[derive(Debug, Deserialize)]
pub struct OeeQuery {
    pub cursor: Option<String>,
    pub limit: Option<i64>,
    #[serde(rename = "lineId")]
    pub line_id: Option<String>,
    #[serde(rename = "maxAgeSeconds")]
    pub max_age_seconds: Option<i32>,
}

pub async fn list_oee_snapshots(
    State(state): State<AppState>,
    Query(params): Query<OeeQuery>,
) -> Result<Json<OeeSnapshotListResponse>, (StatusCode, Json<ErrorResponse>)> {
    let limit = params.limit.unwrap_or(50).clamp(1, 200);

    let (cursor_ts, cursor_id) = parse_cursor(params.cursor.as_deref()).map_err(|_| {
        (
            StatusCode::BAD_REQUEST,
            Json(ErrorResponse {
                code: "CURSOR_INVALID".to_string(),
                message: "Invalid pagination cursor".to_string(),
                correlation_id: Uuid::new_v4(),
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
                correlation_id: Uuid::new_v4(),
            }),
        )
    })?;

    // Fetch limit+1 rows to determine whether a next page exists.
    let rows = query_latest_oee_snapshots(
        &client,
        params.line_id.as_deref(),
        params.max_age_seconds,
        cursor_ts,
        cursor_id,
        limit + 1,
    )
    .await
    .map_err(|e| {
        error!(error = %e, "query oee_snapshot failed");
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse {
                code: "QUERY_FAILED".to_string(),
                message: "Failed to query OEE snapshots".to_string(),
                correlation_id: Uuid::new_v4(),
            }),
        )
    })?;

    let has_more = rows.len() as i64 > limit;
    let page: Vec<_> = rows.into_iter().take(limit as usize).collect();

    let now = Utc::now();
    let items: Vec<OeeSnapshotResponse> = page
        .iter()
        .map(|row| {
            let measured_at: chrono::DateTime<Utc> = row.get("measured_at");
            let data_age = (now - measured_at).num_seconds().max(0);
            let oee: rust_decimal::Decimal = row.get("oee_percent");
            let avail: rust_decimal::Decimal = row.get("availability");
            let perf: rust_decimal::Decimal = row.get("performance");
            let qual: rust_decimal::Decimal = row.get("quality");
            OeeSnapshotResponse {
                line_id: row.get("line_id"),
                oee_percent: oee.to_f64().unwrap_or(0.0),
                availability: avail.to_f64().unwrap_or(0.0),
                performance: perf.to_f64().unwrap_or(0.0),
                quality: qual.to_f64().unwrap_or(0.0),
                measured_at: measured_at.to_rfc3339(),
                data_age_seconds: data_age,
            }
        })
        .collect();

    let next_cursor = if has_more {
        page.last().map(|row| {
            let ts: chrono::DateTime<Utc> = row.get("created_at");
            let id: Uuid = row.get("oee_snapshot_id");
            Cursor::encode(ts, id)
        })
    } else {
        None
    };

    Ok(Json(OeeSnapshotListResponse {
        items,
        pagination: CursorPage {
            next_cursor,
            limit,
        },
    }))
}
