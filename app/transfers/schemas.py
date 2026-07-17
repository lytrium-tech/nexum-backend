"""
app/transfers/schemas.py
========================
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.accounts.schemas import AccountRead


class TransferRequest(BaseModel):
    """Contrato HTTP público para crear una transferencia V1 de la misma moneda."""

    source_account_id: UUID
    destination_account_id: UUID
    amount: Decimal = Field(
        ...,
        gt=Decimal("0"),
        max_digits=14,
        decimal_places=2,
        allow_inf_nan=False,
    )
    description: str | None = Field(None, max_length=255)
    command_id: UUID = Field(..., description="Clave de idempotencia obligatoria")

    model_config = ConfigDict(extra="forbid")

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class TransferCreate(TransferRequest):
    """Comando interno; conserva trazabilidad de orígenes backend no expuestos por HTTP."""

    occurred_at: datetime | None = None
    source_message_id: UUID | None = None
    raw_message: str | None = None


class LedgerEventsRef(BaseModel):
    out_event_id: UUID
    in_event_id: UUID


class TransferResult(BaseModel):
    id: UUID
    source_account: AccountRead
    destination_account: AccountRead
    amount: Decimal
    currency: str
    target_amount: Decimal | None = None
    target_currency: str | None = None
    fx_rate: Decimal | None = None
    rate_source: str | None = None
    rate_timestamp: datetime | None = None
    is_estimated: bool = False
    description: str | None
    status: str
    is_idempotent: bool = False
    ledger_events: LedgerEventsRef | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
