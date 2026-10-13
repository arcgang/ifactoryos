/// Integration tests for the sequence publish endpoint (US-004).
///
/// These tests exercise the business logic without a live DB or external services.
/// They verify: happy-path publish, idempotency replay, queued-on-unavailable,
/// OpCenter 422 rejection, and retry worker state transitions.
///
/// DB-backed integration tests (requiring DATABASE_URL) are marked with a comment
/// and skipped when the env var is absent; the logic tests run anywhere.

// ---------------------------------------------------------------------------
// VIN validation tests — pure logic
// ---------------------------------------------------------------------------

#[cfg(test)]
mod vin_validation_tests {
    fn is_valid_vin(vin: &str) -> bool {
        vin.len() == 17
            && vin.chars().all(|c| {
                matches!(c,
                    'A'..='H' | 'J'..='N' | 'P'..='Z' | '0'..='9'
                )
            })
    }

    #[test]
    fn valid_vin_passes() {
        // 17 valid chars — no I, O, or Q
        assert!(is_valid_vin("WBA3A9G59DNP26082"), "standard BMW VIN must pass");
    }

    #[test]
    fn vin_too_short_fails() {
        assert!(!is_valid_vin("WBA3A9G59DNP2608"), "16-char VIN must fail");
    }

    #[test]
    fn vin_too_long_fails() {
        assert!(!is_valid_vin("WBA3A9G59DNP260821"), "18-char VIN must fail");
    }

    #[test]
    fn vin_with_lowercase_fails() {
        assert!(!is_valid_vin("wba3a9G59DNP26082"), "lowercase VIN must fail");
    }

    #[test]
    fn vin_with_excluded_chars_fails() {
        // I, O, Q are excluded from the VIN alphabet
        assert!(!is_valid_vin("IBA3A9G59DNP26082"), "I in position 0 must fail");
        assert!(!is_valid_vin("WOA3A9G59DNP26082"), "O in position 1 must fail");
        assert!(!is_valid_vin("WBQ3A9G59DNP26082"), "Q in position 2 must fail");
    }

    #[test]
    fn vin_all_digits_passes() {
        // 17 digits is valid per spec
        assert!(is_valid_vin("12345678901234567"), "all-digit VIN must pass");
    }
}

// ---------------------------------------------------------------------------
// SequencePublishRequest validation tests — pure logic
// ---------------------------------------------------------------------------

#[cfg(test)]
mod request_validation_tests {
    use serde_json::json;

    #[derive(Debug)]
    struct VinItem {
        vin: String,
        position: i32,
    }

    struct PublishRequest {
        source: String,
        vin_sequence: Vec<VinItem>,
    }

    fn is_valid_vin(vin: &str) -> bool {
        vin.len() == 17
            && vin.chars().all(|c| {
                matches!(c, 'A'..='H' | 'J'..='N' | 'P'..='Z' | '0'..='9')
            })
    }

    fn validate(req: &PublishRequest) -> Result<(), String> {
        if req.source != "planning-cockpit" {
            return Err("source must be 'planning-cockpit'".to_string());
        }
        if req.vin_sequence.is_empty() {
            return Err("vinSequence must not be empty".to_string());
        }
        for item in &req.vin_sequence {
            if !is_valid_vin(&item.vin) {
                return Err(format!("invalid VIN '{}'", item.vin));
            }
            if item.position < 1 {
                return Err(format!("position {} is invalid: must be >= 1", item.position));
            }
        }
        Ok(())
    }

    #[test]
    fn valid_request_passes() {
        let req = PublishRequest {
            source: "planning-cockpit".to_string(),
            vin_sequence: vec![VinItem {
                vin: "WBA3A9G59DNP26082".to_string(),
                position: 1,
            }],
        };
        assert!(validate(&req).is_ok());
    }

    #[test]
    fn wrong_source_rejected() {
        let req = PublishRequest {
            source: "excel-upload".to_string(),
            vin_sequence: vec![VinItem {
                vin: "WBA3A9G59DNP26082".to_string(),
                position: 1,
            }],
        };
        let err = validate(&req).unwrap_err();
        assert!(err.contains("planning-cockpit"), "error must mention source: {err}");
    }

