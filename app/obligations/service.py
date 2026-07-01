import uuid
from uuid import UUID
from datetime import datetime

from app.obligations.models import Obligation
from app.obligations.repository import ObligationRepository
from app.obligations.schemas import ObligationCreate, ObligationRead, ObligationUpdate
from app.core.errors import NotFoundError


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
