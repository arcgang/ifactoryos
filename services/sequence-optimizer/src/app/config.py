"""Application configuration — validated once at import time."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # PostgreSQL / Citus
    citus_url: str = "postgresql://postgres:local@localhost:5434/app"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8085"

    # Topics
    constraint_telemetry_topic: str = "bmw.munich.i4.uns.constraints.live.v1"
    constraint_alert_topic: str = "sequence.constraint.alert"
    constraint_dlq_topic: str = "uns.munich.i4.dlq.planning-cockpit-constraint-consumer"

    # Consumer group
    constraint_consumer_group: str = "planning-cockpit-constraint-consumer"

    # JWT / OIDC
    oidc_issuer_url: str = "http://localhost:8090/default"
    oidc_client_id: str = "local"

    # Required role for PUT /constraints/thresholds
    threshold_writer_role: str = "constraint-threshold-writer"


def get_settings() -> Settings:
    return Settings()
