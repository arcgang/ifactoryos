use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Domain row types (mirrors the DB schema)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OeeSnapshot {
    pub oee_snapshot_id: Uuid,
    pub line_id: String,
    pub oee_percent: Decimal,
    pub availability: Decimal,
    pub performance: Decimal,
    pub quality: Decimal,
    pub measured_at: DateTime<Utc>,
    pub received_at: DateTime<Utc>,
    pub opcenter_event_id: Option<String>,
    pub kafka_offset: Option<i64>,
    pub correlation_id: Uuid,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AndonAlert {
    pub alert_id: Uuid,
    pub opcenter_event_id: Option<String>,
    pub station_id: String,
    pub line_id: String,
    pub alert_type: String,
    pub predicted_failure_type: String,
    pub minutes_to_predicted_failure: Option<i32>,
    pub recommended_action: String,
    pub status: String,
    pub detected_at: DateTime<Utc>,
    pub received_at: DateTime<Utc>,
    pub acknowledged_at: Option<DateTime<Utc>>,
    pub resolved_at: Option<DateTime<Utc>>,
    pub kafka_offset: Option<i64>,
    pub correlation_id: Uuid,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

// ---------------------------------------------------------------------------
// Inbound event shapes from OpCenter (GraphQL subscription / REST fallback)
// ---------------------------------------------------------------------------

/// Raw OEE event as received from OpCenter.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OpcenterOeeEvent {
    pub opcenter_event_id: Option<String>,
    pub line_id: String,
    pub oee_percent: Decimal,
    pub availability: Decimal,
    pub performance: Decimal,
    pub quality: Decimal,
    pub measured_at: DateTime<Utc>,
}

/// Raw Andon event as received from OpCenter.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OpcenterAndonEvent {
    pub opcenter_event_id: Option<String>,
    pub station_id: String,
    pub line_id: String,
    pub alert_type: String,
    pub predicted_failure_type: String,
    pub minutes_to_predicted_failure: Option<i32>,
    pub recommended_action: String,
    pub detected_at: DateTime<Utc>,
    pub event_type: String,
}

// ---------------------------------------------------------------------------
// Kafka event payloads published to UNS (matches AsyncAPI contract)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OeeEventPayload {
    pub event_id: Uuid,
    pub event_type: String,
    pub line_id: String,
    pub oee_percent: f64,
    pub availability: f64,
    pub performance: f64,
    pub quality: f64,
    pub measured_at: String,
    pub received_at: String,
    pub correlation_id: Uuid,
    pub opcenter_event_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AndonAlertEventPayload {
    pub event_id: Uuid,
    pub event_type: String,
    pub alert_id: Uuid,
    pub station_id: String,
    pub line_id: String,
    pub alert_type: String,
    pub predicted_failure_type: String,
    pub minutes_to_predicted_failure: Option<i32>,
    pub recommended_action: String,
    pub status: String,
    pub detected_at: String,
    pub received_at: String,
    pub correlation_id: Uuid,
    pub opcenter_event_id: Option<String>,
}

// ---------------------------------------------------------------------------
// HTTP API response shapes (matches openapi/opcenter-sync-service.json)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OeeSnapshotResponse {
    pub line_id: String,
    pub oee_percent: f64,
    pub availability: f64,
    pub performance: f64,
    pub quality: f64,
    pub measured_at: String,
    /// Seconds since measuredAt; consumers use this to detect staleness (>30 s = stale).
    pub data_age_seconds: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OeeSnapshotListResponse {
    pub items: Vec<OeeSnapshotResponse>,
    pub pagination: CursorPage,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AndonAlertResponse {
    pub alert_id: Uuid,
    pub station_id: String,
    pub line_id: String,
    pub alert_type: String,
    pub predicted_failure_type: String,
    pub minutes_to_predicted_failure: Option<i32>,
    pub recommended_action: String,
    pub status: String,
    pub detected_at: String,
    pub resolved_at: Option<String>,
    pub acknowledged_at: Option<String>,
    pub opcenter_event_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AndonAlertListResponse {
    pub items: Vec<AndonAlertResponse>,
    pub pagination: CursorPage,
}

/// Opaque cursor page token for keyset pagination.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CursorPage {
    pub next_cursor: Option<String>,
    pub limit: i64,
}

/// Standard error envelope used across all endpoints.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ErrorResponse {
    pub code: String,
    pub message: String,
    pub correlation_id: Uuid,
}

/// GET /health response.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HealthResponse {
    pub status: String,
    pub subscription_mode: String,
    pub kafka_ok: bool,
    pub db_ok: bool,
    pub last_oee_received_at: Option<String>,
    pub last_andon_received_at: Option<String>,
}
