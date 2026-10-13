"""Tests for JiraClient — POST /rest/api/3/issue calls and OAuth2 token fetch."""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.jira_client import JiraClient, JiraClientError, _TokenCache, _fetch_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(
    *,
    client_id: str = "test-client-id",
    client_secret: str = "test-client-secret",
    base_url: str = "https://bmwgroup.atlassian.net",
    project_key: str = "IFOSQUALITY",
    field_cluster_id: str = "customfield_10100",
    field_root_cause_probability: str = "customfield_10101",
    field_affected_vin_count: str = "customfield_10102",
    field_prep_stage_adjustment: str = "customfield_10103",
) -> Any:
    s = MagicMock()
    s.jira_oauth2_client_id = client_id
    s.jira_oauth2_client_secret = client_secret
    s.jira_base_url = base_url
    s.jira_project_key = project_key
    s.jira_field_cluster_id = field_cluster_id
    s.jira_field_root_cause_probability = field_root_cause_probability
    s.jira_field_affected_vin_count = field_affected_vin_count
    s.jira_field_prep_stage_adjustment = field_prep_stage_adjustment
    return s


def _http_response(status_code: int, body: Any) -> httpx.Response:
    """Build a minimal httpx.Response for mocking."""
    if isinstance(body, (dict, list)):
        content = json.dumps(body).encode()
        headers = {"content-type": "application/json"}
    else:
        content = str(body).encode()
        headers = {"content-type": "text/plain"}
    return httpx.Response(status_code, content=content, headers=headers)


def _token_resp(token: str = "access-token-xyz", expires_in: int = 3600) -> httpx.Response:
    return _http_response(200, {"access_token": token, "expires_in": expires_in, "token_type": "Bearer"})


def _issue_resp(issue_id: str = "10001", issue_key: str = "IFOSQUALITY-42") -> httpx.Response:
    return _http_response(
        201,
        {
            "id": issue_id,
            "key": issue_key,
            "self": f"https://bmwgroup.atlassian.net/rest/api/3/issue/{issue_id}",
        },
    )


def _make_http_mock(responses: list[httpx.Response]) -> MagicMock:
    """Return a mock httpx.AsyncClient that serves responses in order."""
    call_count = 0

    async def _post(*args: Any, **kwargs: Any) -> httpx.Response:
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    mock = MagicMock()
    mock.post = _post
    mock.calls: list[tuple[Any, ...]] = []
    return mock


def _make_http_mock_recording(responses: list[httpx.Response]) -> tuple[MagicMock, list[dict[str, Any]]]:
    """Like _make_http_mock but also records call args."""
    call_count = 0
    recorded: list[dict[str, Any]] = []

    async def _post(*args: Any, **kwargs: Any) -> httpx.Response:
        nonlocal call_count
        recorded.append({"args": args, "kwargs": kwargs})
        resp = responses[call_count]
        call_count += 1
        return resp

    mock = MagicMock()
    mock.post = _post
    return mock, recorded


# ---------------------------------------------------------------------------
# _fetch_token
# ---------------------------------------------------------------------------


class TestFetchToken:
    @pytest.mark.asyncio
    async def test_returns_token_and_expires_in_on_200(self) -> None:
        http, recorded = _make_http_mock_recording([_token_resp("tok-abc", 1800)])
        token, ttl = await _fetch_token(client_id="cid", client_secret="csec", http=http)  # type: ignore[arg-type]
        assert token == "tok-abc"
        assert ttl == 1800

    @pytest.mark.asyncio
    async def test_raises_jira_client_error_on_non_200(self) -> None:
        http = _make_http_mock([_http_response(401, "Unauthorized")])
        with pytest.raises(JiraClientError) as exc_info:
            await _fetch_token(client_id="cid", client_secret="csec", http=http)  # type: ignore[arg-type]
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_sends_client_credentials_form_payload(self) -> None:
        http, recorded = _make_http_mock_recording([_token_resp()])
        await _fetch_token(client_id="my-cid", client_secret="my-csec", http=http)  # type: ignore[arg-type]
        assert len(recorded) == 1
        kwargs = recorded[0]["kwargs"]
        assert kwargs["data"]["grant_type"] == "client_credentials"
        assert kwargs["data"]["client_id"] == "my-cid"


# ---------------------------------------------------------------------------
# _TokenCache
# ---------------------------------------------------------------------------


