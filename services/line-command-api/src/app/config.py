"""Application configuration — validated once at import time."""

from __future__ import annotations

from datetime import timedelta

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # JWT / OIDC
    oidc_issuer_url: str = "http://localhost:8090/default"
    oidc_client_id: str = "local"

    # Roles required for AR instruction endpoint
    associate_role: str = "ifos-associate"
    shift_lead_role: str = "ifos-shiftlead"

    # PostgreSQL (Citus)
    citus_url: str = "postgresql://postgres:local@localhost:5434/app"

    # vin-digital-thread-api upstream
    vdt_base_url: str = "http://localhost:8001"
    vdt_timeout_seconds: float = 10.0

    # Fallback PDF QR URL template — {vin} is interpolated at runtime
    fallback_pdf_qr_url_template: str = "https://docs.internal.ifos.example/instructions/{vin}.pdf"

    # Cache TTL for ar_instruction_cache rows
    cache_ttl_seconds: int = 1800  # 30 minutes default

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_sasl_username: str = ""
    kafka_sasl_password: str = ""

    # Torque consumer
    torque_consumer_enabled: bool = True

    # Audit log (Azure Blob WORM)
    audit_storage_account: str = "stauditlogdev"
    audit_container_name: str = "audit-events"

    # Expert call — Works Council plant ID for video streaming approval lookup
    expert_call_plant_id: str = "MUNICH"


def get_settings() -> Settings:
    return Settings()


settings = Settings()
