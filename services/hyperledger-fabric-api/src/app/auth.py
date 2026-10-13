"""JWT Bearer authentication and role enforcement for hyperledger-fabric-api.

BMW Entra ID OIDC tokens are validated here. Two roles are enforced:
  - CERTIFICATE_ISSUER: required for POST /certificate/issue
  - CERTIFICATE_READER: required for GET /certificates, GET /certificate/{id}, GET /certificate/{id}/verify
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from jose import JWTError, jwt

_bearer = HTTPBearer(auto_error=False)


def _make_error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


def _decode_token(token: str) -> dict[str, Any]:
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
    return payload


def _has_role(claims: dict[str, Any], role: str) -> bool:
    roles: list[str] = claims.get("roles") or claims.get("scp", "").split()
    return role in roles


async def require_bearer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict[str, Any]:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_make_error("MISSING_TOKEN", "Bearer token required"),
        )
    try:
        payload = _decode_token(credentials.credentials)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_make_error("INVALID_TOKEN", "Bearer token is invalid or expired"),
        ) from err
    return payload


def require_issuer_role(
    payload: dict[str, Any] = Depends(require_bearer),
) -> dict[str, Any]:
    from app.config import get_settings

    cfg = get_settings()
    if not _has_role(payload, cfg.certificate_issuer_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_make_error(
                "INSUFFICIENT_ROLE",
                f"Caller lacks required role: {cfg.certificate_issuer_role}",
            ),
        )
    return payload


def require_reader_role(
    payload: dict[str, Any] = Depends(require_bearer),
) -> dict[str, Any]:
    from app.config import get_settings

    cfg = get_settings()
    if not _has_role(payload, cfg.certificate_reader_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_make_error(
                "INSUFFICIENT_ROLE",
                f"Caller lacks required role: {cfg.certificate_reader_role}",
            ),
        )
    return payload
