import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class IntelligenceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

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
