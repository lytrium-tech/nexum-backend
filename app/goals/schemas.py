import calendar
import zoneinfo
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.core.currency import get_minimum_unit, round_up_to_minimum_unit
from app.goals.enums import GoalTransactionType


class GoalCreate(BaseModel):
    name: str = Field(min_length=1)
    target_amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    target_date: date | None = None
    source_message_id: UUID | None = None
    raw_message: str | None = None


class GoalUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    target_amount: Decimal | None = Field(None, gt=0)
    target_date: date | None = None


class GoalAccountReservationRead(BaseModel):
    account_id: UUID
    account_name: str
    account_currency: str = Field(
        min_length=3, max_length=3, description="La moneda fuente (account_currency)."
    )
    contributed_amount: Decimal = Field(
        ge=0, max_digits=14, decimal_places=2, description="Expresado en la moneda fuente."
    )
    released_amount: Decimal = Field(
        ge=0, max_digits=14, decimal_places=2, description="Expresado en la moneda fuente."
    )
    reserved_amount: Decimal = Field(
        ge=0, max_digits=14, decimal_places=2, description="Expresado en la moneda fuente."
    )
    goal_currency: str = Field(min_length=3, max_length=3)
    applied_contributed_amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    applied_released_amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    applied_reserved_amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    account_is_active: bool
    is_releasable: bool
    release_block_reason: Literal["currency_mismatch_legacy", "account_inactive"] | None = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    @field_validator("account_currency", "goal_currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()


class GoalRead(BaseModel):
    id: UUID
    name: str
    target_amount: Decimal
    current_amount: Decimal
    target_date: date | None
    currency: str = Field(min_length=3, max_length=3)
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
    def currency_minimum_unit(self) -> Decimal:
        return get_minimum_unit(self.currency)

    @computed_field
    def is_flexible(self) -> bool:
        return self.target_date is None

    @computed_field
    def monthly_required(self) -> Decimal:
        if self.is_flexible or self.target_amount <= 0:
            return Decimal("0.00")
        today = datetime.now(UTC).date()
        if today >= self.target_date:
            # required_this_period is measured from the start-of-period state;
            # contributed_this_period is subtracted later exactly once.
            return self.remaining_amount + self.contributed_this_period

        days_left = (self.target_date - today).days
        months_left = max(Decimal(days_left) / Decimal("30"), Decimal("1"))
        start_of_period_remaining = self.remaining_amount + self.contributed_this_period
        raw_required = start_of_period_remaining / months_left
        return round_up_to_minimum_unit(raw_required, self.currency)

    @computed_field
    def required_this_period(self) -> Decimal:
        if self.is_flexible:
            return Decimal("0.00")
        if self.status == "completed" or self.remaining_amount == 0:
            return Decimal("0.00")
        return self.monthly_required

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

        today = datetime.now(UTC).date()
        if self.target_date and today > self.target_date:
            return "overdue"

        if self.remaining_required_this_period > 0:
            if self.contributed_this_period == 0:
                return "pending"
            return "partial"
        if self.contributed_this_period > self.required_this_period:
            return "overfunded"
        return "covered"

    @computed_field
    def days_remaining_in_period(self) -> int:
        today = datetime.now(UTC).date()
        last_day = calendar.monthrange(today.year, today.month)[1]
        return last_day - today.day

    @computed_field
    def daily_required_this_period(self) -> Decimal:
        if self.remaining_required_this_period <= 0:
            return Decimal("0.00")
        days = self.days_remaining_in_period
        if days <= 0:
            return self.remaining_required_this_period
        raw_daily = self.remaining_required_this_period / Decimal(str(days))
        return round_up_to_minimum_unit(raw_daily, self.currency)


class GoalDetailRead(GoalRead):
    reservations_by_account: list[GoalAccountReservationRead] = Field(default_factory=list)


class GoalContributionCreate(BaseModel):
    account_id: UUID
    amount: Decimal = Field(
        gt=0,
        max_digits=14,
        decimal_places=2,
        allow_inf_nan=False,
    )
    currency: str | None = Field(
        None,
        min_length=3,
        max_length=3,
        description="Legacy compatibility only. The account currency is authoritative.",
    )
    command_id: UUID | None = None
    description: str | None = Field(None, max_length=255)
    source_message_id: UUID | None = None
    raw_message: str | None = None

    model_config = ConfigDict(extra="forbid")


class GoalContributionResult(BaseModel):
    transaction_id: UUID
    event_id: UUID
    goal_id: UUID
    account_id: UUID
    source_amount: Decimal = Field(max_digits=14, decimal_places=2)
    source_currency: str = Field(min_length=3, max_length=3)
    applied_amount: Decimal = Field(max_digits=14, decimal_places=2)
    goal_currency: str = Field(min_length=3, max_length=3)
    goal_current_amount: Decimal
    goal_remaining_amount: Decimal
    goal_status: str
    account_balance: Decimal
    goal_reserved_amount: Decimal
    available_balance: Decimal
    idempotent: bool
    created_at: AwareDatetime

    # Legacy fields to maintain backwards compatibility if needed by frontend
    contribution_id: UUID | None = None
    amount: Decimal | None = None
    currency: str | None = None
    fx_rate: Decimal | None = None
    rate_source: str | None = None
    rate_timestamp: datetime | None = None
    is_estimated: bool = False
    balance_after: Decimal | None = None
    progress_percentage: Decimal | None = None
    status: str | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("source_currency", "goal_currency", "currency")
    @classmethod
    def uppercase_currency(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None


class GoalTransactionRead(BaseModel):
    id: UUID
    transaction_type: GoalTransactionType
    account_id: UUID
    source_amount: Decimal = Field(max_digits=14, decimal_places=2)
    source_currency: str = Field(min_length=3, max_length=3)
    applied_amount: Decimal = Field(max_digits=14, decimal_places=2)
    goal_currency: str = Field(min_length=3, max_length=3)
    event_id: UUID | None
    description: str | None = Field(None, max_length=255)
    created_at: AwareDatetime
    origin: Literal["legacy", "native"]
    channel: Literal["manual", "automatic", "legacy"]

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    @field_validator("source_currency", "goal_currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()


class GoalTransactionsResponse(BaseModel):
    items: list[GoalTransactionRead]
    total: int
    limit: int
    offset: int

    model_config = ConfigDict(extra="forbid")


class GoalReleaseCreate(BaseModel):
    account_id: UUID
    amount: Decimal = Field(
        gt=0,
        max_digits=14,
        decimal_places=2,
        allow_inf_nan=False,
    )
    command_id: UUID | None = None
    description: str | None = Field(None, max_length=255)

    model_config = ConfigDict(extra="forbid")


class GoalReleaseResult(BaseModel):
    transaction_id: UUID
    event_id: UUID
    goal_id: UUID
    account_id: UUID
    released_amount: Decimal = Field(max_digits=14, decimal_places=2)
    source_currency: str = Field(min_length=3, max_length=3)
    applied_amount: Decimal = Field(max_digits=14, decimal_places=2)
    goal_currency: str = Field(min_length=3, max_length=3)
    goal_current_amount: Decimal
    goal_remaining_amount: Decimal
    goal_status: str
    account_balance: Decimal
    goal_account_reserved_amount: Decimal
    goal_total_reserved_amount: Decimal
    account_total_reserved_amount: Decimal
    available_balance: Decimal
    idempotent: bool
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")

    @field_validator("source_currency", "goal_currency")
    @classmethod
    def uppercase_currency(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None


class GoalAutoContributionScheduleRead(BaseModel):
    id: UUID
    goal_id: UUID
    account_id: UUID
    amount: Decimal = Field(max_digits=14, decimal_places=2)
    frequency: Literal["weekly", "monthly"]
    execution_day: str
    timezone: str
    start_date: date | None
    status: Literal["active", "paused", "cancelled"]
    pause_reason: (
        Literal["user_paused", "goal_completed", "account_inactive", "goal_archived"] | None
    )
    next_run_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class GoalAutoContributionScheduleCreate(BaseModel):
    account_id: UUID
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    frequency: Literal["weekly", "monthly"]
    execution_day: str
    timezone: str
    start_date: date | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            zoneinfo.ZoneInfo(value)
        except Exception as e:
            raise ValueError("Timezone inválida") from e
        return value

    @model_validator(mode="after")
    def validate_frequency_day(self) -> "GoalAutoContributionScheduleCreate":
        day = self.execution_day.lower()
        self.execution_day = day
        if self.frequency == "weekly":
            if day not in {
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            }:
                raise ValueError("execution_day inválido para frecuencia weekly")
        elif self.frequency == "monthly":
            if day not in {str(value) for value in range(1, 32)}:
                raise ValueError("execution_day inválido para frecuencia monthly")
        return self


class GoalAutoContributionScheduleUpdate(BaseModel):
    account_id: UUID | None = None
    amount: Decimal | None = Field(None, gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    frequency: Literal["weekly", "monthly"] | None = None
    execution_day: str | None = None
    timezone: str | None = None
    start_date: date | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_null_non_nullable_updates(cls, value):
        if isinstance(value, dict):
            for field_name in (
                "account_id",
                "amount",
                "frequency",
                "execution_day",
                "timezone",
            ):
                if field_name in value and value[field_name] is None:
                    raise ValueError(f"{field_name} no puede ser null")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            zoneinfo.ZoneInfo(value)
        except Exception as e:
            raise ValueError("Timezone inválida") from e
        return value

    @field_validator("execution_day")
    @classmethod
    def normalize_execution_day(cls, value: str | None) -> str | None:
        return value.lower() if value is not None else None
