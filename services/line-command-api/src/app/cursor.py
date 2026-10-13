"""Opaque keyset cursor helpers for list endpoints.

Cursor format: base64(ISO8601-timestamp:UUID)
This matches the opcenter-sync-service convention so both services
produce and consume the same cursor shape.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone


def encode_cursor(ts: datetime, row_id: str) -> str:
    raw = f"{ts.astimezone(timezone.utc).isoformat()}:{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_part, id_part = raw.rsplit(":", 1)
        ts = datetime.fromisoformat(ts_part)
        return ts, id_part
    except Exception as exc:
        raise ValueError(f"Invalid cursor: {cursor!r}") from exc
