use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use chrono::{DateTime, Utc};
use serde::Deserialize;
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

use crate::{
    auth::BearerClaims,
    errors::AppError,
    models::{CursorPage, GateMeasurement, GateResult, GateResultListResponse},
};

// ---------------------------------------------------------------------------
// Cursor encoding — opaque base64url-encoded "received_at_ns|gate_result_id"
// ---------------------------------------------------------------------------

fn encode_cursor(received_at: DateTime<Utc>, id: Uuid) -> String {
    let raw = format!("{}|{}", received_at.timestamp_nanos_opt().unwrap_or(0), id);
    b64url_encode(raw.as_bytes())
}

fn decode_cursor(cursor: &str) -> Option<(DateTime<Utc>, Uuid)> {
    let bytes = b64url_decode(cursor)?;
    let s = std::str::from_utf8(&bytes).ok()?;
    let (ts_str, id_str) = s.split_once('|')?;
    let nanos: i64 = ts_str.parse().ok()?;
    let secs = nanos / 1_000_000_000;
    let nsub = (nanos % 1_000_000_000) as u32;
    let dt = DateTime::from_timestamp(secs, nsub)?;
    let id = Uuid::parse_str(id_str).ok()?;
    Some((dt, id))
}

fn b64url_encode(input: &[u8]) -> String {
    let table = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::with_capacity((input.len() * 4 + 2) / 3);
    let mut i = 0;
    while i + 2 < input.len() {
        let a = input[i];
        let b = input[i + 1];
        let c = input[i + 2];
        out.push(table[((a >> 2) & 0x3f) as usize] as char);
        out.push(table[(((a << 4) | (b >> 4)) & 0x3f) as usize] as char);
        out.push(table[(((b << 2) | (c >> 6)) & 0x3f) as usize] as char);
        out.push(table[(c & 0x3f) as usize] as char);
        i += 3;
    }
    if i + 1 == input.len() {
        let a = input[i];
        out.push(table[((a >> 2) & 0x3f) as usize] as char);
        out.push(table[((a << 4) & 0x3f) as usize] as char);
    } else if i + 2 == input.len() {
        let a = input[i];
        let b = input[i + 1];
        out.push(table[((a >> 2) & 0x3f) as usize] as char);
        out.push(table[(((a << 4) | (b >> 4)) & 0x3f) as usize] as char);
        out.push(table[((b << 2) & 0x3f) as usize] as char);
    }
    out
}

fn b64url_decode(input: &str) -> Option<Vec<u8>> {
    let val = |c: u8| -> Option<u8> {
        match c {
            b'A'..=b'Z' => Some(c - b'A'),
            b'a'..=b'z' => Some(c - b'a' + 26),
            b'0'..=b'9' => Some(c - b'0' + 52),
            b'-' | b'+' => Some(62),
            b'_' | b'/' => Some(63),
            b'=' => Some(0),
            _ => None,
        }
    };
    let bytes = input.as_bytes();
    // Handle unpadded input — pad to multiple of 4
    let pad = (4 - bytes.len() % 4) % 4;
    let padded: Vec<u8> = bytes.iter().copied().chain(std::iter::repeat(b'=').take(pad)).collect();
    if padded.len() % 4 != 0 {
        return None;
    }
    let mut out = Vec::with_capacity(padded.len() / 4 * 3);
    let mut i = 0;
    while i + 4 <= padded.len() {
        let a = val(padded[i])?;
        let b = val(padded[i + 1])?;
        let c = val(padded[i + 2])?;
        let d = val(padded[i + 3])?;
        out.push(a << 2 | b >> 4);
        if padded[i + 2] != b'=' {
            out.push(b << 4 | c >> 2);
        }
        if padded[i + 3] != b'=' {
            out.push(c << 6 | d);
        }
        i += 4;
    }
    Some(out)
}

// ---------------------------------------------------------------------------
// Query parameter models
// ---------------------------------------------------------------------------

/// Query parameters for `GET /gate-results`.
#[derive(Debug, Deserialize)]
pub struct ListGateResultsParams {
    pub cursor: Option<String>,
    #[serde(default = "default_limit")]
    pub limit: i64,
    pub vin: Option<String>,
    #[serde(rename = "gateType")]
    pub gate_type: Option<String>,
    pub result: Option<String>,
    #[serde(rename = "receivedAfter")]
    pub received_after: Option<DateTime<Utc>>,
}

