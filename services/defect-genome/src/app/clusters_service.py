"""Business logic for defect cluster list and get operations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.alerts_service import decode_cursor, encode_cursor


def build_cluster_list_response(
    rows: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    records = [_row_to_cluster(r) for r in rows[:limit]]
    next_cursor: str | None = None
    if len(rows) > limit:
        next_cursor = encode_cursor(str(rows[limit - 1]["cluster_id"]))
    return {
        "data": records,
        "page": {"next_cursor": next_cursor, "limit": limit},
    }


def _row_to_cluster(row: dict[str, Any]) -> dict[str, Any]:
    def _iso(v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            return v.isoformat()
        return str(v)

    result: dict[str, Any] = {
        "cluster_id": str(row["cluster_id"]),
        "defect_class": row["defect_class"],
        "status": row["status"],
        "root_cause_signature": row["root_cause_signature"],
        "confidence_score": float(row["confidence_score"]),
        "affected_vin_count": int(row["affected_vin_count"]),
        "most_recent_vin": row["most_recent_vin"],
        "detected_at": _iso(row["detected_at"]),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
        "corrective_action_jira_id": row.get("corrective_action_jira_id"),
    }
    if row.get("process_replay_url") is not None:
        result["process_replay_url"] = row["process_replay_url"]
    if row.get("top_factors") is not None:
        result["top_factors"] = row["top_factors"]
    return result


def filter_clusters(
    store: list[dict[str, Any]],
    *,
    defect_class_filter: str | None,
    status_filter: str | None,
    cursor_id: str | None,
) -> list[dict[str, Any]]:
    result = store
    if defect_class_filter is not None:
        result = [r for r in result if r["defect_class"] == defect_class_filter]
    if status_filter is not None:
        result = [r for r in result if r["status"] == status_filter]
    if cursor_id is not None:
        ids = [str(r["cluster_id"]) for r in result]
        try:
            idx = ids.index(cursor_id)
            result = result[idx + 1 :]
        except ValueError:
            result = []
    return result
