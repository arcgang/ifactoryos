use axum::{
    routing::get,
    Router,
};
use sqlx::PgPool;

use crate::handlers::andon::get_andon_alert;
use crate::handlers::gate_results::{get_gate_result, list_gate_results};

/// Build the application router with all routes wired.
///
/// The pool is stored in shared state and injected into each handler via
/// `State<PgPool>`.
pub fn build_router(pool: PgPool) -> Router {
    Router::new()
        .route("/andon/alerts/:alertId", get(get_andon_alert))
        .route("/gate-results", get(list_gate_results))
        .route("/gate-results/:gateResultId", get(get_gate_result))
        .with_state(pool)
}
