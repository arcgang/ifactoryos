"""Business logic for cluster alert list and acknowledge operations."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from typing import Any


def encode_cursor(row_id: str) -> str:
    return base64.urlsafe_b64encode(row_id.encode()).decode()


def decode_cursor(cursor: str) -> str | None:
    try:
        return base64.urlsafe_b64decode(cursor.encode()).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None


def build_alert_list_response(
    rows: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    """Build the AlertListResponse from DB rows, applying cursor pagination."""
    records = [_row_to_alert(r) for r in rows[:limit]]
    next_cursor: str | None = None
    if len(rows) > limit:
        next_cursor = encode_cursor(str(rows[limit - 1]["alert_id"]))
    return {
        "data": records,
        "page": {"next_cursor": next_cursor, "limit": limit},
    }


def _row_to_alert(row: dict[str, Any]) -> dict[str, Any]:
    def _iso(v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            return v.isoformat()
        return str(v)

    return {
        "alert_id": str(row["alert_id"]),
        "cluster_id": str(row["cluster_id"]),
        "defect_class": row["defect_class"],
        "status": row["status"],
        "affected_vin_count": int(row["affected_vin_count"]),
        "root_cause_signature": row["root_cause_signature"],
        "confidence_score": float(row["confidence_score"]),
        "most_recent_vin": row["most_recent_vin"],
        "process_replay_url": row["process_replay_url"],
        "triggered_at": _iso(row["triggered_at"]),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
        "acknowledged_at": _iso(row.get("acknowledged_at")),
        "acknowledged_by_user_id": row.get("acknowledged_by_user_id"),
        "corrective_action_jira_id": row.get("corrective_action_jira_id"),
    }
