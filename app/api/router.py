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

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings
from app.accounts.router import router as accounts_router
from app.categories.router import router as categories_router
from app.users.router import router as users_router
# ── Routers ───────────────────────────────────────────────────────────────────

# Router raíz — endpoints sin prefijo (health check, sistema)
api_router = APIRouter()

# Router v1 — todos los dominios futuros viven aquí
v1_router = APIRouter(prefix="/api/v1")


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


# ── Registro del router v1 ────────────────────────────────────────────────────
# Los routers de dominio se registran aquí a medida que se implementan:


# from app.ledger.router import router as ledger_router
# from app.cash.router import router as cash_router
# from app.goals.router import router as goals_router
# from app.obligations.router import router as obligations_router
# from app.intelligence.router import router as intelligence_router
# from app.conversations.router import router as conversations_router

v1_router.include_router(users_router)
v1_router.include_router(accounts_router)
v1_router.include_router(categories_router)
# v1_router.include_router(ledger_router, prefix="/ledger", tags=["Ledger"])
# v1_router.include_router(cash_router, prefix="/cash", tags=["Cash"])
# v1_router.include_router(goals_router, prefix="/goals", tags=["Goals"])
# v1_router.include_router(obligations_router, prefix="/obligations", tags=["Obligations"])
# v1_router.include_router(intelligence_router, prefix="/intelligence", tags=["Intelligence"])
# v1_router.include_router(conversations_router, prefix="/conversations", tags=["Conversations"])

api_router.include_router(v1_router)
