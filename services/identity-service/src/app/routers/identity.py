"""Internal identity pseudonymisation and GDPR deletion endpoints.

GET  /internal/identity/pseudonymise?employee_id=<id>
POST /internal/identity/deletion-request

These endpoints are internal-only (no IBM API Connect exposure).  They are
protected by mTLS SPIFFE/SPIRE SVID — the ARO ingress enforces this; the
service itself trusts the SVID verified by the sidecar.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.audit import fire_audit_event, sha256_hex
from app.identity_service import DBConnection, pseudonymise, request_deletion
from app.models import (
    DeletionRequestBody,
    DeletionRequestResponse,
    ErrorResponse,
    PseudonymiseResponse,
)

router = APIRouter(prefix="/internal/identity", tags=["Identity"])


def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


# ---------------------------------------------------------------------------
# Dependency: DB connection from pool
# ---------------------------------------------------------------------------

async def _get_conn() -> Any:
    from app.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


# ---------------------------------------------------------------------------
# GET /internal/identity/pseudonymise
# ---------------------------------------------------------------------------

@router.get(
    "/pseudonymise",
    response_model=PseudonymiseResponse,
    responses={
        400: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    operation_id="pseudonymiseEmployee",
    summary="Return or create a pseudonymous token for an employee ID",
)
async def pseudonymise_employee(
    background: BackgroundTasks,
    employee_id: str = Query(..., description="8-digit numeric BMW employee ID"),
    conn: DBConnection = Depends(_get_conn),
) -> PseudonymiseResponse:
    import re

    if not re.match(r"^\d{8}$", employee_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error(
                "INVALID_EMPLOYEE_ID",
                "employee_id must be an 8-digit numeric string",
            ),
        )

    token, created = await pseudonymise(conn, employee_id)

    if created:
        utc_ts = datetime.now(timezone.utc).isoformat()
        after_hash = sha256_hex({"pseudonymous_token": str(token), "employee_id": employee_id})
        background.add_task(
            fire_audit_event,
            user_id=str(token),
            action_type="IDENTITY_PSEUDONYMISED",
            entity_id=str(token),
            after_hash=after_hash,
            utc_ts=utc_ts,
            correlation_id=str(uuid.uuid4()),
        )

    return PseudonymiseResponse(pseudonymous_token=token, created=created)


# ---------------------------------------------------------------------------
# POST /internal/identity/deletion-request
# ---------------------------------------------------------------------------

@router.post(
    "/deletion-request",
    response_model=DeletionRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    operation_id="requestDeletion",
    summary="GDPR Article 17 deletion request — tombstones the identity row",
)
async def request_identity_deletion(
    body: DeletionRequestBody,
    background: BackgroundTasks,
    conn: DBConnection = Depends(_get_conn),
) -> DeletionRequestResponse:
    corr = str(uuid.uuid4())
    requested_at_str = body.requested_at.astimezone(timezone.utc).isoformat()

    token = await request_deletion(
        conn,
        employee_id=body.employee_id,
        name=body.name,
        requested_at=body.requested_at,
    )

    after_hash = sha256_hex(
        {
            "action": "IDENTITY_DELETION_REQUESTED",
            "pseudonymous_token": str(token),
            "requested_at": requested_at_str,
            "correlation_id": corr,
        }
    )

    background.add_task(
        fire_audit_event,
        user_id=str(token),
        action_type="IDENTITY_DELETION_REQUESTED",
        entity_id=str(token),
        after_hash=after_hash,
        utc_ts=requested_at_str,
        correlation_id=corr,
    )

    return DeletionRequestResponse(
        action="IDENTITY_DELETION_REQUESTED",
        pseudonymous_token=token,
        requested_at=requested_at_str,
        correlation_id=corr,
    )
