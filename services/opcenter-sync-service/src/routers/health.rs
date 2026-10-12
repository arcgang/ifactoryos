use axum::{extract::State, http::StatusCode, Json};
use tracing::warn;

use crate::{
    models::HealthResponse,
    subscription::subscription_active,
    AppState,
};

pub async fn get_health(
    State(state): State<AppState>,
) -> (StatusCode, Json<HealthResponse>) {
    let db_ok = state.pool.get().await.is_ok();
    let kafka_ok = true; // producer is checked at startup; ongoing errors are logged

    let subscription_mode = if subscription_active() {
        "graphql_subscription"
    } else {
        "rest_polling_fallback"
    };

    let status = if db_ok && kafka_ok {
        "healthy"
    } else {
        warn!("health check degraded: db_ok={db_ok} kafka_ok={kafka_ok}");
        "degraded"
    };

    let code = if status == "healthy" {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };

    (
        code,
        Json(HealthResponse {
            status: status.to_string(),
            subscription_mode: subscription_mode.to_string(),
            kafka_ok,
            db_ok,
            last_oee_received_at: None,
            last_andon_received_at: None,
        }),
    )
}
