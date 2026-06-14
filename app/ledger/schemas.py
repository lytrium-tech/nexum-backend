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
    transfer_id: UUID | None = None


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


# ────────────────────────────────────────────────────────────────────────────
# Esquemas para Queries (Frontend Readiness)
# ────────────────────────────────────────────────────────────────────────────

class AccountRef(BaseModel):
    id: UUID
    name: str
    model_config = ConfigDict(from_attributes=True)

class CategoryRef(BaseModel):
    id: UUID
    name: str
    model_config = ConfigDict(from_attributes=True)

class LedgerEventDetail(BaseModel):
    id: UUID
    user_id: UUID
    occurred_at: datetime
    event_type: str
    direction: str
    amount: Decimal
    currency: str
    description: str | None
    account: AccountRef | None = None
    category: CategoryRef | None = None
    source: str
    raw_message: str | None = None
    
    # Detalle adicional
    source_message_id: UUID | None = None
    command_id: UUID | None = None
    # No exponemos trace_id aquí aún porque está en messages, no en financial_events
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class LedgerPaginationInfo(BaseModel):
    limit: int
    offset: int
    total: int

class LedgerEventsResponse(BaseModel):
    items: list[LedgerEventDetail]
    pagination: LedgerPaginationInfo

class LedgerSummaryResponse(BaseModel):
    total_income: Decimal
    total_expense: Decimal
    net_cashflow: Decimal
    total_goal_contributions: Decimal
    total_obligation_payments: Decimal
    total_credit_card_payments: Decimal
    total_credit_card_purchases: Decimal
    events_count: int

class LedgerTimelineGroup(BaseModel):
    date: str
    income: Decimal
    expense: Decimal
    net: Decimal
    items: list[LedgerEventDetail]

class LedgerTimelineResponse(BaseModel):
    groups: list[LedgerTimelineGroup]
