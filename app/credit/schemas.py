import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


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
    model_config = ConfigDict(extra="forbid")


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
        description="Current amount due from closed billing statements.",
    )
    unbilled_debt: Decimal = Field(
        default=Decimal("0.00"),
        description="Unbilled debt from current open cycle and future installment principal.",
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
    # Explicit cycle, multi-currency, FX evidence and partition
    cycle_id: uuid.UUID | None = Field(
        default=None, description="Assigned canonical billing cycle identifier."
    )
    billing_period: str | None = Field(
        default=None, description="Assigned billing cycle period (YYYY-MM)."
    )
    original_amount: Decimal | None = Field(
        default=None, description="Original purchase amount in merchant currency."
    )
    original_currency: str | None = Field(default=None, description="Original purchase currency.")
    booked_amount: Decimal | None = Field(
        default=None, description="Booked purchase amount in card currency."
    )
    booked_currency: str | None = Field(
        default=None, description="Card currency in which purchase is booked."
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
    monthly_interest_rate: Decimal = Field(
        default=Decimal("0.00"), description="Monthly interest rate percentage."
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
    billed_debt: Decimal = Field(description="Billed debt due in the current cycle.")
    unbilled_debt: Decimal = Field(description="Unbilled debt for future cycles.")
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
    # Multi-currency, cycle identity, assessed vs estimated interest, provenance and partition
    original_amount: Decimal | None = None
    original_currency: str | None = None
    booked_amount: Decimal | None = None
    booked_currency: str | None = None
    cycle_id: uuid.UUID | None = None
    assessed_interest_amount: Decimal | None = None
    estimated_interest_amount: Decimal | None = None
    lifecycle_status: str | None = None
    provenance: str | None = None
    debt_partition: str | None = None

    @computed_field
    @property
    def remaining_principal(self) -> Decimal:
        return max(Decimal("0.00"), self.principal_amount - self.paid_amount)

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
