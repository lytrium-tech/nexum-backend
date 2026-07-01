from datetime import datetime
from uuid import UUID

from app.core.errors import NotFoundError
from app.obligations.models import Obligation, ObligationPeriod
from app.obligations.period_engine import PeriodEngine
from app.obligations.repository import ObligationRepository
from app.obligations.schemas import ObligationCreate, ObligationPeriodRead, ObligationRead


class ObligationService:
    def __init__(self, repository: ObligationRepository):
        self.repository = repository

    async def list_obligations(self, auth_user_id: UUID, include_archived: bool = False) -> list[ObligationRead]:
        obligations = await self.repository.list_by_user(auth_user_id, include_archived=include_archived)
        return [ObligationRead.model_validate(o) for o in obligations]

    async def get_obligation(self, auth_user_id: UUID, obligation_id: UUID) -> ObligationRead:
        obligation = await self.repository.get_by_id(obligation_id)
        if not obligation:
            raise NotFoundError("Obligation not found")
        return ObligationRead.model_validate(obligation)

    async def create_obligation(self, auth_user_id: UUID, payload: ObligationCreate) -> ObligationRead:
        obligation = Obligation(
            user_id=auth_user_id,
            name=payload.name,
            description=payload.description,
            category_id=payload.category_id,
            currency=payload.currency,
            type=payload.type,
            frequency=payload.frequency,
            payment_mode=payload.payment_mode,
            base_amount=payload.base_amount,
            start_date=payload.start_date,
            first_due_date=payload.first_due_date,
            due_day=payload.due_day,
            due_month=payload.due_month,
            interval_count=payload.interval_count,
            end_date=payload.end_date,
            end_count=payload.end_count,
            status="active",
            metadata_=payload.metadata
        )
        created = await self.repository.create(obligation)
        return ObligationRead.model_validate(created)

    async def list_periods(self, auth_user_id: UUID, obligation_id: UUID) -> list[ObligationPeriodRead]:
        obligation = await self.repository.get_by_id(obligation_id)
        if not obligation or obligation.user_id != auth_user_id:
            raise NotFoundError("Obligation not found")
        
        periods = await self.repository.session.execute(
            __import__('sqlalchemy').select(ObligationPeriod).where(ObligationPeriod.obligation_id == obligation_id).order_by(ObligationPeriod.sequence_number)
        )
        return [ObligationPeriodRead.model_validate(p) for p in periods.scalars().all()]

    async def sync_periods(self, auth_user_id: UUID, obligation_id: UUID) -> list[ObligationPeriodRead]:
        obligation = await self.repository.get_by_id(obligation_id)
        if not obligation or obligation.user_id != auth_user_id:
            raise NotFoundError("Obligation not found")
        
        engine = PeriodEngine(self.repository.session)
        await engine.sync_periods(obligation, datetime.now().date())
        
        return await self.list_periods(auth_user_id, obligation_id)

    async def skip_period(self, auth_user_id: UUID, period_id: UUID) -> ObligationPeriodRead:
        period = await self.repository.session.execute(
            __import__('sqlalchemy').select(ObligationPeriod).where(ObligationPeriod.id == period_id)
        )
        period = period.scalar_one_or_none()
        if not period:
            raise NotFoundError("Period not found")
            
        obligation = await self.repository.get_by_id(period.obligation_id)
        if not obligation or obligation.user_id != auth_user_id:
            raise NotFoundError("Obligation not found")
            
        engine = PeriodEngine(self.repository.session)
        skipped = await engine.skip_period(period_id)
        return ObligationPeriodRead.model_validate(skipped)