class TestTokenCache:
    @pytest.mark.asyncio
    async def test_caches_token_on_second_call(self) -> None:
        responses = [_token_resp("first-token", 3600), _token_resp("second-token", 3600)]
        http = _make_http_mock(responses)
        cache = _TokenCache()
        t1 = await cache.get(client_id="cid", client_secret="csec", http=http)  # type: ignore[arg-type]
        t2 = await cache.get(client_id="cid", client_secret="csec", http=http)  # type: ignore[arg-type]
        assert t1 == "first-token"
        assert t2 == "first-token"  # second response never consumed — cached

    @pytest.mark.asyncio
    async def test_new_instance_starts_with_empty_cache(self) -> None:
        """Each _TokenCache instance maintains independent state."""
        cache_a = _TokenCache()
        cache_b = _TokenCache()
        http_a = _make_http_mock([_token_resp("token-a")])
        http_b = _make_http_mock([_token_resp("token-b")])
        t_a = await cache_a.get(client_id="cid", client_secret="csec", http=http_a)  # type: ignore[arg-type]
        t_b = await cache_b.get(client_id="cid", client_secret="csec", http=http_b)  # type: ignore[arg-type]
        assert t_a == "token-a"
        assert t_b == "token-b"


# ---------------------------------------------------------------------------
# JiraClient.create_issue
# ---------------------------------------------------------------------------


class TestJiraClientCreateIssue:
    @pytest.mark.asyncio
    async def test_create_issue_returns_parsed_body_on_201(self) -> None:
        http = _make_http_mock([_token_resp(), _issue_resp("99", "IFOSQUALITY-99")])
        client = JiraClient(settings=_settings(), http=http)  # type: ignore[arg-type]
        result = await client.create_issue({"project": {"key": "IFOSQUALITY"}})
        assert result["id"] == "99"
        assert result["key"] == "IFOSQUALITY-99"

    @pytest.mark.asyncio
    async def test_create_issue_sends_bearer_authorization_header(self) -> None:
        http, recorded = _make_http_mock_recording([_token_resp("my-token"), _issue_resp()])
        client = JiraClient(settings=_settings(), http=http)  # type: ignore[arg-type]
        await client.create_issue({"project": {"key": "IFOSQUALITY"}})
        assert len(recorded) == 2
        issue_kwargs = recorded[1]["kwargs"]
        assert issue_kwargs["headers"]["Authorization"] == "Bearer my-token"

    @pytest.mark.asyncio
    async def test_create_issue_posts_to_correct_url(self) -> None:
        http, recorded = _make_http_mock_recording([_token_resp(), _issue_resp()])
        client = JiraClient(settings=_settings(base_url="https://bmwgroup.atlassian.net"), http=http)  # type: ignore[arg-type]
        await client.create_issue({"project": {"key": "IFOSQUALITY"}})
        assert len(recorded) == 2
        # Second call is the issue create
        url_arg = recorded[1]["args"][0] if recorded[1]["args"] else recorded[1]["kwargs"].get("url")
        assert url_arg == "https://bmwgroup.atlassian.net/rest/api/3/issue"

    @pytest.mark.asyncio
    async def test_create_issue_raises_jira_client_error_on_400(self) -> None:
        http = _make_http_mock([_token_resp(), _http_response(400, {"errorMessages": ["bad field"]})])
        client = JiraClient(settings=_settings(), http=http)  # type: ignore[arg-type]
        with pytest.raises(JiraClientError) as exc_info:
            await client.create_issue({"project": {"key": "IFOSQUALITY"}})
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_issue_raises_jira_client_error_on_500(self) -> None:
        http = _make_http_mock([_token_resp(), _http_response(500, "Internal Server Error")])
        client = JiraClient(settings=_settings(), http=http)  # type: ignore[arg-type]
        with pytest.raises(JiraClientError) as exc_info:
            await client.create_issue({})
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_create_issue_fields_are_wrapped_under_fields_key(self) -> None:
        http, recorded = _make_http_mock_recording([_token_resp(), _issue_resp()])
        client = JiraClient(settings=_settings(), http=http)  # type: ignore[arg-type]
        await client.create_issue({"project": {"key": "IFOSQUALITY"}, "summary": "test"})
        issue_kwargs = recorded[1]["kwargs"]
        sent = issue_kwargs["json"]
        assert "fields" in sent
        assert sent["fields"]["summary"] == "test"

    @pytest.mark.asyncio
    async def test_create_issue_accepts_200_in_addition_to_201(self) -> None:
        http = _make_http_mock([_token_resp(), _http_response(200, {"id": "1", "key": "IFOSQUALITY-1", "self": "https://x"})])
        client = JiraClient(settings=_settings(), http=http)  # type: ignore[arg-type]
        result = await client.create_issue({})
        assert result["key"] == "IFOSQUALITY-1"
