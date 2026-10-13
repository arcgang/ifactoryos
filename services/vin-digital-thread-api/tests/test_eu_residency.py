"""Unit tests for EU data residency enforcement.

Acceptance criteria (US-028 / BR-13):
  - Startup raises RuntimeError when Neo4j host is not in the EU allowlist.
  - Startup succeeds when the host matches an allowed EU entry.
  - An empty allowlist disables the check (local dev mode).
"""

from __future__ import annotations

import pytest


def test_eu_allowlist_rejects_non_eu_host() -> None:
    from app.eu_residency import assert_eu_residency

    with pytest.raises(RuntimeError, match="EU residency check failed"):
        assert_eu_residency(
            "bolt://neo4j-us-east.example.com:7687",
            "neo4j.germanywestcentral.azure.com,neo4j-munich-onprem.internal",
        )


def test_eu_allowlist_accepts_matching_host() -> None:
    from app.eu_residency import assert_eu_residency

    # Should not raise
    assert_eu_residency(
        "bolt://neo4j.germanywestcentral.azure.com:7687",
        "neo4j.germanywestcentral.azure.com,neo4j-munich-onprem.internal",
    )


def test_eu_allowlist_accepts_subdomain_of_allowed_entry() -> None:
    from app.eu_residency import assert_eu_residency

    assert_eu_residency(
        "bolt://primary.neo4j.germanywestcentral.azure.com:7687",
        "neo4j.germanywestcentral.azure.com",
    )


def test_eu_allowlist_empty_disables_check() -> None:
    from app.eu_residency import assert_eu_residency

    # Empty allowlist = local dev mode; no exception raised for any host.
    assert_eu_residency("bolt://localhost:7687", "")


def test_eu_allowlist_empty_string_after_strip_disables_check() -> None:
    from app.eu_residency import assert_eu_residency

    assert_eu_residency("bolt://us-east-1.aws.example.com:7687", "  ")


def test_eu_allowlist_rejects_localhost_when_allowlist_set() -> None:
    from app.eu_residency import assert_eu_residency

    with pytest.raises(RuntimeError, match="EU residency check failed"):
        assert_eu_residency(
            "bolt://localhost:7687",
            "neo4j.germanywestcentral.azure.com",
        )


def test_eu_allowlist_case_insensitive() -> None:
    from app.eu_residency import assert_eu_residency

    # Hostnames are normalised to lowercase before comparison.
    assert_eu_residency(
        "bolt://NEO4J.GERMANYWESTCENTRAL.AZURE.COM:7687",
        "neo4j.germanywestcentral.azure.com",
    )


def test_eu_allowlist_second_entry_matches() -> None:
    from app.eu_residency import assert_eu_residency

    assert_eu_residency(
        "bolt://neo4j-munich-onprem.internal:7687",
        "neo4j.germanywestcentral.azure.com,neo4j-munich-onprem.internal",
    )
