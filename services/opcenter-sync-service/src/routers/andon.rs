use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    Json,
};
use serde::Deserialize;
use tracing::error;
use uuid::Uuid;

use crate::{
    cursor::{parse_cursor, Cursor},
    db::query_andon_alerts,
    models::{AndonAlertListResponse, AndonAlertResponse, CursorPage, ErrorResponse},
    AppState,
};

#[derive(Debug, Deserialize)]
pub struct AndonQuery {
    pub cursor: Option<String>,
    pub limit: Option<i64>,
    pub status: Option<String>,
    #[serde(rename = "lineId")]
    pub line_id: Option<String>,
    #[serde(rename = "stationId")]
    pub station_id: Option<String>,
}

pub async fn list_andon_alerts(
    State(state): State<AppState>,
    Query(params): Query<AndonQuery>,
) -> Result<Json<AndonAlertListResponse>, (StatusCode, Json<ErrorResponse>)> {
    let limit = params.limit.unwrap_or(50).clamp(1, 200);

    if let Some(s) = &params.status {
        if !["open", "acknowledged", "resolved", "false_positive"].contains(&s.as_str()) {
            return Err((
                StatusCode::BAD_REQUEST,
                Json(ErrorResponse {
                    code: "STATUS_INVALID".to_string(),
                    message: format!("Invalid status value: {s}"),
                    correlation_id: Uuid::new_v4(),
                }),
            ));
        }
    }

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

    let rows = query_andon_alerts(
        &client,
        params.status.as_deref(),
        params.line_id.as_deref(),
        params.station_id.as_deref(),
        cursor_ts,
        cursor_id,
        limit + 1,
    )
    .await
    .map_err(|e| {
        error!(error = %e, "query andon_alert failed");
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse {
                code: "QUERY_FAILED".to_string(),
                message: "Failed to query Andon alerts".to_string(),
                correlation_id: Uuid::new_v4(),
            }),
        )
    })?;

    let has_more = rows.len() as i64 > limit;
    let page: Vec<_> = rows.into_iter().take(limit as usize).collect();

    let items: Vec<AndonAlertResponse> = page
        .iter()
        .map(|row| {
            let detected_at: chrono::DateTime<chrono::Utc> = row.get("detected_at");
            let resolved_at: Option<chrono::DateTime<chrono::Utc>> = row.get("resolved_at");
            let acknowledged_at: Option<chrono::DateTime<chrono::Utc>> =
                row.get("acknowledged_at");
            AndonAlertResponse {
                alert_id: row.get("alert_id"),
                station_id: row.get("station_id"),
                line_id: row.get("line_id"),
                alert_type: row.get("alert_type"),
                predicted_failure_type: row.get("predicted_failure_type"),
                minutes_to_predicted_failure: row.get("minutes_to_predicted_failure"),
                recommended_action: row.get("recommended_action"),
                status: row.get("status"),
                detected_at: detected_at.to_rfc3339(),
                resolved_at: resolved_at.map(|t| t.to_rfc3339()),
                acknowledged_at: acknowledged_at.map(|t| t.to_rfc3339()),
                opcenter_event_id: row.get("opcenter_event_id"),
            }
        })
        .collect();

    let next_cursor = if has_more {
        page.last().map(|row| {
            let ts: chrono::DateTime<chrono::Utc> = row.get("detected_at");
            let id: Uuid = row.get("alert_id");
            Cursor::encode(ts, id)
        })
    } else {
        None
    };

    Ok(Json(AndonAlertListResponse {
        items,
        pagination: CursorPage {
            next_cursor,
            limit,
        },
    }))
}

pub async fn get_andon_alert(
    State(state): State<AppState>,
    Path(alert_id): Path<Uuid>,
) -> Result<Json<AndonAlertResponse>, (StatusCode, Json<ErrorResponse>)> {
    let client = state.pool.get().await.map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse {
                code: "DB_UNAVAILABLE".to_string(),
                message: "Database unavailable".to_string(),
                correlation_id: Uuid::new_v4(),
            }),
        )
    })?;

    let row = client
        .query_opt(
            "SELECT alert_id, opcenter_event_id, station_id, line_id, alert_type, \
                    predicted_failure_type, minutes_to_predicted_failure, \
                    recommended_action, status, detected_at, acknowledged_at, resolved_at \
             FROM andon_alert \
             WHERE alert_id = $1",
            &[&alert_id],
        )
        .await
        .map_err(|e| {
            error!(error = %e, "get andon_alert query failed");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(ErrorResponse {
                    code: "QUERY_FAILED".to_string(),
                    message: "Failed to fetch Andon alert".to_string(),
                    correlation_id: Uuid::new_v4(),
                }),
            )
        })?;

    match row {
        None => Err((
            StatusCode::NOT_FOUND,
            Json(ErrorResponse {
                code: "ALERT_NOT_FOUND".to_string(),
                message: format!("Andon alert {alert_id} not found"),
                correlation_id: Uuid::new_v4(),
            }),
        )),
        Some(row) => {
            let detected_at: chrono::DateTime<chrono::Utc> = row.get("detected_at");
            let resolved_at: Option<chrono::DateTime<chrono::Utc>> = row.get("resolved_at");
            let acknowledged_at: Option<chrono::DateTime<chrono::Utc>> =
                row.get("acknowledged_at");
            Ok(Json(AndonAlertResponse {
                alert_id: row.get("alert_id"),
                station_id: row.get("station_id"),
                line_id: row.get("line_id"),
                alert_type: row.get("alert_type"),
                predicted_failure_type: row.get("predicted_failure_type"),
                minutes_to_predicted_failure: row.get("minutes_to_predicted_failure"),
                recommended_action: row.get("recommended_action"),
                status: row.get("status"),
                detected_at: detected_at.to_rfc3339(),
                resolved_at: resolved_at.map(|t| t.to_rfc3339()),
                acknowledged_at: acknowledged_at.map(|t| t.to_rfc3339()),
                opcenter_event_id: row.get("opcenter_event_id"),
            }))
        }
    }
}
