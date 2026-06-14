import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

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
        self.repo = IntelligenceRepository(session)

    def _safe_decimal(self, val) -> Decimal:
        if val is None:
            return Decimal("0.00")
        return Decimal(str(val))

    async def get_snapshot(self, user_id: uuid.UUID) -> IntelligenceSnapshotRead:
        snap = await self.repo.get_financial_snapshot(user_id)
        if not snap:
            snap = {}

        cons = await self.repo.get_consumption_summary(user_id)
        if not cons:
            cons = {}

        cf = await self.repo.get_cashflow(user_id)
        if not cf:
            cf = {}

        return IntelligenceSnapshotRead(
            available_real=self._safe_decimal(snap.get("available_real")),
            safe_money=self._safe_decimal(snap.get("safe_money")),
            free_money=self._safe_decimal(snap.get("free_money")),
            total_income_current_month=self._safe_decimal(cf.get("monthly_income")),
            cash_consumption_outflow=self._safe_decimal(cons.get("cash_consumption_outflow")),
            credit_card_consumption_committed=self._safe_decimal(
                cons.get("credit_card_consumption_committed")
            ),
            total_consumption_committed=self._safe_decimal(cons.get("total_consumption_committed")),
            committed_outflow_current_month=self._safe_decimal(cf.get("committed_outflow")),
            wealth_allocation_current_month=self._safe_decimal(cf.get("wealth_allocation")),
            pending_obligations_total=self._safe_decimal(snap.get("pending_obligations_total")),
            goals_required_this_period=self._safe_decimal(
                snap.get("monthly_goals_required_remaining")
            ),
            credit_cards_required_payment=self._safe_decimal(snap.get("monthly_cc_payment")),
            total_credit_card_debt=self._safe_decimal(snap.get("credit_card_debt")),
            calculated_at=snap.get("calculated_at", datetime.now(UTC)),
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
        snap = await self.repo.get_financial_snapshot(user_id)
        if not snap:
            snap = {}

        avail = self._safe_decimal(snap.get("available_real"))
        pending_obl = self._safe_decimal(snap.get("pending_obligations_total"))
        goals_req = self._safe_decimal(snap.get("monthly_goals_required_remaining"))
        cc_req = self._safe_decimal(snap.get("monthly_cc_payment"))
        free = self._safe_decimal(snap.get("free_money"))

        explanation = {
            "step_1": "Tomamos todo el dinero disponible en cuentas líquidas.",
            "step_2": "Restamos las obligaciones pendientes del mes actual.",
            "step_3": "Restamos la cuota estimada de tarjetas de crédito.",
            "step_4": "Restamos el dinero necesario para cumplir tus metas de este mes.",
            "result": "El saldo resultante es tu dinero verdaderamente libre, sin compromisos.",
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
        credit_service = CreditCardService(self.repo.session)
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
                    billed_debt=cc.billed_debt,
                    unbilled_debt=cc.unbilled_debt,
                    estimated_available_credit=cc.available_credit,
                    monthly_cc_payment=cc.monthly_cc_payment,
                    cutoff_day=cc.cutoff_day,
                    due_day=cc.payment_due_day,
                    next_payment_due_date=cc.next_payment_due_date
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
