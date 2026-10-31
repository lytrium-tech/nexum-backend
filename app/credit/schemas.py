import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field, model_validator


class CreditCardBase(BaseModel):
    name: str = Field(..., min_length=1)
    bank: str = Field(..., min_length=1)
    credit_limit: Decimal = Field(..., gt=0)
    cutoff_day: int = Field(..., ge=1, le=31)
    due_day: int = Field(..., ge=1, le=31)
    management_fee: Decimal = Field(default=Decimal("0.00"), ge=0)
    monthly_interest_rate_pct: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        validation_alias=AliasChoices("monthly_interest_rate_pct", "monthly_interest_rate"),
        serialization_alias="monthly_interest_rate_pct",
        description="Monthly interest rate in percentage points: 2.11 means 2.11% per month.",
    )
    annual_interest_rate: Decimal = Field(default=Decimal("0.00"), ge=0)
    network: str | None = None
    franchise: str | None = None
    currency: str = Field(min_length=3, max_length=3)

    @property
    def monthly_interest_rate(self) -> Decimal:
        return self.monthly_interest_rate_pct


class CreditCardCreate(CreditCardBase):
    model_config = ConfigDict(extra="forbid")


class CreditCardUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    bank: str | None = Field(None, min_length=1)
    credit_limit: Decimal | None = Field(None, gt=0)
    cutoff_day: int | None = Field(None, ge=1, le=31)
    due_day: int | None = Field(None, ge=1, le=31)
    management_fee: Decimal | None = Field(None, ge=0)
    monthly_interest_rate_pct: Decimal | None = Field(
        None,
        ge=0,
        validation_alias=AliasChoices("monthly_interest_rate_pct", "monthly_interest_rate"),
        serialization_alias="monthly_interest_rate_pct",
        description="Monthly interest rate in percentage points: 2.11 means 2.11% per month.",
    )
    annual_interest_rate: Decimal | None = Field(None, ge=0)
    network: str | None = None
    franchise: str | None = None
    is_active: bool | None = None

    model_config = ConfigDict(extra="forbid")


class CreditCardRead(CreditCardBase):
    id: uuid.UUID
    is_active: bool
    current_debt: Decimal = Field(
        default=Decimal("0.00"),
        description="Legacy compatibility alias for total_debt (booked card currency).",
        json_schema_extra={"deprecated": True},
    )
    total_debt: Decimal = Field(
        default=Decimal("0.00"),
        description="Total posted debt across canonical and legacy partitions in card currency.",
    )
    available_credit: Decimal = Field(
        default=Decimal("0.00"),
        description="Available credit line (limit - total_debt; Nexum-modeled estimate unless issuer fact).",
    )
    billed_debt: Decimal = Field(
        default=Decimal("0.00"),
        description="Cycle-derived billed debt metadata; not an exhaustive partition of total debt.",
    )
    unbilled_debt: Decimal = Field(
        default=Decimal("0.00"),
        description="Cycle-derived unbilled debt metadata; not an exhaustive partition of total debt.",
    )
    payment_required: Decimal = Field(
        default=Decimal("0.00"),
        description="Total required payment for current cycle (equals billed_debt).",
    )
    minimum_payment: Decimal | None = Field(
        default=None,
        description="Authoritative minimum payment from issuer statement; null/unknown when no issuer fact exists.",
    )
    next_payment_estimate: Decimal = Field(
        default=Decimal("0.00"),
        description="Nexum-modeled estimated upcoming payment.",
    )
    statement_balance: Decimal | None = Field(
        default=None,
        description="Immutable snapshot balance of the most recent closed statement; null if no closed statement exists.",
    )
    data_quality: dict[str, str] = Field(
        default_factory=dict,
        description="Metadata dictionary tracking provenance and estimation sources for computed credit fields.",
    )
    # Canonical lifecycle and provenance
    canonical_mode: bool = Field(
        default=False,
        description="Whether this card operates under canonical closure and immutable statements.",
    )
    canonical_activated_at: datetime | None = Field(
        default=None,
        description="Timestamp when canonical mode was activated for this card.",
    )
    provenance: str | None = Field(
        default=None,
        description="Source provenance of card facts (e.g. manual_input, issuer_authoritative).",
    )
    lifecycle_status: str | None = Field(
        default=None,
        description="Lifecycle status of the card entity.",
    )
    # Deprecated aliases kept temporarily for frontend compatibility.
    estimated_current_debt: Decimal = Field(
        default=Decimal("0.00"),
        description="Deprecated compatibility alias for total_debt.",
        json_schema_extra={"deprecated": True},
    )
    monthly_cc_payment: Decimal = Field(
        default=Decimal("0.00"),
        description="Deprecated compatibility alias for next_payment_estimate.",
        json_schema_extra={"deprecated": True},
    )

    @computed_field(
        description="Deprecated compatibility alias for available_credit.",
        json_schema_extra={"deprecated": True},
    )
    @property
    def estimated_available_credit(self) -> Decimal:
        return self.available_credit

    model_config = ConfigDict(from_attributes=True)


