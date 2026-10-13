"""POST /defect/cluster-query.

Implements operationId: runClusterQuery.
Returns a ClusterQueryResult with root-cause probability distribution.
When model_confidence < 0.60 (low_confidence=True), advisor_v1_fallback is populated.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import require_bearer

router = APIRouter(tags=["Cluster Query"])

_VALID_DEFECT_CLASSES = {"dust", "orange_peel", "run", "crater", "pinhole"}
_LOW_CONFIDENCE_THRESHOLD = 0.60

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ClusterQueryRequest(BaseModel):
    defect_class: str = Field(..., description="Paint defect class to query")
    vin_filter: list[str] | None = Field(
        default=None,
        description="Optional list of VINs to scope the query; omit for all recent VINs",
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Client-supplied idempotency key; same key returns cached result within 5 minutes",
    )
    time_window_hours: int = Field(
        default=4,
        ge=1,
        le=168,
        description="Rolling time window in hours for cluster detection",
    )


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------


def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


# ---------------------------------------------------------------------------
# POST /defect/cluster-query
# ---------------------------------------------------------------------------


@router.post(
    "/defect/cluster-query",
    operation_id="runClusterQuery",
    summary="Run a DefectGenome cluster query for a defect class and return root-cause probability distribution",
    status_code=200,
    responses={
        200: {},
        400: {},
        401: {},
        422: {},
        500: {},
    },
)
async def run_cluster_query(
    body: ClusterQueryRequest,
    jwt_payload: dict[str, Any] = Depends(require_bearer),
) -> JSONResponse:
    if body.defect_class not in _VALID_DEFECT_CLASSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_error(
                "UNSUPPORTED_DEFECT_CLASS",
                f"Defect class '{body.defect_class}' is not supported. "
                f"Supported classes: {', '.join(sorted(_VALID_DEFECT_CLASSES))}",
            ),
        )

    query_id = str(uuid.uuid4())
    cluster_id = str(uuid.uuid4())
    queried_at = datetime.now(timezone.utc).isoformat()

    # Stub DBSCAN+XGBoost result — in production the streaming consumer maintains
    # pre-indexed cluster state; the HTTP path runs XGBoost on that cluster vector.
    model_confidence = 0.82
    low_confidence = model_confidence < _LOW_CONFIDENCE_THRESHOLD

    top_factors = [
        {
            "rank": 1,
            "factor_name": "e-coat voltage drop at stage 6",
            "factor_category": "prep_stage",
            "probability_score": 0.74,
            "supporting_vin_count": 3,
        },
    ]

    result: dict[str, Any] = {
        "query_id": query_id,
        "cluster_id": cluster_id,
        "defect_class": body.defect_class,
        "model_confidence": model_confidence,
        "low_confidence": low_confidence,
        "top_factors": top_factors,
        "affected_vin_count": len(body.vin_filter) if body.vin_filter else 0,
        "queried_at": queried_at,
        "model_version": "1.0.0",
    }

    if body.vin_filter:
        result["affected_vins"] = body.vin_filter

    if low_confidence:
        result["advisor_v1_fallback"] = {
            "rule_id": "ADVISOR_V1_PAINT_PREP_DEFAULT",
            "rule_description": "Default prep-stage check for low-confidence cluster",
            "recommended_action": "Inspect e-coat bath parameters and phosphate stage",
            "matched_conditions": ["voltage_anomaly", "low_sample_count"],
        }

    return JSONResponse(status_code=200, content=result)
