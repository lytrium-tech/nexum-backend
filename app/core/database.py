"""
app/core/database.py
====================
Motor de base de datos y sesiones SQLAlchemy asíncronas con lazy initialization.

Diseño:
- El engine NO se crea durante el import del módulo.
- Se inicializa explícitamente en el startup de la aplicación (lifespan).
- En development/test, la app puede arrancar sin DATABASE_URL.
- En production, la config valida que DATABASE_URL esté presente antes de llegar aquí.
- Los tests unitarios usan override de dependency sin necesitar conexión real.

Flujo:
    startup → init_engine()  (si DATABASE_URL está configurada)
    request → get_db_session() → yield session
    shutdown → close_engine()
"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings
from app.core.errors import InfrastructureError

# ── Estado interno ────────────────────────────────────────────────────────────
# El engine y la session factory se inicializan una sola vez en el startup.

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


# ── Inicialización y cierre ───────────────────────────────────────────────────


async def init_engine() -> None:
    """
    Inicializa el engine asíncrono de SQLAlchemy.
    Llamar una sola vez en el startup de la aplicación (lifespan).
    No llama si DATABASE_URL no está configurada (permitido en development/test).
    """
    global _engine, _session_factory

    if not settings.DATABASE_URL:
        return  # Permitido en development y test — engine permanece None

    _engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )

    _session_factory = async_sessionmaker(
        bind=_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )


async def close_engine() -> None:
    """
    Cierra el pool de conexiones.
    Llamar en el shutdown de la aplicación (lifespan).
    Es seguro llamarlo aunque el engine no haya sido inicializado.
    """
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def get_engine() -> AsyncEngine:
    """
    Retorna el engine activo.

    Raises:
        InfrastructureError: Si el engine no ha sido inicializado.
    """
    if _engine is None:
        raise InfrastructureError(
            message="La base de datos no está disponible. El engine no ha sido inicializado.",
            error_code="database_not_initialized",
        )
    return _engine


# ── Base declarativa para modelos ORM ─────────────────────────────────────────


class Base(DeclarativeBase):
    """Clase base para todos los modelos ORM de Nexum."""

    pass


# ── Dependency de FastAPI ─────────────────────────────────────────────────────


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """
    Dependency de FastAPI que provee una sesión de base de datos por request.

    Raises:
        InfrastructureError: Si el engine no ha sido inicializado.

    Uso:
        @router.get("/example")
        async def example(db: DbSession) -> ...:
            result = await db.execute(...)
    """
    if _session_factory is None:
        raise InfrastructureError(
            message="La base de datos no está disponible.",
            error_code="database_not_initialized",
        )

    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Alias tipado para usar como Depends en routers
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
