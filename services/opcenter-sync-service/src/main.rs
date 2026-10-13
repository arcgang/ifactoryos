mod auth;
mod config;
mod cursor;
mod db;
mod event_processor;
mod kafka;
mod metrics;
mod models;
mod retry_worker;
mod routers;
mod schema_validator;
mod subscription;

use std::sync::Arc;

use axum::{routing::get, routing::post, Router};
use rdkafka::producer::FutureProducer;
use tracing::info;
use tracing_subscriber::{fmt, prelude::*, EnvFilter};

/// Shared application state injected into every Axum handler via State extractor.
#[derive(Clone)]
pub struct AppState {
    pub pool: db::DbPool,
    pub settings: Arc<config::Settings>,
    pub producer: Arc<FutureProducer>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Structured JSON logging; level controlled by RUST_LOG env var.
    tracing_subscriber::registry()
        .with(EnvFilter::from_default_env())
        .with(fmt::layer().json())
        .init();

    // Fail fast with a clear message if any required env var is absent.
    let settings = Arc::new(
        config::Settings::from_env()
            .expect("configuration error — check all required env vars (see .env.example)"),
    );

    info!("starting opcenter-sync-service");

    // One pool for the process lifetime.
    let pool = db::create_pool(&settings)?;

    // Apply migrations idempotently at startup.
    db::run_migrations(&pool).await?;

    let producer = Arc::new(kafka::create_producer(&settings)?);

    // Start GraphQL subscription loop (REST polling fallback spawned internally).
    subscription::spawn_subscription_loop(settings.clone(), pool.clone(), producer.clone());

    // Start retry worker for queued sequence publish jobs.
    retry_worker::spawn_retry_worker(settings.clone(), pool.clone(), producer.clone());

    let state = AppState {
        pool,
        settings: settings.clone(),
        producer,
    };

    let app = Router::new()
        .route("/health", get(routers::health::get_health))
        .route("/oee/snapshots", get(routers::oee::list_oee_snapshots))
        .route("/andon/alerts", get(routers::andon::list_andon_alerts))
        .route(
            "/andon/alerts/:alert_id",
            get(routers::andon::get_andon_alert),
        )
        .route(
            "/sequence/publish",
            post(routers::sequence::publish_sequence),
        )
        .route("/sequence/jobs", get(routers::sequence::list_sync_jobs))
        .route(
            "/sequence/jobs/:sync_job_id",
            get(routers::sequence::get_sync_job),
        )
        .route("/metrics", get(serve_metrics))
        .with_state(state);

    let addr = settings.bind_addr.parse()?;
    info!(bind_addr = %addr, "listening");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

async fn serve_metrics() -> String {
    metrics::gather_metrics()
}
