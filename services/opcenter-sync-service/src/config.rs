use std::env;

/// Application configuration — read from environment at startup and fail fast.
#[derive(Clone, Debug)]
pub struct Config {
    pub database_url: String,
    pub bind_addr: String,
    /// Issuer claim expected in Bearer JWT tokens.
    pub jwt_issuer: String,
    /// Audience claim expected in Bearer JWT tokens.
    pub jwt_audience: String,
}

impl Config {
    /// Read all required environment variables; panic with a clear message on any missing one.
    pub fn from_env() -> Self {
        let database_url = require_env("DATABASE_URL");
        let bind_addr = env::var("BIND_ADDR").unwrap_or_else(|_| "0.0.0.0:8080".to_string());
        let jwt_issuer = require_env("JWT_ISSUER");
        let jwt_audience =
            env::var("JWT_AUDIENCE").unwrap_or_else(|_| "opcenter-sync-service".to_string());

        Self {
            database_url,
            bind_addr,
            jwt_issuer,
            jwt_audience,
        }
    }
}

fn require_env(name: &str) -> String {
    env::var(name).unwrap_or_else(|_| panic!("Required environment variable {name} is not set"))
}