class CreditCardPurchaseCreate(BaseModel):
    amount: Decimal | None = Field(
        default=None,
        gt=0,
        description="Booked amount in card currency (legacy compatibility alias). Prefer original_amount/original_currency.",
        json_schema_extra={"deprecated": True},
    )
    category_id: uuid.UUID | None = Field(default=None, description="Category identifier.")
    description: str | None = Field(
        default=None, description="Purchase description or commercial label."
    )
    installments_total: int = Field(
        default=1, ge=1, description="Total number of installments (1 for revolving)."
    )
    zero_interest: bool = Field(
        default=False,
        description="Explicitly excludes this purchase from Nexum interest projections.",
    )
    source_message_id: uuid.UUID | None = Field(
        default=None, description="Source message ID for conversational traceability."
    )
    raw_message: str | None = Field(
        default=None, description="Original unparsed conversational message text."
    )
    # Multi-currency and canonical lifecycle
    original_amount: Decimal | None = Field(
        default=None,
        gt=0,
        description="Original purchase amount in transaction currency.",
    )
    original_currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        description="Original currency of the transaction (e.g. USD, EUR).",
    )
    currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        description="Booked card currency (must match card currency if provided).",
    )
    fx_quote_id: uuid.UUID | None = Field(default=None, description="FX rate quote identifier.")
    lifecycle_status: str | None = Field(
        default="posted",
        pattern=r"^(pending|posted)$",
        description="Lifecycle status: 'posted' (commits debt/installments) or 'pending' (pre-authorization hold).",
    )
    occurred_at: datetime | None = Field(
        default=None,
        description="Effective purchase timestamp (defaults to current financial time in Bogota).",
    )

    @model_validator(mode="after")
    def validate_amount_presence(self) -> "CreditCardPurchaseCreate":
        if self.amount is None and self.original_amount is None:
            raise ValueError("Either amount or original_amount must be provided")
        return self


