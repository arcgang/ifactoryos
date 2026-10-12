from dataclasses import dataclass


@dataclass(frozen=True)
class Health:
    status: str
    version: str


def health() -> Health:
    """Service health payload for the readiness/liveness probe."""
    from app import __version__

    return Health(status="ok", version=__version__)
