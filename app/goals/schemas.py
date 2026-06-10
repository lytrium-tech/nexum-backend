from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field


class GoalCreate(BaseModel):
    name: str = Field(min_length=1)
    target_amount: Decimal = Field(gt=0)
    target_date: date | None = None


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

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    def remaining_amount(self) -> Decimal:
        return max(Decimal("0"), self.target_amount - self.current_amount)

    @computed_field
    def progress_percentage(self) -> Decimal:
        if self.target_amount > 0:
            return min(round((self.current_amount / self.target_amount) * Decimal("100"), 2), Decimal("100"))
        return Decimal("0")

    @computed_field
    def monthly_required(self) -> Decimal | None:
        if not self.target_date or self.target_amount <= 0:
            return None
        today = datetime.now(UTC).date()
        days_left = (self.target_date - today).days
        months_left = max(Decimal(days_left) / Decimal("30"), Decimal("1"))
        return round(self.remaining_amount / months_left, 2)

    @computed_field
    def daily_required(self) -> Decimal | None:
        if not self.target_date or self.target_amount <= 0:
            return None
        today = datetime.now(UTC).date()
        days_left = (self.target_date - today).days
        days_left_dec = max(Decimal(days_left), Decimal("1"))
        return round(self.remaining_amount / days_left_dec, 2)


class GoalContributionCreate(BaseModel):
    account_id: UUID
    amount: Decimal = Field(gt=0)


class GoalContributionResult(BaseModel):
    contribution_id: UUID | None
    event_id: UUID | None
    amount: Decimal
    balance_after: Decimal
    goal_current_amount: Decimal
    progress_percentage: Decimal | None
    status: str
