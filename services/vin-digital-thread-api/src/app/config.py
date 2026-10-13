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

    # Roles allowed to read thread data (OBO tokens — quality-engineer or quality-lead)
    reader_roles: str = "quality-engineer,quality-lead"

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "localpassword"

    # Neo4j read timeout for Process Replay queries (seconds)
    neo4j_read_timeout_s: float = 9.0

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

    # Completeness cache TTL in seconds (30 s per architecture decision)
    completeness_cache_ttl_s: int = 30

    def reader_role_set(self) -> set[str]:
        return {r.strip() for r in self.reader_roles.split(",") if r.strip()}


# Module-level singleton. Tests replace this via monkeypatch to inject fakes
# without touching os.environ. Production leaves it None until first call.
_settings: Any = None


def get_settings() -> Settings:
    """Return the active Settings instance, creating it on first call.

    Tests may set the module-level ``_settings`` to a fake before calling
    any code that uses configuration — the first non-None value wins.
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings  # type: ignore[return-value]


settings = get_settings()
