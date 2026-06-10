"""
tests/unit/test_core.py
========================
Tests unitarios del core transversal de Nexum — Fase 3.

Cobertura:
    - Health check (shape y status)
    - Trace ID: generado, entrante válido conservado, inválido reemplazado
    - Shape de errores HTTP (NotFoundError, InfrastructureError, ConflictError)
    - Auth bypass permitido en development
    - Auth bypass prohibido en production (validación de config)
    - Config de producción rechaza arranque sin secretos
    - App arranca en development sin DATABASE_URL
    - Dependency DB lanza InfrastructureError si no hay engine inicializado

Todos los tests usan ASGITransport y overrides de dependency.
Ningún test requiere conexión real a Supabase.
"""

import uuid
from unittest.mock import patch

import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.database import get_db_session
from app.core.errors import (
    ConflictError,
    InfrastructureError,
    NotFoundError,
)
from app.core.middleware import _parse_trace_id
from app.main import app

# ── Fixture de cliente HTTP ───────────────────────────────────────────────────


@pytest.fixture
async def client() -> AsyncClient:
    """Cliente HTTP async con lifespan de la app activado."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ── Health check ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_returns_200(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_returns_correct_body(client: AsyncClient) -> None:
    response = await client.get("/health")
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "nexum-backend"


@pytest.mark.asyncio
async def test_health_response_has_expected_keys(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert set(response.json().keys()) == {"status", "service"}


# ── Trace ID ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trace_id_in_response_header(client: AsyncClient) -> None:
    """El response debe incluir header X-Trace-ID con un UUID válido."""
    response = await client.get("/health")
    assert "x-trace-id" in response.headers
    trace_id = response.headers["x-trace-id"]
    # Verificar que sea un UUID válido
    parsed = uuid.UUID(trace_id)
    assert str(parsed) == trace_id


@pytest.mark.asyncio
async def test_trace_id_valid_incoming_is_preserved(client: AsyncClient) -> None:
    """Si el cliente envía un UUID válido, debe conservarse en la respuesta."""
    incoming = str(uuid.uuid4())
    response = await client.get("/health", headers={"X-Trace-ID": incoming})
    assert response.headers["x-trace-id"] == incoming


@pytest.mark.asyncio
async def test_trace_id_invalid_incoming_is_replaced(client: AsyncClient) -> None:
    """Si el cliente envía un valor inválido, debe generarse un nuevo UUID."""
    response = await client.get("/health", headers={"X-Trace-ID": "not-a-uuid"})
    trace_id = response.headers["x-trace-id"]
    # Debe ser un UUID válido pero no el original
    assert trace_id != "not-a-uuid"
    uuid.UUID(trace_id)  # No lanza excepción → es válido


@pytest.mark.asyncio
async def test_trace_id_missing_generates_new(client: AsyncClient) -> None:
    """Si el cliente no envía X-Trace-ID, se genera uno nuevo."""
    response = await client.get("/health")
    trace_id = response.headers.get("x-trace-id")
    assert trace_id is not None
    uuid.UUID(trace_id)  # Válido


# ── Utilidad interna de parse_trace_id ───────────────────────────────────────


def test_parse_trace_id_with_valid_uuid() -> None:
    valid = str(uuid.uuid4())
    assert _parse_trace_id(valid) == valid


def test_parse_trace_id_with_invalid_string() -> None:
    result = _parse_trace_id("invalid-value")
    uuid.UUID(result)  # Debe ser un UUID nuevo válido


def test_parse_trace_id_with_none() -> None:
    result = _parse_trace_id(None)
    uuid.UUID(result)  # Debe ser un UUID nuevo válido


# ── Shape de errores ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_error_shape_from_not_found(client: AsyncClient) -> None:
    """Un NotFoundError debe retornar la shape estándar de error."""
    temp_router = APIRouter()

    @temp_router.get("/test-not-found-nf")
    async def raise_not_found() -> None:
        raise NotFoundError(message="Recurso de prueba no encontrado.")

    app.include_router(temp_router)

    response = await client.get("/test-not-found-nf")
    assert response.status_code == 404
    data = response.json()
    assert data["ok"] is False
    assert data["error_code"] == "not_found"
    assert "message" in data
    assert "detail" in data


@pytest.mark.asyncio
async def test_error_shape_from_infrastructure_error(client: AsyncClient) -> None:
    """Un InfrastructureError debe retornar HTTP 503 con la shape estándar."""
    temp_router = APIRouter()

    @temp_router.get("/test-infra-error-ie")
    async def raise_infra() -> None:
        raise InfrastructureError(message="Servicio no disponible.")

    app.include_router(temp_router)

    response = await client.get("/test-infra-error-ie")
    assert response.status_code == 503
    data = response.json()
    assert data["ok"] is False
    assert data["error_code"] == "infrastructure_error"


@pytest.mark.asyncio
async def test_error_shape_from_conflict_error(client: AsyncClient) -> None:
    """Un ConflictError debe retornar HTTP 409."""
    temp_router = APIRouter()

    @temp_router.get("/test-conflict-ce")
    async def raise_conflict() -> None:
        raise ConflictError(message="Estado en conflicto.")

    app.include_router(temp_router)

    response = await client.get("/test-conflict-ce")
    assert response.status_code == 409
    data = response.json()
    assert data["ok"] is False
    assert data["error_code"] == "conflict"


# ── Auth bypass ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_bypass_returns_dev_user(client: AsyncClient) -> None:
    """Con bypass habilitado, get_current_user retorna usuario de desarrollo."""
    from app.core.security import get_current_user

    # Asegurar que bypass está habilitado (es el default en development)
    with patch("app.core.security.settings") as mock_settings:
        mock_settings.AUTH_BYPASS_ENABLED = True
        mock_settings.is_production = False
        mock_settings.DEV_USER_ID = "00000000-0000-0000-0000-000000000001"
        mock_settings.DEV_USER_EMAIL = "dev@nexum.local"

        user = await get_current_user(credentials=None)

    assert user.user_id == "00000000-0000-0000-0000-000000000001"
    assert user.email == "dev@nexum.local"
    assert user.is_dev is True


@pytest.mark.asyncio
async def test_auth_bypass_raises_without_credentials_when_disabled() -> None:
    """Sin bypass y sin credenciales, debe lanzar AuthenticationError."""
    from app.core.errors import AuthenticationError
    from app.core.security import get_current_user

    with patch("app.core.security.settings") as mock_settings:
        mock_settings.AUTH_BYPASS_ENABLED = False
        mock_settings.is_production = False

        with pytest.raises(AuthenticationError):
            await get_current_user(credentials=None)


# ── Validación de configuración de producción ─────────────────────────────────


def test_config_production_fails_without_database_url() -> None:
    """Settings con APP_ENV=production debe fallar si DATABASE_URL está vacía."""
    with pytest.raises(Exception, match="DATABASE_URL"):
        Settings(
            APP_ENV="production",
            DATABASE_URL="",
            SUPABASE_URL="https://example.supabase.co",
            SUPABASE_SERVICE_ROLE_KEY="fake-service-key",
            GEMINI_API_KEY="fake-gemini-key",
            AUTH_BYPASS_ENABLED=False,
        )


def test_config_production_fails_with_auth_bypass_enabled() -> None:
    """Settings con APP_ENV=production y AUTH_BYPASS_ENABLED=true debe fallar."""
    with pytest.raises(Exception, match="AUTH_BYPASS_ENABLED"):
        Settings(
            APP_ENV="production",
            DATABASE_URL="postgresql+asyncpg://user:pass@host/db",
            SUPABASE_URL="https://example.supabase.co",
            SUPABASE_SERVICE_ROLE_KEY="fake-service-key",
            GEMINI_API_KEY="fake-gemini-key",
            AUTH_BYPASS_ENABLED=True,
        )


def test_config_development_starts_without_database_url() -> None:
    """En development, DATABASE_URL vacía no debe lanzar error en la config."""
    s = Settings(APP_ENV="development", DATABASE_URL="")
    assert s.DATABASE_URL == ""
    assert s.is_development is True


def test_config_log_level_derived_from_debug() -> None:
    """effective_log_level debe derivarse de DEBUG si LOG_LEVEL no está definido."""
    s_debug = Settings(APP_ENV="development", DEBUG=True, LOG_LEVEL="")
    assert s_debug.effective_log_level == "DEBUG"

    s_prod_like = Settings(APP_ENV="development", DEBUG=False, LOG_LEVEL="")
    assert s_prod_like.effective_log_level == "INFO"


def test_config_explicit_log_level_takes_priority() -> None:
    """LOG_LEVEL explícito tiene prioridad sobre el derivado de DEBUG."""
    s = Settings(APP_ENV="development", DEBUG=True, LOG_LEVEL="warning")
    assert s.effective_log_level == "WARNING"


# ── DB dependency sin engine ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_db_session_raises_infrastructure_error_without_engine() -> None:
    """
    Si no hay engine inicializado, get_db_session debe lanzar InfrastructureError.
    No requiere conexión real a Supabase.
    """
    import app.core.database as db_module

    # Guardar estado original
    original_engine = db_module._engine
    original_factory = db_module._session_factory

    try:
        # Simular que no hay engine
        db_module._engine = None
        db_module._session_factory = None

        with pytest.raises(InfrastructureError) as exc_info:
            async for _ in get_db_session():
                pass

        assert exc_info.value.error_code == "database_not_initialized"
    finally:
        # Restaurar estado
        db_module._engine = original_engine
        db_module._session_factory = original_factory
