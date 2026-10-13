"""FastAPI routers for defect-genome endpoints."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import caller_subject, require_bearer
from app.models import (
    ClusterListResponse,
    ClusterQueryRequest,
    ClusterQueryResult,
    ClusterStatus,
    ConfidenceThresholdConfig,
    CorrectiveActionRequest,
    CorrectiveActionResult,
    DefectClass,
    DefectCluster,
    ModelFeedbackAck,
    ModelFeedbackRequest,
)

logger = logging.getLogger(__name__)


def _make_error(code: str, message: str, details: list[Any] | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "correlation_id": str(uuid.uuid4()),
        "details": details or [],
    }


def _emit_otel_span(name: str, attributes: dict) -> None:
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("defect-genome")
        with tracer.start_as_current_span(name) as span:
            for k, v in attributes.items():
                span.set_attribute(k, v)
    except ImportError:
        logger.debug("OTel span %s skipped (SDK not installed)", name)


async def _get_pool() -> asyncpg.Pool:
    from app.db import get_pool

    return await get_pool()


defect_router = APIRouter(tags=["DefectClusters"])
model_router = APIRouter(tags=["Models"])


@defect_router.post(
    "/defect/cluster-query",
    response_model=ClusterQueryResult,
    operation_id="runClusterQuery",
    status_code=status.HTTP_200_OK,
)
async def run_cluster_query(
    body: ClusterQueryRequest,
    payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> ClusterQueryResult:
    from app.cluster_service import run_cluster_query as _run_cluster_query

    caller_id = caller_subject(payload)

    _emit_otel_span(
        "defect.cluster_query",
        {
            "defect_class": body.defect_class.value,
            "time_window_hours": body.time_window_hours,
            "has_idempotency_key": body.idempotency_key is not None,
            "caller_id": caller_id,
        },
    )

    return await _run_cluster_query(request=body, pool=pool, caller_id=caller_id)


@defect_router.get(
    "/defect/clusters",
    response_model=ClusterListResponse,
    operation_id="listClusters",
)
async def list_clusters(
    defectClass: DefectClass | None = Query(default=None),
    status_filter: ClusterStatus | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> ClusterListResponse:
    from app.cluster_service import list_clusters as _list_clusters

    return await _list_clusters(
        pool=pool,
        defect_class=defectClass,
        status=status_filter,
        cursor=cursor,
        limit=limit,
    )


@defect_router.get(
    "/defect/clusters/{clusterId}",
    response_model=DefectCluster,
    operation_id="getCluster",
)
async def get_cluster(
    clusterId: uuid.UUID,
    _payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> DefectCluster:
    from app.cluster_service import get_cluster as _get_cluster

    cluster = await _get_cluster(pool=pool, cluster_id=clusterId)
    if cluster is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_make_error("CLUSTER_NOT_FOUND", f"Cluster {clusterId} not found"),
        )
    return cluster


@defect_router.post(
    "/defect/clusters/{clusterId}/corrective-action",
    response_model=CorrectiveActionResult,
    operation_id="createCorrectiveAction",
    status_code=status.HTTP_201_CREATED,
)
async def create_corrective_action(
    clusterId: uuid.UUID,
    body: CorrectiveActionRequest,
    payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> CorrectiveActionResult:
    from app.cluster_service import create_corrective_action as _create_corrective_action

    caller_id = caller_subject(payload)

    _emit_otel_span(
        "defect.corrective_action",
        {
            "cluster_id": str(clusterId),
            "assigned_factor_rank": body.assigned_root_cause_factor_rank,
            "caller_id": caller_id,
        },
    )

    return await _create_corrective_action(
        cluster_id=clusterId,
        request=body,
        pool=pool,
        caller_id=caller_id,
    )


@model_router.get(
    "/models/confidence-threshold",
    response_model=ConfidenceThresholdConfig,
    operation_id="getConfidenceThreshold",
)
async def get_confidence_threshold(
    _payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> ConfidenceThresholdConfig:
    from app.cluster_service import get_confidence_config

    return await get_confidence_config(pool=pool)


@model_router.post(
    "/models/feedback",
    response_model=ModelFeedbackAck,
    operation_id="submitModelFeedback",
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_model_feedback(
    body: ModelFeedbackRequest,
    payload: dict[str, Any] = Depends(require_bearer),
    pool: asyncpg.Pool = Depends(_get_pool),
) -> ModelFeedbackAck:
    from app.cluster_service import submit_model_feedback as _submit_model_feedback

    caller_id = caller_subject(payload)
    return await _submit_model_feedback(request=body, pool=pool, caller_id=caller_id)
