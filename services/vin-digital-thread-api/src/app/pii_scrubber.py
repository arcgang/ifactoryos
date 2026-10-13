"""PII scrubber: replace raw employee IDs with pseudonymous tokens before any Neo4j write.

Inspects the `parameters` map for keys operator_id, employee_id, worker_name, name.
Values matching the 8-digit numeric BMW employee ID pattern are replaced by calling
the bmw-entra-id identity service GET /internal/identity/pseudonymise?employee_id=<value>.
Raw employee IDs are NEVER written to Neo4j or published in event payloads.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_PII_KEYS = frozenset({"operator_id", "employee_id", "worker_name", "name"})
_EMPLOYEE_ID_RE = re.compile(r"^\d{8}$")

# Timeout for a single pseudonymisation lookup.
_PSEUDONYMISE_TIMEOUT_S = 2.0


async def scrub_parameters(
    parameters: dict[str, Any],
    identity_service_url: str,
    timeout_s: float = _PSEUDONYMISE_TIMEOUT_S,
) -> dict[str, Any]:
    """Return a copy of *parameters* with PII values replaced by pseudonymous tokens.

    Only values under PII-sensitive keys that match the 8-digit employee ID pattern
    are pseudonymised; all other values pass through unchanged.
    """
    scrubbed: dict[str, Any] = {}
    for key, value in parameters.items():
        if key in _PII_KEYS and isinstance(value, str) and _EMPLOYEE_ID_RE.match(value):
            scrubbed[key] = await _pseudonymise(value, identity_service_url, timeout_s)
        else:
            scrubbed[key] = value
    return scrubbed


async def _pseudonymise(
    employee_id: str,
    identity_service_url: str,
    timeout_s: float,
) -> str:
    """Call the identity service and return the pseudonymous UUID token."""
    url = f"{identity_service_url}/internal/identity/pseudonymise"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url, params={"employee_id": employee_id})
            resp.raise_for_status()
            data = resp.json()
            token: str = data["token"]
            return token
    except httpx.HTTPStatusError as err:
        logger.error(
            "Identity service returned %s for employee_id scrub; using opaque placeholder",
            err.response.status_code,
        )
        return f"PSEUDONYMISED:{employee_id[:2]}XXXXXX"
    except (httpx.RequestError, KeyError, ValueError) as err:
        logger.error(
            "Failed to pseudonymise employee_id; using opaque placeholder: %s",
            err,
        )
        return f"PSEUDONYMISED:{employee_id[:2]}XXXXXX"
