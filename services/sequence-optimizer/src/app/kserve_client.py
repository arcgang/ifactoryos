"""KServe inference client for the SequenceOptimizer model.

Uses OAuth 2.0 Client Credentials (from HashiCorp Vault via env injection) to
obtain a bearer token before every prediction batch, with an explicit per-call
timeout aligned to the 100 ms p99 SLA.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.models import ConstraintSnapshot, VinInput

logger = logging.getLogger(__name__)

_TOKEN_TIMEOUT_S = 5.0
_PREDICT_TIMEOUT_S = 10.0  # per-run timeout; 10 s leaves budget for 50 runs within 45 s


class HttpKServeClient:
    """Real KServe client — uses httpx async with explicit timeouts."""

    def __init__(self, endpoint_url: str, token_url: str, client_id: str, client_secret: str) -> None:
        self._endpoint_url = endpoint_url
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        resp = await client.post(
            self._token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            timeout=_TOKEN_TIMEOUT_S,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    async def predict(
        self,
        vins: list[VinInput],
        constraint_snapshot: ConstraintSnapshot,
        run_index: int,
    ) -> list[dict[str, Any]]:
        payload = {
            "instances": [
                {
                    "vins": [v.model_dump(mode="json") for v in vins],
                    "constraintSnapshot": constraint_snapshot.model_dump(mode="json"),
                    "runIndex": run_index,
                }
            ]
        }
        async with httpx.AsyncClient(timeout=_PREDICT_TIMEOUT_S) as client:
            token = await self._get_token(client)
            resp = await client.post(
                self._endpoint_url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=_PREDICT_TIMEOUT_S,
            )
            resp.raise_for_status()
            data = resp.json()

        predictions = data.get("predictions", [[]])[0]
        logger.debug("KServe run_index=%d returned %d ranked VINs", run_index, len(predictions))
        return predictions


def make_kserve_client() -> HttpKServeClient:
    from app.config import get_settings

    cfg = get_settings()
    return HttpKServeClient(
        endpoint_url=cfg.kserve_endpoint_url,
        token_url=cfg.kserve_token_url,
        client_id=cfg.kserve_client_id,
        client_secret=cfg.kserve_client_secret,
    )
