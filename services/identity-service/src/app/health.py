"""Health check model."""

from __future__ import annotations

from pydantic import BaseModel


class Health(BaseModel):
    status: str
    version: str


def health() -> Health:
    from app import __version__

    return Health(status="ok", version=__version__)
