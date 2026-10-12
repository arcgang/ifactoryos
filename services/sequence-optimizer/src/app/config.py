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
    sequence_optimized_topic: str = "bmw.munich.i4.sequence.optimized.v1"
    resequence_triggered_topic: str = "sequence.resequence.triggered"

    # Consumer group
    constraint_consumer_group: str = "planning-cockpit-constraint-consumer"

    # JWT / OIDC
    oidc_issuer_url: str = "http://localhost:8090/default"
    oidc_client_id: str = "local"

    # Required role for PUT /constraints/thresholds
    threshold_writer_role: str = "constraint-threshold-writer"

    # Required role for POST /resequence
    resequence_writer_role: str = "production-planner"

    # Required role for POST /my26/config/save
    my26_config_writer_role: str = "production-planner"

    # Required role for POST /overrides
    override_writer_role: str = "production-planner"

    # MY26 config activated event topic
    my26_config_activated_topic: str = "uns.munich.i4.sequence.my26.config.activated.v1"

    # KServe model endpoint
    kserve_endpoint_url: str = "http://sequence-optimizer.kserve.svc.cluster.local/v1/models/sequence-optimizer:predict"
    kserve_timeout_s: float = 43.0

    # Audit log endpoint
    audit_log_url: str = "http://audit-log.internal.svc.cluster.local/api/v1/audit/event"
    audit_log_timeout_s: float = 1.0

    # Idempotency cache TTL in seconds
    idempotency_cache_ttl_s: int = 600

    # OAuth 2.0 client credentials for KServe
    kserve_client_id: str = ""
    kserve_client_secret: str = ""
    kserve_token_url: str = "http://localhost:8090/oauth2/token"


def get_settings() -> Settings:
    return Settings()
