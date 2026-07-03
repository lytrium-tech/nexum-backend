from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.obligations.models import Obligation, ObligationPayment, ObligationPeriod


class ObligationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, obligation_id: UUID) -> Obligation | None:
        result = await self.session.execute(
            select(Obligation).where(Obligation.id == obligation_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, obligation_id: UUID) -> Obligation | None:
        result = await self.session.execute(
            select(Obligation).where(Obligation.id == obligation_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_by_user(self, user_id: UUID, include_archived: bool = False) -> list[Obligation]:
        stmt = select(Obligation).where(Obligation.user_id == user_id)
        if not include_archived:
            stmt = stmt.where(Obligation.status != "archived")
        stmt = stmt.order_by(Obligation.created_at.desc())

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, obligation: Obligation) -> Obligation:
        self.session.add(obligation)
        await self.session.flush()
        return obligation

    async def create_period(self, period: ObligationPeriod) -> ObligationPeriod:
        self.session.add(period)
        await self.session.flush()
        return period

    async def create_payment(self, payment: ObligationPayment) -> ObligationPayment:
        self.session.add(payment)
        await self.session.flush()
        return payment

    async def get_pending_period_amounts_for_snapshot(self, user_id: UUID) -> dict[str, "Decimal"]:
        """
        Returns a dictionary mapping currency code to the sum of pending amounts
        for all relevant periods (overdue, pending_payment, partially_paid).
        """
        from decimal import Decimal

        from sqlalchemy import func

        stmt = (
            select(
                Obligation.currency,
                func.sum(
                    ObligationPeriod.amount
                    - func.coalesce(ObligationPeriod.paid_amount, Decimal("0.00"))
                ).label("total_pending"),
            )
            .select_from(Obligation)
            .join(ObligationPeriod, Obligation.id == ObligationPeriod.obligation_id)
            .where(Obligation.user_id == user_id)
            .where(ObligationPeriod.status.in_(["overdue", "pending_payment", "partially_paid"]))
            .where(ObligationPeriod.amount.is_not(None))
            .group_by(Obligation.currency)
        )

        result = await self.session.execute(stmt)
        return {
            row.currency.upper(): (
                row.total_pending if row.total_pending is not None else Decimal("0.00")
            )
            for row in result.all()
        }
