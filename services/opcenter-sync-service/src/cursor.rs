/// Cursor encoding/decoding for keyset pagination.
/// Cursor = base64(ISO-8601 created_at + ":" + UUID).
use anyhow::{bail, Context};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::{DateTime, Utc};
use uuid::Uuid;

pub struct Cursor {
    pub ts: DateTime<Utc>,
    pub id: Uuid,
}

impl Cursor {
    pub fn encode(ts: DateTime<Utc>, id: Uuid) -> String {
        let raw = format!("{}:{}", ts.to_rfc3339(), id);
        URL_SAFE_NO_PAD.encode(raw.as_bytes())
    }

    pub fn decode(s: &str) -> anyhow::Result<Self> {
        let bytes = URL_SAFE_NO_PAD
            .decode(s)
            .context("cursor: invalid base64")?;
        let raw = String::from_utf8(bytes).context("cursor: not valid utf8")?;
        let mut parts = raw.splitn(2, ':');
        let ts_str = parts.next().unwrap_or("");
        let id_str = parts.next().unwrap_or("");
        let ts = ts_str
            .parse::<DateTime<Utc>>()
            .context("cursor: invalid timestamp")?;
        let id = Uuid::parse_str(id_str).context("cursor: invalid uuid")?;
        Ok(Cursor { ts, id })
    }
}

/// Parse an optional cursor string into (Option<DateTime>, Option<Uuid>).
/// Returns (None, None) when cursor is absent (first page).
pub fn parse_cursor(
    cursor: Option<&str>,
) -> anyhow::Result<(Option<DateTime<Utc>>, Option<Uuid>)> {
    match cursor {
        None | Some("") => Ok((None, None)),
        Some(s) => {
            let c = Cursor::decode(s)?;
            Ok((Some(c.ts), Some(c.id)))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    #[test]
    fn roundtrip_cursor() {
        let ts = Utc.with_ymd_and_hms(2026, 10, 12, 6, 0, 0).unwrap();
        let id = Uuid::parse_str("00000000-0000-0000-0000-000000000001").unwrap();
        let encoded = Cursor::encode(ts, id);
        let decoded = Cursor::decode(&encoded).unwrap();
        assert_eq!(decoded.ts, ts);
        assert_eq!(decoded.id, id);
    }

    #[test]
    fn decode_rejects_garbage() {
        assert!(Cursor::decode("not-a-cursor!!!").is_err());
    }
}
