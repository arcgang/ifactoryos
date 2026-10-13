use opcenter_sync_service::{config::Config, db, router};
use tracing_subscriber::{fmt, prelude::*, EnvFilter};

#[tokio::main]
async fn main() {
    // Load .env for local development; ignore if absent.
    let _ = dotenvy::dotenv();

    // Structured JSON logging driven by RUST_LOG.
    tracing_subscriber::registry()
        .with(EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .with(fmt::layer().json())
        .init();

    // Read and validate all configuration at startup — fail fast on missing vars.
    let cfg = Config::from_env();

    // Single shared connection pool for the process lifetime.
    let pool = db::create_pool(&cfg.database_url)
        .await
        .expect("Failed to connect to the database");

    let app = router::build_router(pool);

    let addr: std::net::SocketAddr = cfg
        .bind_addr
        .parse()
        .expect("BIND_ADDR is not a valid socket address");

    tracing::info!(addr = %addr, "opcenter-sync-service starting");

    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .expect("Failed to bind TCP listener");

    axum::serve(listener, app)
        .await
        .expect("Server error");
}
