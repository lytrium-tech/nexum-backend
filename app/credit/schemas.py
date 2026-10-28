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
    minimum_payment: Decimal = Decimal("0.00")
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


class CreditCardEarlyPaymentCreate(BaseModel):
    account_id: uuid.UUID
    amount: Decimal | None = Field(None, gt=0)
    allocation_mode: str = "reduce_installment_amount"
    source_message_id: uuid.UUID | None = None
    raw_message: str | None = None


class CreditCardEarlyPaymentResult(BaseModel):
    status: str
    event_id: uuid.UUID | None = None
    early_payment_id: uuid.UUID | None = None
    amount: Decimal
    current_debt: Decimal
    available_credit: Decimal
    account_balance: Decimal

class CreditCardEarlyPaymentPreviewCreate(BaseModel):
    account_id: uuid.UUID
    amount: Decimal | None = Field(None, gt=0)

class PaymentPreviewResult(BaseModel):
    source_amount: Decimal
    source_currency: str
    target_amount: Decimal
    target_currency: str
    fx_rate: Decimal
    rate_source: str
    is_estimated: bool


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
    minimum_payment: Decimal = Decimal("0.00")
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

    @computed_field
    @property
    def remaining_principal(self) -> Decimal:
        return max(Decimal("0.00"), self.principal_amount - self.paid_amount)

    @computed_field
    @property
    def is_pay_early_eligible(self) -> bool:
        from datetime import date

        current_period = date.today().strftime("%Y-%m")
        return (
            self.installments_total > 1
            and self.status != "paid"
            and self.scheduled_period > current_period
        )

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


# ── Canonical Schema Foundations (Goal 2) ────────────────────────────────────


class CreditCardBillingCycleRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    credit_card_id: uuid.UUID
    billing_period: str
    start_date: date
    cutoff_date: date
    due_date: date
    status: str
    provenance: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreditCardStatementComponentRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    credit_card_id: uuid.UUID
    statement_id: uuid.UUID
    component_type: str
    amount: Decimal
    currency: str
    principal_amount: Decimal = Decimal("0.00")
    interest_amount: Decimal = Decimal("0.00")
    fee_amount: Decimal = Decimal("0.00")
    transaction_id: uuid.UUID | None = None
    installment_id: uuid.UUID | None = None
    statement_charge_id: uuid.UUID | None = None
    provenance: str
    description: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreditCardStatementFactRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    credit_card_id: uuid.UUID
    statement_id: uuid.UUID
    fact_type: str
    amount: Decimal | None = None
    currency: str | None = None
    provenance: str
    observed_at: datetime
    effective_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreditCardPaymentRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    credit_card_id: uuid.UUID
    account_id: uuid.UUID | None = None
    payment_transaction_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None
    booked_amount: Decimal
    booked_currency: str = "COP"
    source_amount: Decimal | None = None
    source_currency: str | None = None
    fx_rate: Decimal | None = None
    fx_quote_id: uuid.UUID | None = None
    fx_rate_snapshot_id: uuid.UUID | None = None
    principal_applied: Decimal = Decimal("0.00")
    interest_applied: Decimal = Decimal("0.00")
    fee_applied: Decimal = Decimal("0.00")
    unallocated_amount: Decimal = Decimal("0.00")
    payment_date: date
    status: str
    provenance: str
    allocation_source: str
    command_id: uuid.UUID | None = None
    command_fingerprint: str | None = None
    created_at: datetime

    @property
    def amount(self) -> Decimal:
        """Compatibility alias for booked_amount."""
        return self.booked_amount

    @property
    def currency(self) -> str:
        """Compatibility alias for booked_currency."""
        return self.booked_currency

    model_config = ConfigDict(from_attributes=True)


class CreditCardPaymentAllocationRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    credit_card_id: uuid.UUID
    payment_id: uuid.UUID
    target_installment_id: uuid.UUID | None = None
    target_charge_id: uuid.UUID | None = None
    target_statement_component_id: uuid.UUID | None = None
    allocated_amount: Decimal
    allocated_principal: Decimal = Decimal("0.00")
    allocated_interest: Decimal = Decimal("0.00")
    allocated_fees: Decimal = Decimal("0.00")
    allocation_order: int = 1
    allocation_source: str
    provenance: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreditCardAdjustmentRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    credit_card_id: uuid.UUID
    original_transaction_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None
    adjustment_type: str
    booked_amount: Decimal
    booked_currency: str = "COP"
    original_amount: Decimal | None = None
    original_currency: str | None = None
    fx_rate: Decimal | None = None
    fx_rate_snapshot_id: uuid.UUID | None = None
    fx_quote_id: uuid.UUID | None = None
    fx_provider: str | None = None
    fx_rate_timestamp: datetime | None = None
    status: str
    provenance: str
    allocation_source: str
    command_id: uuid.UUID | None = None
    command_fingerprint: str | None = None
    reason: str | None = None
    occurred_at: datetime
    created_at: datetime

    @property
    def amount(self) -> Decimal:
        """Compatibility alias for booked_amount."""
        return self.booked_amount

    @property
    def currency(self) -> str:
        """Compatibility alias for booked_currency."""
        return self.booked_currency

    model_config = ConfigDict(from_attributes=True)


class CreditCardAdjustmentAllocationRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    credit_card_id: uuid.UUID
    adjustment_id: uuid.UUID
    target_installment_id: uuid.UUID | None = None
    target_transaction_id: uuid.UUID | None = None
    target_charge_id: uuid.UUID | None = None
    allocated_amount: Decimal
    allocated_principal: Decimal = Decimal("0.00")
    allocated_interest: Decimal = Decimal("0.00")
    allocated_fees: Decimal = Decimal("0.00")
    allocation_order: int = 1
    allocation_source: str
    provenance: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
