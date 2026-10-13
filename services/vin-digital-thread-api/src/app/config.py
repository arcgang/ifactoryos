"""Application configuration — validated once at import time."""

from __future__ import annotations

from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # JWT / OIDC
    oidc_issuer_url: str = "http://localhost:8090/default"
    oidc_client_id: str = "local"

    # Audit log (Azure Blob WORM) — read by audit_client SDK directly from env
    audit_storage_account: str = "stauditlogdev"
    audit_container_name: str = "audit-events"

    # Required role for event ingestion (service-to-service Client Credentials)
    ingestor_role: str = "thread-ingestor"

    # Required roles for read endpoints (quality engineers and leads)
    quality_reader_roles: str = "quality-engineer,quality-lead"

    # Path to paint prep stage configuration YAML (14 stages with expected parameters)
    paint_prep_stage_config_path: str = "paint_prep_stage_config.yaml"

    # Completeness cache TTL — if updated_at is within this many seconds, skip Neo4j re-query
    completeness_cache_ttl_s: int = 30

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "localpassword"

    # PostgreSQL/Citus
    citus_url: str = "postgresql://postgres:local@localhost:5434/app"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    uns_manufacturing_topic: str = "factory.munich.uns.manufacturing.event.v1"
    uns_consumer_group: str = "vin-digital-thread-api-consumer"

    # Identity service — GDPR pseudonymisation (bmw-entra-id)
    identity_service_url: str = "http://localhost:8083"
    identity_service_timeout_s: float = 2.0

    # EU data residency — comma-separated list of allowed Neo4j hostnames/IP prefixes.
    # Empty string disables the allowlist check (local dev only).
    neo4j_eu_host_allowlist: str = ""


# Module-level singleton. Tests replace this via monkeypatch to inject fakes
# without touching os.environ. Production leaves it None until first call.
_settings: Any = None


def get_settings() -> Settings:
    """Return the active Settings instance, creating it on first call.

_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


settings = get_settings()
    Tests may set the module-level ``_settings`` to a fake before calling
    any code that uses configuration — the first non-None value wins.
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings  # type: ignore[return-value]
