"""VIN constraint explanation persistence — store and retrieve top-factor breakdowns."""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg

from app.models import ConstraintFactor, ConstraintFactorName, VinExplanation


async def store_explanation(
    run_id: uuid.UUID,
    vin: str,
    position: int,
    top_factors: list[ConstraintFactor],
    conn: asyncpg.Connection,
) -> None:
    factors_json = [
        {
            "factorName": f.factorName.value,
            "weightedScore": f.weightedScore,
            "dataSource": f.dataSource,
            **({"rawValue": f.rawValue} if f.rawValue is not None else {}),
        }
        for f in top_factors
    ]
    await conn.execute(
        """
        INSERT INTO vin_constraint_explanation (run_id, vin, position, top_factors)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (run_id, vin) DO UPDATE
            SET position    = EXCLUDED.position,
                top_factors = EXCLUDED.top_factors
        """,
        run_id,
        vin,
        position,
        json.dumps(factors_json),
    )


async def get_explanation(
    run_id: uuid.UUID,
    vin: str,
    pool: asyncpg.Pool,
) -> VinExplanation | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT run_id, vin, position, top_factors
            FROM vin_constraint_explanation
            WHERE run_id = $1 AND vin = $2
            """,
            run_id,
            vin,
        )
    if row is None:
        return None
    return _row_to_explanation(row)


def _row_to_explanation(row: Any) -> VinExplanation:
    raw = row["top_factors"]
    if isinstance(raw, str):
        factors_data = json.loads(raw)
    else:
        factors_data = raw

    top_factors = [
        ConstraintFactor(
            factorName=ConstraintFactorName(f["factorName"]),
            weightedScore=f["weightedScore"],
            dataSource=f["dataSource"],
            rawValue=f.get("rawValue"),
        )
        for f in factors_data
    ]

    return VinExplanation(
        runId=row["run_id"],
        vin=row["vin"],
        position=row["position"],
        topFactors=top_factors,
    )
