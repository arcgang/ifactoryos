/// JWT validation for opcenter-sync-service.
/// Validates BMW Entra ID bearer tokens and enforces role claims.
use anyhow::{anyhow, bail};
use jsonwebtoken::{decode, decode_header, Algorithm, DecodingKey, Validation};
use serde::{Deserialize, Serialize};

use crate::config::Settings;

/// Claims shape for decoding — `aud` can be string or array.
#[derive(Debug, Serialize, Deserialize)]
pub struct RawClaims {
    pub sub: Option<String>,
    pub iss: Option<String>,
    #[serde(default)]
    pub roles: Vec<String>,
    pub exp: u64,
}

/// Validate the bearer token and return an error if the required role is absent.
/// For local development (jwt_issuer="dev"), validation is bypassed.
pub fn require_role(token: &str, role: &str, settings: &Settings) -> anyhow::Result<RawClaims> {
    if settings.jwt_issuer == "dev" {
        // Development bypass — do not use in production
        return Ok(RawClaims {
            sub: Some("dev-user".to_string()),
            iss: Some("dev".to_string()),
            roles: vec![role.to_string()],
            exp: u64::MAX,
        });
    }

    let header = decode_header(token).map_err(|e| anyhow!("invalid JWT header: {e}"))?;

    let algorithm = match header.alg {
        jsonwebtoken::Algorithm::RS256 => Algorithm::RS256,
        jsonwebtoken::Algorithm::RS384 => Algorithm::RS384,
        jsonwebtoken::Algorithm::RS512 => Algorithm::RS512,
        other => bail!("unsupported JWT algorithm: {other:?}"),
    };

    let mut validation = Validation::new(algorithm);
    validation.set_issuer(&[&settings.jwt_issuer]);
    validation.set_audience(&[&settings.jwt_audience]);

    // Key must be loaded from JWKS at startup or per-request.
    // For this service, the public key is injected as PEM via env var JWT_PUBLIC_KEY.
    // The Settings struct exposes jwt_public_key as a PEM string.
    let decoding_key = DecodingKey::from_rsa_pem(settings.jwt_public_key.as_bytes())
        .map_err(|e| anyhow!("invalid JWT public key: {e}"))?;

    let token_data = decode::<RawClaims>(token, &decoding_key, &validation)
        .map_err(|e| anyhow!("JWT validation failed: {e}"))?;

    if !token_data.claims.roles.iter().any(|r| r == role) {
        bail!("caller does not have required role '{role}'");
    }

    Ok(token_data.claims)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dev_settings() -> Settings {
        Settings {
            database_url: "postgres://localhost/test".to_string(),
            db_max_connections: 5,
            kafka_bootstrap_servers: "localhost:9092".to_string(),
            kafka_sasl_username: String::new(),
            kafka_sasl_password: String::new(),
            kafka_topic_oee: "bmw.munich.opcenter.oee.updated".to_string(),
            kafka_topic_andon: "bmw.munich.opcenter.andon.alert".to_string(),
            kafka_topic_sequence_published: "bmw.munich.opcenter.sequence.published".to_string(),
            kafka_topic_sequence_publish_failed: "bmw.munich.opcenter.sequence.publish_failed"
                .to_string(),
            opcenter_graphql_ws_url: "wss://opcenter.example.com/graphql".to_string(),
            opcenter_rest_url: "https://opcenter.example.com/api/v1".to_string(),
            opcenter_api_key: "test-key".to_string(),
            planning_cockpit_api_key: "pc-api-key".to_string(),
            audit_log_url: "http://audit-log.internal/api/v1/events".to_string(),
            jwt_issuer: "dev".to_string(),
            jwt_audience: "opcenter-sync-service".to_string(),
            jwt_public_key: String::new(),
            spiffe_svid_cert_path: "/tmp/cert.pem".to_string(),
            spiffe_svid_key_path: "/tmp/key.pem".to_string(),
            spiffe_bundle_path: "/tmp/bundle.pem".to_string(),
            schema_registry_url: "http://localhost:8085".to_string(),
            rest_poll_interval_secs: 10,
            http_timeout_secs: 10,
            bind_addr: "0.0.0.0:8080".to_string(),
            retry_interval_secs: 30,
        }
    }

    #[test]
    fn dev_bypass_grants_any_role() {
        let settings = dev_settings();
        let result = require_role("any-token", "sequence-publish", &settings);
        assert!(result.is_ok(), "dev bypass must succeed: {:?}", result.err());
    }

    #[test]
    fn dev_bypass_returns_expected_sub() {
        let settings = dev_settings();
        let claims = require_role("any-token", "sequence-publish", &settings).unwrap();
        assert_eq!(claims.sub.as_deref(), Some("dev-user"));
    }
}
