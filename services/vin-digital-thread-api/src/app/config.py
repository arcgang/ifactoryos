"""Application configuration — validated once at import time."""

from __future__ import annotations

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

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "localpassword"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"


settings = Settings()
