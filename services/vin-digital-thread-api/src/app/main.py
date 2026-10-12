"""FastAPI application factory and lifespan hook."""

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

    yield

    from app.db import close_pool
    from app.kafka_producer import stop_producer

    await close_pool()
    await stop_producer()


def _error_body(code: str, message: str) -> dict:
    return {"code": code, "message": message, "correlation_id": str(uuid.uuid4())}


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

    return app


app = create_app()
