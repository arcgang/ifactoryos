"""FastAPI application factory for defect-genome."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.health import Health, health
from app.routers.alerts import router as alerts_router
from app.routers.cluster_query import router as cluster_query_router
from app.routers.clusters import router as clusters_router
from app.routers.constraint_alerts import router as constraint_alerts_router
from app.routers.corrective_action import router as corrective_action_router


def _error_body(code: str, message: str, details: list[Any] | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "correlation_id": str(uuid.uuid4()),
        "details": details or [],
    }


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from app.config import settings  # validates env vars at startup  # noqa: F401
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="defect-genome",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
        lifespan=_lifespan,
    )

    app.include_router(alerts_router)
    app.include_router(constraint_alerts_router)
    app.include_router(cluster_query_router)
    app.include_router(clusters_router)
    app.include_router(corrective_action_router)

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
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("INTERNAL_ERROR", "An unexpected error occurred"),
        )

    return app


app = create_app()
