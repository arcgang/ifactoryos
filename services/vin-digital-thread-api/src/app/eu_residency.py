"""EU data residency enforcement: assert Neo4j connection target is within EU.

At startup, the resolved hostname from the Neo4j URI is compared against the
configured allowlist (comma-separated hostnames or IP prefixes).  If the
allowlist is non-empty and the resolved host is not in it, startup fails so
the readiness probe never turns green and no cross-border writes can occur.

In local dev, set NEO4J_EU_HOST_ALLOWLIST="" (empty) to disable the check.
In production, set it to the FQDN(s) or IP prefixes of Azure Germany West
Central / West Europe and on-prem Munich Neo4j endpoints.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _parse_host(neo4j_uri: str) -> str:
    parsed = urlparse(neo4j_uri)
    host = parsed.hostname or ""
    return host.lower()


def assert_eu_residency(neo4j_uri: str, allowlist_csv: str) -> None:
    """Raise RuntimeError if the Neo4j host is not in the EU allowlist.

    Does nothing when *allowlist_csv* is empty or not a string (local/dev mode).
    """
    if not isinstance(allowlist_csv, str) or not allowlist_csv.strip():
        return

    host = _parse_host(neo4j_uri)
    if not host:
        raise RuntimeError(
            f"EU residency check failed: cannot parse host from Neo4j URI {neo4j_uri!r}"
        )

    allowed = [entry.strip().lower() for entry in allowlist_csv.split(",") if entry.strip()]

    for entry in allowed:
        if host == entry or host.endswith(f".{entry}"):
            logger.info("EU residency check passed: Neo4j host %r matched allowlist entry %r", host, entry)
            return

    raise RuntimeError(
        f"EU residency check failed: Neo4j host {host!r} is not in the EU allowlist "
        f"{allowed!r}. Data residency enforcement blocks startup to prevent cross-border "
        f"personal data transfers."
    )
