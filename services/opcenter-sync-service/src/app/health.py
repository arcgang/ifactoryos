"""GET /health endpoint."""

from __future__ import annotations

from pydantic import BaseModel

from app import __version__


class Health(BaseModel):
    status: str
    version: str


def health() -> Health:
    return Health(status="ok", version=__version__)
