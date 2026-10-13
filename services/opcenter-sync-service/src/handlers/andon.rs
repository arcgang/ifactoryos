use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use chrono::{DateTime, Utc};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

use crate::{
    auth::BearerClaims,
    errors::AppError,
    models::AndonAlert,
};

/// Database row mapped directly from the `andon_alert` table.
#[derive(FromRow)]
struct AndonAlertRow {
    alert_id: Uuid,
    opcenter_event_id: Option<String>,
    station_id: String,
    line_id: String,
    alert_type: String,
    predicted_failure_type: String,
    minutes_to_predicted_failure: Option<i32>,
    recommended_action: String,
    status: String,
    detected_at: DateTime<Utc>,
    acknowledged_at: Option<DateTime<Utc>>,
    resolved_at: Option<DateTime<Utc>>,
}

/// `GET /andon/alerts/{alertId}`
///
/// Returns an Andon alert by ID from the `andon_alert` table.
///
/// - 401 if the request carries no valid Bearer token.
/// - 404 if no alert matches `alertId`.
/// - 500 on database failure.
pub async fn get_andon_alert(
    _claims: BearerClaims,
    State(pool): State<PgPool>,
    Path(alert_id): Path<Uuid>,
) -> Result<impl IntoResponse, AppError> {
    let row: Option<AndonAlertRow> = sqlx::query_as(
        r#"
        SELECT
            alert_id,
            opcenter_event_id,
            station_id,
            line_id,
            alert_type,
            predicted_failure_type,
            minutes_to_predicted_failure,
            recommended_action,
            status,
            detected_at,
            acknowledged_at,
            resolved_at
        FROM andon_alert
        WHERE alert_id = $1
        "#,
    )
    .bind(alert_id)
    .fetch_optional(&pool)
    .await
    .map_err(|err| {
        tracing::error!(
            alert_id = %alert_id,
            error = %err,
            "database error fetching andon alert"
        );
        AppError::internal("An unexpected error occurred")
    })?;

    let row = row.ok_or_else(|| {
        AppError::not_found(format!("Andon alert '{alert_id}' not found"))
    })?;

    let alert = AndonAlert {
        alert_id: row.alert_id,
        opcenter_event_id: row.opcenter_event_id,
        station_id: row.station_id,
        line_id: row.line_id,
        alert_type: row.alert_type,
        predicted_failure_type: row.predicted_failure_type,
        minutes_to_predicted_failure: row.minutes_to_predicted_failure,
        recommended_action: row.recommended_action,
        status: row.status,
        detected_at: row.detected_at,
        acknowledged_at: row.acknowledged_at,
        resolved_at: row.resolved_at,
    };

    Ok((StatusCode::OK, Json(alert)))
}
