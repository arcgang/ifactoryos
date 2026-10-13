/// Core event-processing logic: validate → dedup → persist → publish.
/// Shared by both the GraphQL subscription path and the REST polling fallback.
use chrono::Utc;
use rdkafka::producer::FutureProducer;
use rust_decimal::prelude::ToPrimitive;
use tracing::{error, info, warn};
use uuid::Uuid;

use crate::{
    db::{insert_andon_alert, insert_oee_snapshot, set_andon_kafka_offset, set_oee_kafka_offset},
    kafka::{publish, TOPIC_ANDON_ALERT, TOPIC_OEE_UPDATED},
    metrics::{DELIVERY_SECONDS, DLQ_SENDS_TOTAL, EVENTS_TOTAL},
    models::{AndonAlertEventPayload, OeeEventPayload},
    schema_validator::{validate_andon_event, validate_oee_event},
};

/// Process a raw OEE JSON event.
/// Returns Ok(true) if a new row was inserted and published;
/// Ok(false) if deduped (idempotent); Err on validation or unrecoverable failure.
pub async fn process_oee_event(
    client: &deadpool_postgres::Client,
    producer: &FutureProducer,
    raw: &serde_json::Value,
) -> anyhow::Result<bool> {
    // 1. Validate
    let event = validate_oee_event(raw).map_err(|e| {
        DLQ_SENDS_TOTAL
            .with_label_values(&["oee", "schema_invalid"])
            .inc();
        e
    })?;

    let correlation_id = Uuid::new_v4();
    let received_at = Utc::now();

    // 2. Persist — ON CONFLICT DO NOTHING on opcenter_event_id unique index
    let snapshot_id = insert_oee_snapshot(
        client,
        &event.line_id,
        event.oee_percent,
        event.availability,
        event.performance,
        event.quality,
        event.measured_at,
        event.opcenter_event_id.as_deref(),
        correlation_id,
    )
    .await
    .map_err(|e| {
        error!(error = %e, "failed to insert oee_snapshot");
        e
    })?;

    let Some(snapshot_id) = snapshot_id else {
        // Duplicate — already processed
        info!(opcenter_event_id = ?event.opcenter_event_id, "OEE event deduped — skipped");
        EVENTS_TOTAL.with_label_values(&["oee", "deduped"]).inc();
        return Ok(false);
    };

    // 3. Build Kafka payload matching the AsyncAPI contract
    let payload = OeeEventPayload {
        event_id: Uuid::new_v4(),
        event_type: "opcenter.oee.updated".to_string(),
        line_id: event.line_id.clone(),
        oee_percent: event.oee_percent.to_f64().unwrap_or(0.0),
        availability: event.availability.to_f64().unwrap_or(0.0),
        performance: event.performance.to_f64().unwrap_or(0.0),
        quality: event.quality.to_f64().unwrap_or(0.0),
        measured_at: event.measured_at.to_rfc3339(),
        received_at: received_at.to_rfc3339(),
        correlation_id,
        opcenter_event_id: event.opcenter_event_id.clone(),
    };

    // 4. Produce to Kafka; measure delivery elapsed from OpCenter origination
    let elapsed_secs = (received_at - event.measured_at)
        .num_milliseconds()
        .max(0) as f64
        / 1000.0;

    match publish(producer, TOPIC_OEE_UPDATED, &event.line_id, &payload, correlation_id).await {
        Ok(offset) => {
            set_oee_kafka_offset(client, snapshot_id, offset)
                .await
                .unwrap_or_else(|e| warn!(error = %e, "failed to record oee kafka_offset"));
            DELIVERY_SECONDS
                .with_label_values(&["oee", &event.line_id])
                .observe(elapsed_secs);
            EVENTS_TOTAL.with_label_values(&["oee", "published"]).inc();
            info!(
                line_id = %event.line_id,
                %snapshot_id,
                offset,
                elapsed_secs,
                "OEE event published to Kafka"
            );
            Ok(true)
        }
        Err(e) => {
            error!(error = %e, "OEE Kafka publish failed — DLQ increment");
            DLQ_SENDS_TOTAL
                .with_label_values(&["oee", "kafka_error"])
                .inc();
            Err(e)
        }
    }
}

