"""Unit tests for the PII scrubber.

Acceptance criteria (US-028 / BR-13):
  - operator_id / employee_id values matching 8-digit numeric pattern are replaced
    by a pseudonymous token from the identity service.
  - Raw employee IDs are never present in the returned parameters.
  - Non-PII keys and non-matching values pass through unchanged.
  - Identity service failure falls back to an opaque placeholder (never raw ID).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_TOKEN = "caf4e5d6-0000-4000-a000-000000000001"
_IDENTITY_URL = "http://identity-svc"


def _mock_identity_ok(token: str = _TOKEN) -> MagicMock:
    """Return a context-manager mock for httpx.AsyncClient that returns *token*."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"token": token})

    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _mock_identity_error(status_code: int = 503) -> MagicMock:
    """Return an httpx.AsyncClient mock that raises HTTPStatusError on get()."""
    import httpx

    response = MagicMock()
    response.status_code = status_code

    client = AsyncMock()
    client.get = AsyncMock(
        side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=response)
    )
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ---------------------------------------------------------------------------
# Tests: happy path — numeric employee ID replaced by pseudonymous token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrub_operator_id_replaced_by_token() -> None:
    from app.pii_scrubber import scrub_parameters

    with patch("app.pii_scrubber.httpx.AsyncClient", return_value=_mock_identity_ok()):
        result = await scrub_parameters(
            {"operator_id": "12345678", "torque_nm": 42.5},
            identity_service_url=_IDENTITY_URL,
        )

    assert result["operator_id"] == _TOKEN, "Raw employee ID must be replaced by pseudonymous token"
    assert result["torque_nm"] == 42.5, "Non-PII value must pass through unchanged"


@pytest.mark.asyncio
async def test_scrub_employee_id_replaced() -> None:
    from app.pii_scrubber import scrub_parameters

    with patch("app.pii_scrubber.httpx.AsyncClient", return_value=_mock_identity_ok()):
        result = await scrub_parameters(
            {"employee_id": "87654321"},
            identity_service_url=_IDENTITY_URL,
        )

    assert result["employee_id"] == _TOKEN


@pytest.mark.asyncio
async def test_scrub_worker_name_matching_pattern_replaced() -> None:
    from app.pii_scrubber import scrub_parameters

    with patch("app.pii_scrubber.httpx.AsyncClient", return_value=_mock_identity_ok()):
        result = await scrub_parameters(
            {"worker_name": "11223344"},
            identity_service_url=_IDENTITY_URL,
        )

    assert result["worker_name"] == _TOKEN


# ---------------------------------------------------------------------------
# Tests: non-matching values pass through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrub_non_numeric_name_not_changed() -> None:
    from app.pii_scrubber import scrub_parameters

    result = await scrub_parameters(
        {"worker_name": "Alice Müller"},
        identity_service_url=_IDENTITY_URL,
    )

    assert result["worker_name"] == "Alice Müller"


@pytest.mark.asyncio
async def test_scrub_short_numeric_id_not_changed() -> None:
    from app.pii_scrubber import scrub_parameters

    result = await scrub_parameters(
        {"operator_id": "1234"},
        identity_service_url=_IDENTITY_URL,
    )

    assert result["operator_id"] == "1234", "7-digit value must not be pseudonymised"


@pytest.mark.asyncio
async def test_scrub_non_pii_key_not_changed() -> None:
    from app.pii_scrubber import scrub_parameters

    result = await scrub_parameters(
        {"torque_nm": "12345678", "active": True},
        identity_service_url=_IDENTITY_URL,
    )

    assert result["torque_nm"] == "12345678", "Key not in PII allowlist must pass through unchanged"
    assert result["active"] is True


# ---------------------------------------------------------------------------
# Tests: identity service failure — opaque placeholder, never raw employee ID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrub_identity_service_error_returns_placeholder() -> None:
    from app.pii_scrubber import scrub_parameters

    with patch("app.pii_scrubber.httpx.AsyncClient", return_value=_mock_identity_error(503)):
        result = await scrub_parameters(
            {"operator_id": "99887766"},
            identity_service_url=_IDENTITY_URL,
        )

    assert result["operator_id"] != "99887766", "Raw employee ID must never be returned on error"
    assert result["operator_id"].startswith("PSEUDONYMISED:"), (
        "Placeholder must use the PSEUDONYMISED: prefix"
    )


@pytest.mark.asyncio
async def test_scrub_raw_employee_id_never_in_result_on_any_failure() -> None:
    from app.pii_scrubber import scrub_parameters

    with patch("app.pii_scrubber.httpx.AsyncClient", return_value=_mock_identity_error(500)):
        result = await scrub_parameters(
            {"employee_id": "55443322"},
            identity_service_url=_IDENTITY_URL,
        )

    assert "55443322" not in result.values(), "Raw employee ID must never appear in scrubbed output"


# ---------------------------------------------------------------------------
# Tests: multiple PII fields in one call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrub_multiple_pii_fields() -> None:
    from app.pii_scrubber import scrub_parameters

    call_count = 0

    def _side_effect_client(*args, **kwargs):
        nonlocal call_count
        tokens = ["token-A", "token-B"]

        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"token": tokens[call_count % 2]})

        client = AsyncMock()
        client.get = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        call_count += 1
        return client

    with patch("app.pii_scrubber.httpx.AsyncClient", side_effect=_side_effect_client):
        result = await scrub_parameters(
            {
                "operator_id": "11111111",
                "employee_id": "22222222",
                "torque_nm": 50.0,
            },
            identity_service_url=_IDENTITY_URL,
        )

    assert result["operator_id"] in ("token-A", "token-B"), "operator_id must be pseudonymised"
    assert result["employee_id"] in ("token-A", "token-B"), "employee_id must be pseudonymised"
    assert result["torque_nm"] == 50.0
    assert "11111111" not in result.values()
    assert "22222222" not in result.values()
