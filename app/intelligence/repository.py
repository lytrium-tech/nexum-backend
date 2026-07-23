import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class IntelligenceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_cash_metrics(self, user_id: uuid.UUID) -> dict[str, Any]:
        query = text(
            "SELECT COALESCE(SUM(balance), 0) as total_balance, COUNT(id) as active_accounts_count, COUNT(DISTINCT currency) as currency_count "
            "FROM accounts WHERE user_id = :user_id AND is_active = true"
        )
        result = await self.session.execute(query, {"user_id": user_id})
        return dict(result.mappings().first() or {})

    async def get_cash_metrics_by_currency(self, user_id: uuid.UUID) -> list[dict[str, Any]]:
        query = text(
            "SELECT currency, COALESCE(SUM(balance), 0) as total_balance, COUNT(id) as active_accounts_count "
            "FROM accounts WHERE user_id = :user_id AND is_active = true GROUP BY currency"
        )
        result = await self.session.execute(query, {"user_id": user_id})
        return [dict(r) for r in result.mappings().all()]

    async def get_cashflow_metrics(
        self, user_id: uuid.UUID, month_start: datetime, next_month_start: datetime
    ) -> dict[str, Any]:
        query = text(
            "SELECT "
            "COALESCE(SUM(CASE WHEN event_type = 'income' THEN amount ELSE 0 END), 0) as income_current_period, "
            "COALESCE(SUM(CASE WHEN event_type = 'expense' THEN amount ELSE 0 END), 0) as cash_expenses_current_period, "
            "COALESCE(SUM(CASE WHEN event_type = 'credit_card_purchase' THEN amount ELSE 0 END), 0) as credit_card_consumption_current_period, "
            "COALESCE(SUM(CASE WHEN event_type = 'credit_card_payment' THEN amount ELSE 0 END), 0) as debt_payments_current_period, "
            "COALESCE(SUM(CASE WHEN event_type = 'goal_contribution' AND direction = 'outflow' THEN amount ELSE 0 END), 0) as goal_contributions_current_period, "
            "COALESCE(SUM(CASE WHEN event_type = 'obligation_payment' THEN amount ELSE 0 END), 0) as obligation_payments_current_period, "
            "COUNT(DISTINCT currency) as currency_count "
            "FROM financial_events "
            "WHERE user_id = :user_id AND occurred_at >= :start AND occurred_at < :end"
        )
        result = await self.session.execute(
            query, {"user_id": user_id, "start": month_start, "end": next_month_start}
        )
        return dict(result.mappings().first() or {})

    async def get_cashflow_metrics_by_currency(
        self, user_id: uuid.UUID, month_start: datetime, next_month_start: datetime
    ) -> list[dict[str, Any]]:
        query = text(
            "SELECT currency, "
            "COALESCE(SUM(CASE WHEN event_type = 'income' THEN amount ELSE 0 END), 0) as income_current_period, "
            "COALESCE(SUM(CASE WHEN event_type = 'expense' THEN amount ELSE 0 END), 0) as cash_expenses_current_period, "
            "COALESCE(SUM(CASE WHEN event_type = 'credit_card_purchase' THEN amount ELSE 0 END), 0) as credit_card_consumption_current_period, "
            "COALESCE(SUM(CASE WHEN event_type = 'credit_card_payment' THEN amount ELSE 0 END), 0) as debt_payments_current_period, "
            "COALESCE(SUM(CASE WHEN event_type = 'goal_contribution' AND direction = 'outflow' THEN amount ELSE 0 END), 0) as goal_contributions_current_period, "
            "COALESCE(SUM(CASE WHEN event_type = 'obligation_payment' THEN amount ELSE 0 END), 0) as obligation_payments_current_period "
            "FROM financial_events "
            "WHERE user_id = :user_id AND occurred_at >= :start AND occurred_at < :end "
            "GROUP BY currency"
        )
        result = await self.session.execute(
            query, {"user_id": user_id, "start": month_start, "end": next_month_start}
        )
        return [dict(r) for r in result.mappings().all()]

    async def get_historical_cashflow_metrics(
        self, user_id: uuid.UUID, before: datetime
    ) -> dict[str, Any]:
        # Historical metrics (all time before the current month)
        query = text(
            "SELECT "
            "COALESCE(SUM(CASE WHEN event_type = 'income' THEN amount ELSE 0 END), 0) as historical_income, "
            "COALESCE(SUM(CASE WHEN event_type IN ('expense', 'credit_card_payment', 'obligation_payment') OR (event_type = 'goal_contribution' AND direction = 'outflow') THEN amount ELSE 0 END), 0) as historical_expenses "
            "FROM financial_events "
            "WHERE user_id = :user_id AND occurred_at < :before"
        )
        result = await self.session.execute(query, {"user_id": user_id, "before": before})
        return dict(result.mappings().first() or {})

    async def get_debt_metrics(self, user_id: uuid.UUID) -> dict[str, Any]:
        query = text(
            "SELECT COALESCE(SUM(current_debt), 0) as credit_card_total_debt "
            "FROM credit_cards WHERE user_id = :user_id AND is_active = true"
        )
        result = await self.session.execute(query, {"user_id": user_id})
        return dict(result.mappings().first() or {})

    async def get_goals_metrics(self, user_id: uuid.UUID) -> dict[str, Any]:
        query = text(
            "SELECT COUNT(id) as active_goals_count, "
            "COALESCE(SUM(target_amount), 0) as total_target, "
            "COALESCE(SUM(current_amount), 0) as total_saved "
            "FROM goals WHERE user_id = :user_id AND status = 'active'"
        )
        result = await self.session.execute(query, {"user_id": user_id})
        return dict(result.mappings().first() or {})

    async def get_obligations_metrics(self, user_id: uuid.UUID, period: str) -> dict[str, Any]:
        query = text(
            "SELECT COUNT(op.id) as pending_count, "
            "COALESCE(SUM(op.amount - COALESCE(op.paid_amount, 0)), 0) as pending_amount "
            "FROM obligation_periods op "
            "JOIN obligations o ON op.obligation_id = o.id "
            "WHERE o.user_id = :user_id "
            "AND op.status IN ('pending_payment', 'partially_paid', 'overdue', 'pending_amount_definition') "
            # [SAFETY PATCH] Only sum COP to prevent cross-currency corruption in V1.7
            "AND o.currency = 'COP'"
        )
        result = await self.session.execute(query, {"user_id": user_id})
        return dict(result.mappings().first() or {})

    async def get_transfers_metrics(
        self, user_id: uuid.UUID, month_start: datetime, next_month_start: datetime
    ) -> dict[str, Any]:
        query = text(
            "SELECT COALESCE(SUM(amount), 0) as monthly_transfer_volume "
            "FROM transfers "
            "WHERE user_id = :user_id AND created_at >= :start AND created_at < :end"
        )
        result = await self.session.execute(
            query, {"user_id": user_id, "start": month_start, "end": next_month_start}
        )
        return dict(result.mappings().first() or {})

    async def get_recent_activity(self, user_id: uuid.UUID, limit: int = 5) -> list[dict[str, Any]]:
        query = text(
            "SELECT id, event_type, amount, currency, description, occurred_at "
            "FROM financial_events "
            "WHERE user_id = :user_id "
            "ORDER BY occurred_at DESC LIMIT :limit"
        )
        result = await self.session.execute(query, {"user_id": user_id, "limit": limit})
        return [dict(r) for r in result.mappings().all()]

    async def get_financial_snapshot(self, user_id: uuid.UUID) -> dict[str, Any] | None:
        query = text(
            "SELECT * FROM public.v_financial_snapshot_current_month WHERE user_id = :user_id"
        )
        result = await self.session.execute(query, {"user_id": user_id})
        row = result.mappings().first()
        return dict(row) if row else None

    async def get_cashflow(self, user_id: uuid.UUID) -> dict[str, Any] | None:
        # Keep this endpoint independent from legacy views that classify every
        # goal_contribution as an outflow. Reserved allocations are neutral;
        # reconciliation balance_adjustment events are neither income nor
        # operational cashflow.
        query = text(
            "WITH period_bounds AS ("
            "  SELECT "
            "    date_trunc('month', now() AT TIME ZONE 'America/Bogota') "
            "      AT TIME ZONE 'America/Bogota' AS start_at, "
            "    (date_trunc('month', now() AT TIME ZONE 'America/Bogota') "
            "      + interval '1 month') AT TIME ZONE 'America/Bogota' AS end_at"
            "), aggregates AS ("
            "  SELECT "
            "    COALESCE(SUM(CASE WHEN event_type = 'income' "
            "      THEN amount ELSE 0 END), 0) AS monthly_income, "
            "    COALESCE(SUM(CASE WHEN event_type = 'expense' "
            "      THEN amount ELSE 0 END), 0) AS consumption_outflow, "
            "    COALESCE(SUM(CASE WHEN event_type = 'goal_contribution' "
            "      AND direction = 'outflow' THEN amount ELSE 0 END), 0) "
            "      AS wealth_allocation, "
            "    COALESCE(SUM(CASE WHEN event_type = 'credit_card_payment' "
            "      THEN amount ELSE 0 END), 0) AS debt_service, "
            "    COALESCE(SUM(CASE WHEN event_type = 'obligation_payment' "
            "      THEN amount ELSE 0 END), 0) AS committed_outflow "
            "  FROM financial_events, period_bounds "
            "  WHERE user_id = :user_id "
            "    AND occurred_at >= period_bounds.start_at "
            "    AND occurred_at < period_bounds.end_at"
            ") "
            "SELECT "
            "  to_char(now() AT TIME ZONE 'America/Bogota', 'YYYY-MM') AS period, "
            "  monthly_income, consumption_outflow, wealth_allocation, "
            "  debt_service, committed_outflow, "
            "  monthly_income - consumption_outflow - wealth_allocation "
            "    - debt_service - committed_outflow AS net_liquidity_change "
            "FROM aggregates"
        )
        result = await self.session.execute(query, {"user_id": user_id})
        row = result.mappings().first()
        return dict(row) if row else None

    async def get_consumption_summary(self, user_id: uuid.UUID) -> dict[str, Any] | None:
        query = text(
            "SELECT * FROM public.v_consumption_summary_current_month WHERE user_id = :user_id"
        )
        result = await self.session.execute(query, {"user_id": user_id})
        row = result.mappings().first()
        return dict(row) if row else None

    async def get_account_balances(self, user_id: uuid.UUID) -> list[dict[str, Any]]:
        query = text("SELECT * FROM public.v_account_balances WHERE user_id = :user_id")
        result = await self.session.execute(query, {"user_id": user_id})
        return [dict(r) for r in result.mappings().all()]

    async def get_credit_card_debt(self, user_id: uuid.UUID) -> list[dict[str, Any]]:
        query = text("SELECT * FROM public.v_credit_card_debt WHERE user_id = :user_id")
        result = await self.session.execute(query, {"user_id": user_id})
        return [dict(r) for r in result.mappings().all()]

    async def get_goals(self, user_id: uuid.UUID) -> list[dict[str, Any]]:
        query = text("SELECT * FROM public.v_goals_current_month WHERE user_id = :user_id")
        result = await self.session.execute(query, {"user_id": user_id})
        return [dict(r) for r in result.mappings().all()]

    async def get_pending_obligations(self, user_id: uuid.UUID) -> list[dict[str, Any]]:
        query = text(
            "SELECT "
            "  o.id as obligation_id, "
            "  o.name as name, "
            "  COALESCE(op.amount - COALESCE(op.paid_amount, 0), 0) as amount, "
            "  CAST(EXTRACT(DAY FROM op.due_date) AS INTEGER) as due_day, "
            "  o.frequency as frequency, "
            "  true as is_pending "
            "FROM obligation_periods op "
            "JOIN obligations o ON op.obligation_id = o.id "
            "WHERE o.user_id = :user_id "
            "AND op.status IN ('pending_payment', 'partially_paid', 'overdue', 'pending_amount_definition') "
            "ORDER BY op.due_date ASC"
        )
        result = await self.session.execute(query, {"user_id": user_id})
        return [dict(r) for r in result.mappings().all()]
