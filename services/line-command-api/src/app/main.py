"""FastAPI application factory and lifespan hook for line-command-api."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.health import Health, health
from app.routers.ar_instructions import router as ar_router
from app.routers.expert_calls import router as expert_calls_router
from app.routers.torque import router as torque_router


def _error_body(code: str, message: str, details: list[Any] | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "correlation_id": str(uuid.uuid4()),
        "details": details or [],
    }


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    import asyncio

    from app.config import settings  # validates at startup

    consumer_task: asyncio.Task | None = None

    if settings.torque_consumer_enabled:
        from app.db import get_pool
        from app.torque_consumer import run_torque_consumer

        pool = await get_pool()
        consumer_task = asyncio.create_task(
            run_torque_consumer(
                bootstrap_servers=settings.kafka_bootstrap_servers,
                sasl_username=settings.kafka_sasl_username,
                sasl_password=settings.kafka_sasl_password,
                vdt_base_url=settings.vdt_base_url,
                vdt_timeout_seconds=settings.vdt_timeout_seconds,
                pool=pool,
            )
        )

    yield

    if consumer_task is not None:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass

    from app.db import close_pool
    from app.kafka_producer import stop_producer

    await close_pool()
    await stop_producer()


def create_app() -> FastAPI:
    app = FastAPI(
        title="line-command-api",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
        lifespan=_lifespan,
    )

    app.include_router(ar_router)
    app.include_router(expert_calls_router)
    app.include_router(torque_router)

    @app.get("/health", response_model=Health, tags=["Observability"])
    def health_check() -> Health:
        return health()

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
        details = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_body("VALIDATION_ERROR", "Request validation failed", details),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("INTERNAL_ERROR", "An unexpected error occurred"),
        )

    return app


app = create_app()
