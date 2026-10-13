"""GET /defect/clusters and GET /defect/clusters/{clusterId}.

Implements operationIds: listClusters, getCluster.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.alerts_service import decode_cursor
from app.auth import require_bearer
from app.clusters_service import build_cluster_list_response, filter_clusters, _row_to_cluster

router = APIRouter(tags=["Defect Clusters"])

# ---------------------------------------------------------------------------
# Shared in-memory cluster store — replaced by real DB in production.
# ---------------------------------------------------------------------------

_CLUSTERS: list[dict[str, Any]] = []


def get_cluster_store() -> list[dict[str, Any]]:
    return _CLUSTERS


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------

def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


# ---------------------------------------------------------------------------
# GET /defect/clusters
# ---------------------------------------------------------------------------

_VALID_DEFECT_CLASSES = {"dust", "orange_peel", "run", "crater", "pinhole"}
_VALID_CLUSTER_STATUSES = {"open", "in_progress", "resolved"}


@router.get(
    "/defect/clusters",
    operation_id="listClusters",
    summary="List defect clusters with optional filters",
    status_code=200,
    responses={
        200: {},
        400: {},
        401: {},
        500: {},
    },
)
async def list_clusters(
    defect_class_param: str | None = Query(default=None, alias="defectClass"),
    status_filter: str | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    jwt_payload: dict[str, Any] = Depends(require_bearer),
    cluster_store: list[dict[str, Any]] = Depends(get_cluster_store),
) -> JSONResponse:
    if defect_class_param is not None and defect_class_param not in _VALID_DEFECT_CLASSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error(
                "INVALID_DEFECT_CLASS",
                f"defectClass must be one of: {', '.join(sorted(_VALID_DEFECT_CLASSES))}",
            ),
        )
    if status_filter is not None and status_filter not in _VALID_CLUSTER_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error(
                "INVALID_STATUS",
                f"status must be one of: {', '.join(sorted(_VALID_CLUSTER_STATUSES))}",
            ),
        )

    cursor_id: str | None = None
    if cursor is not None:
        cursor_id = decode_cursor(cursor)
        if cursor_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_error("INVALID_CURSOR", "cursor parameter is malformed"),
            )

    rows = filter_clusters(
        cluster_store,
        defect_class_filter=defect_class_param,
        status_filter=status_filter,
        cursor_id=cursor_id,
    )
    body = build_cluster_list_response(rows, limit=limit)
    return JSONResponse(status_code=200, content=body)


# ---------------------------------------------------------------------------
# GET /defect/clusters/{clusterId}
# ---------------------------------------------------------------------------

@router.get(
    "/defect/clusters/{clusterId}",
    operation_id="getCluster",
    summary="Get a specific defect cluster by ID",
    status_code=200,
    responses={
        200: {},
        401: {},
        404: {},
        500: {},
    },
)
async def get_cluster(
    clusterId: str,
    jwt_payload: dict[str, Any] = Depends(require_bearer),
    cluster_store: list[dict[str, Any]] = Depends(get_cluster_store),
) -> JSONResponse:
    try:
        cluster_uuid = str(uuid.UUID(clusterId))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_CLUSTER_ID", "clusterId must be a valid UUID"),
        )

    cluster = _find_cluster(cluster_store, cluster_uuid)
    if cluster is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("CLUSTER_NOT_FOUND", f"No cluster found with id {clusterId}"),
        )

    return JSONResponse(status_code=200, content=_row_to_cluster(cluster))


def _find_cluster(
    store: list[dict[str, Any]], cluster_id: str
) -> dict[str, Any] | None:
    for row in store:
        if str(row["cluster_id"]) == cluster_id:
            return row
    return None