fn default_limit() -> i64 {
    50
}

// ---------------------------------------------------------------------------
// Database row structs
// ---------------------------------------------------------------------------

#[derive(FromRow)]
struct GateResultRow {
    gate_result_id: Uuid,
    vin: String,
    gate_id: String,
    gate_type: String,
    result: String,
    measurements: serde_json::Value,
    measured_at: DateTime<Utc>,
    received_from_opcenter_at: DateTime<Utc>,
    opcenter_event_id: Option<String>,
}

impl GateResultRow {
    fn into_model(self) -> Result<GateResult, AppError> {
        let measurements: Vec<GateMeasurement> =
            serde_json::from_value(self.measurements).map_err(|err| {
                tracing::error!(gate_result_id = %self.gate_result_id, error = %err, "failed to deserialise measurements JSONB");
                AppError::internal("An unexpected error occurred")
            })?;
        Ok(GateResult {
            gate_result_id: self.gate_result_id,
            vin: self.vin,
            gate_id: self.gate_id,
            gate_type: self.gate_type,
            result: self.result,
            measurements,
            measured_at: self.measured_at,
            received_from_opcenter_at: self.received_from_opcenter_at,
            opcenter_event_id: self.opcenter_event_id,
        })
    }
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

/// `GET /gate-results`
///
/// Returns paginated gate results, optionally filtered by VIN, gateType,
/// result, and receivedAfter.  Cursor pagination over
/// (received_from_opcenter_at DESC, gate_result_id DESC).
///
/// - 200 GateResultListResponse
/// - 401 if no valid Bearer token
/// - 500 on database failure
pub async fn list_gate_results(
    _claims: BearerClaims,
    State(pool): State<PgPool>,
    Query(params): Query<ListGateResultsParams>,
) -> Result<impl IntoResponse, AppError> {
    // Validate limit bounds (1–200, default 50).
    if params.limit < 1 || params.limit > 200 {
        return Err(AppError::bad_request(
            "limit must be between 1 and 200",
        ));
    }

    // Validate VIN format if provided.
    if let Some(ref vin) = params.vin {
        if !is_valid_vin(vin) {
            return Err(AppError::bad_request(
                "vin must be a 17-character alphanumeric string matching ^[A-HJ-NPR-Z0-9]{17}$",
            ));
        }
    }

    // Validate gateType enum if provided.
    if let Some(ref gt) = params.gate_type {
        if !["dimensional", "leak", "electrical", "nvh", "adas"].contains(&gt.as_str()) {
            return Err(AppError::bad_request(
                "gateType must be one of: dimensional, leak, electrical, nvh, adas",
            ));
        }
    }

    // Validate result enum if provided.
    if let Some(ref r) = params.result {
        if !["pass", "conditional_pass", "fail"].contains(&r.as_str()) {
            return Err(AppError::bad_request(
                "result must be one of: pass, conditional_pass, fail",
            ));
        }
    }

    // Decode cursor if present.
    let cursor_vals: Option<(DateTime<Utc>, Uuid)> = if let Some(ref c) = params.cursor {
        Some(decode_cursor(c).ok_or_else(|| AppError::bad_request("cursor is invalid"))?)
    } else {
        None
    };

    // Fetch limit+1 rows so we can detect whether a next page exists.
    let fetch_limit = params.limit + 1;

    // The cursor is decoded to (received_from_opcenter_at, gate_result_id).
    // Keyset: rows where received_from_opcenter_at < cursor_ts, OR equal
    // timestamp but gate_result_id (UUID text) < cursor_id to break ties.
    let rows = if let Some((cursor_ts, cursor_id)) = cursor_vals {
        sqlx::query_as::<_, GateResultRow>(
            r#"
            SELECT
                gate_result_id,
                vin,
                gate_id,
                gate_type,
                result,
                measurements,
                measured_at,
                received_from_opcenter_at,
                opcenter_event_id
            FROM gate_result
            WHERE
                ($1::TEXT IS NULL OR vin = $1)
                AND ($2::TEXT IS NULL OR gate_type = $2)
                AND ($3::TEXT IS NULL OR result = $3)
                AND ($4::TIMESTAMPTZ IS NULL OR received_from_opcenter_at > $4)
                AND (
                    received_from_opcenter_at < $5
                    OR (received_from_opcenter_at = $5 AND gate_result_id < $6)
                )
            ORDER BY received_from_opcenter_at DESC, gate_result_id DESC
            LIMIT $7
            "#,
        )
        .bind(params.vin.as_deref())
        .bind(params.gate_type.as_deref())
        .bind(params.result.as_deref())
        .bind(params.received_after)
        .bind(cursor_ts)
        .bind(cursor_id)
        .bind(fetch_limit)
        .fetch_all(&pool)
        .await
        .map_err(|err| {
            tracing::error!(error = %err, "database error listing gate results");
            AppError::internal("An unexpected error occurred")
        })?
    } else {
        sqlx::query_as::<_, GateResultRow>(
            r#"
            SELECT
                gate_result_id,
                vin,
                gate_id,
                gate_type,
                result,
                measurements,
                measured_at,
                received_from_opcenter_at,
                opcenter_event_id
            FROM gate_result
            WHERE
                ($1::TEXT IS NULL OR vin = $1)
                AND ($2::TEXT IS NULL OR gate_type = $2)
                AND ($3::TEXT IS NULL OR result = $3)
                AND ($4::TIMESTAMPTZ IS NULL OR received_from_opcenter_at > $4)
            ORDER BY received_from_opcenter_at DESC, gate_result_id DESC
            LIMIT $5
            "#,
        )
        .bind(params.vin.as_deref())
        .bind(params.gate_type.as_deref())
        .bind(params.result.as_deref())
        .bind(params.received_after)
        .bind(fetch_limit)
        .fetch_all(&pool)
        .await
        .map_err(|err| {
            tracing::error!(error = %err, "database error listing gate results");
            AppError::internal("An unexpected error occurred")
        })?
    };

    let has_next = rows.len() as i64 > params.limit;
    let rows: Vec<GateResultRow> = rows.into_iter().take(params.limit as usize).collect();

    let next_cursor = if has_next {
        rows.last().map(|r| encode_cursor(r.received_from_opcenter_at, r.gate_result_id))
    } else {
        None
    };

    let data: Vec<GateResult> = rows
        .into_iter()
        .map(|r| r.into_model())
        .collect::<Result<_, _>>()?;

    Ok((
        StatusCode::OK,
        Json(GateResultListResponse {
            data,
            page: CursorPage {
                next_cursor,
                limit: params.limit,
            },
        }),
    ))
}

/// `GET /gate-results/{gateResultId}`
///
/// Returns a single gate result by ID.
///
/// - 200 GateResult
/// - 401 if no valid Bearer token
/// - 404 if not found
/// - 500 on database failure
pub async fn get_gate_result(
    _claims: BearerClaims,
    State(pool): State<PgPool>,
    Path(gate_result_id): Path<Uuid>,
) -> Result<impl IntoResponse, AppError> {
    let row: Option<GateResultRow> = sqlx::query_as(
        r#"
        SELECT
            gate_result_id,
            vin,
            gate_id,
            gate_type,
            result,
            measurements,
            measured_at,
            received_from_opcenter_at,
            opcenter_event_id
        FROM gate_result
        WHERE gate_result_id = $1
        "#,
    )
    .bind(gate_result_id)
    .fetch_optional(&pool)
    .await
    .map_err(|err| {
        tracing::error!(
            gate_result_id = %gate_result_id,
            error = %err,
            "database error fetching gate result"
        );
        AppError::internal("An unexpected error occurred")
    })?;

    let row = row.ok_or_else(|| {
        AppError::not_found(format!("Gate result '{gate_result_id}' not found"))
    })?;

    Ok((StatusCode::OK, Json(row.into_model()?)))
}

// ---------------------------------------------------------------------------
// VIN validation helper
// ---------------------------------------------------------------------------

fn is_valid_vin(vin: &str) -> bool {
    if vin.len() != 17 {
        return false;
    }
    vin.chars().all(|c| matches!(c, 'A'..='H' | 'J'..='N' | 'P'..='Z' | '0'..='9'))
}
