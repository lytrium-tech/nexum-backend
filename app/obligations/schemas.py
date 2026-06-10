from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ObligationCreate(BaseModel):
    name: str = Field(min_length=1)
    amount: Decimal = Field(gt=0)
    due_day: int | None = Field(None, ge=1, le=31)
    frequency: str | None = None
    category_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObligationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    amount: Decimal | None = Field(None, gt=0)
    due_day: int | None = Field(None, ge=1, le=31)
    frequency: str | None = None
    category_id: UUID | None = None
    metadata: dict[str, Any] | None = None


class ObligationRead(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    amount: Decimal
    due_day: int | None
    frequency: str | None
    is_active: bool
    category_id: UUID | None
    currency: str
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ObligationPaymentCreate(BaseModel):
    account_id: UUID
    amount: Decimal = Field(gt=0)


class ObligationPaymentResult(BaseModel):
    payment_id: UUID | None
    event_id: UUID | None
    amount: Decimal
    balance_after: Decimal
    status: str
