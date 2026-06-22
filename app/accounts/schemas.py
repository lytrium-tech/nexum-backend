from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.accounts.enums import AccountType
from app.ledger.enums import EventType


class AccountCreate(BaseModel):
    name: str = Field(min_length=1)
    type: AccountType
    currency: str = Field(min_length=3, max_length=3)
    initial_balance: Decimal = Field(
        default=Decimal("0.00"), description="Saldo inicial para abrir la cuenta"
    )


class AccountUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    is_active: bool | None = None
    # balance explícitamente no incluido


class AccountRead(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    type: AccountType
    balance: Decimal
    currency: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class AccountSummary(BaseModel):
    totals_by_currency: dict[str, Decimal]
    accounts_count: int
    active_accounts_count: int


class BalanceAdjustmentCreate(BaseModel):
    amount: Decimal = Field(..., gt=0, description="Cantidad a ajustar (siempre positiva)")
    direction: Literal["increase", "decrease"] = Field(
        description="Si aumenta o disminuye el balance"
    )
    type: EventType = Field(description="opening_balance o balance_adjustment")
    description: str | None = Field(None, max_length=255)