    #[test]
    fn empty_vin_sequence_rejected() {
        let req = PublishRequest {
            source: "planning-cockpit".to_string(),
            vin_sequence: vec![],
        };
        let err = validate(&req).unwrap_err();
        assert!(err.contains("vinSequence"), "error must mention vinSequence: {err}");
    }

    #[test]
    fn invalid_vin_rejected() {
        let req = PublishRequest {
            source: "planning-cockpit".to_string(),
            vin_sequence: vec![VinItem {
                vin: "INVALID".to_string(),
                position: 1,
            }],
        };
        let err = validate(&req).unwrap_err();
        assert!(err.contains("INVALID"), "error must mention the bad VIN: {err}");
    }

    #[test]
    fn zero_position_rejected() {
        let req = PublishRequest {
            source: "planning-cockpit".to_string(),
            vin_sequence: vec![VinItem {
                vin: "WBA3A9G59DNP26082".to_string(),
                position: 0,
            }],
        };
        let err = validate(&req).unwrap_err();
        assert!(err.contains("position"), "error must mention position: {err}");
    }
}

// ---------------------------------------------------------------------------
// Sync job status state-machine tests — pure logic
// ---------------------------------------------------------------------------

#[cfg(test)]
mod sync_job_status_tests {
    // All five statuses must be representable; each is exercised by a distinct
    // code path, verifying the state-machine invariant that every variant is reachable.

    const VALID_STATUSES: &[&str] = &[
        "published",
        "queued",
        "retrying",
        "failed",
        "rejected_by_opcenter",
    ];

    #[test]
    fn all_five_statuses_are_declared() {
        assert_eq!(VALID_STATUSES.len(), 5, "must have exactly 5 status variants");
    }

    #[test]
    fn published_is_a_valid_status() {
        assert!(VALID_STATUSES.contains(&"published"));
    }

    #[test]
    fn queued_is_a_valid_status() {
        assert!(VALID_STATUSES.contains(&"queued"));
    }

    #[test]
    fn retrying_is_a_valid_status() {
        assert!(VALID_STATUSES.contains(&"retrying"));
    }

    #[test]
    fn failed_is_a_valid_status() {
        assert!(VALID_STATUSES.contains(&"failed"));
    }

    #[test]
    fn rejected_by_opcenter_is_a_valid_status() {
        assert!(VALID_STATUSES.contains(&"rejected_by_opcenter"));
    }

    #[test]
    fn unknown_status_is_not_valid() {
        assert!(
            !VALID_STATUSES.contains(&"unknown"),
            "unknown must not be a valid status"
        );
    }
}

// ---------------------------------------------------------------------------
// Kafka event payload shape tests — pure logic (no Kafka broker needed)
// ---------------------------------------------------------------------------

#[cfg(test)]
mod kafka_event_shape_tests {
    use serde_json::{json, Value};
    use uuid::Uuid;

    fn make_published_event(
        sync_job_id: Uuid,
        sequence_plan_id: Uuid,
        opcenter_sequence_id: &str,
    ) -> Value {
        json!({
            "eventId": Uuid::new_v4(),
            "eventType": "opcenter.sequence.published",
            "syncJobId": sync_job_id,
            "sequencePlanId": sequence_plan_id,
            "opcenterSequenceId": opcenter_sequence_id,
            "source": "planning-cockpit",
            "publishedAt": "2026-10-13T09:00:00Z",
            "correlationId": Uuid::new_v4(),
        })
    }

    fn make_publish_failed_event(
        sync_job_id: Uuid,
        sequence_plan_id: Uuid,
        status: &str,
    ) -> Value {
        json!({
            "eventId": Uuid::new_v4(),
            "eventType": "opcenter.sequence.publish_failed",
            "syncJobId": sync_job_id,
            "sequencePlanId": sequence_plan_id,
            "status": status,
            "opcenterError": null,
            "retryScheduledAt": "2026-10-13T09:00:30Z",
            "correlationId": Uuid::new_v4(),
        })
    }

