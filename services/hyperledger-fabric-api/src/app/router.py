"""FastAPI router — certificate endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import require_issuer_role, require_reader_role
from app.certificate_service import (
    CertificateNotFoundError,
    FabricHashConflictError,
    FabricTimeoutError,
    FabricUnavailableError,
    get_certificate,
    issue_certificate,
    list_certificates,
    verify_certificate,
)
from app.models import (
    CertificateIssueRequest,
    CertificateIssueResponse,
    CertificateListResponse,
    CertificateRecord,
    CertificateVerifyResponse,
    ErrorResponse,
)

router = APIRouter()


def _corr() -> str:
    return str(uuid.uuid4())


def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": _corr()}


@router.post(
    "/certificate/issue",
    status_code=status.HTTP_201_CREATED,
    response_model=CertificateIssueResponse,
    responses={
        200: {"model": CertificateIssueResponse},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
    tags=["Certificates"],
    operation_id="issueCertificate",
)
async def issue_certificate_endpoint(
    body: CertificateIssueRequest,
    jwt_payload: dict[str, Any] = Depends(require_issuer_role),
) -> Any:
    from app.db import get_pool
    from app.fabric_client import get_fabric_client
    from app.kafka_producer import publish

    pool = await get_pool()
    fabric = get_fabric_client()

    try:
        result = await issue_certificate(
            request=body,
            pool=pool,
            fabric=fabric,
            kafka_publish=publish,
        )
    except FabricHashConflictError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_error("CERTIFICATE_HASH_CONFLICT", str(err)),
        ) from err
    except FabricTimeoutError as err:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=_error("FABRIC_TIMEOUT", str(err)),
        ) from err
    except FabricUnavailableError as err:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_error("FABRIC_UNAVAILABLE", str(err)),
        ) from err

    # 200 when idempotent replay, 201 on new issuance
    if result.idempotent:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=result.model_dump(mode="json"),
        )
    return result


@router.get(
    "/certificates",
    response_model=CertificateListResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
    tags=["Certificates"],
    operation_id="listCertificates",
)
async def list_certificates_endpoint(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    vin: str | None = Query(default=None, max_length=17),
    shiftId: str | None = Query(default=None),
    issuedAfter: str | None = Query(default=None),
    issuedBefore: str | None = Query(default=None),
    jwt_payload: dict[str, Any] = Depends(require_reader_role),
) -> CertificateListResponse:
    from app.db import get_pool

    pool = await get_pool()

    try:
        return await list_certificates(
            pool=pool,
            vin=vin,
            shift_id=shiftId,
            issued_after=issuedAfter,
            issued_before=issuedBefore,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_QUERY_PARAMS", str(err)),
        ) from err


@router.get(
    "/certificate/{certificateId}",
    response_model=CertificateRecord,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
    tags=["Certificates"],
    operation_id="getCertificate",
)
async def get_certificate_endpoint(
    certificateId: str,
    jwt_payload: dict[str, Any] = Depends(require_reader_role),
) -> CertificateRecord:
    from app.db import get_pool

    pool = await get_pool()

    try:
        return await get_certificate(certificate_id=certificateId, pool=pool)
    except CertificateNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("CERTIFICATE_NOT_FOUND", str(err)),
        ) from err
    except FabricUnavailableError as err:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_error("FABRIC_UNAVAILABLE", str(err)),
        ) from err


@router.get(
    "/certificate/{certificateId}/verify",
    response_model=CertificateVerifyResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
    tags=["Certificates"],
    operation_id="verifyCertificate",
)
async def verify_certificate_endpoint(
    certificateId: str,
    jwt_payload: dict[str, Any] = Depends(require_reader_role),
) -> CertificateVerifyResponse:
    from app.db import get_pool
    from app.fabric_client import get_fabric_client

    pool = await get_pool()
    fabric = get_fabric_client()

    try:
        return await verify_certificate(
            certificate_id=certificateId,
            pool=pool,
            fabric=fabric,
        )
    except CertificateNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("CERTIFICATE_NOT_FOUND", str(err)),
        ) from err
    except FabricUnavailableError as err:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_error("FABRIC_UNAVAILABLE", str(err)),
        ) from err
