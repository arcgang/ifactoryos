"""Andon alert service — DB queries for GET /andon/alerts/{alertId} and acknowledge."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Alert fetch
# ---------------------------------------------------------------------------

async def fetch_alert(conn: Any, alert_id: str) -> dict[str, Any] | None:
    """Return the andon_alert row as a dict, or None if not found.

    Uses parameterised query to prevent SQL injection.
    Explicit column list — no SELECT *.
    """
    row = await conn.fetchrow(
        """
        SELECT
            alert_id,
            opcenter_event_id,
            station_id,
            line_id,
            alert_type,
            predicted_failure_type,
            minutes_to_predicted_failure,
            recommended_action,
            status,
            detected_at,
            received_at,
            acknowledged_at,
            resolved_at,
            kafka_offset,
            correlation_id,
            created_at,
            updated_at
        FROM andon_alert
        WHERE alert_id = $1::uuid
        """,
        alert_id,
    )
    if row is None:
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# Weld trace fetch from TimescaleDB uns_telemetry hypertable
# ---------------------------------------------------------------------------

async def fetch_weld_trace(
    ts_conn: Any,
    station_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Return 60-minute weld current and voltage traces for a station.

    Returns {"weld_current_a": [...], "weld_voltage_v": [...]}
    Each entry is {"timestamp": datetime, "value": float}.
    """
    rows = await ts_conn.fetch(
        """
        SELECT time, tag_name, value
        FROM uns_telemetry
        WHERE station_id = $1
          AND tag_name IN ('weld_current_a', 'weld_voltage_v')
          AND time >= NOW() - INTERVAL '60 minutes'
        ORDER BY time ASC
        """,
        station_id,
    )

    current: list[dict[str, Any]] = []
    voltage: list[dict[str, Any]] = []
    for row in rows:
        point = {"timestamp": row["time"], "value": row["value"]}
        if row["tag_name"] == "weld_current_a":
            current.append(point)
        else:
            voltage.append(point)

    return {"weld_current_a": current, "weld_voltage_v": voltage}


# ---------------------------------------------------------------------------
# Acknowledge alert — double-ack guard + status update
# ---------------------------------------------------------------------------

class AlreadyAcknowledgedError(Exception):
    pass


async def acknowledge_alert(
    conn: Any,
    alert_id: str,
    now: datetime | None = None,
) -> datetime:
    """UPDATE andon_alert status to 'acknowledged'.

    Raises AlreadyAcknowledgedError when the current status is not 'open'.
    Returns the acknowledged_at timestamp on success.

    Guards against any non-open terminal state (acknowledged, resolved,
    false_positive), not only the happy-path acknowledged state.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    row = await conn.fetchrow(
        """
        UPDATE andon_alert
        SET
            status         = 'acknowledged',
            acknowledged_at = $2,
            updated_at      = $2
        WHERE alert_id = $1::uuid
          AND status    = 'open'
        RETURNING acknowledged_at
        """,
        alert_id,
        now,
    )

    if row is None:
        # Either the row does not exist or status != 'open'. Distinguish the two.
        existing = await conn.fetchval(
            "SELECT status FROM andon_alert WHERE alert_id = $1::uuid",
            alert_id,
        )
        if existing is None:
            # Caller should have checked existence first; treat as not-found.
            raise AlreadyAcknowledgedError(f"Alert {alert_id!r} not found or not open")
        raise AlreadyAcknowledgedError(
            f"Alert {alert_id!r} is already in terminal state '{existing}'"
        )

    return row["acknowledged_at"]
