import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.credit.service import CreditCardService
from app.intelligence.repository import IntelligenceRepository
from app.intelligence.schemas import (
    AccountBalanceRead,
    FreeMoneyRead,
    IntelligenceBalanceRead,
    IntelligenceCashflowRead,
    IntelligenceCreditCardRead,
    IntelligenceDebtRead,
    IntelligenceGoalRead,
    IntelligenceObligationRead,
    IntelligenceSnapshotRead,
)


class IntelligenceService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = IntelligenceRepository(session)

    def _safe_decimal(self, val) -> Decimal:
        if val is None:
            return Decimal("0.00")
        return Decimal(str(val))

    async def get_snapshot(self, user_id: uuid.UUID) -> IntelligenceSnapshotRead:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Bogota")
        now = datetime.now(tz)
        month_start = datetime(now.year, now.month, 1, tzinfo=tz)
        if now.month == 12:
            next_month_start = datetime(now.year + 1, 1, 1, tzinfo=tz)
        else:
            next_month_start = datetime(now.year, now.month + 1, 1, tzinfo=tz)

        period_str = f"{now.year}-{now.month:02d}"

        cash = await self.repo.get_cash_metrics(user_id)
        cf = await self.repo.get_cashflow_metrics(user_id, month_start, next_month_start)
        historical_cf = await self.repo.get_historical_cashflow_metrics(user_id, month_start)
        goals = await self.repo.get_goals_metrics(user_id)
        obligations = await self.repo.get_obligations_metrics(user_id, period_str)
        transfers = await self.repo.get_transfers_metrics(user_id, month_start, next_month_start)
        recent = await self.repo.get_recent_activity(user_id, limit=5)

        # Build Cashflow current period metrics
        income_current = self._safe_decimal(cf.get("income_current_period"))
        cash_expenses_current = self._safe_decimal(cf.get("cash_expenses_current_period"))
        credit_card_consumption_current = self._safe_decimal(cf.get("credit_card_consumption_current_period"))
        debt_payments_current = self._safe_decimal(cf.get("debt_payments_current_period"))
        goal_contributions_current = self._safe_decimal(cf.get("goal_contributions_current_period"))
        obligation_payments_current = self._safe_decimal(cf.get("obligation_payments_current_period"))

        net_cashflow_current = (
            income_current
            - cash_expenses_current
            - goal_contributions_current
            - obligation_payments_current
            - debt_payments_current
        )

        # Legacy aliases for backward compatibility
        income_legacy = income_current
        expenses_legacy = (
            cash_expenses_current
            + goal_contributions_current
            + obligation_payments_current
            + debt_payments_current
        )
        net_cashflow_legacy = net_cashflow_current

        # Historical
        historical_income = self._safe_decimal(historical_cf.get("historical_income"))
        historical_expenses = self._safe_decimal(historical_cf.get("historical_expenses"))
        historical_net_cashflow = historical_income - historical_expenses
        # Credit Core is the source of truth for billed/unbilled debt separation.
        credit_summary = await CreditCardService(self.session).get_credit_summary(user_id)
        billed_debt = sum((card.billed_debt for card in credit_summary.cards), Decimal("0.00"))
        unbilled_debt = sum((card.unbilled_debt for card in credit_summary.cards), Decimal("0.00"))
        next_payment_estimate = sum(
            (card.next_payment_estimate for card in credit_summary.cards), Decimal("0.00")
        )

        # Sprint 1 - Financial Truth MVP
        available_real = self._safe_decimal(cash.get("total_balance"))

        # Sprint 4 - Obligations V1.1
        # committed_outflows rule: only include remaining_amount for active
        # obligations that are still pending or partially paid this period.
        # Already-paid obligations contribute 0.
        # Inactive obligations are excluded by list_active().
        from app.obligations.repository import ObligationRepository
        from app.obligations.schemas import ObligationRead
        
        obligation_repo = ObligationRepository(self.session)
        user_obligations = await obligation_repo.list_active(user_id)
        obligation_payments = await obligation_repo.get_period_payments(user_id, period_str)
        
        pending_obligations = Decimal("0.00")
        for o in user_obligations:
            or_read = ObligationRead.model_validate(o)
            or_read.paid_this_period = obligation_payments.get(o.id, Decimal("0.00"))
            # Only count obligations that are not fully paid this period
            if or_read.period_status in ("pending", "partial", "overdue"):
                rem = or_read.remaining_amount
                if rem is not None and rem > 0:
                    pending_obligations += rem

        # Sprint 5 - Credit Semantics V1.1: required payment is only billed debt.
        payment_required = billed_debt

        data_quality = {
            "payment_required": "billed_debt",
            "next_payment_estimate": "estimated",
            "statement_balance": "not_available",
        }

        from app.goals.repository import GoalRepository

        goal_repo = GoalRepository(self.session)
        user_goals = await goal_repo.list_active(user_id)

        goals_required = Decimal("0.00")

        # We need to compute remaining_required_this_period for each goal
        from app.goals.schemas import GoalRead

        if user_goals:
            goal_contributions = await goal_repo.get_period_contributions(user_id, period_str)
            for g in user_goals:
                gr = GoalRead.model_validate(g)
                gr.contributed_this_period = goal_contributions.get(g.id, Decimal("0.00"))
                goals_required += gr.remaining_required_this_period

        committed_outflows = pending_obligations + payment_required + goals_required

        free_money = available_real - committed_outflows
        if free_money < 0:
            free_money = Decimal("0.00")

        safe_money = free_money
        data_quality["safe_money"] = "computed_as_free_money"

        from app.intelligence.schemas import SnapshotTruth

        truth = SnapshotTruth(
            available_real=available_real,
            committed_outflows=committed_outflows,
            free_money=free_money,
            safe_money=safe_money,
            payment_required=payment_required,
            goals_required_this_period=goals_required,
            calculation_warnings=[],
            data_quality=data_quality,
        )

        return IntelligenceSnapshotRead(
            period={"month": period_str, "timezone": "America/Bogota"},
            cash={
                "total_balance": self._safe_decimal(cash.get("total_balance")),
                "active_accounts_count": cash.get("active_accounts_count", 0),
            },
            cashflow={
                "income": income_legacy,
                "expenses": expenses_legacy,
                "net_cashflow": net_cashflow_legacy,
                "income_current_period": income_current,
                "cash_expenses_current_period": cash_expenses_current,
                "credit_card_consumption_current_period": credit_card_consumption_current,
                "debt_payments_current_period": debt_payments_current,
                "goal_contributions_current_period": goal_contributions_current,
                "obligation_payments_current_period": obligation_payments_current,
                "committed_outflows_current_period": committed_outflows,
                "net_cashflow_current_period": net_cashflow_current,
            },
            historical={
                "income": historical_income,
                "expenses": historical_expenses,
                "net_cashflow": historical_net_cashflow,
            },
            debt={
                "credit_card_total_debt": credit_summary.total_debt,
                "billed_debt": billed_debt,
                "unbilled_debt": unbilled_debt,
                "payment_required": payment_required,
                "next_payment_estimate": next_payment_estimate,
            },
            goals={
                "active_goals_count": goals.get("active_goals_count", 0),
                "total_target": self._safe_decimal(goals.get("total_target")),
                "total_saved": self._safe_decimal(goals.get("total_saved")),
            },
            obligations={
                "pending_count": obligations.get("pending_count", 0),
                "pending_amount": self._safe_decimal(obligations.get("pending_amount")),
            },
            transfers={
                "monthly_transfer_volume": self._safe_decimal(
                    transfers.get("monthly_transfer_volume")
                )
            },
            recent_activity=recent,
            truth=truth,
        )

    async def get_balance(self, user_id: uuid.UUID) -> IntelligenceBalanceRead:
        accounts_data = await self.repo.get_account_balances(user_id)
        total = Decimal("0.00")
        accounts = []
        for acc in accounts_data:
            balance = self._safe_decimal(acc.get("balance"))
            total += balance
            accounts.append(
                AccountBalanceRead(
                    account_id=acc["account_id"],
                    account_name=acc["account_name"],
                    account_type=acc["account_type"],
                    currency=acc["currency"],
                    balance=balance,
                )
            )

        currency = "COP" if not accounts else accounts[0].currency

        return IntelligenceBalanceRead(
            total_available_real=total, accounts=accounts, currency=currency
        )

    async def get_free_money(self, user_id: uuid.UUID) -> FreeMoneyRead:
        snapshot = await self.get_snapshot(user_id)

        avail = snapshot.truth.available_real
        pending_obl = self._safe_decimal(snapshot.obligations.pending_amount)
        goals_req = snapshot.truth.goals_required_this_period
        cc_req = snapshot.truth.payment_required
        free = snapshot.truth.free_money

        explanation = {
            "step_1": "Tomamos todo el dinero disponible en cuentas líquidas.",
            "step_2": "Restamos las obligaciones pendientes del mes actual.",
            "step_3": "Restamos el pago requerido de tarjetas de crédito ya facturado.",
            "step_4": "Restamos el dinero necesario para cumplir tus metas de este mes.",
            "result": "El saldo resultante es tu dinero libre, descontando compromisos inminentes.",
        }

        return FreeMoneyRead(
            available_real=avail,
            minus_pending_obligations=pending_obl,
            minus_goals_required=goals_req,
            minus_credit_cards_required_payment=cc_req,
            free_money_result=free,
            explanation=explanation,
        )

    async def get_cashflow_summary(self, user_id: uuid.UUID) -> IntelligenceCashflowRead:
        cf = await self.repo.get_cashflow(user_id)
        if not cf:
            cf = {}

        cons = await self.repo.get_consumption_summary(user_id)
        if not cons:
            cons = {}

        return IntelligenceCashflowRead(
            income=self._safe_decimal(cf.get("monthly_income")),
            expense=self._safe_decimal(cf.get("consumption_outflow")),
            cash_consumption_outflow=self._safe_decimal(cons.get("cash_consumption_outflow")),
            credit_card_consumption_committed=self._safe_decimal(
                cons.get("credit_card_consumption_committed")
            ),
            total_consumption_committed=self._safe_decimal(cons.get("total_consumption_committed")),
            committed_outflow=self._safe_decimal(cf.get("committed_outflow")),
            wealth_allocation=self._safe_decimal(cf.get("wealth_allocation")),
            debt_service=self._safe_decimal(cf.get("debt_service")),
            net_cashflow=self._safe_decimal(cf.get("net_liquidity_change")),
            period=cf.get("period", datetime.now(UTC).strftime("%Y-%m")),
        )

    async def get_debt(self, user_id: uuid.UUID) -> IntelligenceDebtRead:
        from app.credit.service import CreditCardService

        credit_service = CreditCardService(self.session)
        summary = await credit_service.get_credit_summary(user_id)

        total_debt = summary.total_debt
        total_monthly_cc = Decimal("0.00")
        cards = []

        for cc in summary.cards:
            total_monthly_cc += cc.monthly_cc_payment
            cards.append(
                IntelligenceCreditCardRead(
                    credit_card_id=cc.card_id,
                    credit_card_name=cc.name,
                    credit_limit=cc.credit_limit,
                    estimated_current_debt=cc.total_debt,
                    current_debt=cc.current_debt,
                    billed_debt=cc.billed_debt,
                    unbilled_debt=cc.unbilled_debt,
                    estimated_available_credit=cc.available_credit,
                    available_credit=cc.available_credit,
                    payment_required=cc.payment_required,
                    next_payment_estimate=cc.next_payment_estimate,
                    statement_balance=cc.statement_balance,
                    monthly_cc_payment=cc.monthly_cc_payment,
                    cutoff_day=cc.cutoff_day,
                    due_day=cc.payment_due_day,
                    next_payment_due_date=cc.next_payment_due_date,
                    data_quality=cc.data_quality,
                )
            )

        obl_data = await self.repo.get_pending_obligations(user_id)
        pending_commitments = []
        for obl in obl_data:
            if obl.get("is_pending"):
                pending_commitments.append(
                    {
                        "obligation_id": str(obl["obligation_id"]),
                        "name": obl["name"],
                        "amount": float(self._safe_decimal(obl.get("amount"))),
                        "due_day": obl.get("due_day"),
                    }
                )

        return IntelligenceDebtRead(
            total_estimated_credit_card_debt=total_debt,
            total_monthly_cc_payment=total_monthly_cc,
            credit_cards=cards,
            pending_commitments=pending_commitments,
        )

    async def get_goals(self, user_id: uuid.UUID) -> list[IntelligenceGoalRead]:
        goals_data = await self.repo.get_goals(user_id)
        goals = []
        for g in goals_data:
            goals.append(
                IntelligenceGoalRead(
                    goal_id=g["goal_id"],
                    name=g["name"],
                    target_amount=self._safe_decimal(g.get("target_amount")),
                    current_amount=self._safe_decimal(g.get("current_amount")),
                    progress_percentage=self._safe_decimal(g.get("progress_percentage")),
                    remaining_amount=self._safe_decimal(g.get("target_amount"))
                    - self._safe_decimal(g.get("current_amount")),
                    monthly_required=self._safe_decimal(g.get("monthly_required"))
                    if g.get("monthly_required") is not None
                    else None,
                    daily_required=self._safe_decimal(g.get("daily_required"))
                    if g.get("daily_required") is not None
                    else None,
                    remaining_required_this_period=self._safe_decimal(
                        g.get("remaining_required_this_period")
                    ),
                    status=g["status"],
                )
            )
        return goals

    async def get_obligations(self, user_id: uuid.UUID) -> list[IntelligenceObligationRead]:
        obl_data = await self.repo.get_pending_obligations(user_id)
        obls = []
        for o in obl_data:
            obls.append(
                IntelligenceObligationRead(
                    obligation_id=o["obligation_id"],
                    name=o["name"],
                    amount=self._safe_decimal(o.get("amount")),
                    due_day=o.get("due_day"),
                    frequency=o["frequency"],
                    is_pending=o["is_pending"],
                )
            )
        return obls

    async def get_credit_cards(self, user_id: uuid.UUID) -> list[IntelligenceCreditCardRead]:
        debt_data = await self.get_debt(user_id)
        return debt_data.credit_cards
