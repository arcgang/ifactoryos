/// Tests for GET /andon/alerts/{alertId} — auth extraction, routing, and error shape.
///
/// These are router-level tests that do not need a live database.  The stub
/// router in `lib.rs` (`test_router()`) validates auth and returns 404 for
/// every valid request (no rows exist).  Each test asserts exact HTTP status
/// codes and error response shapes per the OpenAPI contract.
use axum::{
    body::Body,
    http::{header, Request, StatusCode},
};
use serde_json::Value;
use tower::ServiceExt;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

/// Build the stub test router (validates auth, always 404 for authenticated requests).
fn build_test_app() -> axum::Router {
    opcenter_sync_service::test_router()
}

/// Encode a minimal JWT payload without a signature for test use.
///
/// The service decodes without signature verification (structural only), so any
/// base64url-encoded JSON payload with `sub` and `exp` fields is accepted.
fn make_bearer(sub: &str) -> String {
    let exp: u64 = 9_999_999_999; // far-future constant — never expires in tests
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
        let b = input[i + 1];
        let c = input[i + 2];
        out.push(table[((a >> 2) & 0x3f) as usize] as char);
        out.push(table[(((a << 4) | (b >> 4)) & 0x3f) as usize] as char);
        out.push(table[(((b << 2) | (c >> 6)) & 0x3f) as usize] as char);
        out.push(table[(c & 0x3f) as usize] as char);
        i += 3;
    }
    if i + 1 == input.len() {
        let a = input[i];
        out.push(table[((a >> 2) & 0x3f) as usize] as char);
        out.push(table[((a << 4) & 0x3f) as usize] as char);
    } else if i + 2 == input.len() {
        let a = input[i];
        let b = input[i + 1];
        out.push(table[((a >> 2) & 0x3f) as usize] as char);
        out.push(table[(((a << 4) | (b >> 4)) & 0x3f) as usize] as char);
        out.push(table[((b << 2) & 0x3f) as usize] as char);
    }
    // URL-safe: replace + with - and / with _; no padding
    out.replace('+', "-").replace('/', "_")
}

async fn response_json(resp: axum::response::Response) -> Value {
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    serde_json::from_slice(&body).unwrap()
}

// ---------------------------------------------------------------------------
// Auth tests
// ---------------------------------------------------------------------------

/// No Authorization header → 401
#[tokio::test]
async fn test_missing_auth_returns_401() {
    let app = build_test_app();
    let alert_id = Uuid::new_v4();

    let req = Request::builder()
        .uri(format!("/andon/alerts/{alert_id}"))
        .body(Body::empty())
        .unwrap();

    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
}

/// Authorization header uses wrong scheme → 401
#[tokio::test]
async fn test_malformed_bearer_scheme_returns_401() {
    let app = build_test_app();
    let alert_id = Uuid::new_v4();

    let req = Request::builder()
        .uri(format!("/andon/alerts/{alert_id}"))
        .header(header::AUTHORIZATION, "Token something")
        .body(Body::empty())
        .unwrap();

    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
}

/// All error responses include `correlation_id`, `code`, and `message`.
#[tokio::test]
async fn test_401_error_body_has_required_fields() {
    let app = build_test_app();
    let alert_id = Uuid::new_v4();

    let req = Request::builder()
        .uri(format!("/andon/alerts/{alert_id}"))
        .body(Body::empty())
        .unwrap();

    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);

    let json = response_json(resp).await;
    assert!(json.get("correlation_id").is_some(), "error body missing correlation_id: {json}");
    assert!(json.get("code").is_some(), "error body missing code: {json}");
    assert!(json.get("message").is_some(), "error body missing message: {json}");
}

// ---------------------------------------------------------------------------
// Routing tests
// ---------------------------------------------------------------------------

/// Valid Bearer token + valid UUID → 404 (no rows in stub router, proves routing works).
#[tokio::test]
async fn test_valid_token_returns_404_on_stub_router() {
    let app = build_test_app();
    let alert_id = Uuid::new_v4();

    let req = Request::builder()
        .uri(format!("/andon/alerts/{alert_id}"))
        .header(header::AUTHORIZATION, format!("Bearer {}", make_bearer("sl@bmwgroup.net")))
        .body(Body::empty())
        .unwrap();

    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
}

/// 404 response also carries the required error envelope fields.
#[tokio::test]
async fn test_404_error_body_has_required_fields() {
    let app = build_test_app();
    let alert_id = Uuid::new_v4();

    let req = Request::builder()
        .uri(format!("/andon/alerts/{alert_id}"))
        .header(header::AUTHORIZATION, format!("Bearer {}", make_bearer("user@example.com")))
        .body(Body::empty())
        .unwrap();

    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);

    let json = response_json(resp).await;
    assert!(json.get("correlation_id").is_some(), "404 body missing correlation_id: {json}");
    assert!(json.get("code").is_some(), "404 body missing code: {json}");
    assert!(json.get("message").is_some(), "404 body missing message: {json}");
}

/// Non-UUID path param → client error (4xx) from Axum's path extractor.
#[tokio::test]
async fn test_invalid_uuid_alertid_is_client_error() {
    let app = build_test_app();

    let req = Request::builder()
        .uri("/andon/alerts/not-a-uuid")
        .header(header::AUTHORIZATION, format!("Bearer {}", make_bearer("user@example.com")))
        .body(Body::empty())
        .unwrap();

    let resp = app.oneshot(req).await.unwrap();
    assert!(
        resp.status().is_client_error(),
        "expected 4xx for invalid UUID path param, got {}",
        resp.status()
    );
}
