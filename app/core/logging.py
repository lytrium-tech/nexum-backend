"""
app/core/logging.py
===================
Logging estructurado con structlog para el backend Nexum.

Comportamiento por entorno:
- development / test  → ConsoleRenderer (legible, con colores)
- staging / production → JSONRenderer  (estructurado para observabilidad)

Política de seguridad:
- raw_message nunca aparece en logs automáticos.
- Secretos nunca se registran (SecretStr los oculta a nivel de pydantic).
- trace_id se propaga automáticamente desde el contexto de cada request.
- No se registran bodies ni payloads sensibles de forma automática.
"""

import logging
import sys

import structlog
from structlog.contextvars import merge_contextvars

from app.core.config import settings


def configure_logging() -> None:
    """
    Configura structlog y el logger raíz de Python.
    Debe llamarse una sola vez en el startup de la aplicación.
    """
    shared_processors: list[structlog.types.Processor] = [
        merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
    ]

    # Elegir renderer según entorno
    use_json = settings.APP_ENV in ("staging", "production")

    if use_json:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # Configurar logger raíz
    root_logger = logging.getLogger()
    root_logger.handlers = [handler]

    level = getattr(logging, settings.effective_log_level, logging.INFO)
    root_logger.setLevel(level)

    # Silenciar loggers externos verbosos
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.DEBUG else logging.WARNING
    )
    logging.getLogger("asyncpg").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Retorna un logger estructurado vinculado al nombre del módulo.

    Uso:
        logger = get_logger(__name__)
        logger.info("evento_registrado", user_id="...", amount=1000)

    Nota: trace_id se propaga automáticamente desde el contexto del request.
    No es necesario pasarlo manualmente si el middleware está activo.
    """
    return structlog.get_logger(name)
