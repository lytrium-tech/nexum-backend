from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import normalize_name
from app.obligations.models import Obligation, ObligationPayment


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

    async def list_active(self, user_id: UUID) -> list[Obligation]:
        result = await self.session.execute(
            select(Obligation)
            .where(Obligation.user_id == user_id)
            .where(Obligation.is_active)
            .order_by(Obligation.created_at.desc())
        )
        return list(result.scalars().all())

    async def check_name_exists(self, user_id: UUID, norm_name: str) -> bool:
        result = await self.session.execute(
            select(Obligation).where(Obligation.user_id == user_id).where(Obligation.is_active)
        )
        for obligation in result.scalars().all():
            if normalize_name(obligation.name) == norm_name:
                return True
        return False

    async def get_period_payments(self, user_id: UUID, period: str) -> dict[UUID, Decimal]:
        from sqlalchemy import func

        stmt = (
            select(ObligationPayment.obligation_id, func.sum(ObligationPayment.amount))
            .where(ObligationPayment.user_id == user_id)
            .where(ObligationPayment.period == period)
            .group_by(ObligationPayment.obligation_id)
        )
        result = await self.session.execute(stmt)
        return {row[0]: Decimal(str(row[1] or 0)) for row in result.all()}

    async def create(self, obligation: Obligation) -> Obligation:
        self.session.add(obligation)
        await self.session.flush()
        return obligation

    async def create_payment(self, payment: ObligationPayment) -> ObligationPayment:
        self.session.add(payment)
        await self.session.flush()
        return payment