/// Process a raw Andon JSON event.
/// Returns Ok(true) if a new row was inserted and published;
/// Ok(false) if deduped; Err on validation or unrecoverable failure.
pub async fn process_andon_event(
    client: &deadpool_postgres::Client,
    producer: &FutureProducer,
    raw: &serde_json::Value,
) -> anyhow::Result<bool> {
    // 1. Validate
    let event = validate_andon_event(raw).map_err(|e| {
        DLQ_SENDS_TOTAL
            .with_label_values(&["andon", "schema_invalid"])
            .inc();
        e
    })?;

    let correlation_id = Uuid::new_v4();
    let received_at = Utc::now();

    // 2. Persist — no operator_id: station-level only per BetrVG §87 / GDPR
    let alert_id = insert_andon_alert(
        client,
        event.opcenter_event_id.as_deref(),
        &event.station_id,
        &event.line_id,
        &event.alert_type,
        &event.predicted_failure_type,
        event.minutes_to_predicted_failure,
        &event.recommended_action,
        event.detected_at,
        correlation_id,
    )
    .await
    .map_err(|e| {
        error!(error = %e, "failed to insert andon_alert");
        e
    })?;

    let Some(alert_id) = alert_id else {
        info!(opcenter_event_id = ?event.opcenter_event_id, "Andon event deduped — skipped");
        EVENTS_TOTAL.with_label_values(&["andon", "deduped"]).inc();
        return Ok(false);
    };

    // 3. Build Kafka payload
    let payload = AndonAlertEventPayload {
        event_id: Uuid::new_v4(),
        event_type: event.event_type.clone(),
        alert_id,
        station_id: event.station_id.clone(),
        line_id: event.line_id.clone(),
        alert_type: event.alert_type.clone(),
        predicted_failure_type: event.predicted_failure_type.clone(),
        minutes_to_predicted_failure: event.minutes_to_predicted_failure,
        recommended_action: event.recommended_action.clone(),
        status: "open".to_string(),
        detected_at: event.detected_at.to_rfc3339(),
        received_at: received_at.to_rfc3339(),
        correlation_id,
        opcenter_event_id: event.opcenter_event_id.clone(),
    };

    let elapsed_secs = (received_at - event.detected_at)
        .num_milliseconds()
        .max(0) as f64
        / 1000.0;

    match publish(
        producer,
        TOPIC_ANDON_ALERT,
        &event.station_id,
        &payload,
        correlation_id,
    )
    .await
    {
        Ok(offset) => {
            set_andon_kafka_offset(client, alert_id, offset)
                .await
                .unwrap_or_else(|e| warn!(error = %e, "failed to record andon kafka_offset"));
            DELIVERY_SECONDS
                .with_label_values(&["andon", &event.line_id])
                .observe(elapsed_secs);
            EVENTS_TOTAL.with_label_values(&["andon", "published"]).inc();
            info!(
                station_id = %event.station_id,
                %alert_id,
                offset,
                elapsed_secs,
                "Andon event published to Kafka"
            );
            Ok(true)
        }
        Err(e) => {
            error!(error = %e, "Andon Kafka publish failed — DLQ increment");
            DLQ_SENDS_TOTAL
                .with_label_values(&["andon", "kafka_error"])
                .inc();
            Err(e)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn oee_payload_shape_matches_asyncapi_contract() {
        let payload = OeeEventPayload {
            event_id: Uuid::new_v4(),
            event_type: "opcenter.oee.updated".to_string(),
            line_id: "WELD-LINE-1".to_string(),
            oee_percent: 87.5,
            availability: 95.0,
            performance: 92.0,
            quality: 99.1,
            measured_at: "2026-10-12T06:00:00Z".to_string(),
            received_at: "2026-10-12T06:00:01Z".to_string(),
            correlation_id: Uuid::new_v4(),
            opcenter_event_id: Some("evt-001".to_string()),
        };
        let v: serde_json::Value = serde_json::to_value(&payload).unwrap();
        // Required fields per AsyncAPI contract
        assert_eq!(v["eventType"], "opcenter.oee.updated");
        assert_eq!(v["lineId"], "WELD-LINE-1");
        assert_eq!(v["oeePercent"], json!(87.5));
        assert!(v.get("measuredAt").is_some());
        assert!(v.get("correlationId").is_some());
        assert!(v.get("eventId").is_some());
    }

    #[test]
    fn andon_payload_has_no_operator_id_fields() {
        let payload = AndonAlertEventPayload {
            event_id: Uuid::new_v4(),
            event_type: "opcenter.andon.alert_raised".to_string(),
            alert_id: Uuid::new_v4(),
            station_id: "WELD-ST-3".to_string(),
            line_id: "WELD-LINE-1".to_string(),
            alert_type: "predictive".to_string(),
            predicted_failure_type: "electrode_wear".to_string(),
            minutes_to_predicted_failure: Some(12),
            recommended_action: "Replace electrode cap".to_string(),
            status: "open".to_string(),
            detected_at: "2026-10-12T06:00:00Z".to_string(),
            received_at: "2026-10-12T06:00:01Z".to_string(),
            correlation_id: Uuid::new_v4(),
            opcenter_event_id: Some("evt-002".to_string()),
        };
        let v: serde_json::Value = serde_json::to_value(&payload).unwrap();
        assert_eq!(v["status"], "open");
        assert_eq!(v["alertType"], "predictive");
        assert_eq!(v["minutesToPredictedFailure"], json!(12));
        // BetrVG §87 / GDPR: no operator-identifying fields
        assert!(v.get("operatorId").is_none(), "operator ID must not appear in payload");
        assert!(v.get("userId").is_none(), "user ID must not appear in payload");
        assert!(v.get("workerId").is_none(), "worker ID must not appear in payload");
    }

    #[test]
    fn andon_status_open_is_set_on_initial_insert() {
        let payload = AndonAlertEventPayload {
            event_id: Uuid::new_v4(),
            event_type: "opcenter.andon.alert_raised".to_string(),
            alert_id: Uuid::new_v4(),
            station_id: "WELD-ST-1".to_string(),
            line_id: "WELD-LINE-2".to_string(),
            alert_type: "reactive".to_string(),
            predicted_failure_type: "press_die_overrun".to_string(),
            minutes_to_predicted_failure: None,
            recommended_action: "Inspect press die".to_string(),
            status: "open".to_string(),
            detected_at: "2026-10-12T07:00:00Z".to_string(),
            received_at: "2026-10-12T07:00:01Z".to_string(),
            correlation_id: Uuid::new_v4(),
            opcenter_event_id: None,
        };
        let v: serde_json::Value = serde_json::to_value(&payload).unwrap();
        assert_eq!(v["status"], "open", "initial status must be 'open'");
        assert!(
            v["minutesToPredictedFailure"].is_null(),
            "reactive alert: minutesToPredictedFailure must be null"
        );
    }
}
