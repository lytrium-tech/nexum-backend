"""
Nexum Backend — Entrada principal de la aplicación FastAPI.

Lifecycle (lifespan):
    startup  → configure_logging, init_engine (si DATABASE_URL está configurada)
    shutdown → close_engine

Middlewares (de afuera hacia adentro):
    1. CORSMiddleware
    2. TraceIDMiddleware
    3. Exception handlers
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.database import close_engine, init_engine
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import TraceIDMiddleware

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """
    Gestión del ciclo de vida de la aplicación.
    Startup: configura logging e inicializa el engine de base de datos.
    Shutdown: cierra el pool de conexiones.
    """
    # ── Startup ──────────────────────────────────────────────────────────
    configure_logging()

    logger.info(
        "nexum_backend_startup",
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        env=settings.APP_ENV,
        debug=settings.DEBUG,
        auth_bypass=settings.AUTH_BYPASS_ENABLED,
        database_configured=bool(settings.DATABASE_URL),
    )

    await init_engine()

    if settings.DATABASE_URL:
        logger.info("database_engine_initialized")
    else:
        logger.warning(
            "database_engine_skipped",
            reason="DATABASE_URL no configurada. "
            "Endpoints que requieran DB lanzarán InfrastructureError.",
        )

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    await close_engine()
    logger.info("nexum_backend_shutdown")


def create_app() -> FastAPI:
    """Factory de la aplicación FastAPI."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Sistema operativo financiero personal impulsado por IA.",
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    # ── Middlewares (orden: el último registrado es el primero en ejecutarse) ──
    app.add_middleware(TraceIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Trace-ID"],
        expose_headers=["X-Trace-ID"],
    )

    # ── Exception handlers ────────────────────────────────────────────────
    register_exception_handlers(app)

    # ── Rutas ─────────────────────────────────────────────────────────────
    app.include_router(api_router)

    return app


app = create_app()
