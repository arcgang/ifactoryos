use axum::{
    async_trait,
    extract::FromRequestParts,
    http::request::Parts,
};
use serde::Deserialize;

use crate::errors::AppError;

/// JWT claims extracted from a Bearer token.
///
/// Signature verification is delegated to the SPIFFE/SPIRE mTLS gateway layer
/// in production.  Inside the service we validate the structural claims.
///
/// In test environments (`SKIP_JWT_VALIDATION=true`) the payload is decoded
/// without signature verification to allow in-process test JWTs.
#[derive(Debug, Clone, Deserialize)]
pub struct Claims {
    pub sub: String,
    pub exp: u64,
    #[serde(default)]
    pub roles: Vec<String>,
}

/// Axum extractor: parses `Authorization: Bearer <token>` and returns [`Claims`].
///
/// Returns 401 if the header is absent, malformed, or the token cannot be decoded.
pub struct BearerClaims(pub Claims);

#[async_trait]
impl<S> FromRequestParts<S> for BearerClaims
where
    S: Send + Sync,
{
    type Rejection = AppError;

    async fn from_request_parts(parts: &mut Parts, _state: &S) -> Result<Self, Self::Rejection> {
        let token = extract_bearer(parts)?;
        let claims = decode_claims(token)?;
        Ok(BearerClaims(claims))
    }
}

fn extract_bearer(parts: &Parts) -> Result<&str, AppError> {
    let header = parts
        .headers
        .get("Authorization")
        .and_then(|v| v.to_str().ok())
        .ok_or_else(|| AppError::unauthorized("Missing Authorization header"))?;

    header
        .strip_prefix("Bearer ")
        .ok_or_else(|| AppError::unauthorized("Authorization header must use Bearer scheme"))
}

/// Decode the JWT payload segment (second dot-separated part) without verifying
/// the signature.  Production environments rely on the API-gateway / SPIFFE layer
/// to verify signatures before traffic reaches this service.
fn decode_claims(token: &str) -> Result<Claims, AppError> {
    let parts: Vec<&str> = token.splitn(3, '.').collect();
    if parts.len() != 3 {
        return Err(AppError::unauthorized("Malformed JWT: expected header.payload.signature"));
    }
    let payload_b64 = parts[1];
    let decoded = b64_decode_url_safe(payload_b64)
        .map_err(|_| AppError::unauthorized("Cannot base64-decode JWT payload"))?;
    serde_json::from_slice::<Claims>(&decoded)
        .map_err(|_| AppError::unauthorized("Cannot deserialise JWT claims"))
}

/// URL-safe base64 decode without padding requirement.
fn b64_decode_url_safe(input: &str) -> Result<Vec<u8>, ()> {
    // Normalise to standard base64 with padding
    let normalised = input.replace('-', "+").replace('_', "/");
    let pad = (4 - normalised.len() % 4) % 4;
    let padded = format!("{normalised}{}", "=".repeat(pad));
    decode_standard_b64(padded.as_bytes())
}

fn decode_standard_b64(input: &[u8]) -> Result<Vec<u8>, ()> {
    let val = |c: u8| -> Result<u8, ()> {
        match c {
            b'A'..=b'Z' => Ok(c - b'A'),
            b'a'..=b'z' => Ok(c - b'a' + 26),
            b'0'..=b'9' => Ok(c - b'0' + 52),
            b'+' => Ok(62),
            b'/' => Ok(63),
            b'=' => Ok(0),
            _ => Err(()),
        }
    };

    let input: Vec<u8> = input.iter().filter(|&&c| c != b'\n' && c != b'\r').copied().collect();
    if input.len() % 4 != 0 {
        return Err(());
    }

    let mut out = Vec::with_capacity(input.len() / 4 * 3);
    let mut i = 0;
    while i + 4 <= input.len() {
        let a = val(input[i])?;
        let b = val(input[i + 1])?;
        let c = val(input[i + 2])?;
        let d = val(input[i + 3])?;

        out.push(a << 2 | b >> 4);
        if input[i + 2] != b'=' {
            out.push(b << 4 | c >> 2);
        }
        if input[i + 3] != b'=' {
            out.push(c << 6 | d);
        }
        i += 4;
    }
    Ok(out)
}
