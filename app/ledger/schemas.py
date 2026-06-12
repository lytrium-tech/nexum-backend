"""
app/ledger/schemas.py
=====================
Esquemas Pydantic para el dominio Ledger.
Definen los contratos de entrada y salida, asegurando validación de tipos
y cumplimiento de reglas básicas antes de tocar la capa de persistencia.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.ledger.enums import Direction, EventType


class LedgerEventCreate(BaseModel):
    """
    Payload interno para crear un nuevo evento en el Ledger.
    Diseñado para ser construido por los dominios originadores (Cash, Goals, etc.).
    """

    user_id: UUID
    account_id: UUID | None = None
    category_id: UUID | None = None

    event_type: EventType
    direction: Direction

    amount: Decimal = Field(
        gt=Decimal("0"),
        description="Monto del evento. Debe ser estrictamente mayor a 0.",
    )
    currency: str = Field(default="COP")

    description: str | None = None

    # raw_message permitido solo en entrada. Se recomienda omitir o redactar.
    raw_message: str | None = None
    source: str = Field(default="backend")
    
    source_message_id: UUID | None = None

    occurred_at: datetime | None = None
    # NOTA: `period` fue removido intencionalmente de la creación.
    # PostgreSQL es la ÚNICA fuente autoritativa mediante el trigger `trg_financial_events_period`.

    # default_factory dict para asegurar que siempre sea un dict manejable
    metadata: dict = Field(default_factory=dict)

    command_id: UUID | None = None


class LedgerEventRead(BaseModel):
    """
    Representación de salida de un evento histórico.
    Excluye raw_message por seguridad (no exponer payloads, secretos o PII).
    """

    id: UUID
    user_id: UUID
    account_id: UUID | None
    category_id: UUID | None

    event_type: EventType
    direction: Direction
    amount: Decimal
    currency: str
    description: str | None

    # raw_message no se expone al cliente

    source: str
    occurred_at: datetime
    period: str
    metadata: dict = Field(validation_alias="metadata_")
    created_at: datetime
    command_id: UUID | None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class LedgerEventResult(BaseModel):
    """
    Resultado de la operación de escritura en el Ledger.
    Incluye flag de idempotencia para notificar a capas superiores.
    """

    event: LedgerEventRead
    idempotent: bool = False
