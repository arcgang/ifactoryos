//! Tests for `GET /gate-results` and `GET /gate-results/{gateResultId}`.
//!
//! Router-level tests that do not need a live database.  The stub router in
//! `lib.rs` validates auth and returns 404 for every authenticated request.
//!
//! Unit tests for the consumer (deduplication logic, schema rejection routing)
//! exercise the `gate_result_consumer` module directly in-process.
use axum::{
    body::Body,
    http::{header, Request, StatusCode},
};
use serde_json::Value;
use tower::ServiceExt;
use uuid::Uuid;

use opcenter_sync_service::gate_result_consumer::{
    validate_event, OpcenterGateResultEvent, ProcessError,
};
use opcenter_sync_service::models::GateMeasurement;

// ---------------------------------------------------------------------------
// Shared test helpers (copied from andon_alerts_test.rs to keep tests self-
// contained; each test file compiles independently)
// ---------------------------------------------------------------------------

fn build_test_app() -> axum::Router {
    opcenter_sync_service::test_router()
}

fn make_bearer(sub: &str) -> String {
    let exp: u64 = 9_999_999_999;
    let payload = serde_json::json!({ "sub": sub, "exp": exp, "roles": [] });
    let encoded = b64url_encode(serde_json::to_string(&payload).unwrap().as_bytes());
    format!("header.{encoded}.sig")
}

fn b64url_encode(input: &[u8]) -> String {
    let table = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    let mut i = 0;
    while i + 2 < input.len() {
        let a = input[i];
        let b_byte = input[i + 1];
        let c = input[i + 2];
        out.push(table[((a >> 2) & 0x3f) as usize] as char);
        out.push(table[(((a << 4) | (b_byte >> 4)) & 0x3f) as usize] as char);
        out.push(table[(((b_byte << 2) | (c >> 6)) & 0x3f) as usize] as char);
        out.push(table[(c & 0x3f) as usize] as char);
        i += 3;
    }
    if i + 1 == input.len() {
        let a = input[i];
        out.push(table[((a >> 2) & 0x3f) as usize] as char);
        out.push(table[((a << 4) & 0x3f) as usize] as char);
    } else if i + 2 == input.len() {
        let a = input[i];
        let b_byte = input[i + 1];
        out.push(table[((a >> 2) & 0x3f) as usize] as char);
        out.push(table[(((a << 4) | (b_byte >> 4)) & 0x3f) as usize] as char);
        out.push(table[((b_byte << 2) & 0x3f) as usize] as char);
    }
    out.replace('+', "-").replace('/', "_")
}

async fn response_json(resp: axum::response::Response) -> Value {
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    serde_json::from_slice(&body).unwrap()
}

fn sample_measurement() -> GateMeasurement {
    GateMeasurement {
        measurement_id: "M-001".to_string(),
        name: "gap_width".to_string(),
        value: 2.5,
        unit: "mm".to_string(),
        tolerance_low: 2.0,
        tolerance_high: 3.0,
        within_tolerance: true,
        rework_threshold_low: None,
        rework_threshold_high: None,
    }
}

fn sample_event(gate_type: &str, result: &str) -> OpcenterGateResultEvent {
    OpcenterGateResultEvent {
        opcenter_event_id: Some(Uuid::new_v4().to_string()),
        vin: "WBA1A2345678901AB".to_string(),
        gate_id: "GATE-DIM".to_string(),
        gate_type: gate_type.to_string(),
        result: result.to_string(),
        measurements: vec![sample_measurement()],
        measured_at: chrono::Utc::now(),
    }
}

// ---------------------------------------------------------------------------
// Auth tests for GET /gate-results
// ---------------------------------------------------------------------------

/// No Authorization header → 401
#[tokio::test]
async fn test_list_gate_results_missing_auth_returns_401() {
    let app = build_test_app();

    let req = Request::builder()
        .uri("/gate-results")
        .body(Body::empty())
        .unwrap();

    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
}

/// 401 response includes the required error envelope fields.
#[tokio::test]
async fn test_list_gate_results_401_has_error_envelope() {
    let app = build_test_app();

    let req = Request::builder()
        .uri("/gate-results")
        .body(Body::empty())
        .unwrap();

    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);

    let json = response_json(resp).await;
    assert!(json.get("correlation_id").is_some(), "missing correlation_id: {json}");
    assert!(json.get("code").is_some(), "missing code: {json}");
    assert!(json.get("message").is_some(), "missing message: {json}");
}

// ---------------------------------------------------------------------------
// Auth tests for GET /gate-results/{gateResultId}
// ---------------------------------------------------------------------------

/// No Authorization header → 401
#[tokio::test]
async fn test_get_gate_result_missing_auth_returns_401() {
    let app = build_test_app();
    let id = Uuid::new_v4();

    let req = Request::builder()
        .uri(format!("/gate-results/{id}"))
        .body(Body::empty())
        .unwrap();

    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
}

// ---------------------------------------------------------------------------
// Routing tests — authenticated requests reach the handler (stub → 404)
// ---------------------------------------------------------------------------

/// Valid Bearer + valid UUID → 404 from stub (proves routing works).
#[tokio::test]
async fn test_get_gate_result_valid_token_returns_404_on_stub() {
    let app = build_test_app();
    let id = Uuid::new_v4();

    let req = Request::builder()
        .uri(format!("/gate-results/{id}"))
        .header(
            header::AUTHORIZATION,
            format!("Bearer {}", make_bearer("validator@bmwgroup.net")),
        )
        .body(Body::empty())
        .unwrap();

    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
}

