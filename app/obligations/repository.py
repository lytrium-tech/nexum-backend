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
