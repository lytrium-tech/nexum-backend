"""
app/core/errors.py
==================
Jerarquía de excepciones del backend Nexum y handlers globales de FastAPI.

Jerarquía:
    NexumError
    ├── ValidationError       HTTP 422 — datos inválidos
    ├── NotFoundError         HTTP 404 — recurso no encontrado
    ├── ConflictError         HTTP 409 — regla de negocio / conflicto de estado
    ├── AuthenticationError   HTTP 401 — no autenticado
    ├── ForbiddenError        HTTP 403 — no autorizado
    └── InfrastructureError   HTTP 503 — fallo de dependencia externa

Formato de respuesta HTTP:
    {
        "ok": false,
        "error_code": "string",
        "message": "string",
        "detail": {}
    }
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


# ── Excepción base ────────────────────────────────────────────────────────────


class NexumError(Exception):
    """
    Excepción base del backend Nexum.
    Todas las excepciones de dominio deben heredar de esta clase.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "internal_error"
    message: str = "Ocurrió un error inesperado."

    def __init__(
        self,
        message: str | None = None,
        error_code: str | None = None,
        status_code: int | None = None,
        detail: dict | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.error_code = error_code or self.__class__.error_code
        self.status_code = status_code or self.__class__.status_code
        self.detail: dict = detail or {}
        super().__init__(self.message)


# ── Excepciones de dominio ────────────────────────────────────────────────────


class ValidationError(NexumError):
    """Datos de entrada inválidos o que no cumplen las reglas de validación."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_code = "validation_error"
    message = "Los datos proporcionados no son válidos."


class NotFoundError(NexumError):
    """El recurso solicitado no existe o no es accesible para el usuario."""

    status_code = status.HTTP_404_NOT_FOUND
    error_code = "not_found"
    message = "El recurso solicitado no existe."


class ConflictError(NexumError):
    """
    La operación entra en conflicto con el estado actual del sistema.
    Incluye violaciones de reglas de negocio, duplicados y estado inconsistente.
    """

    status_code = status.HTTP_409_CONFLICT
    error_code = "conflict"
    message = "La operación no puede completarse por un conflicto con el estado actual."


class AuthenticationError(NexumError):
    """No hay credenciales válidas para identificar al usuario."""

    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "authentication_error"
    message = "Autenticación requerida."


class ForbiddenError(NexumError):
    """El usuario está autenticado pero no tiene permiso para esta acción."""

    status_code = status.HTTP_403_FORBIDDEN
    error_code = "forbidden"
    message = "No tienes permiso para realizar esta acción."


class InfrastructureError(NexumError):
    """
    Fallo en una dependencia externa: base de datos, Supabase, Gemini, etc.
    Indica que el servicio no puede completar la solicitud por razones internas.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "infrastructure_error"
    message = "El servicio no está disponible temporalmente. Intenta de nuevo en unos momentos."


# ── Respuesta HTTP compartida ─────────────────────────────────────────────────


def _error_response(
    status_code: int,
    error_code: str,
    message: str,
    detail: dict | None = None,
) -> JSONResponse:
    """Construye una respuesta de error con la forma estándar de Nexum."""
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error_code": error_code,
            "message": message,
            "detail": detail or {},
        },
    )


# ── Handlers de excepciones ───────────────────────────────────────────────────


async def nexum_error_handler(request: Request, exc: NexumError) -> JSONResponse:
    """Handler para todas las excepciones propias de Nexum."""
    return _error_response(
        status_code=exc.status_code,
        error_code=exc.error_code,
        message=exc.message,
        detail=exc.detail,
    )


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handler de último recurso para errores inesperados.
    Registra el traceback completo con trace_id pero responde mensaje genérico al cliente.
    Nunca expone detalles internos en la respuesta.
    """
    logger.exception(
        "Error inesperado en el servidor",
        extra={
            "path": request.url.path,
            "method": request.method,
        },
    )
    return _error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_code="internal_error",
        message="Ocurrió un error inesperado en el servidor.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Registra todos los handlers de excepciones en la instancia de FastAPI."""
    app.add_exception_handler(NexumError, nexum_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, generic_error_handler)
