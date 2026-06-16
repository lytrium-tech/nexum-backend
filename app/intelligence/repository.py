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
            "SELECT COALESCE(SUM(balance), 0) as total_balance, COUNT(id) as active_accounts_count "
            "FROM accounts WHERE user_id = :user_id AND is_active = true"
        )
        result = await self.session.execute(query, {"user_id": user_id})
        return dict(result.mappings().first() or {})

    async def get_cashflow_metrics(
        self, user_id: uuid.UUID, month_start: datetime, next_month_start: datetime
    ) -> dict[str, Any]:
        query = text(
            "SELECT "
            "COALESCE(SUM(CASE WHEN event_type = 'income' THEN amount ELSE 0 END), 0) as income, "
            "COALESCE(SUM(CASE WHEN event_type = 'expense' THEN amount ELSE 0 END), 0) as expenses "
            "FROM financial_events "
            "WHERE user_id = :user_id AND occurred_at >= :start AND occurred_at < :end"
        )
        result = await self.session.execute(
            query, {"user_id": user_id, "start": month_start, "end": next_month_start}
        )
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
        # Pending means: is_active = true AND id NOT IN (SELECT obligation_id FROM obligation_payments WHERE period = :period)
        query = text(
            "SELECT COUNT(id) as pending_count, COALESCE(SUM(amount), 0) as pending_amount "
            "FROM obligations "
            "WHERE user_id = :user_id AND is_active = true "
            "AND id NOT IN (SELECT obligation_id FROM obligation_payments WHERE user_id = :user_id AND period = :period)"
        )
        result = await self.session.execute(query, {"user_id": user_id, "period": period})
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
        query = text("SELECT * FROM public.v_cashflow_current_month WHERE user_id = :user_id")
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
            "SELECT * FROM public.v_pending_obligations_current_month WHERE user_id = :user_id"
        )
        result = await self.session.execute(query, {"user_id": user_id})
        return [dict(r) for r in result.mappings().all()]
