"""Application configuration — validated once at import time."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # JWT / OIDC
    oidc_issuer_url: str = "http://localhost:8090/default"
    oidc_client_id: str = "local"

    # Role required for gate-results endpoints
    quality_gate_validator_role: str = "quality-gate-validator"

    # PostgreSQL (Citus)
    citus_url: str = "postgresql://postgres:local@localhost:5434/app"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_sasl_username: str = ""
    kafka_sasl_password: str = ""

    # Confluent Schema Registry
    schema_registry_url: str = "http://localhost:8085"

    # Gate result timeout for completeness polling (seconds)
    gate_result_timeout_seconds: int = 600  # 10 minutes default

    # OpenTelemetry
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"


def get_settings() -> Settings:
    return Settings()


settings = Settings()