/// 404 response also carries the required error envelope fields.
#[tokio::test]
async fn test_get_gate_result_404_has_error_envelope() {
    let app = build_test_app();
    let id = Uuid::new_v4();

    let req = Request::builder()
        .uri(format!("/gate-results/{id}"))
        .header(
            header::AUTHORIZATION,
            format!("Bearer {}", make_bearer("validator@bmwgroup.net")),
        )
        .body(Body::empty())
        .unwrap();

    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);

    let json = response_json(resp).await;
    assert!(json.get("correlation_id").is_some(), "missing correlation_id: {json}");
    assert!(json.get("code").is_some(), "missing code: {json}");
    assert!(json.get("message").is_some(), "missing message: {json}");
}

/// Non-UUID gateResultId path param → 4xx client error.
#[tokio::test]
async fn test_get_gate_result_invalid_uuid_is_client_error() {
    let app = build_test_app();

    let req = Request::builder()
        .uri("/gate-results/not-a-uuid")
        .header(
            header::AUTHORIZATION,
            format!("Bearer {}", make_bearer("validator@bmwgroup.net")),
        )
        .body(Body::empty())
        .unwrap();

    let resp = app.oneshot(req).await.unwrap();
    assert!(
        resp.status().is_client_error(),
        "expected 4xx for invalid UUID path param, got {}",
        resp.status()
    );
}

/// GET /gate-results with valid token reaches the stub handler (404 expected).
#[tokio::test]
async fn test_list_gate_results_valid_token_returns_404_on_stub() {
    let app = build_test_app();

    let req = Request::builder()
        .uri("/gate-results")
        .header(
            header::AUTHORIZATION,
            format!("Bearer {}", make_bearer("validator@bmwgroup.net")),
        )
        .body(Body::empty())
        .unwrap();

    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
}

// ---------------------------------------------------------------------------
// Consumer unit tests — validate_event
// ---------------------------------------------------------------------------

/// A valid event passes validation without error.
#[test]
fn test_validate_event_valid() {
    let event = sample_event("dimensional", "pass");
    assert!(
        validate_event(&event).is_ok(),
        "expected valid event to pass validation"
    );
}

/// Unknown gate_type routes to DLQ (schema validation error).
#[test]
fn test_validate_event_unknown_gate_type_is_schema_error() {
    let event = OpcenterGateResultEvent {
        gate_type: "unknown_type".to_string(),
        ..sample_event("dimensional", "pass")
    };
    match validate_event(&event) {
        Err(ProcessError::SchemaValidation(msg)) => {
            assert!(
                msg.contains("unknown_type"),
                "error message should name the bad value: {msg}"
            );
        }
        other => panic!("expected SchemaValidation, got {:?}", other),
    }
}

/// Unknown result routes to DLQ (schema validation error).
#[test]
fn test_validate_event_unknown_result_is_schema_error() {
    let event = OpcenterGateResultEvent {
        result: "broken".to_string(),
        ..sample_event("leak", "fail")
    };
    match validate_event(&event) {
        Err(ProcessError::SchemaValidation(msg)) => {
            assert!(
                msg.contains("broken"),
                "error message should name the bad value: {msg}"
            );
        }
        other => panic!("expected SchemaValidation, got {:?}", other),
    }
}

/// Invalid VIN (too short) routes to DLQ.
#[test]
fn test_validate_event_invalid_vin_is_schema_error() {
    let event = OpcenterGateResultEvent {
        vin: "SHORT".to_string(),
        ..sample_event("electrical", "pass")
    };
    match validate_event(&event) {
        Err(ProcessError::SchemaValidation(_)) => {}
        other => panic!("expected SchemaValidation for short VIN, got {:?}", other),
    }
}

/// VIN containing excluded letter I routes to DLQ (I is not in ^[A-HJ-NPR-Z0-9]{17}$).
#[test]
fn test_validate_event_vin_with_excluded_letter_is_schema_error() {
    // VIN with 'I' which is excluded by the regex
    let event = OpcenterGateResultEvent {
        vin: "WBAIIIIIIIIIIIIIA".to_string(),
        ..sample_event("nvh", "conditional_pass")
    };
    match validate_event(&event) {
        Err(ProcessError::SchemaValidation(_)) => {}
        other => panic!("expected SchemaValidation for VIN with 'I', got {:?}", other),
    }
}

/// All five valid gate types pass validation.
#[test]
fn test_validate_event_all_gate_types_pass() {
    for gate_type in &["dimensional", "leak", "electrical", "nvh", "adas"] {
        let event = sample_event(gate_type, "pass");
        assert!(
            validate_event(&event).is_ok(),
            "gate_type '{}' should pass validation", gate_type
        );
    }
}

/// All three valid result values pass validation.
#[test]
fn test_validate_event_all_results_pass() {
    for result in &["pass", "conditional_pass", "fail"] {
        let event = sample_event("dimensional", result);
        assert!(
            validate_event(&event).is_ok(),
            "result '{}' should pass validation", result
        );
    }
}

/// Empty measurements array routes to DLQ.
#[test]
fn test_validate_event_empty_measurements_is_schema_error() {
    let event = OpcenterGateResultEvent {
        measurements: vec![],
        ..sample_event("adas", "fail")
    };
    match validate_event(&event) {
        Err(ProcessError::SchemaValidation(_)) => {}
        other => panic!("expected SchemaValidation for empty measurements, got {:?}", other),
    }
}
