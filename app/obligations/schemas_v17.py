from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.obligations.enums_v17 import (
    AmountType,
    Frequency,
    ObligationStatus,
    ObligationType,
    PeriodStatus,
)


class ObligationV17Response(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    description: Optional[str] = None
    amount: Decimal
    amount_type: Optional[AmountType] = None
    currency: str
    frequency: Frequency
    obligation_type: ObligationType
    status: ObligationStatus
    created_at: datetime
    updated_at: datetime


class ObligationPeriodV17Response(BaseModel):
    id: UUID
    obligation_id: UUID
    due_date: date
    status: PeriodStatus
    is_current: bool = False
    amount_due: Decimal
    amount_paid: Decimal
    created_at: datetime
    updated_at: datetime


class ObligationPaymentV17Response(BaseModel):
    id: UUID
    obligation_id: UUID
    obligation_period_id: UUID
    user_id: UUID
    amount: Decimal
    quote_id: Optional[UUID] = None
    idempotency_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class EmptyStateResponse(BaseModel):
    message: str
    items: List = Field(default_factory=list)


class ApiErrorResponse(BaseModel):
    detail: str
