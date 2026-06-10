"""
app/core/config.py
==================
Configuración central de la aplicación mediante pydantic-settings.
Los valores se cargan desde variables de entorno o archivo .env.

Reglas de seguridad:
- Los secretos se declaran como SecretStr para que nunca aparezcan en logs ni repr.
- En producción, el startup falla si falta cualquier variable crítica.
- AUTH_BYPASS_ENABLED solo puede estar activo en development y test.
"""

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de la aplicación Nexum Backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Aplicación ────────────────────────────────────────────────────────
    APP_NAME: str = "nexum-backend"
    APP_ENV: str = "development"  # development | staging | production
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True

    # ── Logging ───────────────────────────────────────────────────────────
    # Si no se define, se deriva del flag DEBUG.
    LOG_LEVEL: str = ""

    # ── Base de datos ─────────────────────────────────────────────────────
    # Formato: postgresql+asyncpg://user:password@host:port/database
    # Vacío permitido en development/test; obligatorio en production.
    DATABASE_URL: str = ""

    # ── Supabase ──────────────────────────────────────────────────────────
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: SecretStr = SecretStr("")

    # ── Gemini ────────────────────────────────────────────────────────────
    GEMINI_API_KEY: SecretStr = SecretStr("")
    GEMINI_MODEL: str = "gemini-2.0-flash"

    # ── Auth bypass (solo development y test) ─────────────────────────────
    AUTH_BYPASS_ENABLED: bool = True
    DEV_USER_ID: str = "00000000-0000-0000-0000-000000000001"
    DEV_USER_EMAIL: str = "dev@nexum.local"

    # ── Propiedades derivadas ─────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        """Retorna True si el entorno es producción."""
        return self.APP_ENV == "production"

    @property
    def is_development(self) -> bool:
        """Retorna True si el entorno es desarrollo."""
        return self.APP_ENV == "development"

    @property
    def effective_log_level(self) -> str:
        """
        Nivel de log efectivo.
        Si LOG_LEVEL está definido explícitamente, lo usa.
        Si no, deriva del flag DEBUG.
        """
        if self.LOG_LEVEL:
            return self.LOG_LEVEL.upper()
        return "DEBUG" if self.DEBUG else "INFO"

    # ── Validaciones de producción ────────────────────────────────────────

    @model_validator(mode="after")
    def validate_production_requirements(self) -> "Settings":
        """
        Verifica que las variables críticas estén presentes en producción.
        Falla en startup con un mensaje claro si falta alguna.
        """
        if self.APP_ENV == "production":
            missing: list[str] = []

            if not self.DATABASE_URL:
                missing.append("DATABASE_URL")
            if not self.SUPABASE_URL:
                missing.append("SUPABASE_URL")
            if not self.SUPABASE_SERVICE_ROLE_KEY.get_secret_value():
                missing.append("SUPABASE_SERVICE_ROLE_KEY")
            if not self.GEMINI_API_KEY.get_secret_value():
                missing.append("GEMINI_API_KEY")

            if missing:
                raise ValueError(
                    f"Variables obligatorias faltantes en producción: {missing}. "
                    "El backend no puede iniciar sin estas variables configuradas."
                )

            if self.AUTH_BYPASS_ENABLED:
                raise ValueError(
                    "AUTH_BYPASS_ENABLED=true está prohibido en producción. "
                    "Configura AUTH_BYPASS_ENABLED=false en tu entorno de producción."
                )

        return self


# Instancia singleton utilizada en toda la aplicación.
settings = Settings()
