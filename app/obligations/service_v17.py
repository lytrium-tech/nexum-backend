import uuid
from typing import Any
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.obligations.enums_v17 import ObligationStatus, PeriodStatus, AmountType
from app.obligations.models import Obligation, ObligationPeriod
from app.obligations.schemas_v17 import ObligationV17CreateRequest, ObligationPeriodAmountDefineRequest
from fastapi import HTTPException


class ObligationV17Service:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_obligation(
        self, user_id: str, data: ObligationV17CreateRequest
    ) -> tuple[Obligation, ObligationPeriod]:
        """
        Creates a minimal V1.7 obligation and its initial period.
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

        # Create initial period
        initial_period = self._create_initial_period(new_obligation)
        self.session.add(initial_period)

        await self.session.flush()

        return new_obligation, initial_period

    def _create_initial_period(self, obligation: Obligation) -> ObligationPeriod:
        """
        Creates the first, minimal obligation period based on the template.
        """
        is_variable = obligation.amount_type == AmountType.variable.value

        # Determine amount and status
        if is_variable:
            amount = None  # Or 0 if decided, but None is semantic for pending definition
            status = PeriodStatus.pending_amount_definition.value
        else:
            amount = obligation.base_amount
            status = PeriodStatus.pending_payment.value

        # Use YYYY-MM based on first_due_date as a simple period key
        period_key = obligation.first_due_date.strftime("%Y-%m")

        return ObligationPeriod(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            period_key=period_key,
            sequence_number=1,
            start_date=obligation.start_date,
            end_date=obligation.first_due_date,  # Simple documented closure logic
            due_date=obligation.first_due_date,
            amount=amount,
            currency=obligation.currency,
            paid_amount=0,
            status=status,
            is_current=True,
        )

    async def list_obligations(self, user_id: str) -> list[Obligation]:
        """List obligations for a given user."""
        stmt = (
            select(Obligation)
            .where(Obligation.user_id == user_id)
            .order_by(Obligation.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_obligation(self, user_id: str, obligation_id: uuid.UUID) -> Obligation | None:
        """Get a specific obligation for a user."""
        stmt = select(Obligation).where(
            Obligation.user_id == user_id, Obligation.id == str(obligation_id)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_periods_for_obligation(
        self, user_id: str, obligation_id: uuid.UUID
    ) -> list[ObligationPeriod] | None:
        """List periods for a given obligation. Returns None if obligation not found or not owned by user."""
        obligation = await self.get_obligation(user_id, obligation_id)
        if not obligation:
            return None

        stmt = (
            select(ObligationPeriod)
            .where(ObligationPeriod.obligation_id == str(obligation_id))
            .order_by(ObligationPeriod.sequence_number.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_period(self, obligation_id: uuid.UUID, period_id: uuid.UUID) -> ObligationPeriod | None:
        """Get a specific period."""
        stmt = select(ObligationPeriod).where(
            ObligationPeriod.obligation_id == str(obligation_id),
            ObligationPeriod.id == str(period_id)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def define_period_amount(
        self, user_id: str, obligation_id: uuid.UUID, period_id: uuid.UUID, data: ObligationPeriodAmountDefineRequest
    ) -> ObligationPeriod:
        """Define the amount for a variable period in pending_amount_definition status."""
        obligation = await self.get_obligation(user_id, obligation_id)
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")

        period = await self.get_period(obligation_id, period_id)
        if not period:
            raise HTTPException(status_code=404, detail="period_not_found")

        if obligation.amount_type != AmountType.variable.value:
            raise HTTPException(status_code=422, detail="period_not_variable")

        if period.status != PeriodStatus.pending_amount_definition.value:
            raise HTTPException(status_code=422, detail="period_amount_already_defined")

        if data.currency and data.currency != obligation.currency:
            raise HTTPException(status_code=422, detail="currency_mismatch")

        period.amount = data.amount
        period.status = PeriodStatus.pending_payment.value

        await self.session.commit()
        await self.session.refresh(period)

        return period
