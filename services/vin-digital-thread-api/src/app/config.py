"""Service configuration — validated at import, fails fast on missing vars."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str

    citus_url: str

    kafka_bootstrap_servers: str = "localhost:9092"

    oidc_issuer_url: str
    oidc_client_id: str

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
