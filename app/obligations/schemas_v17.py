from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.obligations.enums_v17 import (
    AmountType,
    Frequency,
    ObligationStatus,
    ObligationType,
    PeriodStatus,
)


class ObligationV17Response(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    user_id: UUID
    name: str
    description: str | None = None
    amount: Decimal
    amount_type: AmountType | None = None
    currency: str
    frequency: Frequency
    obligation_type: ObligationType
    status: ObligationStatus
    start_date: date | None = None
    first_due_date: date | None = None
    due_day: int | None = None
    due_month: int | None = None
    interval_count: int | None = None
    end_date: date | None = None
    end_count: int | None = None
    metadata_: dict | None = Field(default=None, alias="metadata")
    created_at: datetime
    updated_at: datetime


class ObligationPeriodV17Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    obligation_id: UUID
    due_date: date
    status: PeriodStatus
    is_current: bool = False
    is_future: bool = False
    is_overdue: bool = False
    requires_amount_definition: bool = False
    amount: Decimal = Decimal("0.00")
    amount_due: Decimal = Decimal("0.00")
    amount_paid: Decimal = Decimal("0.00")
    start_date: date | None = None
    end_date: date | None = None
    sequence_number: int | None = None
    period_sequence: int | None = None
    currency: str | None = None
    created_at: datetime
    updated_at: datetime


class ObligationPaymentV17Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    obligation_id: UUID
    obligation_period_id: UUID | None = None
    user_id: UUID | None = None
    account_id: UUID | None = None
    amount: Decimal
    currency: str | None = None
    source_amount: Decimal | None = None
    source_currency: str | None = None
    fx_rate: Decimal | None = None
    rate_source: str | None = None
    rate_timestamp: datetime | None = None
    paid_at: datetime | None = None
    quote_id: UUID | None = None
    rate_snapshot_id: UUID | None = None
    idempotency_key: str | None = None
    financial_event_id: UUID | None = None
    channel: str = "manual"
    partial_context: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EmptyStateResponse(BaseModel):
    message: str
    items: list = Field(default_factory=list)


class ApiErrorResponse(BaseModel):
    detail: str


class ObligationPaymentsV17ListResponse(BaseModel):
    items: list[ObligationPaymentV17Response]
    total: int
    limit: int
    offset: int


class ObligationV17UpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1)
    description: str | None = None
    metadata_: dict | None = Field(None, alias="metadata")
    base_amount: Decimal | None = None
    status: ObligationStatus | None = None

    @model_validator(mode="after")
    def validate_update(self) -> "ObligationV17UpdateRequest":
        if self.base_amount is not None:
            if (
                not self.base_amount.is_finite()
                or self.base_amount <= Decimal("0")
                or self.base_amount > Decimal("9999999999999.99")
            ):
                raise ValueError(
                    "base_amount must be finite, positive, and at most 9999999999999.99"
                )
            if self.base_amount.quantize(Decimal("0.01")) != self.base_amount:
                raise ValueError("base_amount must have at most 2 decimal places")
        return self


class ObligationV17CreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    description: str | None = None
    obligation_type: ObligationType
    frequency: Frequency
    amount_type: AmountType
    base_amount: Decimal | None = None
    currency: str = Field(default="COP", min_length=3, max_length=3)
    start_date: date
    first_due_date: date
    end_date: date | None = None
    end_count: int | None = Field(None, gt=0)
    metadata_: dict | None = Field(default_factory=dict, alias="metadata")

    @model_validator(mode="after")
    def validate_amounts_and_dates(self) -> "ObligationV17CreateRequest":
        # currency uppercase
        if self.currency:
            self.currency = self.currency.upper()

        # one_time / frequency coherence
        if self.obligation_type == ObligationType.one_time:
            if self.frequency != Frequency.one_time:
                raise ValueError("one_time obligation must have one_time frequency")
        elif self.obligation_type == ObligationType.recurring:
            if self.frequency == Frequency.one_time:
                raise ValueError("recurring obligation cannot have one_time frequency")

        # base_amount precision rules
        if self.base_amount is not None:
            if not self.base_amount.is_finite() or self.base_amount > Decimal("9999999999999.99"):
                raise ValueError("base_amount must be finite and at most 9999999999999.99")
            if self.base_amount.quantize(Decimal("0.01")) != self.base_amount:
                raise ValueError("base_amount must have at most 2 decimal places")

        if self.amount_type == AmountType.fixed:
            if self.base_amount is None or self.base_amount <= Decimal("0"):
                raise ValueError("base_amount must be > 0 when amount_type is fixed")
        elif self.amount_type == AmountType.variable:
            # base_amount can be None or >= 0 if variable
            if self.base_amount is not None and self.base_amount < Decimal("0"):
                raise ValueError("base_amount must be >= 0 when provided for variable amount_type")

        # date rules
        if self.first_due_date < self.start_date:
            raise ValueError("first_due_date cannot be before start_date")

        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")

        return self


class ObligationPeriodAmountDefineRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)
    currency: str | None = Field(None, min_length=3, max_length=3)
    metadata_: dict | None = Field(default_factory=dict, alias="metadata")

    @model_validator(mode="after")
    def validate_currency_and_precision(self) -> "ObligationPeriodAmountDefineRequest":
        if self.currency:
            self.currency = self.currency.upper()
        if (
            not self.amount.is_finite()
            or self.amount <= Decimal("0")
            or self.amount > Decimal("9999999999999.99")
        ):
            raise ValueError("amount must be finite, positive, and at most 9999999999999.99")
        if self.amount.quantize(Decimal("0.01")) != self.amount:
            raise ValueError("amount must have at most 2 decimal places")
        return self


class ObligationPeriodPaymentCreateRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)
    currency: str | None = Field(None, min_length=3, max_length=3)
    payment_date: date | None = None
    source_account_id: UUID | None = None
    metadata_: dict | None = Field(default_factory=dict, alias="metadata")
    idempotency_key: str | None = None
    source_amount: Decimal | None = None
    source_currency: str | None = Field(None, min_length=3, max_length=3)
    quote_id: UUID | None = None
    rate_snapshot_id: UUID | None = None

    @model_validator(mode="after")
    def validate_currency_and_amount(self) -> "ObligationPeriodPaymentCreateRequest":
        if self.currency:
            self.currency = self.currency.upper()
        if self.source_currency:
            self.source_currency = self.source_currency.upper()
        if self.amount is not None:
            if not self.amount.is_finite() or self.amount <= Decimal("0"):
                raise ValueError("amount must be positive and finite")
        return self


class ObligationPeriodPaymentResultResponse(BaseModel):
    payment: ObligationPaymentV17Response
    period: ObligationPeriodV17Response


class ObligationFIFOPaymentCreateRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)
    currency: str | None = Field(None, min_length=3, max_length=3)
    payment_date: date | None = None
    source_account_id: UUID | None = None
    metadata_: dict | None = Field(default_factory=dict, alias="metadata")
    idempotency_key: str | None = None
    source_amount: Decimal | None = None
    source_currency: str | None = Field(None, min_length=3, max_length=3)
    quote_id: UUID | None = None
    rate_snapshot_id: UUID | None = None

    @model_validator(mode="after")
    def validate_currency_and_amount(self) -> "ObligationFIFOPaymentCreateRequest":
        if self.currency:
            self.currency = self.currency.upper()
        if self.source_currency:
            self.source_currency = self.source_currency.upper()
        if self.amount is not None:
            if not self.amount.is_finite() or self.amount <= Decimal("0"):
                raise ValueError("amount must be positive and finite")
        return self


class ObligationFIFOPaymentResultResponse(BaseModel):
    payments: list[ObligationPaymentV17Response]
    periods: list[ObligationPeriodV17Response]
    total_applied: Decimal
    remaining_unapplied: Decimal
    strategy: str = "fifo"


class ObligationPeriodRefreshOverdueResponse(BaseModel):
    updated_periods: list[ObligationPeriodV17Response]
    updated_count: int


class ObligationsV17CurrencyTotal(BaseModel):
    currency: str
    pending_amount: str
    paid_amount: str
    overdue_amount: str
    period_count: int


class ObligationsV17StatusBreakdown(BaseModel):
    pending_payment: int = 0
    partially_paid: int = 0
    paid: int = 0
    overdue: int = 0
    pending_amount_definition: int = 0
    skipped: int = 0
    cancelled: int = 0


class ObligationsV17ActionRequiredItem(BaseModel):
    obligation_id: UUID
    period_id: UUID
    name: str
    reason: str
    currency: str
    due_date: date


class ObligationsV17SummaryResponse(BaseModel):
    month: str
    totals_by_currency: list[ObligationsV17CurrencyTotal]
    status_counts: ObligationsV17StatusBreakdown
    requires_action: list[ObligationsV17ActionRequiredItem]
    overdue_count: int
    pending_definition_count: int
    generated_at: datetime


class ObligationsV17IntelligenceRiskFlag(BaseModel):
    type: str
    severity: str
    count: int


class ObligationsV17IntelligenceContextResponse(BaseModel):
    month: str
    financial_load_by_currency: list[ObligationsV17CurrencyTotal]
    requires_action: list[ObligationsV17ActionRequiredItem]
    risk_flags: list[ObligationsV17IntelligenceRiskFlag]
    narrative_facts: list[str]
    generated_at: datetime


class ObligationPaymentPreviewV17Request(BaseModel):
    source_account_id: UUID
    amount: Decimal = Field(gt=0)
    currency: str | None = Field(None, min_length=3, max_length=3)
    source_currency: str | None = Field(None, min_length=3, max_length=3)
    quote_id: UUID | None = None
    rate_snapshot_id: UUID | None = None
    source_amount: Decimal | None = None  # deprecated, non-authoritative

    @model_validator(mode="after")
    def validate_preview_request(self) -> "ObligationPaymentPreviewV17Request":
        if self.currency:
            self.currency = self.currency.upper()
        if self.source_currency:
            self.source_currency = self.source_currency.upper()
        if (
            not self.amount.is_finite()
            or self.amount <= Decimal("0")
            or self.amount > Decimal("9999999999999.99")
        ):
            raise ValueError("amount must be finite, positive, and at most 9999999999999.99")
        if self.amount.quantize(Decimal("0.01")) != self.amount:
            raise ValueError("amount must have at most 2 decimal places")
        return self


class ObligationPaymentPreviewV17Response(BaseModel):
    obligation_currency: str
    source_currency: str
    applied_amount: Decimal
    source_amount: Decimal
    fx_rate: Decimal | None = None
    rate_timestamp: datetime | None = None
    quote_expires_at: datetime | None = None
    quote_id: UUID | None = None


class ObligationV17ActionState(BaseModel):
    can_pay: bool
    requires_amount_definition: bool
    can_skip: bool
    can_cancel: bool
    has_overdue: bool
    payable_period_count: int
    payable_total_amount: str


class ObligationV17PeriodCounts(BaseModel):
    payable: int
    overdue: int
    pending: int
    paid: int
    cancelled: int
    skipped: int


class ObligationV17OverviewResponse(ObligationV17Response):
    relevant_period: ObligationPeriodV17Response | None
    period_counts: ObligationV17PeriodCounts
    action_state: ObligationV17ActionState
