use sqlx::{postgres::PgPoolOptions, PgPool};
use std::time::Duration;

/// Create a connection pool with an explicit timeout and a bounded size.
///
/// Called once at startup; the pool is shared for the lifetime of the process.
pub async fn create_pool(database_url: &str) -> Result<PgPool, sqlx::Error> {
    PgPoolOptions::new()
        .max_connections(10)
        .acquire_timeout(Duration::from_secs(5))
        .connect(database_url)
        .await
}
