"""Integration tests for VIN digital record mutation audit writes.

Covers all six mutation types with the following contract assertions per AC-04d1d046:
  - blob appears within 1 second of the mutation call for each mutation type
  - blob contains all six required fields (user_id, action_type, entity_id,
    before_hash, after_hash, utc_ts)
  - no blob is written when the bearer token is absent (non-2xx path)
  - expert_call audit blob contains no video, biometric, or gaze fields

The AuditStore test double captures write_event calls in an in-process list.
fire_audit_write dispatches on a daemon thread; tests join that thread by
polling for up to 1 s.
"""

from __future__ import annotations

import hashlib
import time
import uuid

import pytest

from app.audit import AuditStore, NULL_HASH, sha256_hex
from app.mutations import (
    UnauthenticatedError,
    record_expert_call_end,
    record_handover_submitted,
    record_maintenance_dispatch,
    record_overlay_load_timeout,
    record_torque_out_of_tolerance,
    record_unauthorized_access,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VIN = "1HGCM82633A004352"
_STATION = "final-station-07"
_TOKEN = "header.eyJzdWIiOiJ1c2VyQGJtd2dyb3VwLm5ldCJ9.sig"  # sub=user@bmwgroup.net
_TIMESTAMP = "2040-06-01T06:00:00+00:00"
_ALERT_ID = str(uuid.UUID("550e8400-e29b-41d4-a716-446655440000"))
_HANDOVER_ID = str(uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_for_write(store: AuditStore, expected_action: str, deadline_s: float = 1.0) -> None:
    """Poll the store until a record with expected_action appears or deadline expires."""
    start = time.monotonic()
    while time.monotonic() - start < deadline_s:
        for rec in store.records:
            if rec.action_type == expected_action:
                return
        time.sleep(0.005)
    pytest.fail(
        f"Audit write for action_type={expected_action!r} did not appear "
        f"within {deadline_s:.1f} s"
    )


def _get_record(store: AuditStore, action_type: str):
    for rec in store.records:
        if rec.action_type == action_type:
            return rec
    return None


# ---------------------------------------------------------------------------
# 1. Torque out-of-tolerance
# ---------------------------------------------------------------------------

class TestTorqueOutOfTolerance:
    def test_blob_appears_within_1s(self):
        store = AuditStore()
        record_torque_out_of_tolerance(
            vin=_VIN,
            station_id=_STATION,
            torque_nm=52.7,
            timestamp_iso=_TIMESTAMP,
            bearer_token=_TOKEN,
            writer=store,
        )
        _wait_for_write(store, "TORQUE_OUT_OF_TOLERANCE")

    def test_blob_contains_all_six_fields(self):
        store = AuditStore()
        record_torque_out_of_tolerance(
            vin=_VIN,
            station_id=_STATION,
            torque_nm=52.7,
            timestamp_iso=_TIMESTAMP,
            bearer_token=_TOKEN,
            writer=store,
        )
        _wait_for_write(store, "TORQUE_OUT_OF_TOLERANCE")
        rec = _get_record(store, "TORQUE_OUT_OF_TOLERANCE")
        assert rec is not None
        assert rec.user_id == "user@bmwgroup.net"
        assert rec.action_type == "TORQUE_OUT_OF_TOLERANCE"
        assert rec.entity_id == _VIN
        assert rec.before_hash == NULL_HASH
        assert rec.after_hash == sha256_hex(f"52.7{_STATION}{_TIMESTAMP}")
        assert rec.utc_ts  # non-empty ISO timestamp

    def test_no_blob_on_missing_bearer_token(self):
        store = AuditStore()
        with pytest.raises(UnauthenticatedError):
            record_torque_out_of_tolerance(
                vin=_VIN,
                station_id=_STATION,
                torque_nm=52.7,
                timestamp_iso=_TIMESTAMP,
                bearer_token=None,
                writer=store,
            )
        time.sleep(0.05)
        assert len(store.records) == 0

    def test_no_blob_on_empty_bearer_token(self):
        store = AuditStore()
        with pytest.raises(UnauthenticatedError):
            record_torque_out_of_tolerance(
                vin=_VIN,
                station_id=_STATION,
                torque_nm=52.7,
                timestamp_iso=_TIMESTAMP,
                bearer_token="",
                writer=store,
            )
        time.sleep(0.05)
        assert len(store.records) == 0


# ---------------------------------------------------------------------------
# 2. AR overlay load timeout
# ---------------------------------------------------------------------------

class TestOverlayLoadTimeout:
    def test_blob_appears_within_1s(self):
        store = AuditStore()
        record_overlay_load_timeout(
            vin=_VIN,
            station_id=_STATION,
            bearer_token=_TOKEN,
            writer=store,
        )
        _wait_for_write(store, "OVERLAY_LOAD_TIMEOUT")

    def test_blob_contains_all_six_fields(self):
        store = AuditStore()
        record_overlay_load_timeout(
            vin=_VIN,
            station_id=_STATION,
            bearer_token=_TOKEN,
            writer=store,
        )
        _wait_for_write(store, "OVERLAY_LOAD_TIMEOUT")
        rec = _get_record(store, "OVERLAY_LOAD_TIMEOUT")
        assert rec is not None
        assert rec.user_id == "user@bmwgroup.net"
        assert rec.action_type == "OVERLAY_LOAD_TIMEOUT"
        assert rec.entity_id == _VIN
        assert rec.before_hash == NULL_HASH
        assert rec.after_hash == NULL_HASH
        assert rec.utc_ts

    def test_no_blob_on_missing_bearer_token(self):
        store = AuditStore()
        with pytest.raises(UnauthenticatedError):
            record_overlay_load_timeout(
                vin=_VIN,
                station_id=_STATION,
                bearer_token=None,
                writer=store,
            )
        time.sleep(0.05)
        assert len(store.records) == 0


# ---------------------------------------------------------------------------
# 3. Expert call end
# ---------------------------------------------------------------------------

class TestExpertCallEnd:
    def test_blob_appears_within_1s(self):
        store = AuditStore()
        record_expert_call_end(
            vin=_VIN,
            station_id=_STATION,
            call_mode="voice",
            expert_user_id="expert@bmwgroup.net",
            duration_seconds=142,
            bearer_token=_TOKEN,
            writer=store,
        )
        _wait_for_write(store, "EXPERT_CALL_END")

    def test_blob_contains_all_six_fields(self):
        store = AuditStore()
        record_expert_call_end(
            vin=_VIN,
            station_id=_STATION,
            call_mode="voice",
            expert_user_id="expert@bmwgroup.net",
            duration_seconds=142,
            bearer_token=_TOKEN,
            writer=store,
        )
        _wait_for_write(store, "EXPERT_CALL_END")
        rec = _get_record(store, "EXPERT_CALL_END")
        assert rec is not None
        assert rec.user_id == "user@bmwgroup.net"
        assert rec.action_type == "EXPERT_CALL_END"
        assert rec.entity_id == _VIN
        assert rec.before_hash == NULL_HASH
        assert rec.after_hash == sha256_hex("voiceexpert@bmwgroup.net142")
        assert rec.utc_ts

    def test_blob_contains_no_video_biometric_or_gaze_fields(self):
        """Audit record must not carry video, biometric, or gaze data (BetrVG §87)."""
        store = AuditStore()
        record_expert_call_end(
            vin=_VIN,
            station_id=_STATION,
            call_mode="video",
            expert_user_id="expert@bmwgroup.net",
            duration_seconds=60,
            bearer_token=_TOKEN,
            writer=store,
        )
        _wait_for_write(store, "EXPERT_CALL_END")
        rec = _get_record(store, "EXPERT_CALL_END")
        assert rec is not None
        # Check the six expected fields only — no extra attributes on AuditRecord
        rec_fields = {
            "user_id", "action_type", "entity_id",
            "before_hash", "after_hash", "utc_ts", "written_at",
        }
        actual_fields = set(rec.__dataclass_fields__.keys())
        assert actual_fields == rec_fields
        # Confirm no forbidden terms appear anywhere in the serialised record
        serialised = (
            f"{rec.user_id}{rec.action_type}{rec.entity_id}"
            f"{rec.before_hash}{rec.after_hash}{rec.utc_ts}"
        )
        for forbidden in ("biometric", "gaze", "video_stream", "eye_track"):
            assert forbidden not in serialised.lower(), (
                f"Forbidden term {forbidden!r} found in audit record"
            )

    def test_no_blob_on_missing_bearer_token(self):
        store = AuditStore()
        with pytest.raises(UnauthenticatedError):
            record_expert_call_end(
                vin=_VIN,
                station_id=_STATION,
                call_mode="voice",
                expert_user_id="expert@bmwgroup.net",
                duration_seconds=10,
                bearer_token=None,
                writer=store,
            )
        time.sleep(0.05)
        assert len(store.records) == 0


# ---------------------------------------------------------------------------
# 4. Maintenance dispatch
# ---------------------------------------------------------------------------

class TestMaintenanceDispatch:
    _PRE = '{"status":"open","alertId":"' + _ALERT_ID + '"}'
    _POST = '{"status":"dispatched","alertId":"' + _ALERT_ID + '"}'

    def test_blob_appears_within_1s(self):
        store = AuditStore()
        record_maintenance_dispatch(
            alert_id=_ALERT_ID,
            station_id=_STATION,
            pre_dispatch_state=self._PRE,
            post_dispatch_state=self._POST,
            bearer_token=_TOKEN,
            writer=store,
        )
        _wait_for_write(store, "MAINTENANCE_DISPATCH")

    def test_blob_contains_all_six_fields(self):
        store = AuditStore()
        record_maintenance_dispatch(
            alert_id=_ALERT_ID,
            station_id=_STATION,
            pre_dispatch_state=self._PRE,
            post_dispatch_state=self._POST,
            bearer_token=_TOKEN,
            writer=store,
        )
        _wait_for_write(store, "MAINTENANCE_DISPATCH")
        rec = _get_record(store, "MAINTENANCE_DISPATCH")
        assert rec is not None
        assert rec.user_id == "user@bmwgroup.net"
        assert rec.action_type == "MAINTENANCE_DISPATCH"
        assert rec.entity_id == _ALERT_ID
        assert rec.before_hash == sha256_hex(self._PRE)
        assert rec.after_hash == sha256_hex(self._POST)
        assert rec.utc_ts

    def test_no_blob_on_missing_bearer_token(self):
        store = AuditStore()
        with pytest.raises(UnauthenticatedError):
            record_maintenance_dispatch(
                alert_id=_ALERT_ID,
                station_id=_STATION,
                pre_dispatch_state=self._PRE,
                post_dispatch_state=self._POST,
                bearer_token=None,
                writer=store,
            )
        time.sleep(0.05)
        assert len(store.records) == 0


# ---------------------------------------------------------------------------
# 5. Handover submitted
# ---------------------------------------------------------------------------

class TestHandoverSubmitted:
    _PAYLOAD = '{"handoverId":"' + _HANDOVER_ID + '","shiftLeadId":"sl@bmwgroup.net"}'

    def test_blob_appears_within_1s(self):
        store = AuditStore()
        record_handover_submitted(
            handover_id=_HANDOVER_ID,
            shift_lead_id="sl@bmwgroup.net",
            handover_payload=self._PAYLOAD,
            bearer_token=_TOKEN,
            writer=store,
        )
        _wait_for_write(store, "HANDOVER_SUBMITTED")

    def test_blob_contains_all_six_fields(self):
        store = AuditStore()
        record_handover_submitted(
            handover_id=_HANDOVER_ID,
            shift_lead_id="sl@bmwgroup.net",
            handover_payload=self._PAYLOAD,
            bearer_token=_TOKEN,
            writer=store,
        )
        _wait_for_write(store, "HANDOVER_SUBMITTED")
        rec = _get_record(store, "HANDOVER_SUBMITTED")
        assert rec is not None
        assert rec.user_id == "user@bmwgroup.net"
        assert rec.action_type == "HANDOVER_SUBMITTED"
        assert rec.entity_id == _HANDOVER_ID
        assert rec.before_hash == NULL_HASH
        assert rec.after_hash == sha256_hex(self._PAYLOAD)
        assert rec.utc_ts

    def test_no_blob_on_missing_bearer_token(self):
        store = AuditStore()
        with pytest.raises(UnauthenticatedError):
            record_handover_submitted(
                handover_id=_HANDOVER_ID,
                shift_lead_id="sl@bmwgroup.net",
                handover_payload=self._PAYLOAD,
                bearer_token=None,
                writer=store,
            )
        time.sleep(0.05)
        assert len(store.records) == 0


# ---------------------------------------------------------------------------
# 6. Unauthorized access attempt
# ---------------------------------------------------------------------------

class TestUnauthorizedAccess:
    _RESOURCE = "/api/v1/command-center/oee"

    def test_blob_appears_within_1s(self):
        store = AuditStore()
        record_unauthorized_access(
            resource_path=self._RESOURCE,
            attempted_user_id="user@bmwgroup.net",
            bearer_token=_TOKEN,
            writer=store,
        )
        _wait_for_write(store, "UNAUTHORIZED_ACCESS_ATTEMPT")

    def test_blob_contains_all_six_fields(self):
        store = AuditStore()
        record_unauthorized_access(
            resource_path=self._RESOURCE,
            attempted_user_id="user@bmwgroup.net",
            bearer_token=_TOKEN,
            writer=store,
        )
        _wait_for_write(store, "UNAUTHORIZED_ACCESS_ATTEMPT")
        rec = _get_record(store, "UNAUTHORIZED_ACCESS_ATTEMPT")
        assert rec is not None
        assert rec.user_id == "user@bmwgroup.net"
        assert rec.action_type == "UNAUTHORIZED_ACCESS_ATTEMPT"
        assert rec.entity_id == self._RESOURCE
        assert rec.before_hash == NULL_HASH
        assert rec.after_hash == NULL_HASH
        assert rec.utc_ts

    def test_no_blob_on_missing_bearer_token(self):
        store = AuditStore()
        with pytest.raises(UnauthenticatedError):
            record_unauthorized_access(
                resource_path=self._RESOURCE,
                attempted_user_id="user@bmwgroup.net",
                bearer_token=None,
                writer=store,
            )
        time.sleep(0.05)
        assert len(store.records) == 0


# ---------------------------------------------------------------------------
# AuditStore instance isolation
# ---------------------------------------------------------------------------

class TestAuditStoreIsolation:
    def test_two_stores_are_independent(self):
        store_a = AuditStore()
        store_b = AuditStore()
        record_overlay_load_timeout(
            vin=_VIN,
            station_id=_STATION,
            bearer_token=_TOKEN,
            writer=store_a,
        )
        _wait_for_write(store_a, "OVERLAY_LOAD_TIMEOUT")
        # store_b must not receive writes that went to store_a
        assert len(store_b.records) == 0
