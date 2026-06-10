from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
    progress_percentage: Decimal | None
    target_date: date | None
    monthly_required: Decimal | None
    daily_required: Decimal | None
    status: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


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
