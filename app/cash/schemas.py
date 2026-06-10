from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EventSource(StrEnum):
    API = "api"
    PWA = "pwa"
    MANUAL = "manual"
    SYSTEM = "system"


class CashOperationBase(BaseModel):
    account_id: UUID
    amount: Decimal = Field(..., gt=0, description="Debe ser mayor estricto que 0")
    category_id: UUID | None = None
    description: str | None = Field(None, max_length=255)
    source: EventSource = EventSource.API


class CashIncomeCreate(CashOperationBase):
    pass


class CashExpenseCreate(CashOperationBase):
    pass


class CashOperationResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    account_id: UUID
    balance_after: Decimal
    occurred_at: datetime
    status: str = Field(description="'created' o 'idempotent_retry'")
