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


import jwt
from jwt import PyJWKClient

# Configuración del cliente JWKS para caché
_jwks_client: PyJWKClient | None = None

def get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        url = f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(url)
    return _jwks_client

async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> AuthenticatedUser:
    """
    Dependency de FastAPI para obtener el usuario autenticado del request actual.
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

    token = credentials.credentials
    try:
        jwks_client = get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        
        # El issuer de Supabase Auth es el URL base + /auth/v1
        expected_issuer = f"{settings.SUPABASE_URL}/auth/v1"
        
        # Para la audiencia, por defecto es 'authenticated'
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "HS256", "RS256"],
            audience="authenticated",
            issuer=expected_issuer,
            options={"verify_iss": True, "verify_aud": True, "verify_exp": True}
        )
        
        user_id = payload.get("sub")
        if not user_id:
            raise AuthenticationError(message="Token inválido: falta claim 'sub'.")
            
        role = payload.get("role")
        if role != "authenticated":
            raise AuthenticationError(message="Token inválido: rol no autorizado.")

        return AuthenticatedUser(
            user_id=user_id,
            email=payload.get("email"),
            is_dev=False,
        )
    except jwt.ExpiredSignatureError:
        raise AuthenticationError(message="Token expirado.")
    except jwt.InvalidTokenError as e:
        raise AuthenticationError(message=f"Token inválido: {str(e)}")
    except Exception:
        raise AuthenticationError(message="Error validando la firma del token.")


# Alias tipado para usar como Depends en routers de dominio
AuthenticatedIdentity = Annotated[AuthenticatedUser, Depends(get_current_user)]
