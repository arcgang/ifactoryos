"""Application configuration — validated once at import time."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # JWT / OIDC
    oidc_issuer_url: str = "http://localhost:8090/default"
    oidc_client_id: str = "local"

    # Jira OAuth2 — injected by Vault Agent Injector at runtime
    jira_oauth2_client_id: str = ""
    jira_oauth2_client_secret: str = ""
    jira_base_url: str = "https://bmwgroup.atlassian.net"
    jira_project_key: str = "IFOSQUALITY"

    # Process Replay base URL for constructing deep-links
    quality_studio_base_url: str = "https://quality-studio.internal.bmw-factory-os.eu"

    # PostgreSQL (Citus) — alert storage
    citus_url: str = "postgresql://postgres:local@localhost:5434/app"


def get_settings() -> Settings:
    return Settings()


settings = Settings()
