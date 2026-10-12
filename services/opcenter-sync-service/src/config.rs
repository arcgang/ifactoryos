use serde::Deserialize;
use std::time::Duration;

#[derive(Debug, Deserialize, Clone)]
pub struct Settings {
    // PostgreSQL
    pub database_url: String,
    pub db_max_connections: u32,

    // Kafka
    pub kafka_bootstrap_servers: String,
    pub kafka_sasl_username: String,
    pub kafka_sasl_password: String,
    pub kafka_topic_oee: String,
    pub kafka_topic_andon: String,

    // OpCenter GraphQL subscription endpoint
    pub opcenter_graphql_ws_url: String,
    // OpCenter REST fallback base URL
    pub opcenter_rest_url: String,
    // OpCenter API key (injected by Vault agent)
    pub opcenter_api_key: String,

    // SPIFFE/SPIRE SVID paths (mTLS)
    pub spiffe_svid_cert_path: String,
    pub spiffe_svid_key_path: String,
    pub spiffe_bundle_path: String,

    // Schema Registry
    pub schema_registry_url: String,

    // Service tuning
    #[serde(default = "default_rest_poll_interval_secs")]
    pub rest_poll_interval_secs: u64,
    #[serde(default = "default_http_timeout_secs")]
    pub http_timeout_secs: u64,
    #[serde(default = "default_bind_addr")]
    pub bind_addr: String,
}

fn default_rest_poll_interval_secs() -> u64 {
    10
}
fn default_http_timeout_secs() -> u64 {
    10
}
fn default_bind_addr() -> String {
    "0.0.0.0:8080".to_string()
}

impl Settings {
    pub fn from_env() -> anyhow::Result<Self> {
        dotenvy::dotenv().ok();
        let s = config::Config::builder()
            .add_source(config::Environment::default().separator("__"))
            .build()?;
        Ok(s.try_deserialize()?)
    }

    pub fn http_timeout(&self) -> Duration {
        Duration::from_secs(self.http_timeout_secs)
    }

    pub fn rest_poll_interval(&self) -> Duration {
        Duration::from_secs(self.rest_poll_interval_secs)
    }
}
