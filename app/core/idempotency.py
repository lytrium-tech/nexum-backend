"""
app/core/idempotency.py
=======================
Contrato tipado del gestor de idempotencia del backend Nexum.

Propósito:
    Prevenir duplicación de operaciones financieras cuando un cliente reintenta
    una solicitud debido a timeout, red inestable o errores transitorios.

Implementación futura (Fase 5 — Ledger):
    La implementación real conectará con la tabla `public.idempotency_keys`
    en Supabase. Esta fase define únicamente el contrato de interfaz.

Diferencias conceptuales clave:
    - Idempotencia de request:  el mismo HTTP request no debe crear dos recursos.
    - Idempotencia de escritura: la misma operación financiera no debe escribirse
      dos veces en `public.financial_events`, aunque el RPC falle a mitad.

Flujo esperado:
    1. Cliente envía header `Idempotency-Key: <uuid>`.
    2. Backend llama a `check(key)`.
    3. Si existe y está `completed` → retorna respuesta cacheada (sin reejecutar).
    4. Si existe y está `processing` → retorna HTTP 409 (operación en curso).
    5. Si no existe → ejecuta la operación y llama a `save(...)` al completar.
    6. Si la operación falla → llama a `mark_failed(...)` para liberar el estado.

Tabla de Supabase (referencia):
    public.idempotency_keys
        idempotency_key  text       PK
        command_id       uuid       NOT NULL
        source           text       NOT NULL
        status           text       CHECK IN ('processing', 'completed', 'failed')
        response_payload jsonb
        error_metadata   jsonb
        created_at       timestamptz
        updated_at       timestamptz
"""

from typing import Literal
from uuid import UUID

# ── Tipos ─────────────────────────────────────────────────────────────────────

IdempotencyStatus = Literal["processing", "completed", "failed"]


# ── Contrato ──────────────────────────────────────────────────────────────────


class IdempotencyManager:
    """
    Gestor de idempotencia para operaciones financieras críticas.

    En la Fase 5, este manager se conectará a `public.idempotency_keys`
    a través del motor de base de datos SQLAlchemy.
    """

    async def check(self, key: str) -> dict | None:
        """
        Verifica si una clave de idempotencia ya fue procesada.

        Args:
            key: Clave de idempotencia enviada por el cliente
                 (típicamente un UUID v4 en el header `Idempotency-Key`).

        Returns:
            dict con `{"status": ..., "response_payload": ...}` si existe.
            None si la clave es nueva (primera solicitud).

        TODO (Fase 5): Implementar consulta real a public.idempotency_keys.
        """
        raise NotImplementedError(
            "IdempotencyManager.check — pendiente de implementación en Fase 5 (Ledger)."
        )

    async def begin(self, key: str, command_id: UUID, source: str) -> None:
        """
        Registra el inicio de procesamiento de una operación.
        Establece el estado como `processing`.

        Args:
            key:        Clave de idempotencia del cliente.
            command_id: UUID único de esta ejecución de comando.
            source:     Canal de origen (fastapi, whatsapp, web, pwa).

        TODO (Fase 5): Implementar inserción en public.idempotency_keys.
        """
        raise NotImplementedError(
            "IdempotencyManager.begin — pendiente de implementación en Fase 5 (Ledger)."
        )

    async def complete(
        self,
        key: str,
        response_payload: dict,
    ) -> None:
        """
        Marca una operación como completada y cachea su respuesta.

        Args:
            key:              Clave de idempotencia del cliente.
            response_payload: Respuesta a cachear para futuros reintentos.

        TODO (Fase 5): Implementar actualización en public.idempotency_keys.
        """
        raise NotImplementedError(
            "IdempotencyManager.complete — pendiente de implementación en Fase 5 (Ledger)."
        )

    async def mark_failed(
        self,
        key: str,
        error_metadata: dict | None = None,
    ) -> None:
        """
        Marca una operación como fallida para liberar el estado `processing`.

        Args:
            key:            Clave de idempotencia del cliente.
            error_metadata: Metadata del error para diagnóstico (no exponer al cliente).

        TODO (Fase 5): Implementar actualización en public.idempotency_keys.
        """
        raise NotImplementedError(
            "IdempotencyManager.mark_failed — pendiente de implementación en Fase 5 (Ledger)."
        )


# Instancia singleton — se reemplazará por inyección de dependencias en Fase 5
idempotency_manager = IdempotencyManager()
