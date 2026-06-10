import uuid
from decimal import Decimal

from pydantic import BaseModel, Field, computed_field


class CreditCardBase(BaseModel):
    name: str = Field(..., min_length=1)
    bank: str = Field(..., min_length=1)
    credit_limit: Decimal = Field(..., gt=0)
    cutoff_day: int = Field(..., ge=1, le=31)
    due_day: int = Field(..., ge=1, le=31)
    currency: str = "COP"


class CreditCardCreate(CreditCardBase):
    pass


class CreditCardUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    bank: str | None = Field(None, min_length=1)
    credit_limit: Decimal | None = Field(None, gt=0)
    cutoff_day: int | None = Field(None, ge=1, le=31)
    due_day: int | None = Field(None, ge=1, le=31)
    is_active: bool | None = None


class CreditCardRead(CreditCardBase):
    id: uuid.UUID
    is_active: bool
    # Campos que vienen de la BD/Vista
    estimated_current_debt: Decimal = Decimal("0.00")
    monthly_cc_payment: Decimal = Decimal("0.00")

    @computed_field
    @property
    def estimated_available_credit(self) -> Decimal:
        return max(Decimal("0.00"), self.credit_limit - self.estimated_current_debt)

    class Config:
        from_attributes = True


class CreditCardPurchaseCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    category_id: uuid.UUID | None = None
    description: str | None = None
    installments_total: int = Field(default=1, ge=1)


class CreditCardPurchaseResult(BaseModel):
    status: str
    event_id: uuid.UUID | None = None
    transaction_id: uuid.UUID | None = None
    amount: Decimal
    estimated_current_debt: Decimal
    estimated_available_credit: Decimal


class CreditCardPaymentCreate(BaseModel):
    account_id: uuid.UUID
    amount: Decimal = Field(..., gt=0)


class CreditCardPaymentResult(BaseModel):
    status: str
    event_id: uuid.UUID | None = None
    transaction_id: uuid.UUID | None = None
    amount: Decimal
    estimated_current_debt: Decimal
    account_balance: Decimal
