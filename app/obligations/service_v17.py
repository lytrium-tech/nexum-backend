import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ledger.enums import Direction, EventType
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate
from app.ledger.service import LedgerService
from app.obligations.enums_v17 import AmountType, ObligationStatus, PeriodStatus
from app.obligations.models import Obligation, ObligationPayment, ObligationPeriod
from app.obligations.schemas_v17 import (
    ObligationFIFOPaymentCreateRequest,
    ObligationPeriodAmountDefineRequest,
    ObligationPeriodPaymentCreateRequest,
    ObligationV17CreateRequest,
)


class ObligationV17Service:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.ledger_service = LedgerService(LedgerRepository(session))

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

    async def get_period(
        self, obligation_id: uuid.UUID, period_id: uuid.UUID
    ) -> ObligationPeriod | None:
        """Get a specific period."""
        stmt = select(ObligationPeriod).where(
            ObligationPeriod.obligation_id == str(obligation_id),
            ObligationPeriod.id == str(period_id),
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def define_period_amount(
        self,
        user_id: str,
        obligation_id: uuid.UUID,
        period_id: uuid.UUID,
        data: ObligationPeriodAmountDefineRequest,
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

    async def pay_specific_period(
        self,
        user_id: str,
        obligation_id: uuid.UUID,
        period_id: uuid.UUID,
        data: ObligationPeriodPaymentCreateRequest,
    ) -> tuple[ObligationPayment, ObligationPeriod]:
        """Pay a specific period."""
        obligation = await self.get_obligation(user_id, obligation_id)
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")

        if not data.idempotency_key:
            raise HTTPException(status_code=422, detail="missing_idempotency_key")

        stmt = select(ObligationPayment).where(
            ObligationPayment.user_id == str(user_id),
            ObligationPayment.idempotency_key == data.idempotency_key,
        )
        existing = await self.session.execute(stmt)
        if existing.scalars().first():
            raise HTTPException(status_code=409, detail="idempotency_conflict")

        period = await self.get_period(obligation_id, period_id)
        if not period:
            raise HTTPException(status_code=404, detail="period_not_found")

        if period.amount is None or period.status == PeriodStatus.pending_amount_definition.value:
            raise HTTPException(status_code=422, detail="period_amount_not_defined")

        if period.status not in (
            PeriodStatus.pending_payment.value,
            PeriodStatus.partially_paid.value,
        ):
            raise HTTPException(status_code=422, detail="period_not_payable")

        if data.currency and data.currency != obligation.currency:
            raise HTTPException(status_code=422, detail="currency_mismatch")

        from decimal import Decimal

        if data.source_currency and data.source_currency != obligation.currency:
            if not data.quote_id:
                raise HTTPException(status_code=422, detail="quote_required")
            from app.obligations.models import FXQuote

            stmt = select(FXQuote).where(FXQuote.id == data.quote_id)
            quote = (await self.session.execute(stmt)).scalars().first()
            if not quote or str(quote.user_id) != user_id:
                raise HTTPException(status_code=404, detail="fx_quote_not_found")

            if quote.expires_at < datetime.now(UTC):
                raise HTTPException(status_code=422, detail="expired_fx_quote")

            if (
                quote.from_currency != data.source_currency
                or quote.to_currency != obligation.currency
            ):
                raise HTTPException(status_code=422, detail="invalid_fx_quote")

            if data.source_amount is not None and quote.source_amount != data.source_amount:
                raise HTTPException(status_code=422, detail="invalid_fx_quote")

            amount = quote.target_amount
            source_amount = quote.source_amount
            source_currency = quote.from_currency
            fx_rate = quote.rate
            rate_source = quote.provider
            rate_timestamp = quote.rate_timestamp
            quote_id = quote.id
        else:
            amount = data.amount
            source_amount = data.amount
            source_currency = obligation.currency
            fx_rate = Decimal("1.00000000")
            rate_source = None
            rate_timestamp = None
            quote_id = None

        paid_amt = period.paid_amount or Decimal("0")
        remaining_amount = period.amount - paid_amt
        if amount > remaining_amount:
            raise HTTPException(status_code=422, detail="payment_exceeds_remaining_amount")

        payment = ObligationPayment(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            obligation_period_id=period.id,
            user_id=user_id,
            account_id=data.source_account_id,
            amount=amount,
            currency=obligation.currency,
            source_amount=source_amount,
            source_currency=source_currency,
            fx_rate=fx_rate,
            rate_source=rate_source,
            rate_timestamp=rate_timestamp,
            quote_id=quote_id,
            idempotency_key=data.idempotency_key,
        )
        self.session.add(payment)

        period.paid_amount = paid_amt + amount
        if period.paid_amount < period.amount:
            period.status = PeriodStatus.partially_paid.value
        else:
            period.status = PeriodStatus.paid.value

        command_id = uuid.uuid5(
            uuid.NAMESPACE_OID, f"obligations_v1_7:{user_id}:{data.idempotency_key}"
        )

        event_create = LedgerEventCreate(
            user_id=uuid.UUID(user_id),
            account_id=data.source_account_id,
            event_type=EventType.OBLIGATION_PAYMENT,
            direction=Direction.OUTFLOW,
            amount=source_amount,
            currency=source_currency,
            description=f"Obligation Payment V1.7: {obligation.name}",
            occurred_at=datetime.now(UTC),
            command_id=command_id,
            metadata_={
                "obligation_period_id": str(period.id),
                "fx_quote_id": str(quote_id) if quote_id else None,
                "source_amount": str(source_amount),
                "source_currency": source_currency,
                "fx_rate": str(fx_rate),
            },
        )
        await self.ledger_service.record_event(event_create)

        await self.session.commit()
        await self.session.refresh(period)
        await self.session.refresh(payment)

        return payment, period

    async def pay_obligation_fifo(
        self,
        user_id: str,
        obligation_id: uuid.UUID,
        data: "ObligationFIFOPaymentCreateRequest",
    ) -> tuple[list[ObligationPayment], list[ObligationPeriod]]:
        """Pay obligation using FIFO strategy across periods."""
        obligation = await self.get_obligation(user_id, obligation_id)
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")

        if not data.idempotency_key:
            raise HTTPException(status_code=422, detail="missing_idempotency_key")

        stmt = select(ObligationPayment).where(
            ObligationPayment.user_id == str(user_id),
            ObligationPayment.idempotency_key == data.idempotency_key,
        )
        existing = await self.session.execute(stmt)
        if existing.scalars().first():
            raise HTTPException(status_code=409, detail="idempotency_conflict")

        if data.currency and data.currency != obligation.currency:
            raise HTTPException(status_code=422, detail="currency_mismatch")

        from decimal import Decimal

        if data.source_currency and data.source_currency != obligation.currency:
            if not data.quote_id:
                raise HTTPException(status_code=422, detail="quote_required")

            from app.obligations.models import FXQuote

            stmt = select(FXQuote).where(FXQuote.id == data.quote_id)
            quote = (await self.session.execute(stmt)).scalars().first()
            if not quote or str(quote.user_id) != user_id:
                raise HTTPException(status_code=404, detail="fx_quote_not_found")

            if quote.expires_at < datetime.now(UTC):
                raise HTTPException(status_code=422, detail="expired_fx_quote")

            if (
                quote.from_currency != data.source_currency
                or quote.to_currency != obligation.currency
            ):
                raise HTTPException(status_code=422, detail="invalid_fx_quote")

            if data.source_amount is not None and quote.source_amount != data.source_amount:
                raise HTTPException(status_code=422, detail="invalid_fx_quote")

            amount = quote.target_amount
            source_amount = quote.source_amount
            source_currency = quote.from_currency
            fx_rate = quote.rate
            rate_source = quote.provider
            rate_timestamp = quote.rate_timestamp
            quote_id = quote.id
        else:
            amount = data.amount
            source_amount = data.amount if data.source_amount is None else data.source_amount
            source_currency = obligation.currency
            fx_rate = Decimal("1.00000000")
            rate_source = None
            rate_timestamp = None
            quote_id = None

        # Get payable periods: pending_payment, partially_paid
        # Sort by due_date asc, sequence_number asc, created_at asc
        stmt = (
            select(ObligationPeriod)
            .where(
                ObligationPeriod.obligation_id == obligation_id,
                ObligationPeriod.status.in_(
                    [
                        PeriodStatus.pending_payment.value,
                        PeriodStatus.partially_paid.value,
                    ]
                ),
            )
            .order_by(
                ObligationPeriod.due_date.asc(),
                ObligationPeriod.sequence_number.asc(),
                ObligationPeriod.created_at.asc(),
            )
        )
        periods_result = await self.session.execute(stmt)
        periods = periods_result.scalars().all()

        if not periods:
            raise HTTPException(status_code=422, detail="no_payable_periods")

        from decimal import Decimal

        total_remaining = Decimal("0")
        period_data = []
        for p in periods:
            if p.amount is None or p.status == PeriodStatus.pending_amount_definition.value:
                raise HTTPException(status_code=422, detail="period_amount_not_defined")
            paid_amt = p.paid_amount or Decimal("0")
            remaining = p.amount - paid_amt
            total_remaining += remaining
            period_data.append((p, paid_amt, remaining))

        if amount > total_remaining:
            raise HTTPException(status_code=422, detail="payment_exceeds_total_remaining_amount")

        remaining_input = amount
        created_payments = []
        updated_periods = []
        allocations = []

        for p, paid_amt, remaining in period_data:
            if remaining_input <= Decimal("0"):
                break

            payment_slice = min(remaining_input, remaining)
            remaining_input -= payment_slice

            payment = ObligationPayment(
                id=uuid.uuid4(),
                obligation_id=obligation.id,
                obligation_period_id=p.id,
                user_id=user_id,
                account_id=data.source_account_id,
                amount=payment_slice,
                currency=obligation.currency,
                source_amount=(payment_slice / fx_rate).quantize(Decimal("0.0000")),  # Rough split
                source_currency=source_currency,
                fx_rate=fx_rate,
                rate_source=rate_source,
                rate_timestamp=rate_timestamp,
                quote_id=quote_id,
                idempotency_key=data.idempotency_key,
            )
            self.session.add(payment)
            created_payments.append(payment)

            p.paid_amount = paid_amt + payment_slice
            if p.paid_amount < p.amount:
                p.status = PeriodStatus.partially_paid.value
            else:
                p.status = PeriodStatus.paid.value
            updated_periods.append(p)
            allocations.append((p, payment_slice))

        command_id = uuid.uuid5(
            uuid.NAMESPACE_OID, f"obligations_v1_7:{user_id}:{data.idempotency_key}"
        )

        event_create = LedgerEventCreate(
            user_id=uuid.UUID(user_id),
            account_id=data.source_account_id,
            event_type=EventType.OBLIGATION_PAYMENT,
            direction=Direction.OUTFLOW,
            amount=source_amount,
            currency=source_currency,
            description=f"Obligation FIFO Payment V1.7: {obligation.name}",
            occurred_at=datetime.now(UTC),
            command_id=command_id,
            metadata_={
                "fx_quote_id": str(quote_id) if quote_id else None,
                "source_amount": str(source_amount),
                "source_currency": source_currency,
                "fx_rate": str(fx_rate),
                "allocations": [
                    {"obligation_period_id": str(p.id), "amount": str(slice_amt)}
                    for p, slice_amt in allocations
                ],
            },
        )
        await self.ledger_service.record_event(event_create)

        await self.session.commit()
        for p in updated_periods:
            await self.session.refresh(p)
        for payment in created_payments:
            await self.session.refresh(payment)

        return created_payments, updated_periods

    async def skip_period(
        self, user_id: str, obligation_id: uuid.UUID, period_id: uuid.UUID
    ) -> ObligationPeriod:
        obligation = await self.get_obligation(user_id, obligation_id)
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")

        period = await self.get_period(obligation_id, period_id)
        if not period:
            raise HTTPException(status_code=404, detail="period_not_found")

        if period.status not in (
            PeriodStatus.pending_amount_definition.value,
            PeriodStatus.pending_payment.value,
        ):
            raise HTTPException(status_code=422, detail="period_not_skippable")

        period.status = PeriodStatus.skipped.value
        await self.session.commit()
        await self.session.refresh(period)
        return period

    async def cancel_period(
        self, user_id: str, obligation_id: uuid.UUID, period_id: uuid.UUID
    ) -> ObligationPeriod:
        obligation = await self.get_obligation(user_id, obligation_id)
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")

        period = await self.get_period(obligation_id, period_id)
        if not period:
            raise HTTPException(status_code=404, detail="period_not_found")

        from decimal import Decimal

        paid_amt = period.paid_amount or Decimal("0")
        if paid_amt > 0:
            raise HTTPException(status_code=422, detail="period_has_payments")

        if period.status not in (
            PeriodStatus.pending_amount_definition.value,
            PeriodStatus.pending_payment.value,
            PeriodStatus.overdue.value,
            PeriodStatus.skipped.value,
        ):
            raise HTTPException(status_code=422, detail="period_not_cancellable")

        period.status = PeriodStatus.cancelled.value
        await self.session.commit()
        await self.session.refresh(period)
        return period

    async def refresh_overdue_periods(
        self, user_id: str, obligation_id: uuid.UUID
    ) -> tuple[list[ObligationPeriod], int]:
        obligation = await self.get_obligation(user_id, obligation_id)
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")

        from datetime import date

        today = date.today()

        stmt = select(ObligationPeriod).where(
            ObligationPeriod.obligation_id == obligation_id,
            ObligationPeriod.status.in_(
                [
                    PeriodStatus.pending_payment.value,
                    PeriodStatus.partially_paid.value,
                ]
            ),
            ObligationPeriod.due_date < today,
        )
        periods_result = await self.session.execute(stmt)
        periods = periods_result.scalars().all()

        updated_periods = []
        for p in periods:
            p.status = PeriodStatus.overdue.value
            updated_periods.append(p)

        if updated_periods:
            await self.session.commit()
            for p in updated_periods:
                await self.session.refresh(p)

        return updated_periods, len(updated_periods)
