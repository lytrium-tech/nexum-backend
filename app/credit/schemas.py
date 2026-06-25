import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class CreditCardBase(BaseModel):
    name: str = Field(..., min_length=1)
    bank: str = Field(..., min_length=1)
    credit_limit: Decimal = Field(..., gt=0)
    cutoff_day: int = Field(..., ge=1, le=31)
    due_day: int = Field(..., ge=1, le=31)
    management_fee: Decimal = Field(default=Decimal("0.00"), ge=0)
    monthly_interest_rate: Decimal = Field(default=Decimal("0.00"), ge=0)
    annual_interest_rate: Decimal = Field(default=Decimal("0.00"), ge=0)
    network: str | None = None
    franchise: str | None = None
    currency: str = Field(min_length=3, max_length=3)


class CreditCardCreate(CreditCardBase):
    pass


class CreditCardUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    bank: str | None = Field(None, min_length=1)
    credit_limit: Decimal | None = Field(None, gt=0)
    cutoff_day: int | None = Field(None, ge=1, le=31)
    due_day: int | None = Field(None, ge=1, le=31)
    management_fee: Decimal | None = Field(None, ge=0)
    monthly_interest_rate: Decimal | None = Field(None, ge=0)
    annual_interest_rate: Decimal | None = Field(None, ge=0)
    network: str | None = None
    franchise: str | None = None
    is_active: bool | None = None


class CreditCardRead(CreditCardBase):
    id: uuid.UUID
    is_active: bool
    current_debt: Decimal = Decimal("0.00")
    total_debt: Decimal = Decimal("0.00")
    available_credit: Decimal = Decimal("0.00")
    billed_debt: Decimal = Decimal("0.00")
    unbilled_debt: Decimal = Decimal("0.00")
    payment_required: Decimal = Decimal("0.00")
    next_payment_estimate: Decimal = Decimal("0.00")
    statement_balance: Decimal | None = None
    data_quality: dict[str, str] = Field(default_factory=dict)
    # Deprecated aliases kept temporarily for frontend compatibility.
    estimated_current_debt: Decimal = Decimal("0.00")
    monthly_cc_payment: Decimal = Decimal("0.00")

    @computed_field
    @property
    def estimated_available_credit(self) -> Decimal:
        return self.available_credit

    model_config = ConfigDict(from_attributes=True)


class CreditCardPurchaseCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    category_id: uuid.UUID | None = None
    description: str | None = None
    installments_total: int = Field(default=1, ge=1)
    source_message_id: uuid.UUID | None = None
    raw_message: str | None = None


class CreditCardPurchaseResult(BaseModel):
    status: str
    event_id: uuid.UUID | None = None
    transaction_id: uuid.UUID | None = None
    amount: Decimal
    current_debt: Decimal
    available_credit: Decimal
    estimated_current_debt: Decimal
    estimated_available_credit: Decimal


class CreditCardPaymentCreate(BaseModel):
    account_id: uuid.UUID
    amount: Decimal = Field(..., gt=0)
    source_message_id: uuid.UUID | None = None
    raw_message: str | None = None


class CreditCardPaymentResult(BaseModel):
    status: str
    event_id: uuid.UUID | None = None
    transaction_id: uuid.UUID | None = None
    amount: Decimal
    current_debt: Decimal
    available_credit: Decimal
    estimated_current_debt: Decimal
    account_balance: Decimal


class CreditCardStatusRead(BaseModel):
    card_id: uuid.UUID
    name: str
    credit_limit: Decimal
    management_fee: Decimal = Decimal("0.00")
    monthly_interest_rate: Decimal = Decimal("0.00")
    annual_interest_rate: Decimal = Decimal("0.00")
    network: str | None = None
    franchise: str | None = None
    currency: str = "COP"
    current_debt: Decimal = Decimal("0.00")
    total_debt: Decimal
    billed_debt: Decimal
    unbilled_debt: Decimal
    available_credit: Decimal
    payment_required: Decimal = Decimal("0.00")
    next_payment_estimate: Decimal = Decimal("0.00")
    statement_balance: Decimal | None = None
    monthly_cc_payment: Decimal
    cutoff_day: int
    payment_due_day: int
    next_payment_due_date: str
    purchases_count: int
    payments_count: int
    data_quality: dict[str, str] = Field(default_factory=dict)


class CreditSummaryRead(BaseModel):
    total_credit_limit: Decimal
    total_debt: Decimal
    total_available_credit: Decimal
    total_monthly_cc_payment: Decimal = Decimal("0.00")
    total_payment_required: Decimal = Decimal("0.00")
    total_next_payment_estimate: Decimal = Decimal("0.00")
    cards: list[CreditCardStatusRead]


class CreditCardInstallmentRead(BaseModel):
    id: uuid.UUID
    credit_card_id: uuid.UUID
    purchase_transaction_id: uuid.UUID
    installment_number: int
    installments_total: int
    principal_amount: Decimal
    scheduled_period: str
    status: str
    paid_amount: Decimal
    interest_amount: Decimal = Decimal("0.00")
    total_amount: Decimal = Decimal("0.00")
    scheduled_due_date: date | None = None
    revision_id: int = 1

    model_config = ConfigDict(from_attributes=True)


class CreditCardStatementRead(BaseModel):
    id: uuid.UUID
    credit_card_id: uuid.UUID
    billing_period: str
    billing_period_start: date | None = None
    cutoff_date: date | None = None
    due_date: date | None = None
    previous_balance: Decimal
    new_purchases: Decimal
    billed_installments: Decimal
    fees_total: Decimal
    interest_total: Decimal
    payments_received: Decimal
    statement_balance: Decimal
    minimum_payment: Decimal
    status: str
    frozen_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
