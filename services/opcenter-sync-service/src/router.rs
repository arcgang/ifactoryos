use axum::{
    routing::get,
    Router,
};
use sqlx::PgPool;

use crate::handlers::andon::get_andon_alert;

/// Build the application router with all routes wired.
///
/// The pool is stored in shared state and injected into each handler via
/// `State<PgPool>`.
pub fn build_router(pool: PgPool) -> Router {
    Router::new()
        .route("/andon/alerts/:alertId", get(get_andon_alert))
        .with_state(pool)
}
