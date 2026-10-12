"""Configuration — validated once at import time.

All secrets are read from environment; no hardcoded values.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Azure Blob WORM — from immutable-audit-log module outputs storage_account_name / audit_container_name
    audit_storage_account: str = "stauditlogdev"
    audit_container_name: str = "audit-events"

    # Write latency warning threshold (ms) — warn before the 1 s hard limit
    write_warn_ms: int = 950

    # HTTP server
    host: str = "0.0.0.0"
    port: int = 8080


settings = Settings()
