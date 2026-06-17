from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field


class GoalCreate(BaseModel):
    name: str = Field(min_length=1)
    target_amount: Decimal = Field(gt=0)
    target_date: date | None = None
    source_message_id: UUID | None = None
    raw_message: str | None = None


class GoalUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    target_amount: Decimal | None = Field(None, gt=0)
    target_date: date | None = None


class GoalRead(BaseModel):
    id: UUID
    name: str
    target_amount: Decimal
    current_amount: Decimal
    target_date: date | None
    status: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    contributed_this_period: Decimal = Decimal("0.00")

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    def remaining_amount(self) -> Decimal:
        return max(Decimal("0.00"), self.target_amount - self.current_amount)

    @computed_field
    def progress_percentage(self) -> Decimal:
        if self.target_amount > 0:
            return min(
                round((self.current_amount / self.target_amount) * Decimal("100"), 2),
                Decimal("100"),
            )
        return Decimal("0.00")

    @computed_field
    def is_flexible(self) -> bool:
        return self.target_date is None

    @computed_field
    def monthly_required(self) -> Decimal | None:
        if self.is_flexible or self.target_amount <= 0:
            return None
        today = datetime.now(UTC).date()
        if today >= self.target_date:
            return self.remaining_amount

        days_left = (self.target_date - today).days
        months_left = max(Decimal(days_left) / Decimal("30"), Decimal("1"))
        start_of_period_remaining = self.remaining_amount + self.contributed_this_period
        return round(start_of_period_remaining / months_left, 2)

    @computed_field
    def required_this_period(self) -> Decimal:
        if self.is_flexible:
            return Decimal("0.00")
        if self.status == "completed" or self.remaining_amount == 0:
            return Decimal("0.00")
        return self.monthly_required or Decimal("0.00")

    @computed_field
    def remaining_required_this_period(self) -> Decimal:
        if self.is_flexible:
            return Decimal("0.00")

        remaining = self.required_this_period - self.contributed_this_period
        return max(Decimal("0.00"), remaining)

    @computed_field
    def period_status(self) -> str:
        if self.remaining_amount == 0 or self.status == "completed":
            return "completed"
        if self.is_flexible:
            return "flexible"
        if self.contributed_this_period == 0:
            return "pending"
        if self.remaining_required_this_period > 0:
            return "partial"
        return "covered"


class GoalContributionCreate(BaseModel):
    account_id: UUID
    amount: Decimal = Field(gt=0)
    source_message_id: UUID | None = None
    raw_message: str | None = None


class GoalContributionResult(BaseModel):
    contribution_id: UUID | None
    event_id: UUID | None
    amount: Decimal
    balance_after: Decimal
    goal_current_amount: Decimal
    progress_percentage: Decimal | None
    status: str
