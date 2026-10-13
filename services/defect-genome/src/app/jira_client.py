"""Jira REST API v3 client — OAuth 2.0 Client Credentials flow.

Calls POST /rest/api/3/issue to create Corrective Action tickets.
Credentials are injected by the Vault Agent Injector at runtime via
JIRA_OAUTH2_CLIENT_ID and JIRA_OAUTH2_CLIENT_SECRET environment variables.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.config import Settings

_ATLASSIAN_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
_JIRA_AUDIENCE = "api.atlassian.com"

# Issuer-specific claim for Atlassian OAuth 2.0 -- required field
_GRANT_TYPE = "client_credentials"


class JiraClientError(Exception):
    """Raised when the Jira API returns a non-2xx status."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Jira API error {status_code}: {body[:200]}")


class _TokenCache:
    """Thread-safe (asyncio) token cache — one entry per client instance."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get(
        self,
        *,
        client_id: str,
        client_secret: str,
        http: httpx.AsyncClient,
    ) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._expires_at - 60:
                return self._token
            token, ttl = await _fetch_token(
                client_id=client_id,
                client_secret=client_secret,
                http=http,
            )
            self._token = token
            self._expires_at = time.monotonic() + ttl
            return token


async def _fetch_token(
    *,
    client_id: str,
    client_secret: str,
    http: httpx.AsyncClient,
) -> tuple[str, int]:
    """Exchange client credentials for an access token.

    Returns (access_token, expires_in_seconds).
    """
    resp = await http.post(
        _ATLASSIAN_TOKEN_URL,
        data={
            "grant_type": _GRANT_TYPE,
            "client_id": client_id,
            "client_secret": client_secret,
            "audience": _JIRA_AUDIENCE,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10.0,
    )
    if resp.status_code != 200:
        raise JiraClientError(resp.status_code, resp.text)
    payload = resp.json()
    return str(payload["access_token"]), int(payload.get("expires_in", 3600))


class JiraClient:
    """Async Jira REST API v3 client.

    Instantiate once per process; the token cache is per-instance.
    Pass `http` in tests to inject a fake transport.
    """

    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._http = http or httpx.AsyncClient()
        self._token_cache = _TokenCache()

    async def _bearer(self) -> str:
        return await self._token_cache.get(
            client_id=self._settings.jira_oauth2_client_id,
            client_secret=self._settings.jira_oauth2_client_secret,
            http=self._http,
        )

    async def create_issue(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Call POST /rest/api/3/issue and return the created issue body.

        Raises JiraClientError on non-2xx.
        """
        token = await self._bearer()
        url = f"{self._settings.jira_base_url}/rest/api/3/issue"
        resp = await self._http.post(
            url,
            json={"fields": fields},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        if resp.status_code not in (200, 201):
            raise JiraClientError(resp.status_code, resp.text)
        return resp.json()  # type: ignore[return-value]

    async def aclose(self) -> None:
        await self._http.aclose()
