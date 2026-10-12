"""Constraint breach alert service — acknowledge endpoint logic.

In-process store acts as a test double for the persistence layer until the
database migration task lands. Production code will swap _STORE for a
parameterised-query call; the public function signature stays the same.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


ConstraintType = Literal[
    "paint_capacity",
    "body_wip_queue",
    "kitting_readiness",
    "maintenance_window",
    "agv_delay",
    "press_die_overrun",
]

AlertStatus = Literal["active", "acknowledged", "resolved"]

_VIN_PATTERN_LENGTH = 17


@dataclass
class ConstraintAlert:
    alertId: str
    constraintType: ConstraintType
    currentValue: float
    thresholdValue: float
    affectedVins: list[str]
    status: AlertStatus
    detectedAt: str
    acknowledgedAt: str | None = None
    acknowledgedBy: str | None = None


@dataclass
class ErrorDetail:
    code: str
    message: str
    correlation_id: str
    details: list[dict] = field(default_factory=list)


class AlertNotFoundError(Exception):
    pass


class UnauthenticatedError(Exception):
    pass


# ---------------------------------------------------------------------------
# In-memory store — replaced by DB queries when the persistence layer lands.
# Keys are alert UUID strings; values are ConstraintAlert instances.
# ---------------------------------------------------------------------------
class AlertStore:
    """Mutable in-memory store; each instance starts empty and clean."""

    def __init__(self) -> None:
        self._alerts: dict[str, ConstraintAlert] = {}

    def add(self, alert: ConstraintAlert) -> None:
        self._alerts[alert.alertId] = alert

    def get(self, alert_id: str) -> ConstraintAlert | None:
        return self._alerts.get(alert_id)

    def save(self, alert: ConstraintAlert) -> None:
        self._alerts[alert.alertId] = alert


_DEFAULT_STORE = AlertStore()


def acknowledge_alert(
    alert_id: str,
    bearer_token: str | None,
    *,
    acknowledged_by: str,
    store: AlertStore = _DEFAULT_STORE,
    now: datetime | None = None,
) -> ConstraintAlert:
    """Acknowledge a constraint breach alert.

    Raises UnauthenticatedError when bearer_token is absent or empty.
    Raises AlertNotFoundError when alert_id is not found in the store.
    Returns the updated ConstraintAlert on success.
    """
    if not bearer_token:
        raise UnauthenticatedError("Bearer token is required")

    if now is None:
        now = datetime.now(tz=timezone.utc)

    alert = store.get(alert_id)
    if alert is None:
        raise AlertNotFoundError(f"Alert {alert_id!r} not found")

    alert.status = "acknowledged"
    alert.acknowledgedAt = now.isoformat()
    alert.acknowledgedBy = acknowledged_by
    store.save(alert)
    return alert


def make_error(code: str, message: str) -> ErrorDetail:
    return ErrorDetail(
        code=code,
        message=message,
        correlation_id=str(uuid.uuid4()),
    )
