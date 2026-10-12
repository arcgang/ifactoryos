"""FastAPI application factory and lifespan hook."""
"""vin-digital-thread-api FastAPI application factory."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.health import Health, health
from app.router import router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from app.config import get_settings

    # Validate config at startup — fails fast with a clear message.
    get_settings()

    from app.torque_consumer import start_torque_consumer

    await start_torque_consumer()

    yield

    from app.db import close_pool
    from app.kafka_producer import stop_producer
    from app.torque_consumer import stop_torque_consumer

    await close_pool()
    await stop_producer()
    await stop_torque_consumer()


def _error_body(code: str, message: str) -> dict:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.routers.events import router as events_router


def _error_body(code: str, message: str, details: list[Any] | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "correlation_id": str(uuid.uuid4()),
        "details": details or [],
    }


def create_app() -> FastAPI:
    app = FastAPI(
        title="vin-digital-thread-api",
        version="0.1.0",
        lifespan=_lifespan,
    )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            body = {**exc.detail, "correlation_id": str(uuid.uuid4())}
        else:
            body = _error_body("HTTP_ERROR", str(exc.detail))
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        body = {
            "code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "correlation_id": str(uuid.uuid4()),
            "details": exc.errors(),
        }
        return JSONResponse(status_code=422, content=body)

    app.include_router(router)

    @app.get("/health", response_model=Health, tags=["Observability"])
    def health_check() -> Health:
        return health()
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
    )

    app.include_router(events_router)

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
        request: Request, exc: Exception
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("INTERNAL_ERROR", "An unexpected error occurred"),
        )

    return app


app = create_app()
