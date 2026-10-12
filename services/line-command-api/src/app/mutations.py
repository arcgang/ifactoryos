"""VIN digital record mutation service for line-command-api.

Each public function represents a single mutation that must write an
immutable audit entry within 1 second of the mutation committing (AC-04d1d046).

Callers supply the audit writer as a keyword argument — AuditClient in
production, AuditStore in tests.  This keeps the HTTP handlers thin and makes
every mutation function testable without mocking at the module level.

Mutation summary
----------------
1. torque_out_of_tolerance  — TORQUE_OUT_OF_TOLERANCE  entity=vin
2. overlay_load_timeout     — OVERLAY_LOAD_TIMEOUT      entity=vin
3. expert_call_end          — EXPERT_CALL_END            entity=vin  (no biometric/gaze)
4. maintenance_dispatch     — MAINTENANCE_DISPATCH       entity=alert_id
5. handover_submitted       — HANDOVER_SUBMITTED         entity=handover_id
6. unauthorized_access      — UNAUTHORIZED_ACCESS_ATTEMPT entity=resource_path
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from typing import Literal

from app.audit import (
    AuditStore,
    AuditWriterProtocol,
    NULL_HASH,
    fire_audit_write,
    sha256_hex,
)


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------

class UnauthenticatedError(Exception):
    pass


class UnauthorizedError(Exception):
    pass


# ---------------------------------------------------------------------------
# Result dataclasses (one per mutation)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TorqueOutOfToleranceResult:
    vin: str
    station_id: str
    torque_nm: float
    correlation_id: str


@dataclass(frozen=True)
class OverlayLoadTimeoutResult:
    vin: str
    station_id: str
    correlation_id: str


@dataclass(frozen=True)
class ExpertCallEndResult:
    vin: str
    station_id: str
    call_mode: Literal["video", "voice"]
    expert_user_id: str
    duration_seconds: int
    correlation_id: str


@dataclass(frozen=True)
class MaintenanceDispatchResult:
    alert_id: str
    station_id: str
    correlation_id: str


@dataclass(frozen=True)
class HandoverSubmittedResult:
    handover_id: str
    shift_lead_id: str
    correlation_id: str


@dataclass(frozen=True)
class UnauthorizedAccessResult:
    resource_path: str
    attempted_user_id: str
    correlation_id: str


# ---------------------------------------------------------------------------
# Default writer — replaced at startup with the real AuditClient
# ---------------------------------------------------------------------------

_DEFAULT_WRITER: AuditWriterProtocol = AuditStore()


# ---------------------------------------------------------------------------
# 1. Torque out-of-tolerance
# ---------------------------------------------------------------------------

def record_torque_out_of_tolerance(
    vin: str,
    station_id: str,
    torque_nm: float,
    timestamp_iso: str,
    bearer_token: str | None,
    *,
    writer: AuditWriterProtocol = _DEFAULT_WRITER,
) -> TorqueOutOfToleranceResult:
    """Record a torque-out-of-tolerance event and fire an audit write.

    after_hash = SHA-256(torque_nm + station_id + timestamp_iso)
    before_hash = NULL_HASH (no before-state for a sensor reading)
    Raises UnauthenticatedError when bearer_token is absent.
    """
    if not bearer_token:
        raise UnauthenticatedError("Bearer token is required")

    user_id = _user_id_from_token(bearer_token)
    after_hash = sha256_hex(f"{torque_nm}{station_id}{timestamp_iso}")
    correlation_id = str(uuid.uuid4())

    fire_audit_write(
        writer=writer,
        user_id=user_id,
        action_type="TORQUE_OUT_OF_TOLERANCE",
        entity_id=vin,
        before_hash=NULL_HASH,
        after_hash=after_hash,
    )

    return TorqueOutOfToleranceResult(
        vin=vin,
        station_id=station_id,
        torque_nm=torque_nm,
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# 2. AR overlay load timeout
# ---------------------------------------------------------------------------

def record_overlay_load_timeout(
    vin: str,
    station_id: str,
    bearer_token: str | None,
    *,
    writer: AuditWriterProtocol = _DEFAULT_WRITER,
) -> OverlayLoadTimeoutResult:
    """Record an AR overlay load timeout and fire an audit write.

    Both before_hash and after_hash are NULL_HASH — no state transition.
    Raises UnauthenticatedError when bearer_token is absent.
    """
    if not bearer_token:
        raise UnauthenticatedError("Bearer token is required")

    user_id = _user_id_from_token(bearer_token)
    correlation_id = str(uuid.uuid4())

    fire_audit_write(
        writer=writer,
        user_id=user_id,
        action_type="OVERLAY_LOAD_TIMEOUT",
        entity_id=vin,
        before_hash=NULL_HASH,
        after_hash=NULL_HASH,
    )

    return OverlayLoadTimeoutResult(
        vin=vin,
        station_id=station_id,
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# 3. Expert call end — no biometric or gaze data included
# ---------------------------------------------------------------------------

def record_expert_call_end(
    vin: str,
    station_id: str,
    call_mode: Literal["video", "voice"],
    expert_user_id: str,
    duration_seconds: int,
    bearer_token: str | None,
    *,
    writer: AuditWriterProtocol = _DEFAULT_WRITER,
) -> ExpertCallEndResult:
    """Record an expert call end and fire an audit write.

    after_hash = SHA-256(call_mode + expert_user_id + str(duration_seconds))
    before_hash = NULL_HASH
    No biometric or gaze data is included in the audit payload (BetrVG §87).
    Raises UnauthenticatedError when bearer_token is absent.
    """
    if not bearer_token:
        raise UnauthenticatedError("Bearer token is required")

    user_id = _user_id_from_token(bearer_token)
    after_hash = sha256_hex(f"{call_mode}{expert_user_id}{duration_seconds}")
    correlation_id = str(uuid.uuid4())

    fire_audit_write(
        writer=writer,
        user_id=user_id,
        action_type="EXPERT_CALL_END",
        entity_id=vin,
        before_hash=NULL_HASH,
        after_hash=after_hash,
    )

    return ExpertCallEndResult(
        vin=vin,
        station_id=station_id,
        call_mode=call_mode,
        expert_user_id=expert_user_id,
        duration_seconds=duration_seconds,
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# 4. Maintenance dispatch confirmed
# ---------------------------------------------------------------------------

def record_maintenance_dispatch(
    alert_id: str,
    station_id: str,
    pre_dispatch_state: str,
    post_dispatch_state: str,
    bearer_token: str | None,
    *,
    writer: AuditWriterProtocol = _DEFAULT_WRITER,
) -> MaintenanceDispatchResult:
    """Record a confirmed maintenance dispatch and fire an audit write.

    before_hash = SHA-256(pre_dispatch_state)
    after_hash  = SHA-256(post_dispatch_state)
    Raises UnauthenticatedError when bearer_token is absent.
    """
    if not bearer_token:
        raise UnauthenticatedError("Bearer token is required")

    user_id = _user_id_from_token(bearer_token)
    before_hash = sha256_hex(pre_dispatch_state)
    after_hash = sha256_hex(post_dispatch_state)
    correlation_id = str(uuid.uuid4())

    fire_audit_write(
        writer=writer,
        user_id=user_id,
        action_type="MAINTENANCE_DISPATCH",
        entity_id=alert_id,
        before_hash=before_hash,
        after_hash=after_hash,
    )

    return MaintenanceDispatchResult(
        alert_id=alert_id,
        station_id=station_id,
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# 5. Handover submitted
# ---------------------------------------------------------------------------

def record_handover_submitted(
    handover_id: str,
    shift_lead_id: str,
    handover_payload: str,
    bearer_token: str | None,
    *,
    writer: AuditWriterProtocol = _DEFAULT_WRITER,
) -> HandoverSubmittedResult:
    """Record a shift handover submission and fire an audit write.

    after_hash  = SHA-256(handover_payload)
    before_hash = NULL_HASH (no prior state to capture)
    Raises UnauthenticatedError when bearer_token is absent.
    """
    if not bearer_token:
        raise UnauthenticatedError("Bearer token is required")

    user_id = _user_id_from_token(bearer_token)
    after_hash = sha256_hex(handover_payload)
    correlation_id = str(uuid.uuid4())

    fire_audit_write(
        writer=writer,
        user_id=user_id,
        action_type="HANDOVER_SUBMITTED",
        entity_id=handover_id,
        before_hash=NULL_HASH,
        after_hash=after_hash,
    )

    return HandoverSubmittedResult(
        handover_id=handover_id,
        shift_lead_id=shift_lead_id,
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# 6. Unauthorized access attempt
# ---------------------------------------------------------------------------

def record_unauthorized_access(
    resource_path: str,
    attempted_user_id: str,
    bearer_token: str | None,
    *,
    writer: AuditWriterProtocol = _DEFAULT_WRITER,
) -> UnauthorizedAccessResult:
    """Record an unauthorized access attempt and fire an audit write.

    Both before_hash and after_hash are NULL_HASH — no state mutation.
    This function does NOT raise UnauthorizedError; the HTTP layer is
    responsible for returning 403 after calling this.
    Raises UnauthenticatedError when bearer_token is absent.
    """
    if not bearer_token:
        raise UnauthenticatedError("Bearer token is required")

    user_id = _user_id_from_token(bearer_token)
    correlation_id = str(uuid.uuid4())

    fire_audit_write(
        writer=writer,
        user_id=user_id,
        action_type="UNAUTHORIZED_ACCESS_ATTEMPT",
        entity_id=resource_path,
        before_hash=NULL_HASH,
        after_hash=NULL_HASH,
    )

    return UnauthorizedAccessResult(
        resource_path=resource_path,
        attempted_user_id=attempted_user_id,
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# Token helper — extracts sub claim without a full JWT library dependency
# ---------------------------------------------------------------------------

def _user_id_from_token(bearer_token: str) -> str:
    """Extract the subject claim from a JWT bearer token.

    The token is already validated by the api-gateway before reaching this
    service, so we only need the sub claim for the audit trail.  Uses only
    stdlib to avoid adding a JWT library dependency.
    """
    try:
        parts = bearer_token.split(".")
        if len(parts) != 3:
            return bearer_token  # not a JWT — use raw value as identity
        # Base64url decode the payload; add padding as needed
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        return str(claims.get("sub", bearer_token))
    except (ValueError, KeyError, IndexError, UnicodeDecodeError):
        return bearer_token