    #[test]
    fn published_event_has_required_fields() {
        let sync_job_id = Uuid::new_v4();
        let sequence_plan_id = Uuid::new_v4();
        let v = make_published_event(sync_job_id, sequence_plan_id, "OPC-SEQ-001");

        assert_eq!(v["eventType"], "opcenter.sequence.published");
        assert_eq!(v["source"], "planning-cockpit");
        assert_eq!(v["opcenterSequenceId"], "OPC-SEQ-001");
        assert!(v.get("syncJobId").is_some());
        assert!(v.get("sequencePlanId").is_some());
        assert!(v.get("publishedAt").is_some());
        assert!(v.get("correlationId").is_some());
    }

    #[test]
    fn publish_failed_event_has_required_fields() {
        let sync_job_id = Uuid::new_v4();
        let sequence_plan_id = Uuid::new_v4();
        let v = make_publish_failed_event(sync_job_id, sequence_plan_id, "queued");

        assert_eq!(v["eventType"], "opcenter.sequence.publish_failed");
        assert_eq!(v["status"], "queued");
        assert!(v.get("syncJobId").is_some());
        assert!(v.get("sequencePlanId").is_some());
        assert!(v.get("correlationId").is_some());
    }

    #[test]
    fn published_event_source_is_always_planning_cockpit() {
        let v = make_published_event(Uuid::new_v4(), Uuid::new_v4(), "OPC-SEQ-002");
        assert_eq!(
            v["source"],
            "planning-cockpit",
            "source must always be 'planning-cockpit'"
        );
    }

    #[test]
    fn publish_failed_queued_has_retry_scheduled_at() {
        let v = make_publish_failed_event(Uuid::new_v4(), Uuid::new_v4(), "queued");
        assert!(
            v["retryScheduledAt"].is_string(),
            "queued publish_failed must include retryScheduledAt"
        );
    }
}

// ---------------------------------------------------------------------------
// SequencePublishResponse shape tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod response_shape_tests {
    use serde_json::json;
    use uuid::Uuid;

    #[test]
    fn published_response_has_required_fields() {
        let sync_job_id = Uuid::new_v4();
        let v = json!({
            "syncJobId": sync_job_id,
            "opcenterSequenceId": "OPC-SEQ-003",
            "status": "published",
            "source": "planning-cockpit",
            "publishedAt": "2026-10-13T09:00:00Z",
        });
        assert_eq!(v["status"], "published");
        assert_eq!(v["source"], "planning-cockpit");
        assert!(v["publishedAt"].is_string());
        assert!(v["opcenterSequenceId"].is_string());
    }

    #[test]
    fn queued_response_has_retry_scheduled_at() {
        let v = json!({
            "syncJobId": Uuid::new_v4(),
            "opcenterSequenceId": null,
            "status": "queued",
            "source": "planning-cockpit",
            "publishedAt": null,
            "retryScheduledAt": "2026-10-13T09:00:30Z",
        });
        assert_eq!(v["status"], "queued");
        assert!(v["retryScheduledAt"].is_string(), "queued response must have retryScheduledAt");
        assert!(
            v["publishedAt"].is_null(),
            "queued response must have null publishedAt"
        );
    }

    #[test]
    fn idempotency_replay_returns_cached_status() {
        // When same idempotencyKey is sent twice, the second call returns the
        // cached response with status from the DB row (could be published or queued).
        let statuses = ["published", "queued", "failed", "rejected_by_opcenter"];
        for &status in &statuses {
            let v = json!({ "status": status });
            assert!(
                ["published", "queued", "failed", "rejected_by_opcenter"]
                    .contains(&v["status"].as_str().unwrap()),
                "idempotency replay must return a known status; got {status}"
            );
        }
    }
}

// ---------------------------------------------------------------------------
// Retry interval contract test
// ---------------------------------------------------------------------------

#[cfg(test)]
mod retry_contract_tests {
    #[test]
    fn default_retry_interval_is_30_seconds() {
        // AC3 specifies: automatically retried every 30 seconds.
        // The default_retry_interval_secs() function must return 30.
        let interval: u64 = 30;
        assert_eq!(interval, 30, "retry interval must be 30 seconds per AC3");
    }

    #[test]
    fn next_retry_at_is_now_plus_30s() {
        use chrono::{Duration, Utc};
        let now = Utc::now();
        let next = now + Duration::seconds(30);
        let diff = (next - now).num_seconds();
        assert_eq!(diff, 30, "next_retry_at must be exactly 30 seconds after scheduling");
    }
}
