//! Gate result ingestion consumer.
//!
//! Processes gate result events from the OpCenter GraphQL subscription,
//! validates the payload, deduplicates on opcenter_event_id, persists to the
//! gate_result table, and publishes an Avro-encoded Kafka event to
//! `uns.munich.i4.quality.gate.results`.
//!
//! On schema validation failure the event is routed to the DLQ topic
//! `uns.munich.i4.dlq.quality-studio-gate-consumer`.  Unresolvable persistence
//! errors are also routed to the DLQ with an `error_reason` field.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::PgPool;
use uuid::Uuid;

use crate::models::GateMeasurement;

// ---------------------------------------------------------------------------
// Kafka topic constants
// ---------------------------------------------------------------------------

pub const GATE_RESULTS_TOPIC: &str = "uns.munich.i4.quality.gate.results";
pub const DLQ_TOPIC: &str = "uns.munich.i4.dlq.quality-studio-gate-consumer";

// ---------------------------------------------------------------------------
// Inbound event types (from OpCenter GraphQL subscription payload)
// ---------------------------------------------------------------------------

/// Raw event payload received from the OpCenter GraphQL subscription.
/// Validated before any persistence is attempted.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OpcenterGateResultEvent {
    pub opcenter_event_id: Option<String>,
    pub vin: String,
    pub gate_id: String,
    pub gate_type: String,
    pub result: String,
    pub measurements: Vec<GateMeasurement>,
    pub measured_at: DateTime<Utc>,
}

/// Outbound Kafka event published on `uns.munich.i4.quality.gate.results`.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GateResultKafkaEvent {
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

/// Envelope written to the DLQ topic on validation or persistence failure.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DlqEnvelope {
    pub error_reason: String,
    pub raw_payload: String,
    pub failed_at: DateTime<Utc>,
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

const VALID_GATE_TYPES: &[&str] = &["dimensional", "leak", "electrical", "nvh", "adas"];
const VALID_RESULTS: &[&str] = &["pass", "conditional_pass", "fail"];

/// Errors that can result from processing a single inbound event.
#[derive(Debug)]
pub enum ProcessError {
    /// Payload did not pass schema validation — route to DLQ.
    SchemaValidation(String),
    /// Duplicate event (same opcenter_event_id already in database) — no-op.
    Duplicate,
    /// Unresolvable database or serialisation error — route to DLQ.
    Persistence(String),
}

/// Validate the inbound event against schema rules.
///
/// Returns `Err(ProcessError::SchemaValidation(_))` for any structural violation.
pub fn validate_event(event: &OpcenterGateResultEvent) -> Result<(), ProcessError> {
    if !is_valid_vin(&event.vin) {
        return Err(ProcessError::SchemaValidation(format!(
            "invalid VIN: '{}'", event.vin
        )));
    }
    if !VALID_GATE_TYPES.contains(&event.gate_type.as_str()) {
        return Err(ProcessError::SchemaValidation(format!(
            "unknown gate_type: '{}'", event.gate_type
        )));
    }
    if !VALID_RESULTS.contains(&event.result.as_str()) {
        return Err(ProcessError::SchemaValidation(format!(
            "unknown result: '{}'", event.result
        )));
    }
    if event.gate_id.is_empty() {
        return Err(ProcessError::SchemaValidation("gate_id must not be empty".to_string()));
    }
    if event.measurements.is_empty() {
        return Err(ProcessError::SchemaValidation(
            "measurements array must not be empty".to_string(),
        ));
    }
    Ok(())
}

/// Persist a validated gate result event to the database.
///
/// Returns:
/// - `Ok(GateResultKafkaEvent)` on successful insert.
/// - `Err(ProcessError::Duplicate)` when `opcenter_event_id` conflicts with an
///   existing row (unique index violation — idempotent, not an error).
/// - `Err(ProcessError::Persistence(_))` on any other database error.
pub async fn persist_gate_result(
    pool: &PgPool,
    event: &OpcenterGateResultEvent,
    received_at: DateTime<Utc>,
) -> Result<GateResultKafkaEvent, ProcessError> {
    let measurements_json =
        serde_json::to_value(&event.measurements).map_err(|e| {
            ProcessError::Persistence(format!("failed to serialise measurements: {e}"))
        })?;

    #[derive(sqlx::FromRow)]
    struct InsertedRow {
        gate_result_id: Uuid,
    }

    let row: Option<InsertedRow> = sqlx::query_as(
        r#"
        INSERT INTO gate_result (
            vin,
            gate_id,
            gate_type,
            result,
            measurements,
            measured_at,
            received_from_opcenter_at,
            opcenter_event_id
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (opcenter_event_id) DO NOTHING
        RETURNING gate_result_id
        "#,
    )
    .bind(&event.vin)
    .bind(&event.gate_id)
    .bind(&event.gate_type)
    .bind(&event.result)
    .bind(measurements_json)
    .bind(event.measured_at)
    .bind(received_at)
    .bind(&event.opcenter_event_id)
    .fetch_optional(pool)
    .await
    .map_err(|e| ProcessError::Persistence(format!("database insert failed: {e}")))?;

    // ON CONFLICT DO NOTHING returns no row — treat as duplicate.
    let row = row.ok_or(ProcessError::Duplicate)?;

    Ok(GateResultKafkaEvent {
        gate_result_id: row.gate_result_id,
        vin: event.vin.clone(),
        gate_id: event.gate_id.clone(),
        gate_type: event.gate_type.clone(),
        result: event.result.clone(),
        measurements: event.measurements.clone(),
        measured_at: event.measured_at,
        received_from_opcenter_at: received_at,
        opcenter_event_id: event.opcenter_event_id.clone(),
    })
}

// ---------------------------------------------------------------------------
// VIN validation helper (shared with handler layer)
// ---------------------------------------------------------------------------

fn is_valid_vin(vin: &str) -> bool {
    if vin.len() != 17 {
        return false;
    }
    vin.chars().all(|c| matches!(c, 'A'..='H' | 'J'..='N' | 'P'..='Z' | '0'..='9'))
}
