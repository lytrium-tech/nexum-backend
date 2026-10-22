from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.obligations.enums_v17 import PeriodStatus
from app.obligations.models import (
    ObligationAutoPaymentConfig,
    ObligationAutoPaymentRun,
    ObligationPeriod,
)


class ObligationAutoPaymentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_config(
        self, obligation_id: UUID, user_id: UUID | None = None
    ) -> ObligationAutoPaymentConfig | None:
        stmt = select(ObligationAutoPaymentConfig).where(
            ObligationAutoPaymentConfig.obligation_id == obligation_id
        )
        if user_id is not None:
            stmt = stmt.where(ObligationAutoPaymentConfig.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_config_for_update(self, config_id: UUID) -> ObligationAutoPaymentConfig | None:
        stmt = (
            select(ObligationAutoPaymentConfig)
            .where(ObligationAutoPaymentConfig.id == config_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_next_eligible_config_for_update(
        self, as_of: datetime, excluded_config_ids: list[UUID] | None = None
    ) -> ObligationAutoPaymentConfig | None:
        """Claim the next active configuration whose evaluation is due using SKIP LOCKED."""
        stmt = select(ObligationAutoPaymentConfig).where(
            ObligationAutoPaymentConfig.enabled.is_(True),
            ObligationAutoPaymentConfig.status == "enabled",
            or_(
                ObligationAutoPaymentConfig.next_evaluation_at.is_(None),
                ObligationAutoPaymentConfig.next_evaluation_at <= as_of,
            ),
        )
        if excluded_config_ids:
            stmt = stmt.where(ObligationAutoPaymentConfig.id.not_in(excluded_config_ids))

        stmt = (
            stmt.order_by(
                ObligationAutoPaymentConfig.next_evaluation_at.asc().nullsfirst(),
                ObligationAutoPaymentConfig.created_at.asc(),
                ObligationAutoPaymentConfig.id.asc(),
            )
            .limit(1)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_eligible_configs(
        self, as_of: datetime, limit: int = 50
    ) -> list[ObligationAutoPaymentConfig]:
        """List active configurations whose evaluation is due."""
        stmt = (
            select(ObligationAutoPaymentConfig)
            .where(
                ObligationAutoPaymentConfig.enabled.is_(True),
                ObligationAutoPaymentConfig.status == "enabled",
                or_(
                    ObligationAutoPaymentConfig.next_evaluation_at.is_(None),
                    ObligationAutoPaymentConfig.next_evaluation_at <= as_of,
                ),
            )
            .order_by(
                ObligationAutoPaymentConfig.next_evaluation_at.asc().nullsfirst(),
                ObligationAutoPaymentConfig.created_at.asc(),
            )
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_oldest_due_open_period(
        self, obligation_id: UUID, canonical_today: date
    ) -> ObligationPeriod | None:
        """Find the oldest open period with due_date <= canonical_today."""
        open_statuses = [
            PeriodStatus.pending_payment.value,
            PeriodStatus.partially_paid.value,
            PeriodStatus.overdue.value,
            PeriodStatus.pending_amount_definition.value,
        ]
        stmt = (
            select(ObligationPeriod)
            .where(
                ObligationPeriod.obligation_id == obligation_id,
                ObligationPeriod.status.in_(open_statuses),
                ObligationPeriod.due_date <= canonical_today,
            )
            .order_by(
                ObligationPeriod.due_date.asc(),
                ObligationPeriod.sequence_number.asc(),
                ObligationPeriod.created_at.asc(),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_run_by_attempt_key(self, attempt_key: str) -> ObligationAutoPaymentRun | None:
        stmt = select(ObligationAutoPaymentRun).where(
            ObligationAutoPaymentRun.attempt_key == attempt_key
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_runs_paginated(
        self, obligation_id: UUID, limit: int = 50, offset: int = 0
    ) -> tuple[list[ObligationAutoPaymentRun], int]:
        total_stmt = (
            select(func.count())
            .select_from(ObligationAutoPaymentRun)
            .where(ObligationAutoPaymentRun.obligation_id == obligation_id)
        )
        total_res = await self.session.execute(total_stmt)
        total = total_res.scalar() or 0

        items_stmt = (
            select(ObligationAutoPaymentRun)
            .where(ObligationAutoPaymentRun.obligation_id == obligation_id)
            .order_by(
                ObligationAutoPaymentRun.executed_at.desc(),
                ObligationAutoPaymentRun.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        items_res = await self.session.execute(items_stmt)
        items = list(items_res.scalars().all())
        return items, total

    async def create_config(
        self, config: ObligationAutoPaymentConfig
    ) -> ObligationAutoPaymentConfig:
        self.session.add(config)
        await self.session.flush()
        return config

    async def create_run(self, run: ObligationAutoPaymentRun) -> ObligationAutoPaymentRun:
        self.session.add(run)
        await self.session.flush()
        return run
