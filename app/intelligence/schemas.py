import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class IntelligenceBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class IntelligenceSnapshotRead(IntelligenceBase):
    available_real: Decimal
    safe_money: Decimal
    free_money: Decimal
    total_income_current_month: Decimal
    cash_consumption_outflow: Decimal
    credit_card_consumption_committed: Decimal
    total_consumption_committed: Decimal
    committed_outflow_current_month: Decimal
    wealth_allocation_current_month: Decimal
    pending_obligations_total: Decimal
    goals_required_this_period: Decimal
    credit_cards_required_payment: Decimal
    total_credit_card_debt: Decimal
    calculated_at: datetime


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
    billed_debt: Decimal | None = None
    unbilled_debt: Decimal | None = None
    estimated_available_credit: Decimal | None
    monthly_cc_payment: Decimal
    cutoff_day: int | None
    due_day: int | None
    next_payment_due_date: str | None = None


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
