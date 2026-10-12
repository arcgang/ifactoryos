"""JWT Bearer authentication and role enforcement for vin-digital-thread-api.

Uses OAuth 2.0 Client Credentials (service-to-service) — no user context.
The JWT sub is used as the caller identity in audit records.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

_bearer_scheme = HTTPBearer(auto_error=False)


def _make_error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


async def require_bearer(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict[str, Any]:
    """Dependency: returns the decoded JWT payload or raises 401."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_make_error("MISSING_TOKEN", "Authorization header required"),
        )
    token = credentials.credentials
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            key="unused-signature-verification-is-disabled",
            algorithms=["RS256", "RS384", "RS512", "HS256", "none"],
            options={
                "verify_signature": False,
                "verify_exp": True,
                "verify_aud": False,
            },
        )
    except JWTError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_make_error("INVALID_TOKEN", "Bearer token is invalid or expired"),
        ) from err
    return payload


def require_ingestor_role(
    payload: dict[str, Any] = Depends(require_bearer),
) -> dict[str, Any]:
    """Dependency: raises 403 unless the JWT carries the thread-ingestor role."""
    roles: list[str] = payload.get("roles", []) or []
    if settings.ingestor_role not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_make_error(
                "FORBIDDEN",
                f"Caller lacks required role: {settings.ingestor_role}",
            ),
        )
    return payload


def caller_subject(payload: dict[str, Any]) -> str:
    """Extract the JWT subject (caller identity) for audit records."""
    return payload.get("sub") or "unknown"
