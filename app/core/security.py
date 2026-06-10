"""
app/core/security.py
====================
Autenticación base del backend Nexum.

Estado actual:
- No hay validación real de JWT.
- En development/test con AUTH_BYPASS_ENABLED=true, se retorna un usuario de desarrollo fijo.
- En production, AUTH_BYPASS_ENABLED=true falla en startup (validado en config.py).
- El Bearer token NUNCA se usa como user_id.

Próxima fase (Fase 6 — Users/Auth):
- Verificar JWT de Supabase Auth.
- Extraer user_id real del claim `sub`.
- Validar expiración, audience y signature.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.errors import AuthenticationError

# Esquema de extracción Bearer — auto_error=False para manejar el error nosotros mismos
_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    """
    Representación inmutable de un usuario autenticado.

    Campos:
        user_id: UUID del usuario en Supabase (string UUID v4).
        email:   Email del usuario (opcional hasta que el auth real esté implementado).
        is_dev:  True si el usuario fue generado por el modo bypass de desarrollo.
    """

    user_id: str
    email: str | None = None
    is_dev: bool = False


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> AuthenticatedUser:
    """
    Dependency de FastAPI para obtener el usuario autenticado del request actual.

    Flujo actual:
    1. Si AUTH_BYPASS_ENABLED=true (solo en development/test):
       → retorna usuario de desarrollo fijo (DEV_USER_ID).
    2. Si no hay credenciales:
       → lanza AuthenticationError (HTTP 401).
    3. Si hay Bearer token:
       → pendiente de validación JWT real en Fase 6.
       → actualmente lanza AuthenticationError porque no existe validación real.

    Raises:
        AuthenticationError: Si no hay credenciales válidas o el bypass está desactivado.
    """
    # Modo bypass de desarrollo
    if settings.AUTH_BYPASS_ENABLED and not settings.is_production:
        return AuthenticatedUser(
            user_id=settings.DEV_USER_ID,
            email=settings.DEV_USER_EMAIL,
            is_dev=True,
        )

    # Sin credenciales → 401
    if not credentials:
        raise AuthenticationError(
            message="Autenticación requerida. Incluye un Bearer token válido.",
        )

    # TODO (Fase 6): Verificar JWT de Supabase Auth aquí.
    # Por ahora, la presencia de token no implica autenticación válida.
    # Se rechaza con 401 para evitar acceso sin validación real.
    raise AuthenticationError(
        message="La validación de tokens JWT no está implementada todavía.",
        detail={"hint": "AUTH_BYPASS_ENABLED=true está disponible en development."},
    )


# Alias tipado para usar como Depends en routers de dominio
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
