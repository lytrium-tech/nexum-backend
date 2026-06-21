import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IntelligenceBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SnapshotPeriod(BaseModel):
    month: str
    timezone: str


class SnapshotCash(BaseModel):
    total_balance: Decimal
    active_accounts_count: int


class HistoricalCashflow(BaseModel):
    income: Decimal = Decimal("0.00")
    expenses: Decimal = Decimal("0.00")
    net_cashflow: Decimal = Decimal("0.00")


class SnapshotCashflow(BaseModel):
    income: Decimal = Field(..., description="[DEPRECATED] Use income_current_period or historical instead.")
    expenses: Decimal = Field(..., description="[DEPRECATED] Use cash_expenses_current_period or historical instead.")
    net_cashflow: Decimal = Field(..., description="[DEPRECATED] Use net_cashflow_current_period or historical instead.")
    income_current_period: Decimal = Decimal("0.00")
    cash_expenses_current_period: Decimal = Decimal("0.00")
    credit_card_consumption_current_period: Decimal = Decimal("0.00")
    debt_payments_current_period: Decimal = Decimal("0.00")
    goal_contributions_current_period: Decimal = Decimal("0.00")
    obligation_payments_current_period: Decimal = Decimal("0.00")
    committed_outflows_current_period: Decimal = Decimal("0.00")
    net_cashflow_current_period: Decimal = Decimal("0.00")


class SnapshotDebt(BaseModel):
    credit_card_total_debt: Decimal
    billed_debt: Decimal
    unbilled_debt: Decimal
    payment_required: Decimal = Decimal("0.00")
    next_payment_estimate: Decimal = Decimal("0.00")


class SnapshotGoals(BaseModel):
    active_goals_count: int
    total_target: Decimal
    total_saved: Decimal


class SnapshotObligations(BaseModel):
    pending_count: int
    pending_amount: Decimal


class SnapshotTransfers(BaseModel):
    monthly_transfer_volume: Decimal


class SnapshotTruth(BaseModel):
    available_real: Decimal
    committed_outflows: Decimal
    free_money: Decimal
    safe_money: Decimal
    payment_required: Decimal
    goals_required_this_period: Decimal
    calculation_warnings: list[str]
    data_quality: dict[str, str]


class IntelligenceSnapshotRead(BaseModel):
    period: SnapshotPeriod
    cash: SnapshotCash
    cashflow: SnapshotCashflow
    historical: HistoricalCashflow | None = None
    debt: SnapshotDebt
    goals: SnapshotGoals
    obligations: SnapshotObligations
    transfers: SnapshotTransfers
    recent_activity: list[dict[str, Any]]
    truth: SnapshotTruth


class AccountBalanceRead(IntelligenceBase):
    account_id: uuid.UUID
    account_name: str
    account_type: str
    currency: str
    balance: Decimal


class IntelligenceBalanceRead(IntelligenceBase):
    total_available_real: Decimal
    accounts: list[AccountBalanceRead]
    currency: str


class FreeMoneyRead(IntelligenceBase):
    available_real: Decimal
    minus_pending_obligations: Decimal
    minus_goals_required: Decimal
    minus_credit_cards_required_payment: Decimal
    free_money_result: Decimal
    explanation: dict[str, Any]


class IntelligenceCashflowRead(IntelligenceBase):
    income: Decimal
    expense: Decimal
    cash_consumption_outflow: Decimal
    credit_card_consumption_committed: Decimal
    total_consumption_committed: Decimal
    committed_outflow: Decimal
    wealth_allocation: Decimal
    debt_service: Decimal
    net_cashflow: Decimal
    period: str


class IntelligenceCreditCardRead(IntelligenceBase):
    credit_card_id: uuid.UUID
    credit_card_name: str
    credit_limit: Decimal | None
    estimated_current_debt: Decimal
    current_debt: Decimal | None = None
    billed_debt: Decimal | None = None
    unbilled_debt: Decimal | None = None
    estimated_available_credit: Decimal | None
    available_credit: Decimal | None = None
    payment_required: Decimal | None = None
    next_payment_estimate: Decimal | None = None
    statement_balance: Decimal | None = None
    monthly_cc_payment: Decimal
    cutoff_day: int | None
    due_day: int | None
    next_payment_due_date: str | None = None
    data_quality: dict[str, str] = Field(default_factory=dict)


class IntelligenceDebtRead(IntelligenceBase):
    total_estimated_credit_card_debt: Decimal
    total_monthly_cc_payment: Decimal
    credit_cards: list[IntelligenceCreditCardRead]
    pending_commitments: list[dict[str, Any]]


class IntelligenceGoalRead(IntelligenceBase):
    goal_id: uuid.UUID
    name: str
    target_amount: Decimal
    current_amount: Decimal
    progress_percentage: Decimal
    remaining_amount: Decimal
    monthly_required: Decimal | None
    daily_required: Decimal | None
    remaining_required_this_period: Decimal
    status: str


class IntelligenceObligationRead(IntelligenceBase):
    obligation_id: uuid.UUID
    name: str
    amount: Decimal
    due_day: int | None
    frequency: str
    is_pending: bool
