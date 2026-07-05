import uuid
from typing import Any
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.obligations.enums_v17 import ObligationStatus
from app.obligations.models import Obligation
from app.obligations.schemas_v17 import ObligationV17CreateRequest


class ObligationV17Service:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_obligation(
        self, user_id: str, data: ObligationV17CreateRequest
    ) -> Obligation:
        """
        Creates a minimal V1.7 obligation.
        Does not create periods automatically yet.
        """
        # "payment_mode" is equivalent to amount_type string in V1.5 model but we use amount_type directly too
        # To not break V1.5 fields, we should probably set type, frequency, payment_mode
        # In V1.5:
        # type = indefinite | one_time | installment (we'll map from obligation_type)
        # frequency = monthly | weekly | etc
        # payment_mode = fixed | variable (we'll map from amount_type)
        
        legacy_type = "one_time" if data.obligation_type == "one_time" else "indefinite"
        legacy_payment_mode = data.amount_type.value
        
        # Determine due_day from first_due_date for legacy compatibility
        due_day = data.first_due_date.day
        due_month = data.first_due_date.month

        new_obligation = Obligation(
            id=uuid.uuid4(),
            user_id=user_id,
            name=data.name,
            currency=data.currency,
            type=legacy_type,
            frequency=data.frequency.value,
            payment_mode=legacy_payment_mode,
            amount_type=data.amount_type.value,
            base_amount=data.base_amount,
            start_date=data.start_date,
            first_due_date=data.first_due_date,
            due_day=due_day,
            due_month=due_month,
            interval_count=1,
            end_date=data.end_date,
            end_count=data.end_count,
            status=ObligationStatus.active.value,
            metadata_=data.metadata_ or {},
        )
        
        self.session.add(new_obligation)
        await self.session.flush()
        
        return new_obligation
