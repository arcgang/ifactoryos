"""Tests for POST /alerts/{alertId}/acknowledge — all contract-specified cases."""

import uuid
from datetime import datetime, timezone

import pytest

from app.alerts import (
    AlertNotFoundError,
    AlertStore,
    ConstraintAlert,
    UnauthenticatedError,
    acknowledge_alert,
)

_FIXED_NOW = datetime(2040, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
_FIXED_NOW_ISO = "2040-01-01T00:00:00+00:00"

_ALERT_ID = "550e8400-e29b-41d4-a716-446655440000"
_VIN = "1HGCM82633A123456"  # 17-char VIN matching ^[A-HJ-NPR-Z0-9]{17}$


def _fresh_store_with_alert() -> AlertStore:
    store = AlertStore()
    store.add(
        ConstraintAlert(
            alertId=_ALERT_ID,
            constraintType="paint_capacity",
            currentValue=92.5,
            thresholdValue=85.0,
            affectedVins=[_VIN],
            status="active",
            detectedAt="2040-01-01T00:00:00+00:00",
        )
    )
    return store


# ---------------------------------------------------------------------------
# 200 — successful acknowledgement
# ---------------------------------------------------------------------------

def test_acknowledge_returns_acknowledged_status():
    store = _fresh_store_with_alert()
    result = acknowledge_alert(
        _ALERT_ID,
        bearer_token="valid-token",
        acknowledged_by="user@bmwgroup.net",
        store=store,
        now=_FIXED_NOW,
    )
    assert result.status == "acknowledged"


def test_acknowledge_sets_acknowledged_at_to_now():
    store = _fresh_store_with_alert()
    result = acknowledge_alert(
        _ALERT_ID,
        bearer_token="valid-token",
        acknowledged_by="user@bmwgroup.net",
        store=store,
        now=_FIXED_NOW,
    )
    assert result.acknowledgedAt == _FIXED_NOW_ISO


def test_acknowledge_sets_acknowledged_by():
    store = _fresh_store_with_alert()
    result = acknowledge_alert(
        _ALERT_ID,
        bearer_token="valid-token",
        acknowledged_by="planner@bmwgroup.net",
        store=store,
        now=_FIXED_NOW,
    )
    assert result.acknowledgedBy == "planner@bmwgroup.net"


def test_acknowledge_preserves_all_required_fields():
    store = _fresh_store_with_alert()
    result = acknowledge_alert(
        _ALERT_ID,
        bearer_token="valid-token",
        acknowledged_by="user@bmwgroup.net",
        store=store,
        now=_FIXED_NOW,
    )
    assert result.alertId == _ALERT_ID
    assert result.constraintType == "paint_capacity"
    assert result.currentValue == 92.5
    assert result.thresholdValue == 85.0
    assert result.affectedVins == [_VIN]
    assert result.detectedAt == "2040-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# 401 — unauthenticated (missing or empty bearer token)
# ---------------------------------------------------------------------------

def test_missing_bearer_token_raises_unauthenticated():
    store = _fresh_store_with_alert()
    with pytest.raises(UnauthenticatedError):
        acknowledge_alert(
            _ALERT_ID,
            bearer_token=None,
            acknowledged_by="user@bmwgroup.net",
            store=store,
            now=_FIXED_NOW,
        )


def test_empty_bearer_token_raises_unauthenticated():
    store = _fresh_store_with_alert()
    with pytest.raises(UnauthenticatedError):
        acknowledge_alert(
            _ALERT_ID,
            bearer_token="",
            acknowledged_by="user@bmwgroup.net",
            store=store,
            now=_FIXED_NOW,
        )


# ---------------------------------------------------------------------------
# 404 — alert not found
# ---------------------------------------------------------------------------

def test_unknown_alert_id_raises_not_found():
    store = AlertStore()  # empty — no alerts registered
    unknown_id = str(uuid.uuid4())
    with pytest.raises(AlertNotFoundError):
        acknowledge_alert(
            unknown_id,
            bearer_token="valid-token",
            acknowledged_by="user@bmwgroup.net",
            store=store,
            now=_FIXED_NOW,
        )


def test_not_found_message_contains_alert_id():
    store = AlertStore()
    missing_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    with pytest.raises(AlertNotFoundError, match=missing_id):
        acknowledge_alert(
            missing_id,
            bearer_token="valid-token",
            acknowledged_by="user@bmwgroup.net",
            store=store,
            now=_FIXED_NOW,
        )


# ---------------------------------------------------------------------------
# Instance isolation — each AlertStore starts clean (convention check)
# ---------------------------------------------------------------------------

def test_store_instances_are_isolated():
    store_a = AlertStore()
    store_b = AlertStore()
    alert = ConstraintAlert(
        alertId=_ALERT_ID,
        constraintType="agv_delay",
        currentValue=5.0,
        thresholdValue=3.0,
        affectedVins=[_VIN],
        status="active",
        detectedAt="2040-01-01T00:00:00+00:00",
    )
    store_a.add(alert)
    # store_b must not see store_a's data
    assert store_b.get(_ALERT_ID) is None
