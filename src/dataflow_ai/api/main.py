"""FastAPI application factory and middleware configuration."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from dataflow_ai.ai.assistant import AnalyticsAssistant
from dataflow_ai.api.routes.ai import router as ai_router
from dataflow_ai.api.routes.analytics import router as analytics_router
from dataflow_ai.api.routes.auth import router as auth_router
from dataflow_ai.api.routes.payments import router as payments_router
from dataflow_ai.api.routes.privacy import router as privacy_router
from dataflow_ai.settings import Settings
from dataflow_ai.utils import sanitize_secret_string
from dataflow_ai.warehouse.repository import WarehouseRepository

LOGGER = logging.getLogger("dataflow_ai.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()

    # abort startup on invalid security configuration in production
    if settings.is_production:
        validation_errors = settings.validate_for_deployment()
        if validation_errors:
            raise RuntimeError(
                "Production deployment validation failed:\n- " + "\n- ".join(validation_errors)
            )

    repository = WarehouseRepository(settings)
    app = FastAPI(
        title="DataFlow AI API",
        version="0.1.0",
        description="E-commerce analytics API with natural-language query support.",
        debug=settings.debug,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization", "X-Correlation-ID"],
    )

    # security headers & request correlation id middleware
    @app.middleware("http")
    async def security_and_correlation_middleware(request: Request, call_next):
        correlation_id = (
            request.headers.get("X-Correlation-ID")
            or request.headers.get("X-Request-ID")
            or str(uuid.uuid4())
        )
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        return response

    # log unhandled errors with correlation id and return a safe error message
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
        clean_exc = sanitize_secret_string(str(exc))
        LOGGER.exception("Unhandled server error [correlation_id=%s]: %s", correlation_id, clean_exc)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "An internal server error occurred. Please contact support with this correlation ID.",
                "correlation_id": correlation_id,
            },
            headers={"X-Correlation-ID": correlation_id},
        )

    # attach correlation id to client HTTP exceptions
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
        headers = dict(exc.headers or {})
        headers["X-Correlation-ID"] = correlation_id
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "correlation_id": correlation_id},
            headers=headers,
        )

    app.state.settings = settings
    app.state.repository = repository
    app.state.assistant = AnalyticsAssistant(settings, repository)

    @app.get("/health", tags=["platform"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "dataflow-ai-api"}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def root() -> str:
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DataFlow AI</title>
</head>
<body>
    <h1>DataFlow AI Platform</h1>
    <p>API & Analytics Warehouse is running.</p>
</body>
</html>"""

    app.include_router(analytics_router)
    app.include_router(ai_router)
    app.include_router(privacy_router)
    app.include_router(auth_router)
    app.include_router(payments_router)
    return app


app = create_app()


