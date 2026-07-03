import uuid
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from app.accounts.repository import AccountRepository
from app.core.currency import (
    get_fx_rate,
    round_to_minimum_unit,
)
from app.core.errors import NotFoundError
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate
from app.ledger.service import LedgerService
from app.obligations.exceptions import (
    ObligationPaymentExceedsBalanceError,
    ObligationPeriodAmountRequiredError,
)
from app.obligations.models import Obligation, ObligationPayment, ObligationPeriod
from app.obligations.period_engine import PeriodEngine
from app.obligations.repository import ObligationRepository
from app.obligations.schemas import (
    ObligationCreate,
    ObligationPaymentCreate,
    ObligationPaymentPreviewCreate,
    ObligationPaymentPreviewRead,
    ObligationPaymentRead,
    ObligationPeriodAmountUpdate,
    ObligationPeriodRead,
    ObligationRead,
)


class ObligationService:
    def __init__(self, repository: ObligationRepository):
        self.repository = repository
        self.account_repo = AccountRepository(self.repository.session)
        self.ledger_service = LedgerService(LedgerRepository(self.repository.session))

    async def list_obligations(
        self, auth_user_id: UUID, include_archived: bool = False
    ) -> list[ObligationRead]:
        obligations = await self.repository.list_by_user(
            auth_user_id, include_archived=include_archived
        )
        return [ObligationRead.model_validate(o) for o in obligations]

    async def get_obligation(self, auth_user_id: UUID, obligation_id: UUID) -> ObligationRead:
        obligation = await self.repository.get_by_id(obligation_id)
        if not obligation:
            raise NotFoundError("Obligation not found")
        return ObligationRead.model_validate(obligation)

    async def create_obligation(
        self, auth_user_id: UUID, payload: ObligationCreate
    ) -> ObligationRead:
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
            metadata_=payload.metadata,
        )
        created = await self.repository.create(obligation)
        return ObligationRead.model_validate(created)

    async def list_periods(
        self, auth_user_id: UUID, obligation_id: UUID
    ) -> list[ObligationPeriodRead]:
        obligation = await self.repository.get_by_id(obligation_id)
        if not obligation or obligation.user_id != auth_user_id:
            raise NotFoundError("Obligation not found")

        periods = await self.repository.session.execute(
            select(ObligationPeriod)
            .where(ObligationPeriod.obligation_id == obligation_id)
            .order_by(ObligationPeriod.sequence_number)
        )
        return [ObligationPeriodRead.model_validate(p) for p in periods.scalars().all()]

    async def evaluate_lifecycle(self, obligation_id: UUID) -> None:
        obligation = await self.repository.get_by_id(obligation_id)
        if not obligation or obligation.status in ("completed", "archived", "cancelled"):
            return

        if obligation.type == "indefinite":
            return

        stmt = select(ObligationPeriod).where(ObligationPeriod.obligation_id == obligation_id)
        periods = (await self.repository.session.execute(stmt)).scalars().all()

        if not periods:
            return

        if obligation.type == "one_time":
            if all(p.status in ("paid", "skipped", "cancelled") for p in periods):
                obligation.status = "completed"

        elif obligation.type == "end_count":
            req_count = obligation.end_count
            if len(periods) >= req_count:
                req_periods = [p for p in periods if p.sequence_number <= req_count]
                if len(req_periods) == req_count and all(
                    p.status in ("paid", "skipped", "cancelled") for p in req_periods
                ):
                    obligation.status = "completed"

        elif obligation.type == "end_date":
            if all(p.status in ("paid", "skipped", "cancelled") for p in periods):
                last_p = max(periods, key=lambda p: p.sequence_number)
                from app.obligations.period_engine import calculate_period_bounds

                next_start, _, _ = calculate_period_bounds(obligation, last_p.sequence_number + 1)
                if next_start > obligation.end_date:
                    obligation.status = "completed"

    async def sync_periods(
        self, auth_user_id: UUID, obligation_id: UUID
    ) -> list[ObligationPeriodRead]:
        obligation = await self.repository.get_by_id(obligation_id)
        if not obligation or obligation.user_id != auth_user_id:
            raise NotFoundError("Obligation not found")

        engine = PeriodEngine(self.repository.session)
        await engine.sync_periods(obligation, datetime.now().date())

        await self.evaluate_lifecycle(obligation_id)
        return await self.list_periods(auth_user_id, obligation_id)

    async def skip_period(self, auth_user_id: UUID, period_id: UUID) -> ObligationPeriodRead:
        period = await self.repository.session.execute(
            select(ObligationPeriod).where(ObligationPeriod.id == period_id)
        )
        period = period.scalar_one_or_none()
        if not period:
            raise NotFoundError("Period not found")

        obligation = await self.repository.get_by_id(period.obligation_id)
        if not obligation or obligation.user_id != auth_user_id:
            raise NotFoundError("Obligation not found")

        engine = PeriodEngine(self.repository.session)
        skipped = await engine.skip_period(period_id)

        await self.evaluate_lifecycle(obligation.id)
        return ObligationPeriodRead.model_validate(skipped)

    async def define_amount(
        self, auth_user_id: UUID, period_id: UUID, payload: ObligationPeriodAmountUpdate
    ) -> ObligationPeriodRead:
        stmt = select(ObligationPeriod).where(ObligationPeriod.id == period_id).with_for_update()
        period = (await self.repository.session.execute(stmt)).scalar_one_or_none()
        if not period:
            raise NotFoundError("Period not found")

        obligation = await self.repository.get_by_id(period.obligation_id)
        if not obligation or obligation.user_id != auth_user_id:
            raise NotFoundError("Obligation not found")

        if obligation.payment_mode not in ("variable", "variable_amount"):
            raise ValueError("Only variable obligations can define amount")

        if period.status in ("paid", "skipped", "cancelled"):
            raise ValueError(f"Cannot change amount for period in status {period.status}")

        if payload.amount < period.paid_amount:
            raise ValueError("Amount cannot be less than already paid amount")

        period.amount = payload.amount
        if period.status == "pending_amount_definition":
            if period.due_date < datetime.now().date():
                period.status = "overdue"
            else:
                period.status = "pending_payment"

        # If it had partial payments from before and the new amount matches the paid amount, it might become paid.
        if period.paid_amount >= period.amount and period.amount > 0:
            period.status = "paid"

        await self.repository.session.flush()
        await self.evaluate_lifecycle(obligation.id)

        return ObligationPeriodRead.model_validate(period)

    async def _calculate_fx_and_amounts(
        self,
        account_currency: str,
        obligation_currency: str,
        payload_amount: Decimal | None,
        required_obligation_amount: Decimal,
    ) -> tuple[Decimal, Decimal, Decimal, str | None, datetime | None]:
        if account_currency == obligation_currency:
            if payload_amount is None:
                source_amount = required_obligation_amount
                applied_amount = required_obligation_amount
            else:
                source_amount = payload_amount
                applied_amount = payload_amount
            return source_amount, applied_amount, Decimal("1.0"), None, None

        # Cross currency
        fx_info = await get_fx_rate(account_currency, obligation_currency)
        fx_rate = fx_info["fx_rate"]
        rate_source = fx_info["rate_source"]
        rate_timestamp = fx_info["rate_timestamp"]

        if payload_amount is None:
            # We know exactly how much obligation currency we need
            applied_amount = required_obligation_amount
            source_amount = round_to_minimum_unit(applied_amount / fx_rate, account_currency)
        else:
            # User specified applied amount in obligation currency
            applied_amount = payload_amount
            source_amount = round_to_minimum_unit(applied_amount / fx_rate, account_currency)

        return source_amount, applied_amount, fx_rate, rate_source, rate_timestamp

    async def pay_preview_specific_period(
        self, auth_user_id: UUID, period_id: UUID, payload: ObligationPaymentPreviewCreate
    ) -> ObligationPaymentPreviewRead:
        stmt = select(ObligationPeriod).where(ObligationPeriod.id == period_id)
        result = await self.repository.session.execute(stmt)
        period = result.scalar_one_or_none()
        if not period:
            raise NotFoundError("Period not found")

        obligation = await self.repository.get_by_id(period.obligation_id)
        if not obligation or obligation.user_id != auth_user_id:
            raise NotFoundError("Obligation not found")

        if period.status == "pending_amount_definition":
            raise ObligationPeriodAmountRequiredError()
        if period.status in ("paid", "skipped", "cancelled"):
            raise ValueError("Period is not in a payable state")

        remaining_balance = period.remaining_amount
        if remaining_balance is None:
            raise ValueError("Period has no remaining amount to calculate preview")

        account = await self.account_repo.get_by_id(payload.account_id)
        if not account or account.user_id != auth_user_id:
            raise NotFoundError("Account not found")

        source_currency = account.currency
        (
            source_amount,
            applied_amount,
            fx_rate,
            rate_source,
            rate_timestamp,
        ) = await self._calculate_fx_and_amounts(
            source_currency, obligation.currency, payload.amount, remaining_balance
        )

        if applied_amount > remaining_balance:
            raise ObligationPaymentExceedsBalanceError(remaining=str(remaining_balance))

        can_pay = account.balance >= source_amount
        # Note: Do not throw error for insufficient funds in preview, just set can_pay to False

        quote_expires_at = datetime.now() if fx_rate != Decimal("1.0") else None

        return ObligationPaymentPreviewRead(
            period_id=period.id,
            account_id=account.id,
            obligation_currency=obligation.currency,
            source_currency=source_currency,
            requested_amount=payload.amount if payload.amount is not None else remaining_balance,
            applied_amount=applied_amount,
            source_amount=source_amount,
            fx_rate=fx_rate if fx_rate != Decimal("1.0") else None,
            is_estimated=(fx_rate != Decimal("1.0")),
            rate_source=rate_source,
            rate_timestamp=rate_timestamp,
            quote_expires_at=quote_expires_at,
            remaining_amount=remaining_balance,
            can_pay=can_pay,
        )

    async def pay_specific_period(
        self, auth_user_id: UUID, period_id: UUID, payload: ObligationPaymentCreate
    ) -> ObligationPaymentRead:
        # 1. Load Period
        stmt = select(ObligationPeriod).where(ObligationPeriod.id == period_id).with_for_update()
        result = await self.repository.session.execute(stmt)
        period = result.scalar_one_or_none()
        if not period:
            raise NotFoundError("Period not found")

        # 2. Load Obligation
        obligation = await self.repository.get_by_id(period.obligation_id)
        if not obligation or obligation.user_id != auth_user_id:
            raise NotFoundError("Obligation not found")

        # 3. Validations
        if period.status == "pending_amount_definition":
            raise ObligationPeriodAmountRequiredError()
        if period.status in ("paid", "skipped", "cancelled"):
            raise ValueError("Period is not in a payable state")

        remaining_balance = period.amount - period.paid_amount

        # 4. Load Account
        account = await self.account_repo.get_by_id_for_update(payload.account_id)
        if not account or account.user_id != auth_user_id:
            raise NotFoundError("Account not found")

        # 5. Determine FX and Amounts
        source_currency = payload.currency or account.currency
        (
            source_amount,
            applied_amount,
            fx_rate,
            rate_source,
            rate_timestamp,
        ) = await self._calculate_fx_and_amounts(
            source_currency, obligation.currency, payload.amount, remaining_balance
        )

        if applied_amount > remaining_balance:
            raise ObligationPaymentExceedsBalanceError(remaining=str(remaining_balance))

        # 6. Check Balance
        if account.balance < source_amount:
            raise ValueError("Insufficient balance")

        # 7. Create Financial Event
        event_payload = LedgerEventCreate(
            user_id=auth_user_id,
            event_type="obligation_payment",
            direction="outflow",
            amount=source_amount,
            currency=source_currency,
            account_id=account.id,
            source_message_id=payload.source_message_id,
            raw_message=payload.raw_message,
            metadata={
                "obligation_id": str(obligation.id),
                "obligation_period_id": str(period.id),
                "fx_rate": str(fx_rate),
                "rate_source": rate_source,
                "rate_timestamp": rate_timestamp.isoformat() if rate_timestamp else None,
                "applied_amount": str(applied_amount),
                "applied_currency": obligation.currency,
            },
        )
        result = await self.ledger_service.record_event(event_payload)
        event, is_retry = result.event, result.idempotent

        # 8. Update Period
        if not is_retry:
            period.paid_amount += applied_amount
            if period.paid_amount >= period.amount:
                period.status = "paid"
            else:
                period.status = "partially_paid"

            payment = ObligationPayment(
                id=uuid.uuid4(),
                obligation_id=obligation.id,
                obligation_period_id=period.id,
                account_id=account.id,
                financial_event_id=event.id,
                amount=applied_amount,
                currency=obligation.currency,
                source_amount=source_amount,
                source_currency=source_currency,
                fx_rate=fx_rate if fx_rate != Decimal("1.0") else None,
                rate_source=rate_source,
                rate_timestamp=rate_timestamp,
                paid_at=datetime.now(),
                created_at=datetime.now(),
            )
            self.repository.session.add(payment)
        else:
            stmt = select(ObligationPayment).where(ObligationPayment.financial_event_id == event.id)
            payment = (await self.repository.session.execute(stmt)).scalar_one()

        await self.evaluate_lifecycle(obligation.id)
        return ObligationPaymentRead.model_validate(payment)

    async def pay_obligation_fifo(
        self, auth_user_id: UUID, obligation_id: UUID, payload: ObligationPaymentCreate
    ) -> list[ObligationPaymentRead]:
        obligation = await self.repository.get_by_id(obligation_id)
        if not obligation or obligation.user_id != auth_user_id:
            raise NotFoundError("Obligation not found")

        # 1. Sync periods to ensure we have the latest generated periods
        engine = PeriodEngine(self.repository.session)
        await engine.sync_periods(obligation, datetime.now().date())

        # 2. Get all unpaid periods for this obligation
        stmt = (
            select(ObligationPeriod)
            .where(
                ObligationPeriod.obligation_id == obligation.id,
                ObligationPeriod.status.in_(["overdue", "pending_payment", "partially_paid"]),
            )
            .order_by(
                # overdue first, then ordered by sequence_number
                ObligationPeriod.status != "overdue",
                ObligationPeriod.sequence_number,
            )
            .with_for_update()
        )
        result = await self.repository.session.execute(stmt)
        periods = result.scalars().all()

        total_debt = sum(p.amount - p.paid_amount for p in periods)

        # 3. Load Account
        account = await self.account_repo.get_by_id_for_update(payload.account_id)
        if not account or account.user_id != auth_user_id:
            raise NotFoundError("Account not found")

        source_currency = payload.currency or account.currency
        (
            source_amount,
            applied_amount,
            fx_rate,
            rate_source,
            rate_timestamp,
        ) = await self._calculate_fx_and_amounts(
            source_currency, obligation.currency, payload.amount, total_debt
        )

        if applied_amount > total_debt:
            raise ObligationPaymentExceedsBalanceError(remaining=str(total_debt))

        if account.balance < source_amount:
            raise ValueError("Insufficient balance")

        payments_created = []
        remaining_to_apply = applied_amount
        remaining_source = source_amount

        for i, period in enumerate(periods):
            if remaining_to_apply <= 0:
                break

            period_remaining = period.amount - period.paid_amount
            if period_remaining <= 0:
                continue

            apply_to_period = min(period_remaining, remaining_to_apply)

            # calculate source amount proportional to applied amount
            # If it's the last period we are applying to, we just use the rest of the source_amount to avoid rounding issues
            if remaining_to_apply == apply_to_period:
                source_to_period = remaining_source
            else:
                if fx_rate == Decimal("1.0"):
                    source_to_period = apply_to_period
                else:
                    source_to_period = round_to_minimum_unit(
                        apply_to_period / fx_rate, source_currency
                    )

            # Create event for each chunk
            event_payload = LedgerEventCreate(
                user_id=auth_user_id,
                event_type="obligation_payment",
                direction="outflow",
                amount=source_to_period,
                currency=source_currency,
                account_id=account.id,
                source_message_id=payload.source_message_id,
                raw_message=payload.raw_message,
                metadata={
                    "obligation_id": str(obligation.id),
                    "obligation_period_id": str(period.id),
                    "fx_rate": str(fx_rate),
                    "rate_source": rate_source,
                    "rate_timestamp": rate_timestamp.isoformat() if rate_timestamp else None,
                    "applied_amount": str(apply_to_period),
                    "applied_currency": obligation.currency,
                    "fifo_index": str(i),
                },
            )
            # Give a unique command ID based on period id if multiple payments in same message
            if payload.source_message_id:
                event_payload.command_id = f"cmd_pay_{payload.source_message_id}_{period.id}"

            result = await self.ledger_service.record_event(event_payload)
            event, is_retry = result.event, result.idempotent

            if not is_retry:
                period.paid_amount += apply_to_period
                if period.paid_amount >= period.amount:
                    period.status = "paid"
                else:
                    period.status = "partially_paid"

                payment = ObligationPayment(
                    id=uuid.uuid4(),
                    obligation_id=obligation.id,
                    obligation_period_id=period.id,
                    account_id=account.id,
                    financial_event_id=event.id,
                    amount=apply_to_period,
                    currency=obligation.currency,
                    source_amount=source_to_period,
                    source_currency=source_currency,
                    fx_rate=fx_rate if fx_rate != Decimal("1.0") else None,
                    rate_source=rate_source,
                    rate_timestamp=rate_timestamp,
                    paid_at=datetime.now(),
                    created_at=datetime.now(),
                )
                self.repository.session.add(payment)
                payments_created.append(payment)
            else:
                stmt = select(ObligationPayment).where(
                    ObligationPayment.financial_event_id == event.id
                )
                payment = (await self.repository.session.execute(stmt)).scalar_one()
                payments_created.append(payment)

            remaining_to_apply -= apply_to_period
            remaining_source -= source_to_period

            # Since ledger_service flushes, no need to manually flush inside loop if we don't need IDs immediately,
            # but ledger does need it.

        await self.evaluate_lifecycle(obligation.id)
        return [ObligationPaymentRead.model_validate(p) for p in payments_created]
