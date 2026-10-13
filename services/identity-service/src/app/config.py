"""Application configuration — validated once at import time."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # PostgreSQL (Citus on-prem Munich — EU data residency)
    identity_db_url: str = "postgresql://identity_service:localpassword@localhost:5434/app"

    # Audit log (Azure Blob WORM) — shared with other services
    audit_storage_account: str = "stauditlogdev"
    audit_container_name: str = "audit-events"

    # Service identity for audit component field
    service_component: str = "identity-service"


def get_settings() -> Settings:
    return Settings()


settings = Settings()
