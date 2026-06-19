from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ObligationCreate(BaseModel):
    name: str = Field(min_length=1)
    amount: Decimal | None = Field(None, gt=0)
    payment_mode: str = "fixed_full_payment"
    due_day: int | None = Field(None, ge=1, le=31)
    frequency: str | None = None
    category_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObligationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    amount: Decimal | None = Field(None, gt=0)
    payment_mode: str | None = None
    due_day: int | None = Field(None, ge=1, le=31)
    frequency: str | None = None
    category_id: UUID | None = None
    metadata: dict[str, Any] | None = None


class ObligationRead(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    amount: Decimal | None
    payment_mode: str
    due_day: int | None
    frequency: str | None
    is_active: bool
    category_id: UUID | None
    currency: str
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    created_at: datetime
    updated_at: datetime

    paid_this_period: Decimal = Decimal("0.00")
    last_payment_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    def remaining_amount(self) -> Decimal | None:
        if self.payment_mode == "variable_amount" and self.amount is None:
            return None
        
        amt = self.amount or Decimal("0.00")
        if self.payment_mode == "fixed_full_payment":
            return Decimal("0.00") if self.paid_this_period > 0 else amt
            
        return max(Decimal("0.00"), amt - self.paid_this_period)

    @computed_field
    def is_pending(self) -> bool:
        return self.period_status == "pending"

    @computed_field
    def period_status(self) -> str:
        if not self.is_active:
            return "inactive"
            
        status = "pending"
        if self.payment_mode == "fixed_full_payment":
            status = "paid" if self.paid_this_period > 0 else "pending"
        elif self.payment_mode == "variable_amount":
            if self.paid_this_period > 0:
                if self.amount is not None:
                    status = "paid" if self.paid_this_period >= self.amount else "partial"
                else:
                    status = "paid"
            else:
                status = "pending"
        elif self.payment_mode == "partial_allowed":
            if self.paid_this_period == 0:
                status = "pending"
            elif self.amount and self.paid_this_period >= self.amount:
                status = "paid"
            else:
                status = "partial"

        if status != "paid" and self.due_day:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("America/Bogota")
            now = datetime.now(tz)
            try:
                due_date = datetime(now.year, now.month, self.due_day).date()
                if now.date() > due_date:
                    return "overdue"
            except ValueError:
                pass
                
        return status

    @computed_field
    def next_due_date(self) -> str | None:
        # Implement a naive due date based on due_day for current month
        if not self.due_day:
            return None
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Bogota")
        now = datetime.now(tz)
        try:
            due_date = datetime(now.year, now.month, self.due_day).date()
            if due_date < now.date() and self.period_status != "paid":
                # Actually, overdue is if today > due_date and not paid.
                pass
            return due_date.isoformat()
        except ValueError:
            return None


class ObligationPaymentCreate(BaseModel):
    account_id: UUID
    amount: Decimal = Field(gt=0)
    source_message_id: UUID | None = None
    raw_message: str | None = None


class ObligationPaymentResult(BaseModel):
    payment_id: UUID | None
    event_id: UUID | None
    amount: Decimal
    balance_after: Decimal
    status: str
