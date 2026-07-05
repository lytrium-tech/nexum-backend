from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator, ConfigDict

from app.obligations.enums_v17 import (
    AmountType,
    Frequency,
    ObligationStatus,
    ObligationType,
    PeriodStatus,
)


class ObligationV17Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    model_config = ConfigDict(from_attributes=True)

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
    model_config = ConfigDict(from_attributes=True)

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


class ObligationV17CreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    obligation_type: ObligationType
    frequency: Frequency
    amount_type: AmountType
    base_amount: Optional[Decimal] = None
    currency: str = Field(default="COP", min_length=3, max_length=3)
    start_date: date
    first_due_date: date
    end_date: Optional[date] = None
    end_count: Optional[int] = Field(None, gt=0)
    metadata_: Optional[dict] = Field(default_factory=dict, alias="metadata")

    @model_validator(mode="after")
    def validate_amounts_and_dates(self) -> "ObligationV17CreateRequest":
        # currency uppercase
        if self.currency:
            self.currency = self.currency.upper()

        # base_amount rules
        if self.amount_type == AmountType.fixed:
            if self.base_amount is None or self.base_amount <= 0:
                raise ValueError("base_amount must be > 0 when amount_type is fixed")
        elif self.amount_type == AmountType.variable:
            # base_amount can be None or >= 0 if variable
            if self.base_amount is not None and self.base_amount < 0:
                raise ValueError("base_amount must be >= 0 when provided for variable amount_type")

        # date rules
        if self.first_due_date < self.start_date:
            raise ValueError("first_due_date cannot be before start_date")

        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")

        return self

class ObligationPeriodAmountDefineRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    metadata_: Optional[dict] = Field(default_factory=dict, alias="metadata")

    @model_validator(mode="after")
    def validate_currency(self) -> "ObligationPeriodAmountDefineRequest":
        if self.currency:
            self.currency = self.currency.upper()
        return self
