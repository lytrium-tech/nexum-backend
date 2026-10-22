import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.financial_time import financial_date
from app.ledger.models import FinancialEvent
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate
from app.ledger.service import LedgerService
from app.obligations.enums_v17 import AmountType, ObligationStatus, ObligationType, PeriodStatus
from app.obligations.models import Obligation, ObligationPayment, ObligationPeriod
from app.obligations.schemas_v17 import (
    ObligationFIFOPaymentCreateRequest,
    ObligationPaymentPreviewV17Request,
    ObligationPaymentPreviewV17Response,
    ObligationPaymentsV17ListResponse,
    ObligationPaymentV17Response,
    ObligationPeriodAmountDefineRequest,
    ObligationPeriodPaymentCreateRequest,
    ObligationV17CreateRequest,
    ObligationV17UpdateRequest,
)
from app.obligations.settlement import (
    build_idempotency_fingerprint,
    find_replay_by_command_id,
    reconcile_fifo_source_allocations,
    resolve_settlement,
    validate_idempotency_fingerprint,
)


def _extract_event_metadata(event) -> dict:
    if event is None:
        return {}
    meta = getattr(event, "metadata_", None)
    if isinstance(meta, dict):
        return meta
    meta = getattr(event, "metadata", None)
    if isinstance(meta, dict):
        return meta
    return {}


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
        if data.obligation_type == ObligationType.one_time:
            legacy_type = "one_time"
        elif data.end_count:
            legacy_type = "end_count"
        elif data.end_date:
            legacy_type = "end_date"
        else:
            legacy_type = "indefinite"

        legacy_payment_mode = data.amount_type.value
        due_day = data.first_due_date.day
        due_month = data.first_due_date.month

        new_obligation = Obligation(
            id=uuid.uuid4(),
            user_id=user_id,
            name=data.name,
            description=data.description,
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

    async def evaluate_lifecycle(self, obligation_id: uuid.UUID) -> Obligation | None:
        stmt = (
            select(Obligation)
            .where(Obligation.id == obligation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        obligation = (await self.session.execute(stmt)).scalars().first()
        if not obligation or obligation.status != ObligationStatus.active.value:
            return obligation

        if obligation.type == "indefinite":
            return obligation

        stmt_periods = (
            select(ObligationPeriod)
            .where(ObligationPeriod.obligation_id == obligation_id)
            .order_by(ObligationPeriod.sequence_number.asc())
        )
        periods = list((await self.session.execute(stmt_periods)).scalars().all())
        if not periods:
            return obligation

        if obligation.type == "one_time":
            if all(p.status in ("paid", "skipped", "cancelled") for p in periods):
                obligation.status = ObligationStatus.completed.value

        elif obligation.type == "end_count":
            req_count = obligation.end_count
            if req_count and len(periods) >= req_count:
                req_periods = [p for p in periods if p.sequence_number <= req_count]
                if len(req_periods) == req_count and all(
                    p.status in ("paid", "skipped", "cancelled") for p in req_periods
                ):
                    obligation.status = ObligationStatus.completed.value

        elif obligation.type == "end_date":
            if obligation.end_date and all(
                p.status in ("paid", "skipped", "cancelled") for p in periods
            ):
                last_p = max(periods, key=lambda p: p.sequence_number)
                from app.obligations.period_engine import calculate_period_bounds

                next_start, _, _ = calculate_period_bounds(obligation, last_p.sequence_number + 1)
                if next_start > obligation.end_date:
                    obligation.status = ObligationStatus.completed.value

        return obligation

    async def update_obligation(
        self, user_id: uuid.UUID, obligation_id: uuid.UUID, data: ObligationV17UpdateRequest
    ) -> Obligation:
        stmt = (
            select(Obligation)
            .where(Obligation.id == obligation_id, Obligation.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        obligation = (await self.session.execute(stmt)).scalars().first()
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")

        # Cannot reopen completed or cancelled
        if obligation.status in (
            ObligationStatus.completed.value,
            ObligationStatus.cancelled.value,
        ):
            if data.status is not None and data.status.value != obligation.status:
                raise HTTPException(
                    status_code=422,
                    detail="cannot_reopen_completed_or_cancelled_obligation",
                )

        if data.status is not None:
            new_status = data.status.value
            if new_status not in ("active", "archived", "cancelled"):
                raise HTTPException(status_code=422, detail="invalid_status_transition")
            obligation.status = new_status

        fields_set = getattr(data, "model_fields_set", set())

        if "name" in fields_set and data.name is not None:
            obligation.name = data.name

        if "description" in fields_set:
            obligation.description = data.description

        if "metadata_" in fields_set or "metadata" in fields_set:
            obligation.metadata_ = data.metadata_ if data.metadata_ is not None else {}

        if data.base_amount is not None:
            if obligation.amount_type != AmountType.fixed.value and obligation.payment_mode not in (
                "fixed",
                "partial_allowed",
                "fixed_full_payment",
            ):
                raise HTTPException(status_code=422, detail="base_amount_only_allowed_for_fixed")

            obligation.base_amount = data.base_amount

            # Fixed base update affects FUTURE (period.start_date > canonical today),
            # OPEN, completely UNPAID materialized periods
            today = financial_date()
            stmt_future_periods = (
                select(ObligationPeriod)
                .where(
                    ObligationPeriod.obligation_id == obligation.id,
                    ObligationPeriod.start_date > today,
                    ObligationPeriod.status.in_(
                        [PeriodStatus.pending_payment.value, PeriodStatus.overdue.value]
                    ),
                    (ObligationPeriod.paid_amount == Decimal("0"))
                    | (ObligationPeriod.paid_amount.is_(None)),
                )
                .with_for_update()
            )
            future_periods = (await self.session.execute(stmt_future_periods)).scalars().all()
            for p in future_periods:
                if p.start_date > today and (
                    p.paid_amount is None or p.paid_amount == Decimal("0")
                ):
                    p.amount = data.base_amount

        obligation.updated_at = datetime.now(UTC)
        await self.session.flush()
        return obligation

    def _create_initial_period(self, obligation: Obligation) -> ObligationPeriod:
        """
        Creates the first, minimal obligation period based on the template.
        """
        is_variable = obligation.amount_type == AmountType.variable.value

        if is_variable:
            amount = None
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

    async def list_obligations_overview(self, user_id: uuid.UUID) -> list[dict]:
        """Returns obligations with their relevant period, action state, and period counts in a single query."""
        obligations = await self.list_obligations(user_id)
        if not obligations:
            return []

        obligation_ids = [ob.id for ob in obligations]

        stmt = (
            select(ObligationPeriod)
            .where(ObligationPeriod.obligation_id.in_(obligation_ids))
            .order_by(
                ObligationPeriod.obligation_id,
                ObligationPeriod.due_date.asc(),
                ObligationPeriod.sequence_number.asc(),
                ObligationPeriod.created_at.asc(),
            )
        )
        periods_result = await self.session.execute(stmt)
        all_periods = periods_result.scalars().all()

        from collections import defaultdict

        periods_by_ob = defaultdict(list)
        for p in all_periods:
            periods_by_ob[p.obligation_id].append(p)

        overview = []
        for ob in obligations:
            periods = periods_by_ob.get(ob.id, [])

            payable_count = 0
            overdue_count = 0
            pending_count = 0
            paid_count = 0
            cancelled_count = 0
            skipped_count = 0

            payable_total = Decimal("0")

            relevant_period = None
            requires_amount_definition = False

            payable_periods = []
            pending_def_periods = []
            closed_periods = []

            for p in periods:
                status = p.status
                if status in (
                    PeriodStatus.pending_payment.value,
                    PeriodStatus.partially_paid.value,
                ):
                    pending_count += 1
                elif status == PeriodStatus.overdue.value:
                    overdue_count += 1
                elif status == PeriodStatus.paid.value:
                    paid_count += 1
                elif status == PeriodStatus.cancelled.value:
                    cancelled_count += 1
                elif status == PeriodStatus.skipped.value:
                    skipped_count += 1
                elif status == PeriodStatus.pending_amount_definition.value:
                    pending_count += 1
                    today_can = financial_date()
                    if p.due_date < today_can:
                        overdue_count += 1

                is_payable = status in (
                    PeriodStatus.pending_payment.value,
                    PeriodStatus.partially_paid.value,
                    PeriodStatus.overdue.value,
                )
                if is_payable:
                    payable_count += 1
                    if p.amount is not None:
                        paid_amt = p.paid_amount or Decimal("0")
                        amt_due = p.amount - paid_amt
                        if amt_due > 0:
                            payable_total += amt_due
                            payable_periods.append(p)

                if status == PeriodStatus.pending_amount_definition.value:
                    payable_count += 1
                    pending_def_periods.append(p)

                if status in (
                    PeriodStatus.paid.value,
                    PeriodStatus.cancelled.value,
                    PeriodStatus.skipped.value,
                ):
                    closed_periods.append(p)

            if payable_periods:
                relevant_period = payable_periods[0]
            elif pending_def_periods:
                relevant_period = pending_def_periods[0]
                requires_amount_definition = True
            elif closed_periods:
                relevant_period = closed_periods[-1]

            can_pay = payable_count > 0 and not requires_amount_definition
            can_skip = payable_count > 0 or len(pending_def_periods) > 0
            can_cancel = payable_count > 0 or len(pending_def_periods) > 0

            action_state = {
                "can_pay": can_pay,
                "requires_amount_definition": requires_amount_definition,
                "can_skip": can_skip,
                "can_cancel": can_cancel,
                "has_overdue": overdue_count > 0,
                "payable_period_count": payable_count,
                "payable_total_amount": str(payable_total),
            }

            period_counts = {
                "payable": payable_count,
                "overdue": overdue_count,
                "pending": pending_count,
                "paid": paid_count,
                "cancelled": cancelled_count,
                "skipped": skipped_count,
            }

            overview.append(
                {
                    "id": ob.id,
                    "user_id": ob.user_id,
                    "name": ob.name,
                    "description": ob.description,
                    "currency": ob.currency,
                    "frequency": ob.frequency,
                    "amount": ob.base_amount or Decimal("0"),
                    "amount_type": ob.amount_type
                    or (
                        "fixed"
                        if ob.payment_mode in ("fixed", "partial_allowed", "fixed_full_payment")
                        else "variable"
                    ),
                    "obligation_type": "one_time" if ob.type == "one_time" else "recurring",
                    "status": ob.status or "active",
                    "start_date": ob.start_date,
                    "first_due_date": ob.first_due_date,
                    "due_day": ob.due_day,
                    "due_month": ob.due_month,
                    "interval_count": ob.interval_count or 1,
                    "end_date": ob.end_date,
                    "end_count": ob.end_count,
                    "metadata": getattr(ob, "metadata_", getattr(ob, "metadata", {})),
                    "created_at": ob.created_at,
                    "updated_at": ob.updated_at,
                    "relevant_period": relevant_period,
                    "period_counts": period_counts,
                    "action_state": action_state,
                }
            )

        return overview

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

        # Auto-refresh before listing periods
        await self.refresh_overdue_periods(user_id, obligation_id, commit=False)

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
        stmt_obl = (
            select(Obligation)
            .where(Obligation.id == obligation_id, Obligation.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        obligation = (await self.session.execute(stmt_obl)).scalars().first()
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")
        if obligation.status != ObligationStatus.active.value:
            raise HTTPException(status_code=400, detail="obligation_inactive")

        stmt_period = (
            select(ObligationPeriod)
            .where(
                ObligationPeriod.obligation_id == obligation_id,
                ObligationPeriod.id == period_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        period = (await self.session.execute(stmt_period)).scalars().first()
        if not period:
            raise HTTPException(status_code=404, detail="period_not_found")

        if (
            obligation.amount_type != AmountType.variable.value
            and obligation.payment_mode != "variable"
        ):
            raise HTTPException(status_code=422, detail="period_not_variable")

        if period.status != PeriodStatus.pending_amount_definition.value:
            raise HTTPException(status_code=422, detail="period_amount_already_defined")

        if data.currency and data.currency != obligation.currency:
            raise HTTPException(status_code=422, detail="currency_mismatch")

        period.amount = data.amount
        period.status = PeriodStatus.pending_payment.value

        await self.session.flush()

        # Auto-refresh after defining amount
        await self.refresh_overdue_periods(user_id, obligation_id, commit=False)
        await self.session.flush()

        return period

    async def pay_preview_specific_period(
        self,
        user_id: uuid.UUID,
        obligation_id: uuid.UUID,
        period_id: uuid.UUID,
        data: "ObligationPaymentPreviewV17Request",
    ) -> "ObligationPaymentPreviewV17Response":
        """Preview a specific period payment with canonical settlement and availability checks without debit."""
        obligation = await self.get_obligation(user_id, obligation_id)
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")
        if obligation.status != ObligationStatus.active.value:
            raise HTTPException(status_code=400, detail="obligation_inactive")

        period = await self.get_period(obligation_id, period_id)
        if not period:
            raise HTTPException(status_code=404, detail="period_not_found")
        if period.amount is None or period.status == PeriodStatus.pending_amount_definition.value:
            raise HTTPException(status_code=422, detail="period_amount_not_defined")
        if period.status not in (
            PeriodStatus.pending_payment.value,
            PeriodStatus.partially_paid.value,
            PeriodStatus.overdue.value,
        ):
            raise HTTPException(status_code=422, detail="period_not_payable")

        account = await self.account_repo.get_by_id(data.source_account_id)
        if not account or account.user_id != user_id:
            raise HTTPException(status_code=404, detail="account_not_found")
        if not account.is_active:
            raise HTTPException(status_code=400, detail="account_inactive")
        if account.balance is None or not account.balance.is_finite() or account.balance < 0:
            raise HTTPException(status_code=400, detail="account_unavailable")

        applied_amount = data.amount
        paid_amt = period.paid_amount or Decimal("0")
        remaining_amount = period.amount - paid_amt
        if applied_amount > remaining_amount:
            raise HTTPException(status_code=422, detail="payment_exceeds_remaining_amount")

        from app.obligations.settlement import resolve_settlement

        if account.currency == obligation.currency or data.quote_id or data.rate_snapshot_id:
            settlement = await resolve_settlement(
                session=self.session,
                user_id=user_id,
                obligation=obligation,
                account=account,
                requested_amount=applied_amount,
                requested_currency=data.currency,
                requested_source_currency=data.source_currency,
                requested_source_amount=None,
                quote_id=data.quote_id,
                rate_snapshot_id=data.rate_snapshot_id,
            )
            source_amount = settlement.source_amount
            fx_rate = settlement.fx_rate
            rate_timestamp = settlement.rate_timestamp
            quote_expires_at = None
            quote_id = settlement.quote_id
            if quote_id:
                from app.obligations.models import FXQuote

                stmt_q = select(FXQuote).where(FXQuote.id == quote_id)
                q_obj = (await self.session.execute(stmt_q)).scalars().first()
                if q_obj:
                    quote_expires_at = q_obj.expires_at
        else:
            from app.core.currency import get_fx_rate
            from app.fx.provider import StaticFxRateProvider
            from app.fx.service import FXService

            fx_info = await get_fx_rate(account.currency, obligation.currency)
            fx_rate_val = fx_info["fx_rate"]

            provider = StaticFxRateProvider(
                rates={
                    f"{account.currency.upper()}_{obligation.currency.upper()}": fx_rate_val,
                    f"{obligation.currency.upper()}_{account.currency.upper()}": Decimal("1.0")
                    / fx_rate_val,
                }
            )
            fx_service = FXService(provider=provider, session=self.session)
            fx_service.quote_validity_minutes = 1.5
            quote = await fx_service.create_quote_for_target(
                user_id=user_id,
                source_currency=account.currency,
                target_currency=obligation.currency,
                target_amount=applied_amount,
                commit=False,
            )
            settlement = await resolve_settlement(
                session=self.session,
                user_id=user_id,
                obligation=obligation,
                account=account,
                requested_amount=applied_amount,
                requested_currency=data.currency,
                requested_source_currency=data.source_currency,
                requested_source_amount=None,
                quote_id=quote.quote_id,
                rate_snapshot_id=None,
            )
            source_amount = settlement.source_amount
            fx_rate = settlement.fx_rate
            rate_timestamp = settlement.rate_timestamp or fx_info.get("rate_timestamp")
            quote_expires_at = quote.expires_at
            quote_id = quote.quote_id

        # Availability check without debit
        from app.goals.repository import GoalRepository

        goal_repo = GoalRepository(self.session)
        reserved_amount = await goal_repo.calculate_reserved_by_account(account.id, user_id)
        if not reserved_amount.is_finite() or reserved_amount < Decimal("0"):
            raise HTTPException(status_code=409, detail="inconsistent_reservation_state")
        available_balance = account.balance - reserved_amount
        if available_balance < source_amount:
            raise HTTPException(status_code=400, detail="insufficient_balance")

        return ObligationPaymentPreviewV17Response(
            obligation_currency=obligation.currency,
            source_currency=account.currency,
            applied_amount=applied_amount,
            source_amount=source_amount,
            fx_rate=fx_rate
            if fx_rate is not None
            and fx_rate != Decimal("1.0")
            and fx_rate != Decimal("1.00000000")
            else None,
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
        if not data.idempotency_key:
            raise HTTPException(status_code=422, detail="missing_idempotency_key")

        if not data.source_account_id:
            raise HTTPException(status_code=422, detail="source_account_id_required")

        if data.amount is None or not data.amount.is_finite() or data.amount <= Decimal("0"):
            raise HTTPException(status_code=422, detail="invalid_amount")

        command_id = uuid.uuid5(
            uuid.NAMESPACE_OID, f"obligations_v1_7:{user_id}:{data.idempotency_key}"
        )

        # 1. Early idempotency / replay check (before locks, availability, account status, FX expiry)
        existing_event, payments = await find_replay_by_command_id(
            self.session, user_id, command_id
        )
        if existing_event and payments:
            fp = _extract_event_metadata(existing_event).get("fingerprint")
            validate_idempotency_fingerprint(
                fp,
                obligation_id,
                period_id,
                "specific_period",
                data.source_account_id,
                data.amount,
                data.currency or payments[0].currency,
                data.quote_id,
                data.rate_snapshot_id,
                data.payment_date,
                source_currency=data.source_currency,
                existing_payments=payments,
            )
            stmt_period = select(ObligationPeriod).where(
                ObligationPeriod.id == payments[0].obligation_period_id
            )
            existing_period = (await self.session.execute(stmt_period)).scalars().first()
            return payments[0], existing_period

        # Fallback check on exact idempotency key for historical payments
        stmt_pay_key = select(ObligationPayment).where(
            ObligationPayment.user_id == user_id,
            ObligationPayment.idempotency_key == data.idempotency_key,
        )
        existing_pay_key = (await self.session.execute(stmt_pay_key)).scalars().first()
        if existing_pay_key:
            validate_idempotency_fingerprint(
                None,
                obligation_id,
                period_id,
                "specific_period",
                data.source_account_id,
                data.amount,
                data.currency or existing_pay_key.currency,
                data.quote_id,
                data.rate_snapshot_id,
                data.payment_date,
                source_currency=data.source_currency,
                existing_payments=[existing_pay_key],
            )
            stmt_period = select(ObligationPeriod).where(
                ObligationPeriod.id == existing_pay_key.obligation_period_id
            )
            existing_period = (await self.session.execute(stmt_period)).scalars().first()
            return existing_pay_key, existing_period

        # 2. Lock root Obligation (fresh after waits)
        stmt_obl = (
            select(Obligation)
            .where(
                Obligation.id == obligation_id,
                Obligation.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        obligation = (await self.session.execute(stmt_obl)).scalars().first()
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")

        # 3. Re-read idempotency under root lock BEFORE terminal lifecycle validation
        existing_event, payments = await find_replay_by_command_id(
            self.session, user_id, command_id
        )
        if existing_event and payments:
            fp = _extract_event_metadata(existing_event).get("fingerprint")
            validate_idempotency_fingerprint(
                fp,
                obligation_id,
                period_id,
                "specific_period",
                data.source_account_id,
                data.amount,
                data.currency or obligation.currency,
                data.quote_id,
                data.rate_snapshot_id,
                data.payment_date,
                source_currency=data.source_currency,
                existing_payments=payments,
            )
            stmt_period = select(ObligationPeriod).where(
                ObligationPeriod.id == payments[0].obligation_period_id
            )
            existing_period = (await self.session.execute(stmt_period)).scalars().first()
            return payments[0], existing_period

        # Fallback check on exact idempotency key under root lock
        stmt_pay_key = select(ObligationPayment).where(
            ObligationPayment.user_id == user_id,
            ObligationPayment.idempotency_key == data.idempotency_key,
        )
        existing_pay_key = (await self.session.execute(stmt_pay_key)).scalars().first()
        if existing_pay_key:
            validate_idempotency_fingerprint(
                None,
                obligation_id,
                period_id,
                "specific_period",
                data.source_account_id,
                data.amount,
                data.currency or existing_pay_key.currency,
                data.quote_id,
                data.rate_snapshot_id,
                data.payment_date,
                source_currency=data.source_currency,
                existing_payments=[existing_pay_key],
            )
            stmt_period = select(ObligationPeriod).where(
                ObligationPeriod.id == existing_pay_key.obligation_period_id
            )
            existing_period = (await self.session.execute(stmt_period)).scalars().first()
            return existing_pay_key, existing_period

        if obligation.status != ObligationStatus.active.value:
            raise HTTPException(status_code=400, detail="obligation_inactive")

        # 4. Lock Period (fresh read with populate_existing)
        stmt_period = (
            select(ObligationPeriod)
            .where(
                ObligationPeriod.obligation_id == obligation_id,
                ObligationPeriod.id == str(period_id),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        period = (await self.session.execute(stmt_period)).scalars().first()
        if not period:
            raise HTTPException(status_code=404, detail="period_not_found")

        if period.amount is None or period.status == PeriodStatus.pending_amount_definition.value:
            raise HTTPException(status_code=422, detail="period_amount_not_defined")

        if period.status not in (
            PeriodStatus.pending_payment.value,
            PeriodStatus.partially_paid.value,
            PeriodStatus.overdue.value,
        ):
            raise HTTPException(status_code=422, detail="period_not_payable")

        # 5. Lock Account (fresh after waits)
        account = await self.account_repo.get_by_id_for_update(
            data.source_account_id, include_inactive=True
        )
        if not account or account.user_id != user_id:
            raise HTTPException(status_code=404, detail="account_not_found")
        if not account.is_active:
            raise HTTPException(status_code=400, detail="account_inactive")
        if account.balance is None or not account.balance.is_finite() or account.balance < 0:
            raise HTTPException(status_code=400, detail="account_unavailable")

        # 6. Authoritative settlement & FX calculation FIRST
        settlement = await resolve_settlement(
            session=self.session,
            user_id=user_id,
            obligation=obligation,
            account=account,
            requested_amount=data.amount,
            requested_currency=data.currency,
            requested_source_currency=data.source_currency,
            requested_source_amount=data.source_amount,
            quote_id=data.quote_id,
            rate_snapshot_id=data.rate_snapshot_id,
        )

        paid_amt = period.paid_amount or Decimal("0")
        remaining_amount = period.amount - paid_amt
        if settlement.applied_amount > remaining_amount:
            raise HTTPException(status_code=422, detail="payment_exceeds_remaining_amount")

        # 7. Check Canonical Availability (No Goal mutation)
        from app.goals.repository import GoalRepository

        goal_repo = GoalRepository(self.session)
        reserved_amount = await goal_repo.calculate_reserved_by_account(account.id, user_id)
        if not reserved_amount.is_finite() or reserved_amount < Decimal("0"):
            raise HTTPException(status_code=409, detail="inconsistent_reservation_state")
        available_balance = account.balance - reserved_amount

        if available_balance < settlement.source_amount:
            raise HTTPException(status_code=400, detail="insufficient_balance")

        # 8. Debit Account
        await self.account_repo.update_balance(account, -settlement.source_amount)

        # 9. Record Ledger Event
        fingerprint = build_idempotency_fingerprint(
            obligation_id=obligation_id,
            period_id=period_id,
            strategy="specific_period",
            source_account_id=account.id,
            amount=data.amount,
            currency=obligation.currency,
            source_currency=account.currency,
            quote_id=data.quote_id,
            rate_snapshot_id=data.rate_snapshot_id,
            payment_date=data.payment_date,
        )

        event_create = LedgerEventCreate(
            user_id=user_id,
            event_type="obligation_payment",
            direction="outflow",
            account_id=account.id,
            amount=settlement.source_amount,
            currency=settlement.source_currency,
            description=f"Obligation Payment V1.7: {obligation.name}",
            occurred_at=datetime.now(UTC),
            command_id=command_id,
            metadata={
                "fingerprint": fingerprint,
                "idempotency_key": data.idempotency_key,
                "obligation_period_id": str(period.id),
                "channel": "manual",
                "fx_quote_id": str(settlement.quote_id) if settlement.quote_id else None,
                "rate_snapshot_id": str(settlement.rate_snapshot_id)
                if settlement.rate_snapshot_id
                else None,
                "source_amount": str(settlement.source_amount),
                "source_currency": settlement.source_currency,
                "fx_rate": str(settlement.fx_rate),
                "allocations": [
                    {
                        "obligation_period_id": str(period.id),
                        "amount": str(settlement.applied_amount),
                        "paid_before": str(period.paid_amount or Decimal("0")),
                        "paid_after": str(
                            (period.paid_amount or Decimal("0")) + settlement.applied_amount
                        ),
                        "amount_due_before": str(
                            max(Decimal("0"), period.amount - (period.paid_amount or Decimal("0")))
                        ),
                        "amount_due_after": str(
                            max(
                                Decimal("0"),
                                period.amount
                                - (
                                    (period.paid_amount or Decimal("0")) + settlement.applied_amount
                                ),
                            )
                        ),
                    }
                ],
            },
        )
        ledger_result = await self.ledger_service.record_event(event_create)

        if ledger_result.idempotent:
            # The outer unit of work rolls back the debit on a late command collision.
            raise HTTPException(status_code=409, detail="idempotency_conflict")

        payment = ObligationPayment(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            obligation_period_id=period.id,
            user_id=user_id,
            account_id=account.id,
            financial_event_id=ledger_result.event.id,
            amount=settlement.applied_amount,
            currency=obligation.currency,
            source_amount=settlement.source_amount,
            source_currency=settlement.source_currency,
            fx_rate=settlement.fx_rate,
            rate_source=settlement.rate_source,
            rate_timestamp=settlement.rate_timestamp,
            quote_id=settlement.quote_id,
            rate_snapshot_id=settlement.rate_snapshot_id,
            idempotency_key=data.idempotency_key,
        )
        due_b = (
            max(Decimal("0.00"), period.amount - paid_amt) if period.amount is not None else None
        )
        due_a = (
            max(Decimal("0.00"), period.amount - (paid_amt + settlement.applied_amount))
            if period.amount is not None
            else None
        )
        payment.partial_context = {
            "paid_before": paid_amt,
            "paid_after": paid_amt + settlement.applied_amount,
            "amount_due_before": due_b,
            "amount_due_after": due_a,
        }
        self.session.add(payment)

        period.paid_amount = paid_amt + settlement.applied_amount
        if period.paid_amount < period.amount:
            period.status = PeriodStatus.partially_paid.value
        else:
            period.status = PeriodStatus.paid.value

        await self.session.flush()

        await self.refresh_overdue_periods(user_id, obligation_id, commit=False)

        return payment, period

    async def pay_obligation_smart(
        self,
        user_id: uuid.UUID,
        obligation_id: uuid.UUID,
        data: "ObligationFIFOPaymentCreateRequest",
    ) -> tuple[list[ObligationPayment], list[ObligationPeriod]]:
        """
        Smart payment delegates to FIFO with strategy='smart'.
        Replay is resolved before refresh or oldest checks.
        """
        return await self.pay_obligation_fifo(user_id, obligation_id, data, strategy="smart")

    async def pay_obligation_fifo(
        self,
        user_id: uuid.UUID,
        obligation_id: uuid.UUID,
        data: "ObligationFIFOPaymentCreateRequest",
        strategy: str = "fifo",
    ) -> tuple[list[ObligationPayment], list[ObligationPeriod]]:
        """Pay obligation using FIFO strategy across periods."""
        if not data.idempotency_key:
            raise HTTPException(status_code=422, detail="missing_idempotency_key")

        if not data.source_account_id:
            raise HTTPException(status_code=422, detail="source_account_id_required")

        if data.amount is None or not data.amount.is_finite() or data.amount <= Decimal("0"):
            raise HTTPException(status_code=422, detail="invalid_amount")

        command_id = uuid.uuid5(
            uuid.NAMESPACE_OID, f"obligations_v1_7:{user_id}:{data.idempotency_key}"
        )

        # 1. Early idempotency / replay check (exact match, strictly scoped to user)
        existing_event, payments = await find_replay_by_command_id(
            self.session, user_id, command_id
        )
        if existing_event and payments:
            fp = _extract_event_metadata(existing_event).get("fingerprint")
            validate_idempotency_fingerprint(
                fp,
                obligation_id,
                None,
                strategy,
                data.source_account_id,
                data.amount,
                data.currency or payments[0].currency,
                data.quote_id,
                data.rate_snapshot_id,
                data.payment_date,
                source_currency=data.source_currency,
                existing_payments=payments,
            )
            period_ids = [p.obligation_period_id for p in payments]
            stmt_periods = select(ObligationPeriod).where(ObligationPeriod.id.in_(period_ids))
            existing_periods = list((await self.session.execute(stmt_periods)).scalars().all())
            return payments, existing_periods

        # Fallback check on exact idempotency key for historical payments
        stmt_pay_key = (
            select(ObligationPayment)
            .where(
                ObligationPayment.user_id == user_id,
                ObligationPayment.idempotency_key == data.idempotency_key,
            )
            .order_by(ObligationPayment.created_at.asc())
        )
        existing_pay_key = list((await self.session.execute(stmt_pay_key)).scalars().all())
        if existing_pay_key:
            validate_idempotency_fingerprint(
                None,
                obligation_id,
                None,
                strategy,
                data.source_account_id,
                data.amount,
                data.currency or existing_pay_key[0].currency,
                data.quote_id,
                data.rate_snapshot_id,
                data.payment_date,
                source_currency=data.source_currency,
                existing_payments=existing_pay_key,
            )
            period_ids = [p.obligation_period_id for p in existing_pay_key]
            stmt_periods = select(ObligationPeriod).where(ObligationPeriod.id.in_(period_ids))
            existing_periods = list((await self.session.execute(stmt_periods)).scalars().all())
            return existing_pay_key, existing_periods

        # 2. Lock root Obligation (fresh after waits)
        stmt_obl = (
            select(Obligation)
            .where(
                Obligation.id == obligation_id,
                Obligation.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        obligation = (await self.session.execute(stmt_obl)).scalars().first()
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")

        # 3. Re-read idempotency under root lock BEFORE terminal lifecycle validation
        existing_event, payments = await find_replay_by_command_id(
            self.session, user_id, command_id
        )
        if existing_event and payments:
            fp = _extract_event_metadata(existing_event).get("fingerprint")
            validate_idempotency_fingerprint(
                fp,
                obligation_id,
                None,
                strategy,
                data.source_account_id,
                data.amount,
                data.currency or obligation.currency,
                data.quote_id,
                data.rate_snapshot_id,
                data.payment_date,
                source_currency=data.source_currency,
                existing_payments=payments,
            )
            period_ids = [p.obligation_period_id for p in payments]
            stmt_periods = select(ObligationPeriod).where(ObligationPeriod.id.in_(period_ids))
            existing_periods = list((await self.session.execute(stmt_periods)).scalars().all())
            return payments, existing_periods

        if obligation.status != ObligationStatus.active.value:
            raise HTTPException(status_code=400, detail="obligation_inactive")

        # 4. Check if oldest unpaid period requires amount definition (stops FIFO and Smart)
        stmt_oldest = (
            select(ObligationPeriod)
            .where(
                ObligationPeriod.obligation_id == obligation_id,
                ObligationPeriod.status.notin_(
                    [
                        PeriodStatus.paid.value,
                        PeriodStatus.skipped.value,
                        PeriodStatus.cancelled.value,
                    ]
                ),
            )
            .order_by(
                ObligationPeriod.due_date.asc(),
                ObligationPeriod.sequence_number.asc(),
                ObligationPeriod.created_at.asc(),
            )
            .limit(1)
        )
        oldest_period = (await self.session.execute(stmt_oldest)).scalars().first()
        if oldest_period and (
            oldest_period.amount is None
            or oldest_period.status == PeriodStatus.pending_amount_definition.value
        ):
            raise HTTPException(status_code=422, detail="oldest_period_requires_amount_definition")

        # 5. Lock ordered payable periods
        stmt_periods = (
            select(ObligationPeriod)
            .where(
                ObligationPeriod.obligation_id == obligation_id,
                ObligationPeriod.status.in_(
                    [
                        PeriodStatus.pending_payment.value,
                        PeriodStatus.partially_paid.value,
                        PeriodStatus.overdue.value,
                    ]
                ),
            )
            .order_by(
                ObligationPeriod.due_date.asc(),
                ObligationPeriod.sequence_number.asc(),
                ObligationPeriod.created_at.asc(),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        periods_result = await self.session.execute(stmt_periods)
        periods = list(periods_result.scalars().all())

        if not periods:
            raise HTTPException(status_code=422, detail="no_payable_periods")

        total_remaining = Decimal("0")
        period_data = []
        for p in periods:
            if p.amount is None or p.status == PeriodStatus.pending_amount_definition.value:
                raise HTTPException(status_code=422, detail="period_amount_not_defined")
            paid_amt = p.paid_amount or Decimal("0")
            remaining = p.amount - paid_amt
            total_remaining += remaining
            period_data.append((p, paid_amt, remaining))

        # 6. Lock Account (fresh after waits)
        account = await self.account_repo.get_by_id_for_update(
            data.source_account_id, include_inactive=True
        )
        if not account or account.user_id != user_id:
            raise HTTPException(status_code=404, detail="account_not_found")
        if not account.is_active:
            raise HTTPException(status_code=400, detail="account_inactive")
        if account.balance is None or not account.balance.is_finite() or account.balance < 0:
            raise HTTPException(status_code=400, detail="account_unavailable")

        # 7. Authoritative settlement & FX calculation FIRST
        settlement = await resolve_settlement(
            session=self.session,
            user_id=user_id,
            obligation=obligation,
            account=account,
            requested_amount=data.amount,
            requested_currency=data.currency,
            requested_source_currency=data.source_currency,
            requested_source_amount=data.source_amount,
            quote_id=data.quote_id,
            rate_snapshot_id=data.rate_snapshot_id,
        )

        if settlement.applied_amount > total_remaining:
            raise HTTPException(status_code=422, detail="payment_exceeds_total_remaining_amount")

        # 8. Compute allocations from authoritative settlement.applied_amount
        remaining_applied = settlement.applied_amount
        allocations = []
        for p, paid_amt, remaining in period_data:
            if remaining_applied <= Decimal("0"):
                break
            payment_slice = min(remaining_applied, remaining)
            remaining_applied -= payment_slice
            allocations.append((p, paid_amt, payment_slice))

        source_slices = reconcile_fifo_source_allocations(
            total_source_amount=settlement.source_amount,
            allocations=allocations,
        )

        # 9. Check Canonical Availability (No Goal mutation)
        from app.goals.repository import GoalRepository

        goal_repo = GoalRepository(self.session)
        reserved_amount = await goal_repo.calculate_reserved_by_account(account.id, user_id)
        if not reserved_amount.is_finite() or reserved_amount < Decimal("0"):
            raise HTTPException(status_code=409, detail="inconsistent_reservation_state")
        available_balance = account.balance - reserved_amount

        if available_balance < settlement.source_amount:
            raise HTTPException(status_code=400, detail="insufficient_balance")

        # 10. Debit Account
        await self.account_repo.update_balance(account, -settlement.source_amount)

        # 11. Record Ledger Event
        fingerprint = build_idempotency_fingerprint(
            obligation_id=obligation_id,
            period_id=None,
            strategy=strategy,
            source_account_id=account.id,
            amount=data.amount,
            currency=obligation.currency,
            source_currency=account.currency,
            quote_id=data.quote_id,
            rate_snapshot_id=data.rate_snapshot_id,
            payment_date=data.payment_date,
        )

        # Pre-assign payment UUIDs so allocations_meta has payment_id
        allocations_with_pay_id = []
        for p, paid_amt, slice_amt in allocations:
            allocations_with_pay_id.append((p, paid_amt, slice_amt, uuid.uuid4()))

        allocations_meta = []
        for p, paid_amt, slice_amt, pay_id in allocations_with_pay_id:
            paid_before = paid_amt
            paid_after = paid_amt + slice_amt
            due_before = (
                max(Decimal("0.00"), p.amount - paid_before) if p.amount is not None else None
            )
            due_after = (
                max(Decimal("0.00"), p.amount - paid_after) if p.amount is not None else None
            )
            allocations_meta.append(
                {
                    "payment_id": str(pay_id),
                    "obligation_period_id": str(p.id),
                    "amount": str(slice_amt),
                    "paid_before": str(paid_before),
                    "paid_after": str(paid_after),
                    "amount_due_before": str(due_before) if due_before is not None else None,
                    "amount_due_after": str(due_after) if due_after is not None else None,
                }
            )

        event_create = LedgerEventCreate(
            user_id=user_id,
            event_type="obligation_payment",
            direction="outflow",
            account_id=account.id,
            amount=settlement.source_amount,
            currency=settlement.source_currency,
            description=f"Obligation Payment V1.7 ({strategy.upper()}): {obligation.name}",
            occurred_at=datetime.now(UTC),
            command_id=command_id,
            metadata={
                "fingerprint": fingerprint,
                "idempotency_key": data.idempotency_key,
                "channel": "manual",
                "strategy": strategy,
                "fx_quote_id": str(settlement.quote_id) if settlement.quote_id else None,
                "rate_snapshot_id": str(settlement.rate_snapshot_id)
                if settlement.rate_snapshot_id
                else None,
                "source_amount": str(settlement.source_amount),
                "source_currency": settlement.source_currency,
                "fx_rate": str(settlement.fx_rate),
                "allocations": allocations_meta,
            },
        )
        ledger_result = await self.ledger_service.record_event(event_create)

        if ledger_result.idempotent:
            # The outer unit of work rolls back the debit on a late command collision.
            raise HTTPException(status_code=409, detail="idempotency_conflict")

        created_payments = []
        updated_periods = []

        for (p, paid_amt, applied_slice), source_slice in zip(
            allocations, source_slices, strict=False
        ):
            payment = ObligationPayment(
                id=uuid.uuid4(),
                obligation_id=obligation.id,
                obligation_period_id=p.id,
                user_id=user_id,
                account_id=account.id,
                financial_event_id=ledger_result.event.id,
                amount=applied_slice,
                currency=obligation.currency,
                source_amount=source_slice,
                source_currency=settlement.source_currency,
                fx_rate=settlement.fx_rate,
                rate_source=settlement.rate_source,
                rate_timestamp=settlement.rate_timestamp,
                quote_id=settlement.quote_id,
                rate_snapshot_id=settlement.rate_snapshot_id,
                idempotency_key=f"{data.idempotency_key}-{p.id}",
            )
            self.session.add(payment)
            created_payments.append(payment)

            p.paid_amount = paid_amt + applied_slice
            if p.paid_amount < p.amount:
                p.status = PeriodStatus.partially_paid.value
            else:
                p.status = PeriodStatus.paid.value
            updated_periods.append(p)

        await self.session.flush()

        await self.refresh_overdue_periods(user_id, obligation_id, commit=False)

        return created_payments, updated_periods

    async def skip_period(
        self,
        user_id: uuid.UUID,
        obligation_id: uuid.UUID,
        period_id: uuid.UUID,
        commit: bool = True,
    ) -> ObligationPeriod:
        stmt_obl = (
            select(Obligation)
            .where(Obligation.id == obligation_id, Obligation.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        obligation = (await self.session.execute(stmt_obl)).scalars().first()
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")
        if obligation.status != ObligationStatus.active.value:
            raise HTTPException(status_code=400, detail="obligation_inactive")

        stmt_period = (
            select(ObligationPeriod)
            .where(
                ObligationPeriod.obligation_id == obligation_id, ObligationPeriod.id == period_id
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        period = (await self.session.execute(stmt_period)).scalars().first()
        if not period:
            raise HTTPException(status_code=404, detail="period_not_found")

        if period.status not in (
            PeriodStatus.pending_amount_definition.value,
            PeriodStatus.pending_payment.value,
            PeriodStatus.overdue.value,
        ):
            raise HTTPException(status_code=422, detail="period_not_skippable")

        period.status = PeriodStatus.skipped.value
        await self.evaluate_lifecycle(obligation_id)
        if commit:
            await self.session.commit()
            await self.session.refresh(period)
        else:
            await self.session.flush()

        # Ensure missing periods are generated
        await self.refresh_overdue_periods(user_id, obligation_id, commit=commit)

        return period

    async def cancel_period(
        self,
        user_id: uuid.UUID,
        obligation_id: uuid.UUID,
        period_id: uuid.UUID,
        commit: bool = True,
    ) -> ObligationPeriod:
        stmt_obl = (
            select(Obligation)
            .where(Obligation.id == obligation_id, Obligation.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        obligation = (await self.session.execute(stmt_obl)).scalars().first()
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")
        if obligation.status != ObligationStatus.active.value:
            raise HTTPException(status_code=400, detail="obligation_inactive")

        stmt_period = (
            select(ObligationPeriod)
            .where(
                ObligationPeriod.obligation_id == obligation_id, ObligationPeriod.id == period_id
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        period = (await self.session.execute(stmt_period)).scalars().first()
        if not period:
            raise HTTPException(status_code=404, detail="period_not_found")

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
        await self.evaluate_lifecycle(obligation_id)
        if commit:
            await self.session.commit()
            await self.session.refresh(period)
        else:
            await self.session.flush()

        # Ensure missing periods are generated
        await self.refresh_overdue_periods(user_id, obligation_id, commit=commit)

        return period

    async def refresh_overdue_periods(
        self,
        user_id: uuid.UUID,
        obligation_id: uuid.UUID,
        commit: bool = True,
        obligation: Obligation | None = None,
    ) -> tuple[list[ObligationPeriod], int]:
        if obligation is None:
            stmt_obl = (
                select(Obligation)
                .where(Obligation.id == obligation_id, Obligation.user_id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            obligation = (await self.session.execute(stmt_obl)).scalars().first()
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")
        if obligation.status != ObligationStatus.active.value:
            return [], 0

        from app.obligations.period_engine import PeriodEngine

        today = financial_date()

        # 1. Reuse canonical PeriodEngine.sync_periods for overdue refresh and gap generation
        engine = PeriodEngine(self.session)
        await engine.sync_periods(obligation, today)

        # 2. Query overdue periods for response
        stmt_overdue = (
            select(ObligationPeriod)
            .where(
                ObligationPeriod.obligation_id == obligation_id,
                ObligationPeriod.status == PeriodStatus.overdue.value,
            )
            .order_by(ObligationPeriod.sequence_number.asc())
        )
        updated_periods = list((await self.session.execute(stmt_overdue)).scalars().all())

        # 3. Evaluate bounded lifecycle without intermediate commit
        await self.evaluate_lifecycle(obligation_id)

        # 4. Final commit/flush
        if commit:
            await self.session.commit()
            for p in updated_periods:
                await self.session.refresh(p)
        else:
            await self.session.flush()

        return updated_periods, len(updated_periods)

    async def refresh_due_periods_for_user(self, user_id: uuid.UUID, commit: bool = True) -> None:
        """Batch refresh of overdue/missing periods for a user in stable root-lock order."""
        from app.obligations.period_engine import PeriodEngine

        today = financial_date()

        # 1. Lock active obligations in stable ID order (prevents lock inversion/deadlocks)
        stmt_obls = (
            select(Obligation)
            .where(
                Obligation.user_id == user_id,
                Obligation.status == ObligationStatus.active.value,
            )
            .order_by(Obligation.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        active_obls = list((await self.session.execute(stmt_obls)).scalars().all())
        if not active_obls:
            return

        engine = PeriodEngine(self.session)

        # 2. Sync periods and evaluate lifecycle for each active obligation
        for obligation in active_obls:
            await engine.sync_periods(obligation, today)
            await self.evaluate_lifecycle(obligation.id)

        # 3. Final commit / flush without swallowed errors
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()

    async def get_summary(self, user_id: uuid.UUID, month: str | None = None):
        from datetime import date, datetime

        from app.obligations.schemas_v17 import (
            ObligationsV17ActionRequiredItem,
            ObligationsV17CurrencyTotal,
            ObligationsV17StatusBreakdown,
            ObligationsV17SummaryResponse,
        )

        if not month:
            month = financial_date().strftime("%Y-%m")

        # Perform optimized batch refresh
        await self.refresh_due_periods_for_user(user_id, commit=True)

        year, m = map(int, month.split("-"))
        from calendar import monthrange

        first_day = date(year, m, 1)
        last_day = date(year, m, monthrange(year, m)[1])

        stmt = (
            select(ObligationPeriod, Obligation)
            .join(Obligation, ObligationPeriod.obligation_id == Obligation.id)
            .where(Obligation.user_id == user_id)
            .where(Obligation.status == ObligationStatus.active.value)
            .where(
                ((ObligationPeriod.due_date >= first_day) & (ObligationPeriod.due_date <= last_day))
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
                today_fin = financial_date()
                if period.due_date < today_fin:
                    overdue_count += 1
            elif status_val == PeriodStatus.skipped.value:
                status_counts.skipped += 1
            elif status_val == PeriodStatus.cancelled.value:
                status_counts.cancelled += 1

            amt = period.amount or Decimal("0.00")
            paid = period.paid_amount or Decimal("0.00")
            remaining = amt - paid

            if status_val not in (PeriodStatus.skipped.value, PeriodStatus.cancelled.value):
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

    def _map_payment_to_v17_response(
        self, payment: ObligationPayment, event: "FinancialEvent | None" = None
    ) -> ObligationPaymentV17Response:
        partial_context = None
        channel = "legacy" if payment.financial_event_id is None else "manual"
        if event:
            meta = _extract_event_metadata(event)
            channel = meta.get("channel") or (
                "legacy" if payment.financial_event_id is None else "manual"
            )
            allocations = meta.get("allocations", [])
            for alloc in allocations:
                if alloc.get("obligation_period_id") == str(payment.obligation_period_id):
                    if "paid_before" in alloc and "paid_after" in alloc:
                        due_b = alloc.get("amount_due_before")
                        due_a = alloc.get("amount_due_after")
                        partial_context = {
                            "paid_before": Decimal(str(alloc["paid_before"])),
                            "paid_after": Decimal(str(alloc["paid_after"])),
                            "amount_due_before": Decimal(str(due_b)) if due_b is not None else None,
                            "amount_due_after": Decimal(str(due_a)) if due_a is not None else None,
                        }
                    break

        return ObligationPaymentV17Response(
            id=payment.id,
            obligation_id=payment.obligation_id,
            obligation_period_id=payment.obligation_period_id,
            user_id=payment.user_id,
            account_id=payment.account_id,
            amount=payment.amount,
            currency=payment.currency,
            source_amount=payment.source_amount,
            source_currency=payment.source_currency,
            fx_rate=payment.fx_rate,
            rate_source=payment.rate_source,
            rate_timestamp=payment.rate_timestamp,
            paid_at=payment.paid_at,
            quote_id=payment.quote_id,
            rate_snapshot_id=payment.rate_snapshot_id,
            idempotency_key=payment.idempotency_key,
            financial_event_id=payment.financial_event_id,
            channel=channel,
            partial_context=partial_context,
            created_at=payment.created_at,
            updated_at=getattr(payment, "updated_at", payment.created_at),
        )

    async def get_payment(
        self, user_id: uuid.UUID, payment_id: uuid.UUID
    ) -> ObligationPaymentV17Response:
        from app.ledger.models import FinancialEvent

        stmt = select(ObligationPayment).where(ObligationPayment.id == payment_id)
        payment = (await self.session.execute(stmt)).scalars().first()
        if not payment:
            raise HTTPException(status_code=404, detail="payment_not_found")

        stmt_obl = select(Obligation).where(Obligation.id == payment.obligation_id)
        obligation = (await self.session.execute(stmt_obl)).scalars().first()
        if not obligation or obligation.user_id != user_id:
            raise HTTPException(status_code=404, detail="payment_not_found")

        if payment.user_id is not None and payment.user_id != obligation.user_id:
            raise HTTPException(status_code=403, detail="forbidden")

        event = None
        if payment.financial_event_id:
            event = await self.session.get(FinancialEvent, payment.financial_event_id)
            if event and event.user_id != obligation.user_id:
                raise HTTPException(status_code=403, detail="forbidden")

        return self._map_payment_to_v17_response(payment, event)

    async def list_payments_for_obligation(
        self,
        user_id: uuid.UUID,
        obligation_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> ObligationPaymentsV17ListResponse:
        from sqlalchemy import func

        from app.ledger.models import FinancialEvent

        stmt_obl = select(Obligation).where(
            Obligation.id == obligation_id, Obligation.user_id == user_id
        )
        obligation = (await self.session.execute(stmt_obl)).scalars().first()
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")

        limit = max(1, min(100, limit))
        offset = max(0, offset)

        stmt_conflict = (
            select(ObligationPayment.id)
            .where(
                ObligationPayment.obligation_id == obligation_id,
                ObligationPayment.user_id.is_not(None),
                ObligationPayment.user_id != user_id,
            )
            .limit(1)
        )
        if (await self.session.execute(stmt_conflict)).scalar_one_or_none():
            raise HTTPException(status_code=403, detail="forbidden")

        count_stmt = select(func.count(ObligationPayment.id)).where(
            ObligationPayment.obligation_id == obligation_id
        )
        total = (await self.session.execute(count_stmt)).scalar() or 0

        if total == 0:
            return ObligationPaymentsV17ListResponse(items=[], total=0, limit=limit, offset=offset)

        stmt = (
            select(ObligationPayment)
            .where(ObligationPayment.obligation_id == obligation_id)
            .order_by(ObligationPayment.created_at.desc(), ObligationPayment.id.desc())
            .offset(offset)
            .limit(limit)
        )
        payments = list((await self.session.execute(stmt)).scalars().all())

        event_ids = [p.financial_event_id for p in payments if p.financial_event_id]
        events_by_id = {}
        if event_ids:
            ev_stmt = select(FinancialEvent).where(FinancialEvent.id.in_(event_ids))
            events = (await self.session.execute(ev_stmt)).scalars().all()
            events_by_id = {ev.id: ev for ev in events}

        items = []
        for p in payments:
            if p.user_id is not None and p.user_id != obligation.user_id:
                continue
            ev = events_by_id.get(p.financial_event_id)
            if ev is not None and ev.user_id != user_id:
                raise HTTPException(status_code=403, detail="forbidden")
            items.append(self._map_payment_to_v17_response(p, ev))

        return ObligationPaymentsV17ListResponse(
            items=items, total=total, limit=limit, offset=offset
        )
