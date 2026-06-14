"""
app/transfers/schemas.py
========================
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.accounts.schemas import AccountRead

class TransferCreate(BaseModel):
    """Payload para crear transferencia."""
    source_account_id: UUID
    destination_account_id: UUID
    amount: Decimal = Field(..., gt=0, decimal_places=2)
    currency: str = Field(default="COP", min_length=3, max_length=3)
    description: str | None = Field(None, max_length=255)
    occurred_at: datetime | None = None
    command_id: UUID | None = None
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
    description: str | None
    status: str
    ledger_events: LedgerEventsRef | None = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
