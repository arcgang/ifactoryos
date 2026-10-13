"""Application configuration — validated once at import time."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # JWT / OIDC
    oidc_issuer_url: str = "http://localhost:8090/default"
    oidc_client_id: str = "local"

    # RBAC role names (BMW Entra ID)
    certificate_issuer_role: str = "CERTIFICATE_ISSUER"
    certificate_reader_role: str = "CERTIFICATE_READER"

    # PostgreSQL (Citus)
    citus_url: str = "postgresql://postgres:local@localhost:5434/app"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_sasl_username: str = ""
    kafka_sasl_password: str = ""

    # Hyperledger Fabric peer
    fabric_peer_endpoint: str = "localhost:7051"
    fabric_tls_cert_path: str = "/var/run/secrets/fabric/tls.crt"
    fabric_client_cert_path: str = "/var/run/secrets/fabric/client.crt"
    fabric_client_key_path: str = "/var/run/secrets/fabric/client.key"
    fabric_channel_name: str = "mychannel"
    fabric_chaincode_name: str = "certificate"
    fabric_transaction_timeout_seconds: float = 55.0


def get_settings() -> Settings:
    return Settings()


settings = Settings()
