pub mod auth;
pub mod config;
pub mod db;
pub mod errors;
pub mod gate_result_consumer;
pub mod handlers;
pub mod models;
pub mod router;

/// Build a stub router for use in integration tests.
///
/// The stub validates auth identically to the production router but returns 404
/// for every authenticated request (no real database connection is made).
/// This lets tests assert routing behaviour and auth extraction without a live
/// PostgreSQL instance.
///
/// Available only when the `test-helpers` feature is enabled.
#[cfg(feature = "test-helpers")]
pub fn test_router() -> axum::Router {
    use axum::{
        extract::{Path, Query},
        http::StatusCode,
        response::IntoResponse,
        routing::get,
        Json, Router,
    };
    use uuid::Uuid;

    use crate::{
        auth::BearerClaims,
        errors::AppError,
        handlers::gate_results::ListGateResultsParams,
    };

    async fn stub_get_andon_alert(
        _claims: BearerClaims,
        Path(_alert_id): Path<Uuid>,
    ) -> Result<impl IntoResponse, AppError> {
        Err::<(StatusCode, Json<serde_json::Value>), _>(AppError::not_found(
            "stub: no database in unit tests",
        ))
    }

    async fn stub_list_gate_results(
        _claims: BearerClaims,
        Query(_params): Query<ListGateResultsParams>,
    ) -> Result<impl IntoResponse, AppError> {
        Err::<(StatusCode, Json<serde_json::Value>), _>(AppError::not_found(
            "stub: no database in unit tests",
        ))
    }

    async fn stub_get_gate_result(
        _claims: BearerClaims,
        Path(_gate_result_id): Path<Uuid>,
    ) -> Result<impl IntoResponse, AppError> {
        Err::<(StatusCode, Json<serde_json::Value>), _>(AppError::not_found(
            "stub: no database in unit tests",
        ))
    }

    Router::new()
        .route("/andon/alerts/:alertId", get(stub_get_andon_alert))
        .route("/gate-results", get(stub_list_gate_results))
        .route("/gate-results/:gateResultId", get(stub_get_gate_result))
}
