pub mod auth;
pub mod config;
pub mod db;
pub mod errors;
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
        extract::Path,
        http::StatusCode,
        response::IntoResponse,
        routing::get,
        Json, Router,
    };
    use uuid::Uuid;

    use crate::{auth::BearerClaims, errors::AppError};

    async fn stub_get_andon_alert(
        _claims: BearerClaims,
        Path(_alert_id): Path<Uuid>,
    ) -> Result<impl IntoResponse, AppError> {
        Err::<(StatusCode, Json<serde_json::Value>), _>(AppError::not_found(
            "stub: no database in unit tests",
        ))
    }

    Router::new().route("/andon/alerts/:alertId", get(stub_get_andon_alert))
}