class CreditCardPurchaseResult(BaseModel):
    status: str = Field(description="Execution result: 'success' or 'idempotent_retry'.")
    event_id: uuid.UUID | None = Field(default=None, description="Linked Ledger event identifier.")
    transaction_id: uuid.UUID | None = Field(
        default=None, description="Created credit card transaction identifier."
    )
    amount: Decimal = Field(description="Booked purchase amount in card currency.")
    current_debt: Decimal = Field(description="Total posted debt after purchase in card currency.")
    available_credit: Decimal = Field(
        description="Available credit after purchase in card currency."
    )
    estimated_current_debt: Decimal = Field(
        description="Deprecated alias for current_debt.",
        json_schema_extra={"deprecated": True},
    )
    estimated_available_credit: Decimal = Field(
        description="Deprecated alias for available_credit.",
        json_schema_extra={"deprecated": True},
    )
    cycle_id: uuid.UUID | None = Field(
        default=None, description="Assigned canonical billing cycle identifier."
    )
    billing_period: str | None = Field(
        default=None, description="Assigned billing period (YYYY-MM)."
    )
    original_amount: Decimal | None = Field(
        default=None, description="Original purchase amount in merchant currency."
    )
    original_currency: str | None = Field(default=None, description="Original purchase currency.")
    booked_amount: Decimal | None = Field(
        default=None, description="Booked purchase amount in card currency."
    )
    booked_currency: str | None = Field(
        default=None, description="Currency in which purchase is booked."
    )
    fx_rate: Decimal | None = Field(
        default=None, description="Exchange rate applied for currency conversion."
    )
    fx_rate_snapshot_id: uuid.UUID | None = Field(
        default=None, description="Immutable FX rate snapshot evidence identifier."
    )
    lifecycle_status: str | None = Field(
        default=None, description="Lifecycle status ('pending' or 'posted')."
    )
    debt_partition: str | None = Field(
        default=None, description="Debt partition ('canonical' or 'legacy')."
    )
    provenance: str | None = Field(
        default=None, description="Fact provenance (e.g. 'nexum_modeled', 'manual_input')."
    )


class CreditCardOpeningBalanceCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    effective_date: date
    composition_known: bool = False
    principal_amount: Decimal | None = Field(default=None, ge=0)
    assessed_interest_amount: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_composition(self):
        if self.composition_known:
            if self.principal_amount is None or self.assessed_interest_amount is None:
                raise ValueError(
                    "Known composition requires principal and assessed interest amounts"
                )
            if self.principal_amount + self.assessed_interest_amount != self.amount:
                raise ValueError("Known opening balance components must sum to amount")
        elif self.principal_amount is not None or self.assessed_interest_amount is not None:
            raise ValueError("Unknown composition cannot provide component amounts")
        return self


class CreditCardOpeningBalanceResult(BaseModel):
    status: str
    event_id: uuid.UUID | None = None
    transaction_id: uuid.UUID | None = None
    amount: Decimal
    current_debt: Decimal
    available_credit: Decimal
    provenance: str = "manual_input"


class CreditCardPaymentCreate(BaseModel):
    account_id: uuid.UUID = Field(description="Funding account identifier.")
    amount: Decimal = Field(..., gt=0, description="Payment amount in funding account currency.")
    source_message_id: uuid.UUID | None = Field(
        default=None, description="Source message ID for conversational traceability."
    )
    raw_message: str | None = Field(
        default=None, description="Original unparsed conversational message text."
    )


class CreditCardPaymentResult(BaseModel):
    status: str = Field(description="Execution result: 'success' or 'idempotent_retry'.")
    event_id: uuid.UUID | None = Field(default=None, description="Linked Ledger event identifier.")
    transaction_id: uuid.UUID | None = Field(
        default=None, description="Created payment transaction identifier."
    )
    amount: Decimal = Field(description="Payment amount booked to card in card currency.")
    current_debt: Decimal = Field(
        description="Remaining total posted debt after payment in card currency."
    )
    available_credit: Decimal = Field(description="Available credit line after payment.")
    estimated_current_debt: Decimal = Field(
        description="Deprecated alias for current_debt.",
        json_schema_extra={"deprecated": True},
    )
    account_balance: Decimal = Field(
        description="Remaining funding account balance in account currency."
    )


class CreditCardEarlyPaymentCreate(BaseModel):
    account_id: uuid.UUID = Field(description="Funding account identifier.")
    amount: Decimal | None = Field(
        None, gt=0, description="Prepayment amount in funding account currency."
    )
    allocation_mode: str = Field(
        default="reduce_installment_amount",
        description="Allocation strategy for prepayment ('reduce_installment_amount').",
    )
    source_message_id: uuid.UUID | None = Field(
        default=None, description="Source message ID for traceability."
    )
    raw_message: str | None = Field(default=None, description="Original unparsed message text.")


