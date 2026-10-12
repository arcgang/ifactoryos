"""FastAPI router for constraint threshold, sequence optimizer, and MY26 config endpoints."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import (
    caller_subject,
    require_bearer,
    require_my26_config_writer,
    require_override_writer,
    require_resequence_writer,
    require_threshold_writer,
)
from app.models import (
    AlertListResponse,
    ConstraintThreshold,
    My26Config,
    My26ConfigDraft,
    My26ValidationResult,
    OptimizeRequest,
    OptimizeResponse,
    OverrideListResponse,
    OverrideRecord,
    OverrideRequest,
    ResequenceRequest,
    ThresholdListResponse,
    VinExplanation,
)

_logger = logging.getLogger(__name__)


def _emit_otel_span(name: str, attributes: dict) -> None:
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("sequence-optimizer")
        with tracer.start_as_current_span(name) as span:
            for k, v in attributes.items():
                span.set_attribute(k, v)
    except ImportError:
        _logger.debug("OTel span %s skipped (SDK not installed)", name)


router = APIRouter(tags=["Constraints"])


async def _get_pool() -> asyncpg.Pool:
    from app.db import get_pool

    return await get_pool()


@router.get(
    "/constraints/thresholds",
    response_model=ThresholdListResponse,
    operation_id="listConstraintThresholds",
)
async def list_constraint_thresholds(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> ThresholdListResponse:
    from app.thresholds import list_thresholds

    return await list_thresholds(pool=pool, cursor=cursor, limit=limit)


@router.put(
    "/constraints/thresholds",
    response_model=ConstraintThreshold,
    operation_id="upsertConstraintThreshold",
)
async def upsert_constraint_threshold(
    body: ConstraintThreshold,
    payload: dict[str, Any] = Depends(require_threshold_writer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> ConstraintThreshold:
    from app.thresholds import upsert_threshold

    updated_by = caller_subject(payload)
    return await upsert_threshold(pool=pool, threshold=body, updated_by=updated_by)


# ---------------------------------------------------------------------------
# Optimizer endpoints
# ---------------------------------------------------------------------------

optimize_router = APIRouter(tags=["Optimizer"])


@optimize_router.post(
    "/optimize",
    response_model=OptimizeResponse,
    operation_id="runOptimizer",
    status_code=status.HTTP_200_OK,
)
async def run_optimizer(
    body: OptimizeRequest,
    payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> OptimizeResponse:
    from app.kserve_client import make_kserve_client
    from app.my26_config import build_constraint_overrides, load_my26_config_by_version
    from app.optimizer import run_optimize
    from app.validation import validate_option_codes

    user_id = caller_subject(payload)

    # 422: unknown option codes
    unknown = validate_option_codes(body.vins)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "UNKNOWN_OPTION_CODES",
                "message": "One or more VINs carry option codes not present in the constraint model",
                "correlation_id": str(uuid.uuid4()),
                "details": [{"unknown_codes": list(unknown)}],
            },
        )

    # 422: requested MY26 config version must exist and be active
    my26_overrides: dict[str, Any] | None = None
    if body.my26ConfigVersion is not None:
        try:
            config_id = uuid.UUID(body.my26ConfigVersion)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "INVALID_MY26_CONFIG_VERSION",
                    "message": "my26ConfigVersion must be a valid UUID",
                    "correlation_id": str(uuid.uuid4()),
                },
            )
        my26_cfg = await load_my26_config_by_version(config_id, pool)
        if my26_cfg is None or my26_cfg.status.value != "active":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "MY26_CONFIG_NOT_ACTIVE",
                    "message": f"MY26 config version {body.my26ConfigVersion} does not exist or is not active",
                    "correlation_id": str(uuid.uuid4()),
                },
            )
        my26_overrides = build_constraint_overrides(my26_cfg)

    kserve_client = make_kserve_client()
    return await run_optimize(
        request=body,
        pool=pool,
        kserve_client=kserve_client,
        user_id=user_id,
        my26_overrides=my26_overrides,
    )


@optimize_router.get(
    "/optimize/{runId}",
    response_model=OptimizeResponse,
    operation_id="getOptimizeRun",
)
async def get_optimize_run(
    runId: uuid.UUID,
    _payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> OptimizeResponse:
    from app.optimizer import get_run

    result = await get_run(runId, pool)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "RUN_NOT_FOUND",
                "message": f"Optimisation run {runId} not found",
                "correlation_id": str(uuid.uuid4()),
            },
        )
    return result


@optimize_router.get(
    "/optimize/{runId}/explain/{vin}",
    response_model=VinExplanation,
    operation_id="explainVinPosition",
)
async def explain_vin_position(
    runId: uuid.UUID,
    vin: str,
    _payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> VinExplanation:
    import re

    from app.explain import get_explanation

    if not re.match(r"^[A-HJ-NPR-Z0-9]{17}$", vin):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_VIN",
                "message": "VIN must be 17 characters matching [A-HJ-NPR-Z0-9]",
                "correlation_id": str(uuid.uuid4()),
            },
        )

    result = await get_explanation(runId, vin, pool)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "EXPLANATION_NOT_FOUND",
                "message": f"No explanation found for run {runId} and VIN {vin}",
                "correlation_id": str(uuid.uuid4()),
            },
        )
    _emit_otel_span(
        "explain.vin_position",
        {
            "run_id": str(runId),
            "vin": vin,
            "position": result.position,
            "factor_count": len(result.topFactors),
        },
    )
    return result


@optimize_router.post(
    "/resequence",
    response_model=OptimizeResponse,
    operation_id="runTargetedResequence",
    status_code=status.HTTP_200_OK,
)
async def run_targeted_resequence(
    body: ResequenceRequest,
    payload: dict[str, Any] = Depends(require_resequence_writer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> OptimizeResponse:
    from app.kserve_client import make_kserve_client
    from app.resequence import run_resequence

    user_id = caller_subject(payload)
    kserve_client = make_kserve_client()
    return await run_resequence(
        request=body,
        pool=pool,
        kserve_client=kserve_client,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# Alerts endpoints
# ---------------------------------------------------------------------------

alerts_router = APIRouter(tags=["Alerts"])


@alerts_router.get(
    "/api/v1/alerts",
    response_model=AlertListResponse,
    operation_id="listConstraintAlerts",
)
async def list_constraint_alerts(
    status_filter: str | None = Query(default="active", alias="status"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> AlertListResponse:
    from app.resequence import list_active_alerts

    return await list_active_alerts(
        pool=pool,
        cursor=cursor,
        limit=limit,
        status_filter=status_filter,
    )


# ---------------------------------------------------------------------------
# MY26 configuration endpoints
# ---------------------------------------------------------------------------

my26_router = APIRouter(tags=["MY26 Config"])


@my26_router.get(
    "/my26/config",
    response_model=My26Config,
    operation_id="getMy26Config",
)
async def get_my26_config(
    _payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> My26Config:
    from app.my26_config import get_active_my26_config

    config = await get_active_my26_config(pool)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "MY26_CONFIG_NOT_FOUND",
                "message": "No active MY26 configuration exists",
                "correlation_id": str(uuid.uuid4()),
            },
        )
    return config


@my26_router.post(
    "/my26/config/validate",
    response_model=My26ValidationResult,
    operation_id="validateMy26Config",
    status_code=status.HTTP_200_OK,
)
async def validate_my26_config(
    body: My26ConfigDraft,
    _payload: dict[str, Any] = Depends(require_bearer),
) -> My26ValidationResult:
    from app.my26_config import validate_my26_draft

    return validate_my26_draft(body)


@my26_router.post(
    "/my26/config/save",
    response_model=My26Config,
    operation_id="saveMy26Config",
    status_code=status.HTTP_201_CREATED,
)
async def save_my26_config_endpoint(
    body: My26ConfigDraft,
    payload: dict[str, Any] = Depends(require_my26_config_writer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> My26Config:
    from app.my26_config import save_my26_config, validate_my26_draft

    # Re-run validation inline; reject with 409 if unresolved errors exist
    validation = validate_my26_draft(body)
    if not validation.valid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "MY26_CONFIG_VALIDATION_FAILED",
                "message": "Validation failed — resolve all error-severity issues before saving",
                "correlation_id": str(uuid.uuid4()),
                "details": [i.model_dump(mode="json") for i in validation.issues],
            },
        )

    user_id = caller_subject(payload)
    return await save_my26_config(body, pool, user_id)


# ---------------------------------------------------------------------------
# Overrides endpoints
# ---------------------------------------------------------------------------

overrides_router = APIRouter(tags=["Overrides"])


@overrides_router.get(
    "/overrides",
    response_model=OverrideListResponse,
    operation_id="listOverrides",
)
async def list_overrides(
    weekIso: str | None = Query(default=None, pattern=r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$"),
    runId: uuid.UUID | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> OverrideListResponse:
    from app.overrides import list_overrides as _list_overrides

    result = await _list_overrides(
        pool=pool,
        week_iso=weekIso,
        run_id=runId,
        cursor=cursor,
        limit=limit,
    )
    _emit_otel_span(
        "overrides.list",
        {
            "week_iso": weekIso or "",
            "limit": limit,
            "result_count": len(result.data),
        },
    )
    return result


@overrides_router.post(
    "/overrides",
    response_model=OverrideRecord,
    operation_id="createOverride",
    status_code=status.HTTP_201_CREATED,
)
async def create_override(
    body: OverrideRequest,
    payload: dict[str, Any] = Depends(require_override_writer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> OverrideRecord:
    from app.overrides import create_override as _create_override, publish_override_event

    planner_id = caller_subject(payload)
    record = await _create_override(request=body, planner_id=planner_id, pool=pool)
    asyncio.create_task(publish_override_event(record))
    _emit_otel_span(
        "overrides.create",
        {
            "override_id": str(record.overrideId),
            "run_id": str(record.runId),
            "vin": record.vin,
            "planner_id": record.plannerId,
        },
    )
    return record


@overrides_router.get(
    "/overrides/export",
    operation_id="exportOverridesPdf",
    responses={
        200: {"content": {"application/pdf": {}}, "description": "PDF binary"},
        400: {"model": None},
        401: {"model": None},
    },
)
async def export_overrides_pdf(
    weekIso: str = Query(pattern=r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$"),
    _payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> Any:
    from fastapi.responses import Response

    from app.overrides import build_override_pdf, list_overrides as _list_overrides

    resp = await _list_overrides(pool=pool, week_iso=weekIso, limit=200)
    all_records = resp.data
    # If more than 200 records exist, fetch remaining pages
    page = resp.page
    while page.next_cursor is not None:
        resp = await _list_overrides(
            pool=pool, week_iso=weekIso, cursor=page.next_cursor, limit=200
        )
        all_records = all_records + resp.data
        page = resp.page

    pdf_bytes = build_override_pdf(all_records, weekIso)
    _emit_otel_span(
        "overrides.export_pdf",
        {"week_iso": weekIso, "record_count": len(all_records)},
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="overrides-{weekIso}.pdf"'
        },
    )


@overrides_router.get(
    "/overrides/{overrideId}",
    response_model=OverrideRecord,
    operation_id="getOverride",
)
async def get_override(
    overrideId: uuid.UUID,
    _payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> OverrideRecord:
    from app.overrides import get_override as _get_override

    record = await _get_override(overrideId, pool)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "OVERRIDE_NOT_FOUND",
                "message": f"Override {overrideId} not found",
                "correlation_id": str(uuid.uuid4()),
            },
        )
    _emit_otel_span("overrides.get", {"override_id": str(overrideId)})
    return record
