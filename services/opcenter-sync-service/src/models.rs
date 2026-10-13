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

// ---------------------------------------------------------------------------
// Gate result types (from this branch — gate result ingestion pipeline)
// ---------------------------------------------------------------------------

/// A single measurement within a gate result.
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

/// List response wrapper — matches the OpenAPI `GateResultListResponse` schema.
#[derive(Debug, Serialize)]
pub struct GateResultListResponse {
    pub data: Vec<GateResult>,
    pub page: CursorPage,
}

// ---------------------------------------------------------------------------
// Sequence publish — request / response / domain types (US-004)
// ---------------------------------------------------------------------------

/// A single VIN with its production position and option codes.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct VinSequenceItem {
    /// 17-character VIN matching ^[A-HJ-NPR-Z0-9]{17}$
    pub vin: String,
    /// 1-based position in the production sequence
    pub position: i32,
    pub option_codes: Vec<String>,
}

/// POST /sequence/publish request body.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SequencePublishRequest {
    pub sequence_plan_id: Uuid,
    pub vin_sequence: Vec<VinSequenceItem>,
    /// Must equal "planning-cockpit"
    pub source: String,
    pub locked_at: DateTime<Utc>,
    pub planner_user_id: Option<String>,
    pub idempotency_key: Option<Uuid>,
}

/// POST /sequence/publish response body (202 or 200).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SequencePublishResponse {
    pub sync_job_id: Uuid,
    pub opcenter_sequence_id: Option<String>,
    pub status: String,
    pub source: String,
    pub published_at: Option<String>,
    pub audit_event_id: Option<Uuid>,
    pub retry_scheduled_at: Option<String>,
}

/// Sync job domain row — mirrors DB columns.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncJob {
    pub sync_job_id: Uuid,
    pub sequence_plan_id: Uuid,
    pub planner_user_id: Option<String>,
    pub status: String,
    pub source: String,
    pub opcenter_sequence_id: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub last_attempt_at: Option<DateTime<Utc>>,
    pub next_retry_at: Option<DateTime<Utc>>,
    pub attempt_count: i32,
    pub opcenter_error: Option<String>,
    pub published_at: Option<DateTime<Utc>>,
    pub audit_event_id: Option<Uuid>,
}

/// GET /sequence/jobs list response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SyncJobListResponse {
    pub data: Vec<SyncJobResponse>,
    pub page: CursorPage,
}

/// SyncJob shape exposed in the API (matches LLD SyncJob schema).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncJobResponse {
    pub sync_job_id: Uuid,
    pub sequence_plan_id: Uuid,
    pub planner_user_id: Option<String>,
    pub status: String,
    pub source: Option<String>,
    pub opcenter_sequence_id: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub last_attempt_at: Option<String>,
    pub next_retry_at: Option<String>,
    pub attempt_count: i32,
    pub opcenter_error: Option<String>,
}

// ---------------------------------------------------------------------------
// Kafka event payloads for sequence publish / publish-failed (AsyncAPI)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SequencePublishedEvent {
    pub event_id: Uuid,
    pub event_type: String,
    pub sync_job_id: Uuid,
    pub sequence_plan_id: Uuid,
    pub opcenter_sequence_id: String,
    pub source: String,
    pub published_at: String,
    pub correlation_id: Uuid,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SequencePublishFailedEvent {
    pub event_id: Uuid,
    pub event_type: String,
    pub sync_job_id: Uuid,
    pub sequence_plan_id: Uuid,
    pub status: String,
    pub opcenter_error: Option<String>,
    pub retry_scheduled_at: Option<String>,
    pub correlation_id: Uuid,
}