class CreditCardEarlyPaymentResult(BaseModel):
    status: str = Field(description="Execution result: 'success' or 'idempotent_retry'.")
    event_id: uuid.UUID | None = Field(default=None, description="Linked Ledger event identifier.")
    early_payment_id: uuid.UUID | None = Field(
        default=None, description="Early payment record identifier."
    )
    amount: Decimal = Field(description="Prepayment amount applied.")
    current_debt: Decimal = Field(description="Remaining total debt.")
    available_credit: Decimal = Field(description="Remaining available credit.")
    account_balance: Decimal = Field(description="Remaining funding account balance.")


class CreditCardEarlyPaymentPreviewCreate(BaseModel):
    account_id: uuid.UUID = Field(description="Funding account identifier.")
    amount: Decimal | None = Field(None, gt=0, description="Preview prepayment amount.")


class PaymentPreviewResult(BaseModel):
    source_amount: Decimal = Field(description="Amount in funding account currency.")
    source_currency: str = Field(description="Funding account currency.")
    target_amount: Decimal = Field(description="Calculated amount in card currency.")
    target_currency: str = Field(description="Card currency.")
    fx_rate: Decimal = Field(description="Exchange rate applied.")
    rate_source: str = Field(description="FX rate source provider.")
    is_estimated: bool = Field(description="Whether the exchange rate is an estimate.")


class CreditCardStatusRead(BaseModel):
    card_id: uuid.UUID = Field(description="Card identifier.")
    name: str = Field(description="Card label/name.")
    credit_limit: Decimal = Field(description="Card credit limit in card currency.")
    management_fee: Decimal = Field(default=Decimal("0.00"), description="Monthly management fee.")
    monthly_interest_rate_pct: Decimal = Field(
        default=Decimal("0.00"),
        validation_alias=AliasChoices("monthly_interest_rate_pct", "monthly_interest_rate"),
        serialization_alias="monthly_interest_rate_pct",
        description="Monthly interest rate in percentage points: 2.11 means 2.11% per month.",
    )
    annual_interest_rate: Decimal = Field(
        default=Decimal("0.00"), description="Annual interest rate percentage."
    )
    network: str | None = Field(default=None, description="Card network.")
    franchise: str | None = Field(default=None, description="Card franchise.")
    currency: str = Field(default="COP", description="Card currency.")
    current_debt: Decimal = Field(
        default=Decimal("0.00"),
        description="Legacy compatibility alias for total_debt.",
        json_schema_extra={"deprecated": True},
    )
    total_debt: Decimal = Field(
        description="Total posted debt across canonical and legacy partitions."
    )
    billed_debt: Decimal = Field(
        description="Cycle-derived billed debt metadata; not an exhaustive partition of total debt."
    )
    unbilled_debt: Decimal = Field(
        description="Cycle-derived unbilled debt metadata; not an exhaustive partition of total debt."
    )
    available_credit: Decimal = Field(
        description="Available credit (credit_limit - total_debt; Nexum-modeled estimate)."
    )
    payment_required: Decimal = Field(
        default=Decimal("0.00"), description="Payment required for current cycle (billed_debt)."
    )
    minimum_payment: Decimal | None = Field(
        default=None, description="Authoritative minimum payment from issuer (null if unknown)."
    )
    next_payment_estimate: Decimal = Field(
        default=Decimal("0.00"), description="Nexum-modeled estimate for upcoming payment."
    )
    next_payment: "CreditCardNextPaymentRead | None" = None
    statement_balance: Decimal | None = Field(
        default=None, description="Balance of last closed statement snapshot."
    )
    monthly_cc_payment: Decimal = Field(
        description="Compatibility alias for next_payment_estimate.",
        json_schema_extra={"deprecated": True},
    )
    cutoff_day: int = Field(description="Cutoff day of month.")
    payment_due_day: int = Field(description="Payment due day of month.")
    next_payment_due_date: str = Field(description="Computed due date in ISO format.")
    purchases_count: int = Field(description="Count of purchases in current cycle.")
    payments_count: int = Field(description="Count of payments in current cycle.")
    data_quality: dict[str, str] = Field(
        default_factory=dict, description="Metadata dictionary tracking data provenance."
    )

    @property
    def monthly_interest_rate(self) -> Decimal:
        return self.monthly_interest_rate_pct


