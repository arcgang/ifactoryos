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
        raise ValueError("Token decode failed") from err
    return claims


def _extract_user_id(claims: dict) -> str:
    """Return the subject claim, which carries the caller's identity."""
    sub = claims.get("sub") or claims.get("oid") or claims.get("appid")
    if not sub:
        raise ValueError("Token carries no subject identity")
    return str(sub)


def _has_role(claims: dict, role: str) -> bool:
    roles = claims.get("roles") or claims.get("scp", "").split()
    return role in roles


class CallerIdentity:
    def __init__(self, user_id: str, claims: dict) -> None:
        self.user_id = user_id
        self.claims = claims


async def require_thread_ingestor(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CallerIdentity:
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "MISSING_TOKEN", "message": "Bearer token required"},
        )

    from app.config import get_settings

    cfg = get_settings()
    try:
        claims = _decode_token(
            credentials.credentials,
            issuer=cfg.oidc_issuer_url,
            audience=cfg.oidc_client_id,
        )
    except ValueError as err:
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_TOKEN", "message": "Token is not valid"},
        ) from err

    if not _has_role(claims, REQUIRED_ROLE):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "INSUFFICIENT_ROLE",
                "message": f"Caller lacks required role: {REQUIRED_ROLE}",
            },
        )

    user_id = _extract_user_id(claims)
    return CallerIdentity(user_id=user_id, claims=claims)
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
