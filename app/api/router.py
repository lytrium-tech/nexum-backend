"""
app/api/router.py
=================
Router principal de la API de Nexum.

Estructura:
    /health         → health check (sin prefijo, para load balancers y k8s)
    /api/v1/*       → todos los endpoints de dominio (a registrar por fase)

Registro de routers de dominio (se irán descomentando por fase):
    Fase 6:  users
    Fase 6:  accounts, categories
    Fase 7:  cash (ledger)
    Fase 8:  goals
    Fase 9:  obligations
    Fase 10: credit (pendiente de validación financiera)
    Fase 11: intelligence
    Fase 12: conversations
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.router import router as accounts_router
from app.cash.router import router as cash_router
from app.categories.router import router as categories_router
from app.conversations.router import router as conversations_router
from app.core.config import settings
from app.core.database import get_db_session
from app.core.errors import InfrastructureError
from app.credit.router import router as credit_router
from app.fx.router import router as fx_router
from app.fx.router_v17 import router as fx_router_v17
from app.goals.router import router as goals_router
from app.intelligence.router import router as intelligence_router
from app.ledger.router import router as ledger_router
from app.obligations.router import router as obligations_router
from app.obligations.router_v17 import router as obligations_router_v17
from app.transfers.router import router as transfers_router
from app.users.router import router as users_router

# ── Routers ───────────────────────────────────────────────────────────────────

# Router raíz — endpoints sin prefijo (health check, sistema)
api_router = APIRouter()

# Router v1 — todos los dominios futuros viven aquí
v1_router = APIRouter(prefix="/api/v1")

# Router v1.7 — API experimental
v1_7_router = APIRouter(prefix="/api/v1.7")


# ── Health Check ──────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    """Respuesta del endpoint de salud."""

    status: str
    service: str


@api_router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Verifica que el backend esté operativo.",
    tags=["Sistema"],
)
async def health_check() -> HealthResponse:
    """
    Endpoint de salud del backend Nexum.
    No verifica conectividad con base de datos — solo que el proceso responde.
    """
    return HealthResponse(
        status="ok",
        service=settings.APP_NAME,
    )


@api_router.get(
    "/health/readiness",
    response_model=HealthResponse,
    summary="Readiness check",
    description="Verifica que el backend y la base de datos estén operativos.",
    tags=["Sistema"],
)
async def readiness_check(
    session: AsyncSession = Depends(get_db_session),
) -> HealthResponse:
    """
    Verifica que el pool de conexiones de la base de datos esté funcionando.
    Retorna 503 si la base de datos falla.
    """
    try:
        await session.execute(text("SELECT 1"))
        return HealthResponse(
            status="ok",
            service=settings.APP_NAME,
        )
    except Exception as e:
        raise InfrastructureError(
            message="Error de conexión con la base de datos",
            detail=str(e) if settings.DEBUG else None,
        )


# ── Registro del router v1 ────────────────────────────────────────────────────
# Los routers de dominio se registran aquí a medida que se implementan:

v1_router.include_router(users_router)
v1_router.include_router(accounts_router)
v1_router.include_router(categories_router)
v1_router.include_router(cash_router)
v1_router.include_router(goals_router)
v1_router.include_router(obligations_router)
v1_router.include_router(credit_router, prefix="/credit", tags=["Credit"])
v1_router.include_router(ledger_router)
v1_router.include_router(intelligence_router)
v1_router.include_router(conversations_router)
v1_router.include_router(transfers_router)
v1_router.include_router(fx_router)

v1_7_router.include_router(obligations_router_v17, prefix="/obligations", tags=["Obligations V1.7"])
v1_7_router.include_router(fx_router_v17, prefix="/fx", tags=["FX Engine V1.7"])

api_router.include_router(v1_router)
api_router.include_router(v1_7_router)
