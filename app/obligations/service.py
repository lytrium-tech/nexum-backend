import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.accounts.repository import AccountRepository
from app.core.currency import (
    get_fx_rate,
    round_to_minimum_unit,
)
from app.core.errors import NotFoundError
from app.ledger.models import FinancialEvent
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate
from app.ledger.service import LedgerService
from app.obligations.exceptions import (
    ObligationAccountInactiveError,
    ObligationAccountUnavailableError,
    ObligationBelowMinimumAmountError,
    ObligationCurrencyMismatchError,
    ObligationDomainError,
    ObligationIdempotencyConflictDomainError,
    ObligationInactiveError,
    ObligationInconsistentReservationError,
    ObligationInsufficientBalanceError,
    ObligationInvalidAmountError,
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
from app.obligations.settlement import reconcile_fifo_source_allocations


def _parse_source_message_id(source_message_id: Any) -> UUID | None:
    if not source_message_id:
        return None
    if isinstance(source_message_id, UUID):
        return source_message_id
    try:
        return UUID(str(source_message_id))
    except (ValueError, AttributeError):
        return uuid.uuid5(uuid.NAMESPACE_OID, str(source_message_id))


def _get_event_metadata(event: Any) -> dict:
    meta = getattr(event, "metadata_", None)
    if isinstance(meta, dict):
        return meta
    meta = getattr(event, "metadata", None)
    if isinstance(meta, dict):
        return meta
    return {}


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
        if not obligation or obligation.user_id != auth_user_id:
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
        stmt = (
            select(ObligationPeriod)
            .where(ObligationPeriod.id == period_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
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
        account_curr = account_currency.upper()
        obligation_curr = obligation_currency.upper()

        if payload_amount is not None:
            if not payload_amount.is_finite() or payload_amount <= Decimal("0"):
                raise ObligationInvalidAmountError("Payment amount must be finite and positive")
            applied_amount = payload_amount
        else:
            if not required_obligation_amount.is_finite() or required_obligation_amount <= Decimal(
                "0"
            ):
                raise ObligationInvalidAmountError("Required amount must be finite and positive")
            applied_amount = required_obligation_amount

        if account_curr == obligation_curr:
            source_amount = applied_amount
            return source_amount, applied_amount, Decimal("1.0"), None, None

        # Cross currency
        fx_info = await get_fx_rate(account_curr, obligation_curr)
        fx_rate = fx_info["fx_rate"]
        rate_source = fx_info["rate_source"]
        rate_timestamp = fx_info["rate_timestamp"]

        source_amount = round_to_minimum_unit(applied_amount / fx_rate, account_curr)
        if not source_amount.is_finite() or source_amount <= Decimal("0"):
            raise ObligationBelowMinimumAmountError(
                "Conversion resulted in zero or below-minimum source amount"
            )

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
            raise ObligationDomainError(
                "Period is not in a payable state", error_code="period_not_payable"
            )

        remaining_balance = period.remaining_amount
        if remaining_balance is None:
            raise ObligationDomainError(
                "Period has no remaining amount to calculate preview",
                error_code="period_not_payable",
            )

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

    async def _check_source_message_replay(
        self,
        auth_user_id: UUID,
        payload: ObligationPaymentCreate,
        expected_obligation_id: UUID,
        expected_period_id: UUID | None,
        strategy: str,
    ) -> list[ObligationPayment] | None:
        sm_uuid = _parse_source_message_id(payload.source_message_id)
        if not sm_uuid:
            return None

        # Query across obligations by user + source_message_id
        stmt = (
            select(FinancialEvent)
            .where(
                FinancialEvent.user_id == auth_user_id,
                FinancialEvent.source_message_id == sm_uuid,
            )
            .order_by(FinancialEvent.created_at.asc())
        )
        events = list((await self.repository.session.execute(stmt)).scalars().all())
        if not events:
            return None

        event_ids = [e.id for e in events]
        stmt_pay = (
            select(ObligationPayment)
            .where(ObligationPayment.financial_event_id.in_(event_ids))
            .order_by(ObligationPayment.created_at.asc())
        )
        payments = list((await self.repository.session.execute(stmt_pay)).scalars().all())
        if not payments:
            raise ObligationIdempotencyConflictDomainError(
                "Incomplete receipt for source_message_id"
            )

        first_event = events[0]
        meta = _get_event_metadata(first_event)
        fp = meta.get("fingerprint")

        # 1. Target obligation check
        if any(p.obligation_id != expected_obligation_id for p in payments):
            raise ObligationIdempotencyConflictDomainError(
                "source_message_id replay obligation mismatch"
            )

        # 2. Source account check
        if any(p.account_id != payload.account_id for p in payments):
            raise ObligationIdempotencyConflictDomainError(
                "source_message_id replay account mismatch"
            )

        # 3. Strategy & period check
        if strategy == "specific_period":
            if any(p.obligation_period_id != expected_period_id for p in payments):
                raise ObligationIdempotencyConflictDomainError(
                    "source_message_id replay period mismatch"
                )
            if fp and fp.get("strategy") and fp.get("strategy") != "specific_period":
                raise ObligationIdempotencyConflictDomainError(
                    "source_message_id replay strategy mismatch"
                )
        elif strategy == "fifo":
            if fp and fp.get("strategy") and fp.get("strategy") != "fifo":
                raise ObligationIdempotencyConflictDomainError(
                    "source_message_id replay strategy mismatch"
                )

        # 4. Amount / Mode check
        current_mode = "full_remaining" if payload.amount is None else "explicit"
        if fp and "mode" in fp:
            if fp["mode"] != current_mode:
                raise ObligationIdempotencyConflictDomainError(
                    "source_message_id replay mode mismatch"
                )
            if current_mode == "explicit":
                stored_req_amount = Decimal(str(fp["requested_amount"]))
                if stored_req_amount != payload.amount:
                    raise ObligationIdempotencyConflictDomainError(
                        "source_message_id replay amount mismatch"
                    )
        else:
            # Conservative historical check: compare payload.amount to stored applied payment amount(s)
            if payload.amount is not None:
                if strategy == "specific_period":
                    if payments[0].amount != payload.amount:
                        raise ObligationIdempotencyConflictDomainError(
                            "source_message_id replay amount mismatch"
                        )
                elif strategy == "fifo":
                    total_applied = sum((p.amount for p in payments), Decimal("0"))
                    if total_applied != payload.amount:
                        raise ObligationIdempotencyConflictDomainError(
                            "source_message_id replay amount mismatch"
                        )

        return payments

    async def pay_specific_period(
        self, auth_user_id: UUID, period_id: UUID, payload: ObligationPaymentCreate
    ) -> ObligationPaymentRead:
        # 1. Plain read Period to identify owner/root without locking out of order
        stmt_pre = select(ObligationPeriod).where(ObligationPeriod.id == period_id)
        result_pre = await self.repository.session.execute(stmt_pre)
        pre_period = result_pre.scalar_one_or_none()
        if not pre_period:
            raise NotFoundError("Period not found")

        # 2. Lock root Obligation
        obligation = await self.repository.get_by_id_for_update(pre_period.obligation_id)
        if not obligation or obligation.user_id != auth_user_id:
            raise NotFoundError("Obligation not found")

        # 3. Replay check for source_message_id BEFORE current balance/account state/FX checks
        replay_payments = await self._check_source_message_replay(
            auth_user_id=auth_user_id,
            payload=payload,
            expected_obligation_id=pre_period.obligation_id,
            expected_period_id=period_id,
            strategy="specific_period",
        )
        if replay_payments:
            await self.evaluate_lifecycle(pre_period.obligation_id)
            return ObligationPaymentRead.model_validate(replay_payments[0])

        # 4. Strict lifecycle check
        if obligation.status != "active":
            raise ObligationInactiveError()

        # 5. Lock fresh Period (root -> Period -> Account lock order)
        stmt = (
            select(ObligationPeriod)
            .where(ObligationPeriod.id == period_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self.repository.session.execute(stmt)
        period = result.scalar_one_or_none()
        if not period:
            raise NotFoundError("Period not found")

        if period.status == "pending_amount_definition":
            raise ObligationPeriodAmountRequiredError()
        if period.status in ("paid", "skipped", "cancelled"):
            raise ObligationDomainError(
                "Period is not in a payable state", error_code="period_not_payable"
            )

        remaining_balance = period.amount - period.paid_amount

        # 6. Lock Account (fresh after waits)
        account = await self.account_repo.get_by_id_for_update(
            payload.account_id, include_inactive=True
        )
        if not account or account.user_id != auth_user_id:
            raise NotFoundError("Account not found")
        if not account.is_active:
            raise ObligationAccountInactiveError("Account is inactive")
        if (
            account.balance is None
            or not account.balance.is_finite()
            or account.balance < Decimal("0")
        ):
            raise ObligationAccountUnavailableError("Account balance unavailable")

        # 7. Validate source currency hint if supplied
        if payload.currency and payload.currency.upper() != account.currency.upper():
            raise ObligationCurrencyMismatchError("Source currency hint mismatch")

        # 8. Determine FX and Amounts
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

        # 9. Check Canonical Availability (No Goal mutation)
        from app.goals.repository import GoalRepository

        goal_repo = GoalRepository(self.repository.session)
        reserved_amount = await goal_repo.calculate_reserved_by_account(account.id, auth_user_id)
        if not reserved_amount.is_finite() or reserved_amount < Decimal("0"):
            raise ObligationInconsistentReservationError("Inconsistent reservation state")
        available_balance = account.balance - reserved_amount

        if available_balance < source_amount:
            raise ObligationInsufficientBalanceError("Insufficient balance")

        # 10. Debit Account EXACTLY ONCE before Ledger write(s), flush NO commit/rollback
        await self.account_repo.update_balance(account, -source_amount)
        await self.repository.session.flush()

        # 11. Create Financial Event
        sm_uuid = _parse_source_message_id(payload.source_message_id)
        command_id = (
            uuid.uuid5(
                uuid.NAMESPACE_OID,
                f"legacy_pay:{auth_user_id}:{sm_uuid}",
            )
            if sm_uuid
            else None
        )

        fingerprint = {
            "strategy": "specific_period",
            "obligation_id": str(obligation.id),
            "period_id": str(period.id),
            "account_id": str(account.id),
            "mode": "full_remaining" if payload.amount is None else "explicit",
            "requested_amount": str(payload.amount) if payload.amount is not None else None,
            "currency": obligation.currency,
        }

        event_payload = LedgerEventCreate(
            user_id=auth_user_id,
            event_type="obligation_payment",
            direction="outflow",
            amount=source_amount,
            currency=source_currency,
            account_id=account.id,
            source_message_id=sm_uuid,
            raw_message=payload.raw_message,
            command_id=command_id,
            metadata={
                "fingerprint": fingerprint,
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
        if result.idempotent:
            raise ObligationIdempotencyConflictDomainError("Idempotency conflict in ledger")
        event = result.event

        # 12. Update Period & Record Payment
        period.paid_amount += applied_amount
        if period.paid_amount >= period.amount:
            period.status = "paid"
        else:
            period.status = "partially_paid"

        payment = ObligationPayment(
            id=uuid.uuid4(),
            user_id=auth_user_id,
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
        await self.repository.session.flush()

        await self.evaluate_lifecycle(obligation.id)
        return ObligationPaymentRead.model_validate(payment)

    async def pay_obligation_fifo(
        self, auth_user_id: UUID, obligation_id: UUID, payload: ObligationPaymentCreate
    ) -> list[ObligationPaymentRead]:
        # 1. Lock root Obligation
        obligation = await self.repository.get_by_id_for_update(obligation_id)
        if not obligation or obligation.user_id != auth_user_id:
            raise NotFoundError("Obligation not found")

        # 2. Replay check for source_message_id BEFORE current balance/account state/FX checks
        replay_payments = await self._check_source_message_replay(
            auth_user_id=auth_user_id,
            payload=payload,
            expected_obligation_id=obligation_id,
            expected_period_id=None,
            strategy="fifo",
        )
        if replay_payments:
            await self.evaluate_lifecycle(obligation_id)
            return [ObligationPaymentRead.model_validate(p) for p in replay_payments]

        # 3. Strict lifecycle check
        if obligation.status != "active":
            raise ObligationInactiveError()

        # 4. Sync periods to ensure we have the latest generated periods
        engine = PeriodEngine(self.repository.session)
        await engine.sync_periods(obligation, datetime.now().date())

        # 5. Get all unpaid periods for this obligation
        stmt = (
            select(ObligationPeriod)
            .where(
                ObligationPeriod.obligation_id == obligation.id,
                ObligationPeriod.status.in_(["overdue", "pending_payment", "partially_paid"]),
            )
            .order_by(
                ObligationPeriod.status != "overdue",
                ObligationPeriod.sequence_number,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self.repository.session.execute(stmt)
        periods = list(result.scalars().all())

        total_debt = sum((p.amount - p.paid_amount for p in periods), Decimal("0"))

        # 6. Load Account
        account = await self.account_repo.get_by_id_for_update(
            payload.account_id, include_inactive=True
        )
        if not account or account.user_id != auth_user_id:
            raise NotFoundError("Account not found")
        if not account.is_active:
            raise ObligationAccountInactiveError("Account is inactive")
        if (
            account.balance is None
            or not account.balance.is_finite()
            or account.balance < Decimal("0")
        ):
            raise ObligationAccountUnavailableError("Account balance unavailable")

        # 7. Validate source currency hint if supplied
        if payload.currency and payload.currency.upper() != account.currency.upper():
            raise ObligationCurrencyMismatchError("Source currency hint mismatch")

        # 8. Determine FX and Amounts
        source_currency = account.currency
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

        # 9. Precompute valid source allocations BEFORE debit
        allocations: list[tuple[ObligationPeriod, Decimal, Decimal]] = []
        remaining_to_apply = applied_amount

        for period in periods:
            if remaining_to_apply <= Decimal("0"):
                break
            period_remaining = period.amount - period.paid_amount
            if period_remaining <= Decimal("0"):
                continue
            apply_to_period = min(period_remaining, remaining_to_apply)
            allocations.append((period, period.paid_amount, apply_to_period))
            remaining_to_apply -= apply_to_period

        if not allocations or remaining_to_apply > Decimal("0"):
            raise ObligationPaymentExceedsBalanceError(remaining=str(total_debt))

        source_slices = reconcile_fifo_source_allocations(source_amount, allocations)

        # 10. Check Canonical Availability (No Goal mutation)
        from app.goals.repository import GoalRepository

        goal_repo = GoalRepository(self.repository.session)
        reserved_amount = await goal_repo.calculate_reserved_by_account(account.id, auth_user_id)
        if not reserved_amount.is_finite() or reserved_amount < Decimal("0"):
            raise ObligationInconsistentReservationError("Inconsistent reservation state")
        available_balance = account.balance - reserved_amount

        if available_balance < source_amount:
            raise ObligationInsufficientBalanceError("Insufficient balance")

        # 11. Debit Account EXACTLY ONCE before Ledger write(s), flush NO commit/rollback
        await self.account_repo.update_balance(account, -source_amount)
        await self.repository.session.flush()

        # 12. Create Ledger Events & Payments atomically
        sm_uuid = _parse_source_message_id(payload.source_message_id)
        base_command_id = (
            uuid.uuid5(
                uuid.NAMESPACE_OID,
                f"legacy_pay:{auth_user_id}:{sm_uuid}",
            )
            if sm_uuid
            else None
        )

        payments_created = []

        for i, ((period, _, apply_to_period), source_to_period) in enumerate(
            zip(allocations, source_slices)
        ):
            command_id = (
                base_command_id
                if i == 0
                else (uuid.uuid5(base_command_id, f"allocation_{i}") if base_command_id else None)
            )

            fingerprint = {
                "strategy": "fifo",
                "obligation_id": str(obligation.id),
                "period_id": str(period.id),
                "account_id": str(account.id),
                "mode": "full_remaining" if payload.amount is None else "explicit",
                "requested_amount": str(payload.amount) if payload.amount is not None else None,
                "currency": obligation.currency,
                "fifo_index": i,
            }

            event_payload = LedgerEventCreate(
                user_id=auth_user_id,
                event_type="obligation_payment",
                direction="outflow",
                amount=source_to_period,
                currency=source_currency,
                account_id=account.id,
                source_message_id=sm_uuid,
                raw_message=payload.raw_message,
                command_id=command_id,
                metadata={
                    "fingerprint": fingerprint,
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

            result = await self.ledger_service.record_event(event_payload)
            if result.idempotent:
                raise ObligationIdempotencyConflictDomainError("Idempotency conflict in ledger")
            event = result.event

            period.paid_amount += apply_to_period
            if period.paid_amount >= period.amount:
                period.status = "paid"
            else:
                period.status = "partially_paid"

            payment = ObligationPayment(
                id=uuid.uuid4(),
                user_id=auth_user_id,
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

        await self.repository.session.flush()
        await self.evaluate_lifecycle(obligation.id)
        return [ObligationPaymentRead.model_validate(p) for p in payments_created]
