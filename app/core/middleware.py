"""
app/core/middleware.py
======================
Middlewares transversales del backend Nexum.

TraceIDMiddleware:
    - Si el request incluye el header `X-Trace-ID` con un UUID válido, lo conserva.
    - Si el header falta o contiene un valor inválido, genera un UUID nuevo.
    - Inyecta el trace_id en el contexto de structlog para que aparezca en todos
      los logs del ciclo de vida del request.
    - Añade el trace_id al header de respuesta `X-Trace-ID`.
    - Limpia el contexto de structlog al finalizar el request.

Política de seguridad:
    - No se registran bodies ni payloads del request.
    - No se registran headers de autorización ni credenciales.
"""

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from structlog.contextvars import bind_contextvars, clear_contextvars


def _parse_trace_id(value: str | None) -> str:
    """
    Valida y retorna el trace_id del header entrante.
    Si el valor es None o no es un UUID válido, genera uno nuevo.
    """
    if value:
        try:
            # Validar que sea un UUID bien formado
            return str(uuid.UUID(value))
        except ValueError:
            pass
    return str(uuid.uuid4())


class TraceIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware que gestiona el trace_id por request.

    El trace_id permite correlacionar todos los logs de un ciclo de vida HTTP.
    También es útil para el cliente al reportar errores.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Limpiar contexto del request anterior (por seguridad en async)
        clear_contextvars()

        # Extraer o generar trace_id
        incoming = request.headers.get("X-Trace-ID")
        trace_id = _parse_trace_id(incoming)

        # Inyectar en contexto de structlog — se propaga a todos los logs del request
        bind_contextvars(trace_id=trace_id)

        # Procesar el request
        response = await call_next(request)

        # Añadir trace_id al header de respuesta
        response.headers["X-Trace-ID"] = trace_id

        # Limpiar contexto al finalizar
        clear_contextvars()

        return response
