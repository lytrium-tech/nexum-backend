from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.accounts.enums import AccountType


class AccountCreate(BaseModel):
    name: str = Field(min_length=1)
    type: AccountType
    currency: str = Field(default="COP")


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
    total_balance: Decimal
    accounts_count: int
    active_accounts_count: int
    currency: str
