"""Application configuration — validated once at import time."""

from __future__ import annotations

from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # PostgreSQL / Citus
    citus_url: str = "postgresql://postgres:local@localhost:5434/app"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8085"

    # Topics
    defect_events_topic: str = "bmw.munich.paint.defect.events.v1"
    cluster_alert_topic: str = "bmw.munich.defectgenome.cluster.alerts.v1"
    corrective_action_topic: str = "bmw.munich.defectgenome.corrective.actions.v1"
    cluster_query_result_topic: str = "bmw.munich.defectgenome.query.results.v1"
    defect_events_dlq_topic: str = "uns.munich.i4.dlq.defect-genome-paint-consumer"

    # Consumer group
    defect_consumer_group: str = "defect-genome-paint-consumer"

    # JWT / OIDC
    oidc_issuer_url: str = "http://localhost:8090/default"
    oidc_client_id: str = "local"

    # Jira OAuth 2.0 (injected from Vault)
    jira_client_id: str = ""
    jira_client_secret: str = ""
    jira_base_url: str = "https://jira.bmwgroup.net"
    jira_token_url: str = "https://auth.atlassian.com/oauth/token"
    jira_timeout_s: float = 10.0

    # KServe
    kserve_endpoint_url: str = "http://defect-genome.kserve.svc.cluster.local/v1/models/defect-genome:predict"
    kserve_timeout_s: float = 0.09
    kserve_client_id: str = ""
    kserve_client_secret: str = ""
    kserve_token_url: str = "http://localhost:8090/oauth2/token"

    # Audit log
    audit_log_url: str = "http://audit-log.internal.svc.cluster.local/api/v1/audit/event"
    audit_log_timeout_s: float = 1.0

    # Process Replay base URL (deep link into Quality Studio)
    quality_studio_base_url: str = "https://quality-studio.internal.bmw-factory-os.eu"

    # Idempotency cache TTL in seconds (5 minutes per design decision)
    idempotency_cache_ttl_s: int = 300

    # Confidence threshold cache TTL in seconds (30 s per design decision)
    confidence_cache_ttl_s: int = 30

    # Required roles
    defect_genome_reader_role: str = "quality-engineer"
    defect_genome_writer_role: str = "quality-lead"


_settings: Any = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings  # type: ignore[return-value]
