from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.accounts.enums import AccountType


class AccountCreate(BaseModel):
    name: str = Field(min_length=1)
    type: AccountType
    currency: str = Field(min_length=3, max_length=3)
    initial_balance: Decimal = Field(
        default=Decimal("0.00"), description="Saldo inicial para abrir la cuenta"
    )


class AccountUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    type: AccountType | None = None
    model_config = ConfigDict(extra="forbid")


class AccountRead(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    type: AccountType
    balance: Decimal
    currency: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AccountDetailRead(AccountRead):
    has_movements: bool = False
    movement_count: int = 0
    last_movement_at: datetime | None = None


class AccountSummary(BaseModel):
    # Summary global (legado o global)
    totals_by_currency: dict[str, Decimal]
    accounts_count: int
    active_accounts_count: int


class AccountPeriodSummary(BaseModel):
    # Summary de un periodo para una sola cuenta
    total_inflows: Decimal
    total_outflows: Decimal
    net_flow: Decimal
    transfer_inflows: Decimal
    transfer_outflows: Decimal
    movement_count: int
    period_start: datetime | None
    period_end: datetime | None
    currency: str


class BalanceAdjustmentCreate(BaseModel):
    target_balance: Decimal = Field(..., ge=0, description="Saldo real deseado (positivo o cero)")
    reason: str = Field(..., min_length=1, max_length=255)
    idempotency_key: UUID = Field(..., description="Clave de idempotencia única para el ajuste")


class AccountAvailabilityRead(BaseModel):
    account_id: UUID
    currency: str = Field(min_length=3, max_length=3)
    balance: Decimal
    goal_reserved_amount: Decimal
    available_balance: Decimal

    model_config = ConfigDict(extra="forbid")

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()
