use chrono::{DateTime, Utc};
use serde::Serialize;
use uuid::Uuid;

/// Response body for `GET /andon/alerts/{alertId}`.
///
/// Field names and types match the OpenAPI `AndonAlert` schema exactly.
/// `serde(rename_all = "camelCase")` maps Rust snake_case fields to the
/// camelCase names the contract specifies (e.g. `alert_id` → `alertId`).
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AndonAlert {
    pub alert_id: Uuid,
    pub station_id: String,
    pub line_id: String,
    pub alert_type: String,
    pub predicted_failure_type: String,
    pub minutes_to_predicted_failure: Option<i32>,
    pub recommended_action: String,
    pub status: String,
    pub detected_at: DateTime<Utc>,
    pub resolved_at: Option<DateTime<Utc>>,
    pub acknowledged_at: Option<DateTime<Utc>>,
    pub opcenter_event_id: Option<String>,
}
