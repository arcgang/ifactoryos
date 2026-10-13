"""vin-digital-thread-api FastAPI application factory."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.routers.events import router as events_router
from app.routers.thread import router as thread_router


def _error_body(
    code: str,
    message: str,
    details: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "correlation_id": str(uuid.uuid4()),
        "details": details or [],
    }


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from app.config import get_settings
    from app.eu_residency import assert_eu_residency

    cfg = get_settings()

    # Fail fast if Neo4j is outside the EU allowlist.
    assert_eu_residency(cfg.neo4j_uri, cfg.neo4j_eu_host_allowlist)

    # Fail fast if required graph constraints/indexes are absent.
    from neo4j import AsyncGraphDatabase

    from app.neo4j_schema import validate_neo4j_schema

    async with AsyncGraphDatabase.driver(
        cfg.neo4j_uri, auth=(cfg.neo4j_user, cfg.neo4j_password)
    ) as _schema_driver:
        await validate_neo4j_schema(_schema_driver)

    from app.kafka_consumer import start_consumer as start_uns_consumer
    from app.torque_consumer import start_torque_consumer

    await start_torque_consumer()
    await start_uns_consumer()

    yield

    from app.db import close_pool
    from app.kafka_consumer import stop_consumer as stop_uns_consumer
    from app.kafka_producer import stop_producer
    from app.torque_consumer import stop_torque_consumer

    await close_pool()
    await stop_producer()
    await stop_torque_consumer()
    await stop_uns_consumer()


def create_app() -> FastAPI:
    app = FastAPI(
        title="vin-digital-thread-api",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
        lifespan=_lifespan,
    )

    app.include_router(events_router)
    app.include_router(thread_router)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_body("VALIDATION_ERROR", "Request payload failed schema validation", details),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("INTERNAL_ERROR", "An unexpected error occurred"),
        )

    @app.get("/health", tags=["Observability"])
    def health_check() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
