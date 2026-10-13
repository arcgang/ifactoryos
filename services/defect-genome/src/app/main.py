"""FastAPI application factory and lifespan hook for defect-genome."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.health import Health, health
from app.router import defect_router, model_router

logger = logging.getLogger(__name__)


def _error_body(code: str, message: str, details: list[Any] | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "correlation_id": str(uuid.uuid4()),
        "details": details or [],
    }


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from app.config import get_settings
    from app.db import close_pool, get_pool

    get_settings()
    pool = await get_pool()

    consumer_task = asyncio.create_task(_start_consumer(pool))

    yield

    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    from app.kafka_producer import stop_producer

    await close_pool()
    await stop_producer()


async def _start_consumer(pool: "asyncpg.Pool") -> None:
    from app.consumer import run_consumer

    try:
        await run_consumer(pool)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Paint defect event consumer terminated unexpectedly")


def create_app() -> FastAPI:
    app = FastAPI(
        title="defect-genome",
        version=__version__,
        lifespan=_lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            body = {**exc.detail, "correlation_id": exc.detail.get("correlation_id", str(uuid.uuid4()))}
        else:
            body = _error_body("HTTP_ERROR", str(exc.detail))
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(
            status_code=400,
            content=_error_body("VALIDATION_ERROR", "Request payload failed schema validation", details),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content=_error_body("INTERNAL_ERROR", "An unexpected error occurred"),
        )

    app.include_router(defect_router)
    app.include_router(model_router)

    @app.get("/health", response_model=Health, tags=["Observability"])
    def health_check() -> Health:
        return health()

    return app


app = create_app()
