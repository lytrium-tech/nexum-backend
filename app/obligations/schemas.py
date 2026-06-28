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
    currency: str = Field(min_length=3, max_length=3)
    category_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    already_paid_this_period: bool = False
    start_next_period: bool = False
    pending_this_period: bool = False


class ObligationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    amount: Decimal | None = Field(None, gt=0)
    payment_mode: str | None = None
    due_day: int | None = Field(None, ge=1, le=31)
    frequency: str | None = None
    category_id: UUID | None = None
    is_active: bool | None = None
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
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Bogota")
        current_period = f"{datetime.now(tz).year}-{datetime.now(tz).month:02d}"
        if current_period in self.metadata.get("skip_periods", []):
            return Decimal("0.00")

        if self.payment_mode == "variable_amount" and self.amount is None:
            return None

        amt = self.amount or Decimal("0.00")
        if self.payment_mode == "fixed_full_payment":
            return Decimal("0.00") if self.paid_this_period > 0 else amt

        return max(Decimal("0.00"), amt - self.paid_this_period)

    @computed_field
    def is_pending(self) -> bool:
        return self.period_status in ("pending", "partial", "overdue")

    @computed_field
    def period_status(self) -> str:
        if not self.is_active:
            return "inactive"

        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Bogota")
        now = datetime.now(tz)
        current_period = f"{now.year}-{now.month:02d}"

        if current_period in self.metadata.get("skip_periods", []):
            return "covered"

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
            try:
                import calendar

                last_day = calendar.monthrange(now.year, now.month)[1]
                actual_due_day = min(self.due_day, last_day)
                due_date = datetime(now.year, now.month, actual_due_day).date()
                if now.date() > due_date:
                    return "overdue"
            except ValueError:
                pass

        return status

    @computed_field
    def next_due_date(self) -> str | None:
        if not self.due_day:
            return None
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Bogota")
        now = datetime.now(tz)
        try:
            # Handle edge case where due_day is 31 and current month has 30 days
            # For simplicity in this logic, we clip to the last day of the month
            import calendar

            last_day = calendar.monthrange(now.year, now.month)[1]
            actual_due_day = min(self.due_day, last_day)
            due_date = datetime(now.year, now.month, actual_due_day).date()
            return due_date.isoformat()
        except ValueError:
            return None

    @computed_field
    def days_until_due(self) -> int | None:
        if not self.next_due_date:
            return None
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Bogota")
        now = datetime.now(tz).date()
        due_date = datetime.fromisoformat(self.next_due_date).date()
        return (due_date - now).days


class ObligationPaymentCreate(BaseModel):
    account_id: UUID
    amount: Decimal | None = Field(None, gt=0)
    source_message_id: UUID | None = None
    raw_message: str | None = None


class ObligationPaymentResult(BaseModel):
    payment_id: UUID | None
    event_id: UUID | None
    amount: Decimal
    balance_after: Decimal
    status: str