class CreditSummaryRead(BaseModel):
    total_credit_limit: Decimal
    total_debt: Decimal
    total_available_credit: Decimal
    total_monthly_cc_payment: Decimal = Decimal("0.00")
    total_payment_required: Decimal = Decimal("0.00")
    total_next_payment_estimate: Decimal = Decimal("0.00")
    cards: list[CreditCardStatusRead]


class CreditCardNextPaymentRead(BaseModel):
    due_date: date
    status: Literal["overdue", "due_today", "upcoming"]
    scheduled_principal: Decimal
    assessed_interest: Decimal
    estimated_interest: Decimal
    known_charges: Decimal = Field(
        description="Charges known to Nexum; zero is not an issuer guarantee."
    )
    estimated_total: Decimal
    provenance: str
    confidence: str
    estimator_version: str | None = None


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
    # Multi-currency, cycle identity, assessed vs estimated interest, provenance and partition
    original_amount: Decimal | None = None
    original_currency: str | None = None
    booked_amount: Decimal | None = None
    booked_currency: str | None = None
    cycle_id: uuid.UUID | None = None
    assessed_interest_amount: Decimal | None = None
    opening_balance_amount: Decimal | None = None
    estimated_interest_amount: Decimal | None = None
    interest_estimator_version: str | None = None
    lifecycle_status: str | None = None
    provenance: str | None = None
    debt_partition: str | None = None

    @computed_field
    @property
    def remaining_principal(self) -> Decimal:
        return max(Decimal("0.00"), self.principal_amount - self.paid_amount)

    @computed_field
    @property
    def remaining_opening_balance(self) -> Decimal:
        return max(
            Decimal("0.00"),
            (self.opening_balance_amount or Decimal("0.00")) - self.paid_amount,
        )

    @computed_field
    @property
    def is_pay_early_eligible(self) -> bool:
        from app.core.financial_time import financial_date

        current_period = financial_date().strftime("%Y-%m")
        return (
            self.installments_total > 1
            and self.status != "paid"
            and self.scheduled_period > current_period
        )

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
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class CreditCardStatementRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None = None
    credit_card_id: uuid.UUID
    billing_period: str
    cycle_id: uuid.UUID | None = None
    billing_period_start: date | None = None
    cutoff_date: date | None = None
    due_date: date | None = None
    previous_balance: Decimal = Decimal("0.00")
    new_purchases: Decimal = Decimal("0.00")
    billed_installments: Decimal = Decimal("0.00")
    fees_total: Decimal = Decimal("0.00")
    interest_total: Decimal = Decimal("0.00")
    payments_received: Decimal = Decimal("0.00")
    statement_balance: Decimal = Decimal("0.00")
    minimum_payment: Decimal | None = None
    status: str = "open"
    currency: str | None = None
    assessed_interest: Decimal | None = None
    estimated_interest: Decimal | None = None
    provenance: str | None = None
    debt_partition: str | None = None
    frozen_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    components: list[CreditCardStatementComponentRead] = Field(default_factory=list)
    facts: list[CreditCardStatementFactRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class CreditCardCycleClosureSummary(BaseModel):
    as_of_date: date
    cards_inspected: int = 0
    cycles_eligible: int = 0
    cycles_closed: int = 0
    statements_created: int = 0
    statements_skipped: int = 0
    components_created: int = 0
    technical_failures: int = 0
    has_remaining_catchup: bool = False
    remaining_catchup_cycles: int = 0
    card_summaries: list[dict[str, Any]] = Field(default_factory=list)


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

    @computed_field(
        description="Compatibility alias for booked_amount (deprecated).",
        json_schema_extra={"deprecated": True},
    )
    @property
    def amount(self) -> Decimal:
        """Compatibility alias for booked_amount."""
        return self.booked_amount

    @computed_field(
        description="Compatibility alias for booked_currency (deprecated).",
        json_schema_extra={"deprecated": True},
    )
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
    direction: str | None = None
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

    @computed_field(
        description="Compatibility alias for booked_amount (deprecated).",
        json_schema_extra={"deprecated": True},
    )
    @property
    def amount(self) -> Decimal:
        """Compatibility alias for booked_amount."""
        return self.booked_amount

    @computed_field(
        description="Compatibility alias for booked_currency (deprecated).",
        json_schema_extra={"deprecated": True},
    )
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


AdjustmentType = Literal[
    "refund",
    "reversal",
    "chargeback",
    "statement_credit",
    "manual_adjustment",
]
AdjustmentDirection = Literal["credit", "debit"]
AdjustmentProvenance = Literal[
    "manual",
    "issuer_authoritative",
    "nexum_modeled",
    "legacy_unknown",
]
AdjustmentAllocationSource = Literal[
    "nexum_modeled",
    "issuer_authoritative",
    "user_directed",
]


class CreditCardAdjustmentCreate(BaseModel):
    original_transaction_id: uuid.UUID
    amount: Decimal = Field(..., gt=0)
    adjustment_type: AdjustmentType = "refund"
    direction: AdjustmentDirection = "credit"
    provenance: AdjustmentProvenance = "manual"
    allocation_source: AdjustmentAllocationSource = "nexum_modeled"
    reason: str | None = None
    original_currency: str | None = None
    original_amount: Decimal | None = None
    source_message_id: uuid.UUID | None = None
    raw_message: str | None = None

    @model_validator(mode="after")
    def validate_type_and_direction(self) -> "CreditCardAdjustmentCreate":
        # Purchase refunds, chargebacks, and statement credits are strictly credits
        if (
            self.adjustment_type in ("refund", "chargeback", "statement_credit")
            and self.direction != "credit"
        ):
            raise ValueError(
                f"Adjustment type '{self.adjustment_type}' must have direction 'credit', got '{self.direction}'"
            )
        # Payment reversal is debit-to-card only
        if self.adjustment_type == "reversal" and self.direction not in ("credit", "debit"):
            raise ValueError(
                "Reversal must have direction 'credit' (for purchase) or 'debit' (for payment)"
            )
        # Reject spoofed issuer_authoritative provenance or allocation_source on manual endpoint
        if self.provenance == "issuer_authoritative":
            raise ValueError(
                "Cannot claim 'issuer_authoritative' provenance on manual adjustment endpoint"
            )
        if self.allocation_source == "issuer_authoritative":
            raise ValueError(
                "Cannot claim 'issuer_authoritative' allocation_source on manual adjustment endpoint"
            )
        return self


class CreditCardAdjustmentResult(BaseModel):
    status: str
    adjustment_id: uuid.UUID
    event_id: uuid.UUID | None = None
    amount: Decimal
    current_debt: Decimal
    available_credit: Decimal
    adjustment_type: str
    direction: str
    provenance: str = "manual"
    allocation_source: str = "nexum_modeled"


# ── Credit Command Reconciliation Schemas ────────────────────────────────────

CreditCommandStatus = Literal["confirmed", "unknown"]
CreditCommandOperationType = Literal[
    "purchase",
    "payment",
    "pay_early",
    "adjustment",
]


class CreditCommandReconciliationRead(BaseModel):
    command_id: uuid.UUID = Field(
        description="Business command identifier (idempotency key) submitted by the client."
    )
    status: CreditCommandStatus = Field(
        description="Reconciliation outcome: 'confirmed' if committed and provable; 'unknown' otherwise."
    )
    operation_type: CreditCommandOperationType | None = Field(
        default=None,
        description="Specific Credit mutation family for confirmed commands ('purchase', 'payment', 'pay_early', 'adjustment'). Strictly null when unknown.",
    )
    event_id: uuid.UUID | None = Field(
        default=None,
        description="Authoritative FinancialEvent identifier. Strictly null when unknown.",
    )
    resource_id: uuid.UUID | None = Field(
        default=None,
        description="Canonical Credit resource identifier matching the operation type. Strictly null when unknown.",
    )
    transaction_id: uuid.UUID | None = Field(
        default=None,
        description="Persisted CreditCardTransaction identifier for purchase operations. Strictly null otherwise.",
    )
    payment_id: uuid.UUID | None = Field(
        default=None,
        description="Persisted CreditCardPayment identifier for standard payment operations. Strictly null otherwise.",
    )
    early_payment_id: uuid.UUID | None = Field(
        default=None,
        description="Persisted CreditCardEarlyPayment identifier for targeted pay_early operations. Strictly null otherwise.",
    )
    adjustment_id: uuid.UUID | None = Field(
        default=None,
        description="Persisted CreditCardAdjustment identifier for adjustment operations. Strictly null otherwise.",
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_reconciliation_consistency(self) -> "CreditCommandReconciliationRead":
        if self.status == "unknown":
            if self.operation_type is not None:
                raise ValueError("operation_type must be null when status is unknown")
            if self.event_id is not None:
                raise ValueError("event_id must be null when status is unknown")
            if self.resource_id is not None:
                raise ValueError("resource_id must be null when status is unknown")
            if self.transaction_id is not None:
                raise ValueError("transaction_id must be null when status is unknown")
            if self.payment_id is not None:
                raise ValueError("payment_id must be null when status is unknown")
            if self.early_payment_id is not None:
                raise ValueError("early_payment_id must be null when status is unknown")
            if self.adjustment_id is not None:
                raise ValueError("adjustment_id must be null when status is unknown")
        elif self.status == "confirmed":
            if self.operation_type is None:
                raise ValueError("operation_type is required when status is confirmed")
            if self.event_id is None:
                raise ValueError("event_id is required when status is confirmed")
            if self.resource_id is None:
                raise ValueError("resource_id is required when status is confirmed")
            if self.operation_type == "purchase":
                if self.transaction_id is None or self.transaction_id != self.resource_id:
                    raise ValueError("transaction_id must match resource_id for confirmed purchase")
                if (
                    self.payment_id is not None
                    or self.early_payment_id is not None
                    or self.adjustment_id is not None
                ):
                    raise ValueError("Other operation IDs must be null for purchase")
            elif self.operation_type == "payment":
                if self.payment_id is None or self.payment_id != self.resource_id:
                    raise ValueError("payment_id must match resource_id for confirmed payment")
                if (
                    self.transaction_id is not None
                    or self.early_payment_id is not None
                    or self.adjustment_id is not None
                ):
                    raise ValueError("Other operation IDs must be null for payment")
            elif self.operation_type == "pay_early":
                if self.early_payment_id is None or self.early_payment_id != self.resource_id:
                    raise ValueError(
                        "early_payment_id must match resource_id for confirmed pay_early"
                    )
                if (
                    self.transaction_id is not None
                    or self.payment_id is not None
                    or self.adjustment_id is not None
                ):
                    raise ValueError("Other operation IDs must be null for pay_early")
            elif self.operation_type == "adjustment":
                if self.adjustment_id is None or self.adjustment_id != self.resource_id:
                    raise ValueError(
                        "adjustment_id must match resource_id for confirmed adjustment"
                    )
                if (
                    self.transaction_id is not None
                    or self.payment_id is not None
                    or self.early_payment_id is not None
                ):
                    raise ValueError("Other operation IDs must be null for adjustment")
        return self
