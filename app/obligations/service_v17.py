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
    ObligationPaymentPreviewV17Request,
    ObligationPaymentPreviewV17Response,
    ObligationPeriodAmountDefineRequest,
    ObligationPeriodPaymentCreateRequest,
    ObligationV17CreateRequest,
)


class ObligationV17Service:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.ledger_service = LedgerService(LedgerRepository(session))
        from app.accounts.repository import AccountRepository

        self.account_repo = AccountRepository(session)

    async def create_obligation(
        self, user_id: uuid.UUID, data: ObligationV17CreateRequest
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

        from app.obligations.period_engine import calculate_period_bounds, generate_period_key

        p_start, p_end, p_due = calculate_period_bounds(obligation, 1)
        period_key = generate_period_key(obligation.frequency, p_start, 1)

        return ObligationPeriod(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            period_key=period_key,
            sequence_number=1,
            start_date=p_start,
            end_date=p_end,
            due_date=p_due,
            amount=amount,
            currency=obligation.currency,
            paid_amount=0,
            status=status,
            is_current=True,
        )

    async def list_obligations(self, user_id: uuid.UUID) -> list[Obligation]:
        """List obligations for a given user."""
        stmt = (
            select(Obligation)
            .where(Obligation.user_id == user_id)
            .order_by(Obligation.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_obligation(
        self, user_id: uuid.UUID, obligation_id: uuid.UUID
    ) -> Obligation | None:
        """Get a specific obligation for a user."""
        stmt = select(Obligation).where(
            Obligation.user_id == user_id, Obligation.id == obligation_id
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_periods_for_obligation(
        self, user_id: uuid.UUID, obligation_id: uuid.UUID
    ) -> list[ObligationPeriod] | None:
        """List periods for a given obligation. Returns None if obligation not found or not owned by user."""
        obligation = await self.get_obligation(user_id, obligation_id)
        if not obligation:
            return None

        stmt = (
            select(ObligationPeriod)
            .where(ObligationPeriod.obligation_id == obligation_id)
            .order_by(ObligationPeriod.sequence_number.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_period(
        self, obligation_id: uuid.UUID, period_id: uuid.UUID
    ) -> ObligationPeriod | None:
        """Get a specific period."""
        stmt = select(ObligationPeriod).where(
            ObligationPeriod.obligation_id == obligation_id,
            ObligationPeriod.id == str(period_id),
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def define_period_amount(
        self,
        user_id: uuid.UUID,
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

    async def pay_preview_specific_period(
        self,
        user_id: uuid.UUID,
        obligation_id: uuid.UUID,
        period_id: uuid.UUID,
        data: "ObligationPaymentPreviewV17Request",
    ) -> "ObligationPaymentPreviewV17Response":
        """Preview a specific period payment."""
        from app.obligations.schemas_v17 import ObligationPaymentPreviewV17Response

        obligation = await self.get_obligation(user_id, obligation_id)
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")

        period = await self.get_period(obligation_id, period_id)
        if not period:
            raise HTTPException(status_code=404, detail="period_not_found")

        account = await self.account_repo.get_by_id(data.source_account_id)
        if not account or account.user_id != user_id:
            raise HTTPException(status_code=404, detail="account_not_found")

        from decimal import Decimal

        applied_amount = data.amount

        if account.currency == obligation.currency:
            source_amount = applied_amount
            fx_rate = Decimal("1.0")
            rate_timestamp = None
            quote_expires_at = None
            quote_id = None
        else:
            from app.core.currency import get_fx_rate, round_to_minimum_unit
            from app.fx.provider import StaticFxRateProvider
            from app.fx.service import FXService

            fx_info = await get_fx_rate(account.currency, obligation.currency)
            fx_rate = fx_info["fx_rate"]
            rate_timestamp = fx_info["rate_timestamp"]

            # calculate source_amount = applied_amount / fx_rate
            source_amount = round_to_minimum_unit(applied_amount / fx_rate, account.currency)

            # create a quote so the user can use it
            fx_service = FXService(provider=StaticFxRateProvider(), session=self.session)
            quote = await fx_service.create_quote(
                user_id=user_id,
                source_currency=account.currency,
                target_currency=obligation.currency,
                source_amount=source_amount,
            )

            quote_expires_at = quote.expires_at
            quote_id = quote.quote_id

        return ObligationPaymentPreviewV17Response(
            obligation_currency=obligation.currency,
            source_currency=account.currency,
            applied_amount=applied_amount,
            source_amount=source_amount,
            fx_rate=fx_rate if fx_rate != Decimal("1.0") else None,
            rate_timestamp=rate_timestamp,
            quote_expires_at=quote_expires_at,
            quote_id=quote_id,
        )

    async def pay_specific_period(
        self,
        user_id: uuid.UUID,
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
            ObligationPayment.user_id == user_id,
            ObligationPayment.idempotency_key == data.idempotency_key,
        )
        existing = await self.session.execute(stmt)
        if existing.scalars().first():
            raise HTTPException(status_code=409, detail="idempotency_conflict")

        stmt = (
            select(ObligationPeriod)
            .where(
                ObligationPeriod.obligation_id == obligation_id,
                ObligationPeriod.id == str(period_id),
            )
            .with_for_update()
        )
        period = (await self.session.execute(stmt)).scalars().first()
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
            if not quote or quote.user_id != user_id:
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

        command_id = uuid.uuid5(
            uuid.NAMESPACE_OID, f"obligations_v1_7:{user_id}:{data.idempotency_key}"
        )

        event_create = LedgerEventCreate(
            user_id=user_id,
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
        ledger_result = await self.ledger_service.record_event(event_create)

        if ledger_result.idempotent:
            await self.session.rollback()
            stmt = select(ObligationPayment).where(
                ObligationPayment.idempotency_key == data.idempotency_key
            )
            payment = (await self.session.execute(stmt)).scalars().first()
            if not payment:
                stmt = select(ObligationPayment).where(
                    ObligationPayment.financial_event_id == ledger_result.event.id
                )
                payment = (await self.session.execute(stmt)).scalars().first()
            stmt = select(ObligationPeriod).where(ObligationPeriod.id == str(period_id))
            period = (await self.session.execute(stmt)).scalars().first()
            return payment, period

        if data.source_account_id:
            # Debit the account
            account = await self.account_repo.get_by_id_for_update(data.source_account_id)
            if not account or account.user_id != user_id:
                raise HTTPException(status_code=404, detail="account_not_found")
            if account.balance < source_amount:
                raise HTTPException(status_code=400, detail="insufficient_balance")
            await self.account_repo.update_balance(account, -source_amount)

        payment = ObligationPayment(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            obligation_period_id=period.id,
            user_id=user_id,
            account_id=data.source_account_id,
            financial_event_id=ledger_result.event.id,
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

        await self.session.commit()
        await self.session.refresh(period)
        await self.session.refresh(payment)

        return payment, period

    async def pay_obligation_fifo(
        self,
        user_id: uuid.UUID,
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
            ObligationPayment.user_id == user_id,
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
            if not quote or quote.user_id != user_id:
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
            .with_for_update()
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
        allocations = []
        for p, paid_amt, remaining in period_data:
            if remaining_input <= Decimal("0"):
                break
            payment_slice = min(remaining_input, remaining)
            remaining_input -= payment_slice
            allocations.append((p, paid_amt, payment_slice))

        command_id = uuid.uuid5(
            uuid.NAMESPACE_OID, f"obligations_v1_7:{user_id}:{data.idempotency_key}"
        )

        event_create = LedgerEventCreate(
            user_id=user_id,
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
                    for p, _, slice_amt in allocations
                ],
            },
        )
        ledger_result = await self.ledger_service.record_event(event_create)

        if ledger_result.idempotent:
            await self.session.rollback()
            stmt = select(ObligationPayment).where(
                ObligationPayment.idempotency_key == data.idempotency_key
            )
            payments = list((await self.session.execute(stmt)).scalars().all())
            if not payments:
                stmt = select(ObligationPayment).where(
                    ObligationPayment.financial_event_id == ledger_result.event.id
                )
                payments = list((await self.session.execute(stmt)).scalars().all())

            period_ids = [str(p.obligation_period_id) for p in payments]
            if period_ids:
                stmt = select(ObligationPeriod).where(
                    ObligationPeriod.obligation_id == obligation_id,
                    ObligationPeriod.id.in_(period_ids),
                )
                periods = list((await self.session.execute(stmt)).scalars().all())
            else:
                periods = []
            return payments, periods

        if data.source_account_id:
            # Debit the account
            account = await self.account_repo.get_by_id_for_update(data.source_account_id)
            if not account or account.user_id != user_id:
                raise HTTPException(status_code=404, detail="account_not_found")
            if account.balance < source_amount:
                raise HTTPException(status_code=400, detail="insufficient_balance")
            await self.account_repo.update_balance(account, -source_amount)

        created_payments = []
        updated_periods = []

        for p, paid_amt, payment_slice in allocations:
            payment = ObligationPayment(
                id=uuid.uuid4(),
                obligation_id=obligation.id,
                obligation_period_id=p.id,
                user_id=user_id,
                account_id=data.source_account_id,
                financial_event_id=ledger_result.event.id,
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

        await self.session.commit()
        for p in updated_periods:
            await self.session.refresh(p)
        for payment in created_payments:
            await self.session.refresh(payment)

        return created_payments, updated_periods

    async def skip_period(
        self, user_id: uuid.UUID, obligation_id: uuid.UUID, period_id: uuid.UUID
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
        self, user_id: uuid.UUID, obligation_id: uuid.UUID, period_id: uuid.UUID
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
        self, user_id: uuid.UUID, obligation_id: uuid.UUID
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

    async def get_summary(self, user_id: uuid.UUID, month: str | None = None):
        from datetime import date, datetime
        from decimal import Decimal

        from app.obligations.schemas_v17 import (
            ObligationsV17ActionRequiredItem,
            ObligationsV17CurrencyTotal,
            ObligationsV17StatusBreakdown,
            ObligationsV17SummaryResponse,
        )

        if not month:
            month = date.today().strftime("%Y-%m")

        stmt = (
            select(ObligationPeriod, Obligation)
            .join(Obligation, ObligationPeriod.obligation_id == Obligation.id)
            .where(Obligation.user_id == user_id)
            .where(
                (ObligationPeriod.period_key == month)
                | (
                    ObligationPeriod.status.in_(
                        [
                            PeriodStatus.pending_payment.value,
                            PeriodStatus.partially_paid.value,
                            PeriodStatus.overdue.value,
                            PeriodStatus.pending_amount_definition.value,
                        ]
                    )
                )
            )
        )
        result = await self.session.execute(stmt)
        rows = result.all()

        totals_by_currency_map: dict[str, dict] = {}
        status_counts = ObligationsV17StatusBreakdown()
        requires_action = []

        overdue_count = 0
        pending_def_count = 0

        for period, obligation in rows:
            currency = obligation.currency
            if currency not in totals_by_currency_map:
                totals_by_currency_map[currency] = {
                    "currency": currency,
                    "pending_amount": Decimal("0.00"),
                    "paid_amount": Decimal("0.00"),
                    "overdue_amount": Decimal("0.00"),
                    "period_count": 0,
                }

            c_map = totals_by_currency_map[currency]
            c_map["period_count"] += 1

            # Update status counts
            status_val = period.status
            if status_val == PeriodStatus.pending_payment.value:
                status_counts.pending_payment += 1
            elif status_val == PeriodStatus.partially_paid.value:
                status_counts.partially_paid += 1
            elif status_val == PeriodStatus.paid.value:
                status_counts.paid += 1
            elif status_val == PeriodStatus.overdue.value:
                status_counts.overdue += 1
                overdue_count += 1
            elif status_val == PeriodStatus.pending_amount_definition.value:
                status_counts.pending_amount_definition += 1
                pending_def_count += 1
            elif status_val == PeriodStatus.skipped.value:
                status_counts.skipped += 1
            elif status_val == PeriodStatus.cancelled.value:
                status_counts.cancelled += 1

            amt = period.amount or Decimal("0.00")
            paid = period.paid_amount or Decimal("0.00")
            remaining = amt - paid

            # Do not sum amounts for skipped/cancelled into pending
            if status_val not in (PeriodStatus.skipped.value, PeriodStatus.cancelled.value):
                # paid_amount is always added if there's any paid
                c_map["paid_amount"] += paid

                if status_val == PeriodStatus.overdue.value:
                    c_map["overdue_amount"] += remaining
                    c_map["pending_amount"] += remaining
                elif status_val in (
                    PeriodStatus.pending_payment.value,
                    PeriodStatus.partially_paid.value,
                ):
                    c_map["pending_amount"] += remaining
                elif status_val == PeriodStatus.pending_amount_definition.value:
                    # pending_amount_definition has amount=None, so amt=0. Does not add to totals.
                    requires_action.append(
                        ObligationsV17ActionRequiredItem(
                            obligation_id=obligation.id,
                            period_id=period.id,
                            name=obligation.name,
                            reason=status_val,
                            currency=currency,
                            due_date=period.due_date,
                        )
                    )

        # Build response
        currency_totals = []
        for v in sorted(totals_by_currency_map.values(), key=lambda x: x["currency"]):
            currency_totals.append(
                ObligationsV17CurrencyTotal(
                    currency=v["currency"],
                    pending_amount=f"{v['pending_amount']:.2f}",
                    paid_amount=f"{v['paid_amount']:.2f}",
                    overdue_amount=f"{v['overdue_amount']:.2f}",
                    period_count=v["period_count"],
                )
            )

        # Sort requires_action by due_date asc, then name asc
        requires_action.sort(key=lambda x: (x.due_date, x.name))

        return ObligationsV17SummaryResponse(
            month=month,
            totals_by_currency=currency_totals,
            status_counts=status_counts,
            requires_action=requires_action,
            overdue_count=overdue_count,
            pending_definition_count=pending_def_count,
            generated_at=datetime.utcnow(),
        )

    async def get_intelligence_context(self, user_id: uuid.UUID, month: str | None = None):
        from datetime import datetime

        from app.obligations.schemas_v17 import (
            ObligationsV17IntelligenceContextResponse,
            ObligationsV17IntelligenceRiskFlag,
        )

        # Reuse summary logic entirely
        summary = await self.get_summary(user_id, month)

        risk_flags = []
        narrative_facts = []

        if summary.overdue_count > 0:
            risk_flags.append(
                ObligationsV17IntelligenceRiskFlag(
                    type="overdue_obligations",
                    severity="high" if summary.overdue_count > 2 else "medium",
                    count=summary.overdue_count,
                )
            )
            narrative_facts.append(
                f"User has {summary.overdue_count} overdue obligation period(s)."
            )

        if summary.pending_definition_count > 0:
            risk_flags.append(
                ObligationsV17IntelligenceRiskFlag(
                    type="pending_amount_definition",
                    severity="medium",
                    count=summary.pending_definition_count,
                )
            )
            narrative_facts.append(
                f"There are {summary.pending_definition_count} variable obligation(s) pending amount definition."
            )

        if len(summary.totals_by_currency) > 0:
            currencies = [t.currency for t in summary.totals_by_currency]
            narrative_facts.append(f"User has pending obligations in {', '.join(currencies)}.")

        return ObligationsV17IntelligenceContextResponse(
            month=summary.month,
            financial_load_by_currency=summary.totals_by_currency,
            requires_action=summary.requires_action,
            risk_flags=risk_flags,
            narrative_facts=narrative_facts,
            generated_at=datetime.utcnow(),
        )
