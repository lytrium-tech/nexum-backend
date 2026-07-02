from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ObligationCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    category_id: UUID | None = None
    currency: str = Field(min_length=3, max_length=3)
    type: str  # 'indefinite', 'end_date', 'end_count', 'one_time'
    frequency: str  # 'monthly', 'weekly', 'biweekly', 'yearly', 'one_time'
    payment_mode: str  # 'fixed', 'variable'
    base_amount: Decimal | None = Field(None, gt=0)
    start_date: date
    first_due_date: date
    due_day: int | None = Field(None, ge=1, le=31)
    due_month: int | None = Field(None, ge=1, le=12)
    interval_count: int = Field(1, ge=1)
    end_date: date | None = None
    end_count: int | None = Field(None, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObligationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    description: str | None = None
    category_id: UUID | None = None
    base_amount: Decimal | None = Field(None, gt=0)
    status: str | None = None
    metadata: dict[str, Any] | None = None


class ObligationRead(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    description: str | None
    category_id: UUID | None
    currency: str
    type: str
    frequency: str
    payment_mode: str
    base_amount: Decimal | None
    start_date: date
    first_due_date: date
    due_day: int | None
    due_month: int | None
    interval_count: int
    end_date: date | None
    end_count: int | None
    status: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(validation_alias="metadata_")

    model_config = ConfigDict(from_attributes=True)


class ObligationPeriodRead(BaseModel):
    id: UUID
    obligation_id: UUID
    period_key: str
    sequence_number: int
    start_date: date
    end_date: date
    due_date: date
    amount: Decimal | None
    currency: str
    paid_amount: Decimal
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ObligationPaymentRead(BaseModel):
    id: UUID
    obligation_id: UUID
    obligation_period_id: UUID
    account_id: UUID | None
    financial_event_id: UUID | None
    amount: Decimal
    currency: str
    source_amount: Decimal
    source_currency: str
    fx_rate: Decimal | None
    rate_source: str | None
    rate_timestamp: datetime | None
    is_estimated: bool | None
    paid_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ObligationPaymentCreate(BaseModel):
    account_id: UUID
    amount: Decimal | None = Field(None, gt=0)
    currency: str | None = Field(None, min_length=3, max_length=3)
    source_message_id: str | None = None
    raw_message: str | None = None

class ObligationPeriodAmountUpdate(BaseModel):
    amount: Decimal = Field(..., ge=0)
