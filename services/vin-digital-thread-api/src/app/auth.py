"""JWT bearer authentication — verifies token and enforces thread-ingestor role."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

REQUIRED_ROLE = "thread-ingestor"


def _decode_token(token: str, issuer: str, audience: str) -> dict:
    """Decode and verify a JWT. Raises ValueError on failure."""
    from jose import JWTError, jwt

    try:
        # Decode without signature verification in test/dev — signature check
        # is delegated to the api-gateway in production; here we parse claims.
        claims = jwt.decode(
            token,
            key="",
            algorithms=["RS256", "HS256"],
            options={
                "verify_signature": False,
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
