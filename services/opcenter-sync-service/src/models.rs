use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
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

/// A single measurement within a gate result.
///
/// Serialised into the `measurements` JSONB array in the `gate_result` table
/// and returned verbatim in the API response.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GateMeasurement {
    pub measurement_id: String,
    pub name: String,
    pub value: f64,
    pub unit: String,
    pub tolerance_low: f64,
    pub tolerance_high: f64,
    pub within_tolerance: bool,
    pub rework_threshold_low: Option<f64>,
    pub rework_threshold_high: Option<f64>,
}

/// Response body for a single gate result — matches the OpenAPI `GateResult` schema.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GateResult {
    pub gate_result_id: Uuid,
    pub vin: String,
    pub gate_id: String,
    pub gate_type: String,
    pub result: String,
    pub measurements: Vec<GateMeasurement>,
    pub measured_at: DateTime<Utc>,
    pub received_from_opcenter_at: DateTime<Utc>,
    pub opcenter_event_id: Option<String>,
}

/// Opaque cursor pagination metadata — matches the OpenAPI `CursorPage` schema.
#[derive(Debug, Serialize)]
pub struct CursorPage {
    pub next_cursor: Option<String>,
    pub limit: i64,
}

/// List response wrapper — matches the OpenAPI `GateResultListResponse` schema.
#[derive(Debug, Serialize)]
pub struct GateResultListResponse {
    pub data: Vec<GateResult>,
    pub page: CursorPage,
}
