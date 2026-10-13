"""Application configuration — validated once at import time."""

from __future__ import annotations

from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # JWT / OIDC
    oidc_issuer_url: str = "http://localhost:8090/default"
    oidc_client_id: str = "local"

    # PostgreSQL/Citus — mirrored certificate store
    citus_url: str = "postgresql://postgres:local@localhost:5434/app"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"

    # Hyperledger Fabric — injected by Vault agent at pod startup
    fabric_peer_endpoint: str = "localhost:7051"
    fabric_tls_cert_path: str = "/var/hyperledger/tls/ca.crt"
    fabric_client_cert_path: str = "/var/hyperledger/client/client.crt"
    fabric_client_key_path: str = "/var/hyperledger/client/client.key"
    fabric_channel_name: str = "certificate-channel"
    fabric_chaincode_name: str = "certificate"
    fabric_msp_id: str = "quality-studio-fabric-client"

    # Fabric commit timeout (55 s — leaves 5 s buffer against the 60 s SLA)
    fabric_commit_timeout_s: float = 55.0

    # Roles
    certificate_issuer_role: str = "CERTIFICATE_ISSUER"
    certificate_reader_role: str = "CERTIFICATE_READER"


_settings: Any = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings  # type: ignore[return-value]


settings = get_settings()
