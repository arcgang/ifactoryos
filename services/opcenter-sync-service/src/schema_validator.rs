use anyhow::bail;
use rust_decimal::Decimal;
use std::str::FromStr;

use crate::models::{OpcenterAndonEvent, OpcenterOeeEvent};

/// Validate an OEE event payload from OpCenter.
/// Returns a typed model or an error describing the first violation.
pub fn validate_oee_event(raw: &serde_json::Value) -> anyhow::Result<OpcenterOeeEvent> {
    let event: OpcenterOeeEvent = serde_json::from_value(raw.clone())
        .map_err(|e| anyhow::anyhow!("OEE event deserialise error: {e}"))?;

    validate_oee_range("oeePercent", event.oee_percent)?;
    validate_oee_range("availability", event.availability)?;
    validate_oee_range("performance", event.performance)?;
    validate_oee_range("quality", event.quality)?;

    if event.line_id.trim().is_empty() {
        bail!("OEE event: lineId must not be empty");
    }

    Ok(event)
}

fn validate_oee_range(field: &str, value: Decimal) -> anyhow::Result<()> {
    let zero = Decimal::ZERO;
    let hundred = Decimal::from_str("100").unwrap();
    if value < zero || value > hundred {
        bail!("OEE event: {field} = {value} is out of range [0, 100]");
    }
    Ok(())
}

/// Validate an Andon alert event payload from OpCenter.
pub fn validate_andon_event(raw: &serde_json::Value) -> anyhow::Result<OpcenterAndonEvent> {
    let event: OpcenterAndonEvent = serde_json::from_value(raw.clone())
        .map_err(|e| anyhow::anyhow!("Andon event deserialise error: {e}"))?;

    if !["predictive", "reactive"].contains(&event.alert_type.as_str()) {
        bail!(
            "Andon event: unknown alertType '{}'; expected predictive or reactive",
            event.alert_type
        );
    }
    if event.station_id.trim().is_empty() {
        bail!("Andon event: stationId must not be empty");
    }
    if event.line_id.trim().is_empty() {
        bail!("Andon event: lineId must not be empty");
    }
    if event.predicted_failure_type.trim().is_empty() {
        bail!("Andon event: predictedFailureType must not be empty");
    }
    if event.recommended_action.trim().is_empty() {
        bail!("Andon event: recommendedAction must not be empty");
    }

    Ok(event)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn validate_oee_event_valid() {
        let raw = json!({
            "lineId": "WELD-LINE-1",
            "oeePercent": 87.5,
            "availability": 95.0,
            "performance": 92.0,
            "quality": 99.1,
            "measuredAt": "2026-10-12T06:00:00Z",
            "opcenterEventId": "evt-001"
        });
        assert!(validate_oee_event(&raw).is_ok());
    }

    #[test]
    fn validate_oee_event_rejects_out_of_range() {
        let raw = json!({
            "lineId": "WELD-LINE-1",
            "oeePercent": 101.0,
            "availability": 95.0,
            "performance": 92.0,
            "quality": 99.1,
            "measuredAt": "2026-10-12T06:00:00Z"
        });
        let err = validate_oee_event(&raw).unwrap_err();
        assert!(
            err.to_string().contains("oeePercent"),
            "error should mention oeePercent, got: {err}"
        );
    }

    #[test]
    fn validate_oee_event_rejects_negative() {
        let raw = json!({
            "lineId": "WELD-LINE-2",
            "oeePercent": -5.0,
            "availability": 95.0,
            "performance": 92.0,
            "quality": 99.1,
            "measuredAt": "2026-10-12T07:00:00Z"
        });
        let err = validate_oee_event(&raw).unwrap_err();
        assert!(err.to_string().contains("oeePercent"));
    }

    #[test]
    fn validate_oee_event_rejects_empty_line_id() {
        let raw = json!({
            "lineId": "   ",
            "oeePercent": 80.0,
            "availability": 90.0,
            "performance": 90.0,
            "quality": 98.0,
            "measuredAt": "2026-10-12T07:00:00Z"
        });
        let err = validate_oee_event(&raw).unwrap_err();
        assert!(err.to_string().contains("lineId"));
    }

    #[test]
    fn validate_andon_event_valid() {
        let raw = json!({
            "stationId": "WELD-ST-3",
            "lineId": "WELD-LINE-1",
            "alertType": "predictive",
            "predictedFailureType": "electrode_wear",
            "minutesToPredictedFailure": 12,
            "recommendedAction": "Replace electrode cap",
            "detectedAt": "2026-10-12T06:00:00Z",
            "eventType": "opcenter.andon.alert_raised"
        });
        assert!(validate_andon_event(&raw).is_ok());
    }

    #[test]
    fn validate_andon_event_rejects_unknown_alert_type() {
        let raw = json!({
            "stationId": "WELD-ST-3",
            "lineId": "WELD-LINE-1",
            "alertType": "proactive",
            "predictedFailureType": "electrode_wear",
            "recommendedAction": "Replace electrode cap",
            "detectedAt": "2026-10-12T06:00:00Z",
            "eventType": "opcenter.andon.alert_raised"
        });
        let err = validate_andon_event(&raw).unwrap_err();
        assert!(
            err.to_string().contains("alertType"),
            "error should mention alertType, got: {err}"
        );
    }

    #[test]
    fn validate_andon_event_rejects_empty_station() {
        let raw = json!({
            "stationId": "",
            "lineId": "WELD-LINE-1",
            "alertType": "predictive",
            "predictedFailureType": "electrode_wear",
            "recommendedAction": "Replace electrode cap",
            "detectedAt": "2026-10-12T06:00:00Z",
            "eventType": "opcenter.andon.alert_raised"
        });
        let err = validate_andon_event(&raw).unwrap_err();
        assert!(err.to_string().contains("stationId"));
    }
}
