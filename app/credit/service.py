import calendar
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.core.currency import round_to_minimum_unit
from app.core.errors import ValidationError
from app.core.financial_time import financial_date
from app.credit.exceptions import (
    CreditActivationBoundaryError,
    CreditAmbiguousAmountError,
    CreditBillingCycleClosedError,
    CreditCardInactiveError,
    CreditCardNotFoundError,
    CreditDomainError,
    CreditIdempotencyConflictError,
    CreditLifecycleInvalidityError,
    CreditLimitExceededError,
    InvalidPaymentAmountError,
)
from app.credit.models import (
    DEBT_PARTITION_CANONICAL,
    PROVENANCE_ISSUER_AUTHORITATIVE,
    PROVENANCE_LEGACY_UNKNOWN,
    PROVENANCE_MANUAL,
    PROVENANCE_NEXUM_MODELED,
    CreditCard,
    CreditCardBillingCycle,
    CreditCardInstallment,
    CreditCardTransaction,
)
from app.credit.repository import CreditCardRepository
from app.credit.schemas import (
    CreditCardCreate,
    CreditCardEarlyPaymentCreate,
    CreditCardEarlyPaymentPreviewCreate,
    CreditCardEarlyPaymentResult,
    CreditCardInstallmentRead,
    CreditCardPaymentCreate,
    CreditCardPaymentResult,
    CreditCardPurchaseCreate,
    CreditCardPurchaseResult,
    CreditCardRead,
    CreditCardStatementRead,
    CreditCardStatusRead,
    CreditCardUpdate,
    CreditSummaryRead,
    PaymentPreviewResult,
)
from app.credit.utils import (
    calculate_credit_card_dates,
    determine_cycle_for_date,
    get_cycle_dates_for_period,
    validate_lifecycle_status,
)
from app.fx.provider import FxRateProvider, get_default_fx_provider
from app.fx.service import FXService
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate
from app.ledger.service import LedgerService

logger = logging.getLogger(__name__)


class CreditCardService:
    def __init__(self, session: AsyncSession, fx_provider: FxRateProvider | None = None):
        self.session = session
        self.repo = CreditCardRepository(session)
        self.ledger_service = LedgerService(LedgerRepository(session))
        self.account_repo = AccountRepository(session)
        self.fx_provider = fx_provider or get_default_fx_provider()

    def _get_financial_date(self, as_of: datetime | date | None = None) -> date:
        if as_of is None:
            return financial_date()
        if isinstance(as_of, datetime):
            if as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=UTC)
            return financial_date(as_of)
        return as_of

    def _data_quality(self) -> dict[str, str]:
        return {
            "current_debt": "computed_from_credit_card_transactions",
            "payment_required": "billed_debt",
            "next_payment_estimate": "estimated",
            "statement_balance": "not_available",
        }

    def _add_months(self, value: date, months: int) -> date:
        month = value.month - 1 + months
        year = value.year + month // 12
        month = month % 12 + 1
        day = min(value.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def _installment_amounts(self, amount: Decimal, total: int) -> list[Decimal]:
        quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        base = (quantized / Decimal(total)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        amounts = [base for _ in range(total)]
        amounts[-1] = (quantized - sum(amounts[:-1], Decimal("0.00"))).quantize(Decimal("0.01"))
        return amounts

    def _normalize_command_id(self, command_id: uuid.UUID | str) -> uuid.UUID:
        return command_id if isinstance(command_id, uuid.UUID) else uuid.UUID(str(command_id))

    async def _build_installments(
        self,
        user_id: uuid.UUID,
        card: CreditCard,
        transaction_id: uuid.UUID,
        amount: Decimal,
        installments_total: int,
        cycle: CreditCardBillingCycle | None = None,
        original_amount: Decimal | None = None,
        original_currency: str | None = None,
        booked_currency: str | None = None,
        provenance: str = PROVENANCE_MANUAL,
        debt_partition: str = DEBT_PARTITION_CANONICAL,
    ) -> list[CreditCardInstallment]:
        amounts = self._installment_amounts(amount, installments_total)
        if original_amount is not None and original_amount != amount:
            orig_amounts = self._installment_amounts(original_amount, installments_total)
        else:
            orig_amounts = amounts

        if cycle is not None:
            parts = cycle.billing_period.split("-")
            target_year, target_month = int(parts[0]), int(parts[1])
        else:
            fin_date = self._get_financial_date()
            period, _, _, _ = determine_cycle_for_date(fin_date, card.cutoff_day, card.due_day)
            parts = period.split("-")
            target_year, target_month = int(parts[0]), int(parts[1])

        installments = []
        for index, principal in enumerate(amounts, start=1):
            if index == 1:
                inst_year, inst_month = target_year, target_month
                inst_period = f"{inst_year:04d}-{inst_month:02d}"
                inst_cycle_id = cycle.id if cycle else None
                inst_due = (
                    cycle.due_date
                    if cycle
                    else get_cycle_dates_for_period(inst_year, inst_month, card.cutoff_day, card.due_day)[2]
                )
            else:
                offset = index - 1
                inst_month = (target_month - 1 + offset) % 12 + 1
                inst_year = target_year + (target_month - 1 + offset) // 12
                inst_period = f"{inst_year:04d}-{inst_month:02d}"
                inst_cycle_id = None
                _, _, inst_due = get_cycle_dates_for_period(
                    inst_year, inst_month, card.cutoff_day, card.due_day
                )

            # Invariant: Assessed interest is NEVER booked from configured rate alone.
            assessed_interest = Decimal("0.00")
            interest_legacy = Decimal("0.00")
            total_amount = principal

            # Projected interest estimate for informational/modeling purposes only on multi-installment purchases
            if installments_total > 1 and card.monthly_interest_rate and card.monthly_interest_rate > 0:
                estimated_interest = (
                    principal * (card.monthly_interest_rate / Decimal("100"))
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                estimated_interest = Decimal("0.00")

            installments.append(
                CreditCardInstallment(
                    user_id=user_id,
                    credit_card_id=card.id,
                    purchase_transaction_id=transaction_id,
                    installment_number=index,
                    installments_total=installments_total,
                    principal_amount=principal,
                    interest_amount=interest_legacy,
                    total_amount=total_amount,
                    scheduled_due_date=inst_due,
                    scheduled_period=inst_period,
                    status="pending",
                    paid_amount=Decimal("0.00"),
                    original_amount=orig_amounts[index - 1],
                    original_currency=original_currency or card.currency,
                    booked_amount=principal,
                    booked_currency=booked_currency or card.currency,
                    cycle_id=inst_cycle_id,
                    assessed_interest_amount=assessed_interest,
                    estimated_interest_amount=estimated_interest,
                    lifecycle_status="active",
                    provenance=provenance,
                    debt_partition=debt_partition,
                )
            )

        # Invariant: Rounding must exactly conserve booked principal.
        assert sum(i.principal_amount for i in installments) == amount
        return installments

    async def _apply_payment_to_waterfall(
        self, user_id: uuid.UUID, card: CreditCard, amount: Decimal
    ) -> None:
        remaining = amount
        installments = await self.repo.list_pending_installments_for_update(card.id, user_id)

        current_date = self._get_financial_date()
        _, cycle_end, _ = calculate_credit_card_dates(current_date, card.cutoff_day, card.due_day)
        current_period = current_date.strftime("%Y-%m")

        def pay_installment_part(inst, part_amount: Decimal):
            nonlocal remaining
            if remaining <= 0 or part_amount <= 0:
                return
            applied = min(remaining, part_amount)
            inst.paid_amount += applied
            remaining -= applied
            if inst.paid_amount >= inst.total_amount:
                inst.status = "paid"
            elif inst.paid_amount > 0:
                inst.status = "partial"

        # 1. Past due (scheduled_period < current_period)
        for inst in installments:
            if inst.scheduled_period < current_period:
                pay_installment_part(inst, inst.total_amount - inst.paid_amount)

        # 2. Accrued interest & 4. Billed installments (both combined in current period installments)
        # In a real model, we might separate them, but here we process the current period installments' interest first.
        for inst in installments:
            if inst.scheduled_period == current_period:
                # Pay up to the interest amount first
                interest_unpaid = max(Decimal("0.00"), inst.interest_amount - inst.paid_amount)
                pay_installment_part(inst, interest_unpaid)

        # 3. Fees / insurance / taxes billed
        charges = await self.repo.list_unpaid_statement_charges_for_update(card.id)
        for charge in charges:
            if remaining <= 0:
                break
            charge_unpaid = charge.amount - charge.paid_amount
            applied = min(remaining, charge_unpaid)
            charge.paid_amount += applied
            remaining -= applied
            if charge.paid_amount >= charge.amount:
                charge.status = "paid"
            elif charge.paid_amount > 0:
                charge.status = "partial"

        # 4 & 5. Billed installments & billed revolving principal (scheduled_period == current_period)
        for inst in installments:
            if inst.scheduled_period == current_period:
                principal_unpaid = inst.total_amount - inst.paid_amount
                pay_installment_part(inst, principal_unpaid)

        # 6. Unbilled revolving (scheduled_period > current_period and installments_total == 1)
        for inst in installments:
            if inst.scheduled_period > current_period and inst.installments_total == 1:
                pay_installment_part(inst, inst.total_amount - inst.paid_amount)

        if remaining > 0:
            raise InvalidPaymentAmountError(
                message="Overpayment is not allowed when there is no unbilled revolving debt"
            )

    async def _enrich_card(self, card: CreditCard) -> CreditCardRead:
        status = await self._calculate_card_status(card)
        debt = status.current_debt
        monthly_pay = status.next_payment_estimate
        exclude_keys = {
            "current_debt",
            "canonical_mode",
            "canonical_activated_at",
            "provenance",
            "lifecycle_status",
            "minimum_payment",
        }
        card_data = {
            key: value
            for key, value in card.__dict__.items()
            if not key.startswith("_") and key not in exclude_keys
        }
        return CreditCardRead(
            **card_data,
            current_debt=debt,
            total_debt=debt,
            available_credit=status.available_credit,
            billed_debt=status.billed_debt,
            unbilled_debt=status.unbilled_debt,
            payment_required=status.payment_required,
            minimum_payment=status.minimum_payment,
            next_payment_estimate=status.next_payment_estimate,
            statement_balance=None,
            data_quality=status.data_quality,
            canonical_mode=card.canonical_mode,
            canonical_activated_at=card.canonical_activated_at,
            provenance=card.provenance,
            lifecycle_status=card.lifecycle_status,
            estimated_current_debt=debt,
            monthly_cc_payment=monthly_pay,
        )

    async def create_card(self, user_id: uuid.UUID, payload: CreditCardCreate) -> CreditCardRead:
        now_utc = datetime.now(UTC)
        card = CreditCard(
            id=uuid.uuid4(),
            user_id=user_id,
            name=payload.name,
            bank=payload.bank,
            credit_limit=payload.credit_limit,
            cutoff_day=payload.cutoff_day,
            due_day=payload.due_day,
            management_fee=payload.management_fee,
            monthly_interest_rate=payload.monthly_interest_rate,
            annual_interest_rate=payload.annual_interest_rate,
            network=payload.network,
            franchise=payload.franchise,
            currency=payload.currency,
            is_active=True,
            canonical_mode=True,
            canonical_activated_at=now_utc,
            provenance=PROVENANCE_MANUAL,
            lifecycle_status="active",
        )
        await self.repo.add(card)
        return await self._enrich_card(card)

    async def list_cards(self, user_id: uuid.UUID) -> list[CreditCardRead]:
        cards = await self.repo.get_all_for_user(user_id)
        enriched = []
        for c in cards:
            enriched.append(await self._enrich_card(c))
        return enriched

    async def get_card(self, user_id: uuid.UUID, card_id: uuid.UUID) -> CreditCardRead:
        card = await self.repo.get_by_id(card_id)
        if not card or card.user_id != user_id or not card.is_active:
            raise CreditCardNotFoundError()
        return await self._enrich_card(card)

    async def update_card(
        self, user_id: uuid.UUID, card_id: uuid.UUID, payload: CreditCardUpdate
    ) -> CreditCardRead:
        card = await self.repo.get_by_id_for_update(card_id)
        if not card or card.user_id != user_id or not card.is_active:
            raise CreditCardNotFoundError()

        disallowed_keys = {"canonical_mode", "canonical_activated_at", "provenance", "lifecycle_status"}
        for key, value in payload.model_dump(exclude_unset=True).items():
            if key not in disallowed_keys:
                setattr(card, key, value)

        await self.session.flush()
        return await self._enrich_card(card)

    async def delete_card(self, user_id: uuid.UUID, card_id: uuid.UUID) -> None:
        card = await self.repo.get_by_id_for_update(card_id)
        if not card or card.user_id != user_id or not card.is_active:
            raise CreditCardNotFoundError()
        card.is_active = False
        await self.session.flush()

    async def create_purchase(
        self,
        user_id: uuid.UUID,
        card_id: uuid.UUID,
        payload: CreditCardPurchaseCreate,
        command_id: uuid.UUID | str,
    ) -> CreditCardPurchaseResult:
        # 1. Preliminary read-only fetch to inspect card properties without write locking
        preliminary_card = await self.repo.get_by_id(card_id)
        if not preliminary_card or preliminary_card.user_id != user_id:
            raise CreditCardNotFoundError()
        if not preliminary_card.is_active:
            raise CreditCardInactiveError()

        lifecycle_status = payload.lifecycle_status or "posted"
        validate_lifecycle_status(lifecycle_status)
        if lifecycle_status == "reversed":
            raise CreditLifecycleInvalidityError(
                message="Cannot create purchase directly in reversed status",
                detail={"lifecycle_status": lifecycle_status},
            )

        # 2. Multi-currency and amount ambiguity resolution (pre-lock)
        raw_orig_currency = payload.original_currency or payload.currency or preliminary_card.currency
        original_currency = raw_orig_currency.upper()
        booked_currency = (preliminary_card.currency or "COP").upper()

        if payload.amount is None and payload.original_amount is None:
            raise CreditAmbiguousAmountError(
                message="Either amount or original_amount must be provided",
                detail={"error_code": "missing_amount"},
            )

        fx_quote_id = payload.fx_quote_id

        if original_currency == booked_currency:
            if payload.amount is not None and payload.original_amount is not None:
                if payload.amount != payload.original_amount:
                    raise CreditAmbiguousAmountError(
                        message="Conflicting amount and original_amount supplied for same-currency purchase",
                        detail={
                            "amount": str(payload.amount),
                            "original_amount": str(payload.original_amount),
                            "currency": original_currency,
                        },
                    )
            raw_amount = (
                payload.original_amount if payload.original_amount is not None else payload.amount
            )
            original_amount = raw_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            booked_amount = original_amount
            fx_rate = Decimal("1.00000000")
            fx_rate_snapshot_id = None
            provenance = (
                preliminary_card.provenance
                if preliminary_card.provenance in (PROVENANCE_ISSUER_AUTHORITATIVE, PROVENANCE_MANUAL)
                else PROVENANCE_MANUAL
            )
        else:
            if (
                payload.amount is not None
                and payload.original_amount is not None
                and payload.amount != payload.original_amount
            ):
                raise CreditAmbiguousAmountError(
                    message="Conflicting amount and original_amount on cross-currency purchase; client cannot dictate booked amount when backend FX applies",
                    detail={
                        "amount": str(payload.amount),
                        "original_amount": str(payload.original_amount),
                        "original_currency": original_currency,
                        "booked_currency": booked_currency,
                    },
                )
            raw_amount = (
                payload.original_amount if payload.original_amount is not None else payload.amount
            )
            original_amount = raw_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            # Resolve external FX snapshot BEFORE acquiring canonical write lock
            fx_service = FXService(provider=self.fx_provider, session=self.session)
            try:
                snapshot = await fx_service.get_rate_snapshot(
                    base_currency=original_currency,
                    quote_currency=booked_currency,
                    commit=False,
                )
            except (ValidationError, httpx.HTTPError, ValueError, InvalidOperation) as e:
                logger.warning(f"Error fetching FX rate snapshot for purchase: {e}")
                raise CreditDomainError(
                    message="FX rate unavailable for currency conversion",
                    error_code="fx_rate_unavailable",
                )

            fx_rate = snapshot.rate
            fx_rate_snapshot_id = (
                snapshot.id if isinstance(snapshot.id, uuid.UUID) else uuid.UUID(str(snapshot.id))
            )
            raw_converted = original_amount * fx_rate
            booked_amount = round_to_minimum_unit(raw_converted, booked_currency)
            provenance = PROVENANCE_NEXUM_MODELED

        # 3. Acquire canonical lock AFTER external FX work
        card = await self.repo.get_by_id_for_update(card_id)
        if not card or card.user_id != user_id:
            raise CreditCardNotFoundError()
        if not card.is_active:
            raise CreditCardInactiveError()
        if (card.currency or "COP").upper() != booked_currency:
            raise CreditDomainError(
                message="Card currency changed concurrently during purchase processing",
                error_code="concurrent_currency_mutation",
            )

        # 4. Activation boundary and canonical frontier establishment
        now_utc = datetime.now(UTC)
        occurred_at = payload.occurred_at or now_utc
        purchase_date = self._get_financial_date(occurred_at)
        occ_dt = occurred_at if occurred_at.tzinfo else occurred_at.replace(tzinfo=UTC)

        was_legacy = not card.canonical_mode
        prior_activated_at = card.canonical_activated_at
        prior_provenance = card.provenance

        if was_legacy:
            # First new purchase command on a migrated legacy card establishes the canonical frontier.
            # If the purchase occurred_at predates the activation instant (backdated), reject it rather than synthesizing history.
            if occ_dt < now_utc:
                raise CreditActivationBoundaryError(
                    message="Cannot activate card with backdated purchase predating canonical activation instant",
                    detail={
                        "occurred_at": occ_dt.isoformat(),
                        "activation_instant": now_utc.isoformat(),
                    },
                )
            card.canonical_mode = True
            card.canonical_activated_at = now_utc
            if card.provenance == PROVENANCE_LEGACY_UNKNOWN:
                card.provenance = PROVENANCE_MANUAL
        else:
            # Already canonical card: cannot predate its canonical activation timestamp
            if card.canonical_activated_at is not None:
                card_act = (
                    card.canonical_activated_at
                    if card.canonical_activated_at.tzinfo
                    else card.canonical_activated_at.replace(tzinfo=UTC)
                )
                if occ_dt < card_act:
                    raise CreditActivationBoundaryError(
                        message="Cannot post canonical purchase prior to card canonical activation",
                        detail={
                            "occurred_at": occ_dt.isoformat(),
                            "canonical_activated_at": card_act.isoformat(),
                        },
                    )

        debt_partition = DEBT_PARTITION_CANONICAL

        try:
            # 5. Debt and limit validation under canonical lock without negative clamping
            part_result = await self.repo.get_card_debt_partitioned(card.id)
            estimated_debt = part_result["total_debt"]
            estimated_available = card.credit_limit - estimated_debt

            # Limit enforcement under lock applies strictly to posted purchases.
            # Pending purchases record authorization facts without posted limit rejection unless an explicit hold model exists.
            if lifecycle_status == "posted":
                if estimated_available <= Decimal("0.00") or booked_amount > estimated_available:
                    raise CreditLimitExceededError()

            # 6. Insert financial event and idempotency check
            event_payload = LedgerEventCreate(
                user_id=user_id,
                event_type="credit_card_purchase",
                direction="neutral",
                amount=booked_amount,
                currency=booked_currency,
                category_id=payload.category_id,
                description=payload.description,
                source_message_id=payload.source_message_id,
                raw_message=payload.raw_message,
                metadata={
                    "credit_card_id": str(card.id),
                    "original_amount": str(original_amount),
                    "original_currency": original_currency,
                    "booked_amount": str(booked_amount),
                    "booked_currency": booked_currency,
                    "fx_rate": str(fx_rate),
                    "fx_rate_snapshot_id": str(fx_rate_snapshot_id) if fx_rate_snapshot_id else None,
                    "debt_partition": debt_partition,
                },
            )

            event_payload.command_id = self._normalize_command_id(command_id)
            result = await self.ledger_service.record_event(event_payload)
            event, is_retry = result.event, result.idempotent

            if is_retry:
                transaction = await self.repo.get_transaction_by_event(event.id)
                if transaction:
                    # Deep idempotency check against parameter tampering
                    if transaction.amount != booked_amount or transaction.credit_card_id != card.id:
                        raise CreditIdempotencyConflictError(
                            message="Idempotency key collision with conflicting parameters",
                            detail={
                                "transaction_id": str(transaction.id),
                                "expected_amount": str(booked_amount),
                                "recorded_amount": str(transaction.amount),
                            },
                        )
                retry_lifecycle = transaction.lifecycle_status if transaction else lifecycle_status
                return CreditCardPurchaseResult(
                    status="idempotent_retry",
                    event_id=event.id,
                    transaction_id=transaction.id if transaction else None,
                    amount=event.amount,
                    current_debt=estimated_debt,
                    available_credit=estimated_available,
                    estimated_current_debt=estimated_debt,
                    estimated_available_credit=estimated_available,
                    cycle_id=transaction.cycle_id if (transaction and retry_lifecycle == "posted") else None,
                    billing_period=transaction.period if (transaction and retry_lifecycle == "posted") else None,
                    original_amount=transaction.original_amount if transaction else original_amount,
                    original_currency=transaction.original_currency if transaction else original_currency,
                    booked_amount=transaction.booked_amount if transaction else booked_amount,
                    booked_currency=transaction.booked_currency if transaction else booked_currency,
                    fx_rate=transaction.fx_rate if transaction else fx_rate,
                    fx_rate_snapshot_id=transaction.fx_rate_snapshot_id if transaction else fx_rate_snapshot_id,
                    lifecycle_status=retry_lifecycle,
                    debt_partition=transaction.debt_partition if transaction else debt_partition,
                    provenance=transaction.provenance if transaction else provenance,
                )

            # 7. Cycle assignment and creation:
            # - posted: assigns/creates cycle, enforces closed/settled protection
            # - pending: creates no cycle, assigns no cycle, performs no closed/settled lookup
            if lifecycle_status == "posted":
                billing_period, cycle_start, cycle_cutoff, cycle_due = determine_cycle_for_date(
                    purchase_date, card.cutoff_day, card.due_day
                )

                existing_cycle = await self.repo.get_billing_cycle_by_period_for_update(card.id, billing_period)
                if existing_cycle and isinstance(existing_cycle, CreditCardBillingCycle):
                    if existing_cycle.status in ("closed", "settled"):
                        raise CreditBillingCycleClosedError(
                            message=f"Cannot post purchase to {existing_cycle.status} billing cycle {billing_period}",
                            detail={
                                "cycle_id": str(existing_cycle.id),
                                "billing_period": billing_period,
                                "status": existing_cycle.status,
                            },
                        )
                    cycle = existing_cycle
                else:
                    cycle = CreditCardBillingCycle(
                        id=uuid.uuid4(),
                        user_id=user_id,
                        credit_card_id=card.id,
                        billing_period=billing_period,
                        start_date=cycle_start,
                        cutoff_date=cycle_cutoff,
                        due_date=cycle_due,
                        status="open",
                        provenance=PROVENANCE_NEXUM_MODELED,
                    )
                    await self.repo.add_billing_cycle(cycle)
                assigned_cycle_id = cycle.id
                assigned_period = billing_period
            else:
                cycle = None
                assigned_cycle_id = None
                assigned_period = None

            transaction = CreditCardTransaction(
                id=uuid.uuid4(),
                user_id=user_id,
                credit_card_id=card.id,
                type="purchase",
                amount=booked_amount,
                category_id=payload.category_id,
                description=payload.description,
                installments_total=payload.installments_total,
                monthly_amount=(
                    (booked_amount / Decimal(payload.installments_total)).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    if lifecycle_status == "posted"
                    else None
                ),
                event_id=event.id,
                occurred_at=occurred_at,
                period=assigned_period,
                cycle_id=assigned_cycle_id,
                original_amount=original_amount,
                original_currency=original_currency,
                booked_amount=booked_amount,
                booked_currency=booked_currency,
                fx_rate=fx_rate,
                fx_rate_snapshot_id=fx_rate_snapshot_id,
                fx_quote_id=fx_quote_id,
                lifecycle_status=lifecycle_status,
                provenance=provenance,
                debt_partition=debt_partition,
            )
            await self.repo.add_transaction(transaction)

            if lifecycle_status == "posted":
                installments = await self._build_installments(
                    user_id=user_id,
                    card=card,
                    transaction_id=transaction.id,
                    amount=booked_amount,
                    installments_total=payload.installments_total,
                    cycle=cycle,
                    original_amount=original_amount,
                    original_currency=original_currency,
                    booked_currency=booked_currency,
                    provenance=provenance,
                    debt_partition=debt_partition,
                )
                await self.repo.add_installments(installments)
                transaction.monthly_amount = installments[0].principal_amount

                final_debt = estimated_debt + booked_amount
                final_available = estimated_available - booked_amount
            else:
                final_debt = estimated_debt
                final_available = estimated_available

            return CreditCardPurchaseResult(
                status="success",
                event_id=event.id,
                transaction_id=transaction.id,
                amount=event.amount,
                current_debt=final_debt,
                available_credit=final_available,
                estimated_current_debt=final_debt,
                estimated_available_credit=final_available,
                cycle_id=assigned_cycle_id,
                billing_period=assigned_period,
                original_amount=original_amount,
                original_currency=original_currency,
                booked_amount=booked_amount,
                booked_currency=booked_currency,
                fx_rate=fx_rate,
                fx_rate_snapshot_id=fx_rate_snapshot_id,
                lifecycle_status=lifecycle_status,
                debt_partition=debt_partition,
                provenance=provenance,
            )
        except Exception:
            if was_legacy:
                card.canonical_mode = False
                card.canonical_activated_at = prior_activated_at
                card.provenance = prior_provenance
            raise

    async def create_payment(
        self,
        user_id: uuid.UUID,
        card_id: uuid.UUID,
        payload: CreditCardPaymentCreate,
        command_id: uuid.UUID | str,
    ) -> CreditCardPaymentResult:
        account = await self.account_repo.get_by_id_for_update(payload.account_id)
        if not account:
            from app.core.errors import NotFoundError

            raise NotFoundError(message="Cuenta no encontrada.")

        if account.user_id != user_id or not account.is_active:
            from app.accounts.exceptions import AccountForbiddenError

            raise AccountForbiddenError()

        if account.balance < payload.amount:
            from app.cash.exceptions import InsufficientFundsError

            raise InsufficientFundsError()

        card = await self.repo.get_by_id_for_update(card_id)
        if not card or card.user_id != user_id:
            raise CreditCardNotFoundError()
        if not card.is_active:
            raise CreditCardInactiveError()

        debt, _ = await self.repo.get_card_debt(card.id)
        estimated_debt = Decimal(str(debt))

        source_currency = account.currency
        target_currency = card.currency

        if not source_currency or not target_currency or source_currency == target_currency:
            applied_amount = payload.amount
            fx_rate = Decimal("1.0")
            rate_source = "internal"
            rate_timestamp = None
            is_estimated = False
        else:
            from app.core.currency import FXProviderError, UnsupportedCurrencyError, get_fx_rate
            from app.core.errors import ForbiddenError, FXProviderUnavailableError

            try:
                fx_info = await get_fx_rate(source_currency, target_currency)
            except UnsupportedCurrencyError as e:
                raise ForbiddenError(message=str(e))
            except FXProviderError:
                raise FXProviderUnavailableError()

            fx_rate = fx_info["fx_rate"]
            rate_source = fx_info["rate_source"]
            rate_timestamp = fx_info["rate_timestamp"]
            is_estimated = True
            from app.core.currency import round_to_minimum_unit

            applied_amount = round_to_minimum_unit(payload.amount * fx_rate, target_currency)

        if applied_amount > estimated_debt:
            raise InvalidPaymentAmountError()

        # Create financial event (direction=outflow for payment)
        event_payload = LedgerEventCreate(
            user_id=user_id,
            event_type="credit_card_payment",
            direction="outflow",
            amount=payload.amount,
            currency=source_currency,
            account_id=payload.account_id,
            source_message_id=payload.source_message_id,
            raw_message=payload.raw_message,
            metadata={
                "credit_card_id": str(card.id),
                "fx_rate": str(fx_rate),
                "rate_source": rate_source,
                "rate_timestamp": rate_timestamp.isoformat() if rate_timestamp else None,
                "is_estimated": is_estimated,
                "applied_amount": str(applied_amount),
                "applied_currency": target_currency,
            },
        )

        event_payload.command_id = self._normalize_command_id(command_id)
        result = await self.ledger_service.record_event(event_payload)
        event, is_retry = result.event, result.idempotent

        if is_retry:
            transaction = await self.repo.get_transaction_by_event(event.id)
            return CreditCardPaymentResult(
                status="idempotent_retry",
                event_id=event.id,
                transaction_id=transaction.id if transaction else None,
                amount=event.amount,
                current_debt=estimated_debt,
                available_credit=max(Decimal("0.00"), card.credit_limit - estimated_debt),
                estimated_current_debt=estimated_debt,
                account_balance=account.balance,
            )

        account.balance -= payload.amount

        transaction = CreditCardTransaction(
            user_id=user_id,
            credit_card_id=card.id,
            type="payment",
            amount=applied_amount,
            account_id=payload.account_id,
            event_id=event.id,
            metadata_={
                "source_amount": str(payload.amount),
                "source_currency": source_currency,
                "applied_amount": str(applied_amount),
                "applied_currency": target_currency,
                "fx_rate": str(fx_rate),
                "rate_source": rate_source,
                "rate_timestamp": rate_timestamp.isoformat() if rate_timestamp else None,
                "is_estimated": is_estimated,
            },
        )
        await self.repo.add_transaction(transaction)
        await self._apply_payment_to_waterfall(user_id, card, applied_amount)

        new_debt = estimated_debt - applied_amount
        new_available = max(Decimal("0.00"), card.credit_limit - new_debt)

        return CreditCardPaymentResult(
            status="success",
            event_id=event.id,
            transaction_id=transaction.id,
            amount=payload.amount,
            current_debt=new_debt,
            available_credit=new_available,
            estimated_current_debt=new_debt,
            account_balance=account.balance,
        )

    async def create_early_payment(
        self,
        user_id: uuid.UUID,
        card_id: uuid.UUID,
        purchase_id: uuid.UUID,
        payload: CreditCardEarlyPaymentCreate,
        command_id: uuid.UUID | str,
    ) -> CreditCardEarlyPaymentResult:
        from app.credit.exceptions import CreditDomainError
        from app.credit.models import CreditCardEarlyPayment

        card = await self.repo.get_by_id_for_update(card_id)
        if not card or card.user_id != user_id or not card.is_active:
            raise CreditCardNotFoundError()

        transaction = await self.repo.get_transaction_by_id(purchase_id)
        if not transaction or transaction.credit_card_id != card.id:
            raise CreditDomainError("Purchase not found or does not belong to this card")

        # Get pending installments
        all_installments = await self.repo.list_installments_for_transaction_for_update(purchase_id)
        installments = [i for i in all_installments if i.status != "paid"]

        # Validation 1: single-installment purchase is not eligible
        if any(i.installments_total == 1 for i in all_installments):
            raise CreditDomainError(
                "PAY_EARLY_NOT_ELIGIBLE: Single-installment purchases are not eligible for early payment."
            )

        # Validation 2: must have future pending/unbilled installments
        current_period = self._get_financial_date().strftime("%Y-%m")
        future_installments = [i for i in installments if i.scheduled_period > current_period]
        if not future_installments:
            raise CreditDomainError(
                "PAY_EARLY_NOT_ELIGIBLE: No future pending/unbilled installments found for early payment."
            )

        total_remaining_principal = sum(
            i.principal_amount - (i.paid_amount if i.paid_amount is not None else Decimal("0.00"))
            for i in installments
        )

        account = await self.account_repo.get_by_id_for_update(payload.account_id)
        if not account or account.user_id != user_id or not account.is_active:
            raise ValueError("Account not found or inactive")

        source_currency = account.currency
        target_currency = card.currency

        # Determine fx_rate first
        if not source_currency or not target_currency or source_currency == target_currency:
            fx_rate = Decimal("1.0")
            rate_source = "internal"
            rate_timestamp = None
            is_estimated = False
        else:
            from app.core.currency import FXProviderError, UnsupportedCurrencyError, get_fx_rate
            from app.core.errors import ForbiddenError, FXProviderUnavailableError

            try:
                fx_info = await get_fx_rate(source_currency, target_currency)
            except UnsupportedCurrencyError as e:
                raise ForbiddenError(message=str(e))
            except FXProviderError:
                raise FXProviderUnavailableError()

            fx_rate = fx_info["fx_rate"]
            rate_source = fx_info["rate_source"]
            rate_timestamp = fx_info["rate_timestamp"]
            is_estimated = True

        from app.core.currency import round_to_minimum_unit

        if total_remaining_principal <= 0:
            raise CreditDomainError("PAY_EARLY_NOT_ELIGIBLE: Remaining principal is zero.")

        # Calculate applied_amount and source_amount (source currency)
        if payload.amount is None:
            # Pay full remaining principal
            applied_amount = total_remaining_principal
            if fx_rate == Decimal("1.0"):
                source_amount = applied_amount
            else:
                # Convert back to source currency
                source_amount = round_to_minimum_unit(applied_amount / fx_rate, source_currency)
        else:
            # Custom amount provided
            source_amount = payload.amount
            if fx_rate == Decimal("1.0"):
                applied_amount = source_amount
            else:
                applied_amount = round_to_minimum_unit(source_amount * fx_rate, target_currency)

        # Check balance
        if account.balance < source_amount:
            raise ValueError("Insufficient balance")

        if applied_amount > total_remaining_principal:
            raise InvalidPaymentAmountError(
                message="Early payment amount exceeds remaining principal"
            )

        # Create financial event
        event_payload = LedgerEventCreate(
            user_id=user_id,
            event_type="credit_card_payment",
            direction="outflow",
            amount=source_amount,
            currency=source_currency,
            account_id=payload.account_id,
            source_message_id=payload.source_message_id,
            raw_message=payload.raw_message,
            metadata={
                "credit_card_id": str(card.id),
                "purchase_transaction_id": str(purchase_id),
                "fx_rate": str(fx_rate),
                "rate_source": rate_source,
                "rate_timestamp": rate_timestamp.isoformat() if rate_timestamp else None,
                "is_estimated": is_estimated,
                "applied_amount": str(applied_amount),
                "applied_currency": target_currency,
            },
        )
        event_payload.command_id = self._normalize_command_id(command_id)
        result = await self.ledger_service.record_event(event_payload)
        event, is_retry = result.event, result.idempotent

        debt, _ = await self.repo.get_card_debt(card.id)

        if is_retry:
            early_payment = await self.repo.get_early_payment_by_event(event.id)
            return CreditCardEarlyPaymentResult(
                status="idempotent_retry",
                event_id=event.id,
                early_payment_id=early_payment.id if early_payment else None,
                amount=event.amount,
                current_debt=Decimal(str(debt)),
                available_credit=max(Decimal("0.00"), card.credit_limit - Decimal(str(debt))),
                account_balance=account.balance,
            )

        account.balance -= source_amount

        early_payment = CreditCardEarlyPayment(
            user_id=user_id,
            credit_card_id=card.id,
            purchase_transaction_id=purchase_id,
            amount=applied_amount,
            allocation_mode=payload.allocation_mode,
            event_id=event.id,
            metadata_={
                "source_amount": str(source_amount),
                "source_currency": source_currency,
                "applied_amount": str(applied_amount),
                "applied_currency": target_currency,
                "fx_rate": str(fx_rate),
                "rate_source": rate_source,
                "rate_timestamp": rate_timestamp.isoformat() if rate_timestamp else None,
                "is_estimated": is_estimated,
            },
        )
        self.session.add(early_payment)

        # Recalculate unbilled installments
        remaining_to_allocate = total_remaining_principal - applied_amount
        num_installments = len(installments)
        new_amounts = self._installment_amounts(remaining_to_allocate, num_installments)

        for inst, new_amt in zip(installments, new_amounts):
            paid_val = inst.paid_amount if inst.paid_amount is not None else Decimal("0.00")
            new_principal = paid_val + new_amt

            if new_principal <= Decimal("0.00"):
                inst.paid_amount = inst.principal_amount
                inst.status = "paid"
            else:
                inst.principal_amount = new_principal
                inst.total_amount = inst.principal_amount + (
                    inst.interest_amount if inst.interest_amount is not None else Decimal("0.00")
                )
                if inst.principal_amount <= paid_val:
                    inst.status = "paid"
                else:
                    inst.status = "pending"

        await self.session.flush()

        new_debt = Decimal(str(debt)) - applied_amount

        return CreditCardEarlyPaymentResult(
            status="success",
            event_id=event.id,
            early_payment_id=early_payment.id,
            amount=source_amount,
            current_debt=new_debt,
            available_credit=max(Decimal("0.00"), card.credit_limit - new_debt),
            account_balance=account.balance,
        )

    async def preview_early_payment(
        self,
        user_id: uuid.UUID,
        card_id: uuid.UUID,
        purchase_id: uuid.UUID,
        payload: CreditCardEarlyPaymentPreviewCreate,
    ) -> PaymentPreviewResult:
        from decimal import Decimal

        card = await self.repo.get_by_id_for_update(card_id)
        if not card or card.user_id != user_id or not card.is_active:
            raise CreditCardNotFoundError()

        transaction = await self.repo.get_transaction_by_id(purchase_id)
        if not transaction or transaction.credit_card_id != card.id:
            from app.credit.exceptions import CreditDomainError
            raise CreditDomainError("Purchase not found or does not belong to this card")

        # Get pending installments
        all_installments = await self.repo.list_installments_for_transaction_for_update(purchase_id)
        installments = [i for i in all_installments if i.status != "paid"]

        from app.credit.exceptions import CreditDomainError
        if any(i.installments_total == 1 for i in all_installments):
            raise CreditDomainError(
                "PAY_EARLY_NOT_ELIGIBLE: Single-installment purchases are not eligible for early payment."
            )

        current_period = self._get_financial_date().strftime("%Y-%m")
        future_installments = [i for i in installments if i.scheduled_period > current_period]
        if not future_installments:
            raise CreditDomainError(
                "PAY_EARLY_NOT_ELIGIBLE: No future pending/unbilled installments found for early payment."
            )

        total_remaining_principal = sum(
            i.principal_amount - (i.paid_amount if i.paid_amount is not None else Decimal("0.00"))
            for i in installments
        )
        if total_remaining_principal <= 0:
            raise CreditDomainError("PAY_EARLY_NOT_ELIGIBLE: Remaining principal is zero.")

        account = await self.account_repo.get_by_id_for_update(payload.account_id)
        if not account or account.user_id != user_id or not account.is_active:
            raise ValueError("Account not found or inactive")

        source_currency = account.currency
        target_currency = card.currency

        if not source_currency or not target_currency or source_currency == target_currency:
            fx_rate = Decimal("1.0")
            rate_source = "internal"
            is_estimated = False
        else:
            from app.core.currency import FXProviderError, UnsupportedCurrencyError, get_fx_rate
            from app.core.errors import ForbiddenError, FXProviderUnavailableError

            try:
                fx_info = await get_fx_rate(source_currency, target_currency)
            except UnsupportedCurrencyError as e:
                raise ForbiddenError(message=str(e))
            except FXProviderError:
                raise FXProviderUnavailableError()

            fx_rate = fx_info["fx_rate"]
            rate_source = fx_info["rate_source"]
            is_estimated = True

        from app.core.currency import round_to_minimum_unit

        if payload.amount is None:
            applied_amount = total_remaining_principal
            if fx_rate == Decimal("1.0"):
                source_amount = applied_amount
            else:
                source_amount = round_to_minimum_unit(applied_amount / fx_rate, source_currency)
        else:
            source_amount = payload.amount
            if fx_rate == Decimal("1.0"):
                applied_amount = source_amount
            else:
                applied_amount = round_to_minimum_unit(source_amount * fx_rate, target_currency)

        return PaymentPreviewResult(
            source_amount=source_amount,
            source_currency=source_currency or "COP",
            target_amount=applied_amount,
            target_currency=target_currency or "COP",
            fx_rate=fx_rate,
            rate_source=rate_source,
            is_estimated=is_estimated,
        )

    async def _calculate_card_status(self, card: CreditCard) -> CreditCardStatusRead:
        current_date = self._get_financial_date()
        cycle_start, cycle_end, next_due = calculate_credit_card_dates(
            current_date, card.cutoff_day, card.due_day
        )

        last_cutoff = cycle_start - timedelta(days=1)
        status_data = await self.repo.get_card_status_data(card.id, last_cutoff)
        next_payment_estimate = await self.repo.get_next_payment_estimate(card.id)

        billed_purchases = status_data["billed_purchases"]
        unbilled_purchases = status_data["unbilled_purchases"]
        total_payments = status_data["total_payments"]

        # Apply payments to billed purchases first
        billed_debt = billed_purchases - total_payments
        if billed_debt < 0:
            unbilled_debt = max(Decimal("0.00"), unbilled_purchases + billed_debt)
            billed_debt = Decimal("0.00")
        else:
            unbilled_debt = unbilled_purchases

        total_debt = billed_debt + unbilled_debt
        available_credit = card.credit_limit - total_debt

        statement_balance = billed_debt
        payment_required = statement_balance

        # Founder direction: minimum payment remains nullable/unknown unless issuer fact.
        # Do not invent a formula.
        minimum_payment = None

        data_quality = self._data_quality()
        data_quality["available_credit"] = "estimated_from_limit_minus_debt"
        data_quality["minimum_payment"] = "unknown_unless_issuer_fact"

        return CreditCardStatusRead(
            card_id=card.id,
            name=card.name,
            credit_limit=card.credit_limit,
            management_fee=card.management_fee or Decimal("0.00"),
            monthly_interest_rate=card.monthly_interest_rate or Decimal("0.00"),
            annual_interest_rate=card.annual_interest_rate or Decimal("0.00"),
            network=card.network,
            franchise=card.franchise,
            currency=card.currency,
            current_debt=total_debt,
            total_debt=total_debt,
            billed_debt=billed_debt,
            unbilled_debt=unbilled_debt,
            available_credit=available_credit,
            payment_required=payment_required,
            minimum_payment=minimum_payment,
            next_payment_estimate=next_payment_estimate,
            statement_balance=statement_balance,
            monthly_cc_payment=next_payment_estimate,
            cutoff_day=card.cutoff_day,
            payment_due_day=card.due_day,
            next_payment_due_date=next_due.isoformat(),
            purchases_count=status_data["purchases_count"],
            payments_count=status_data["payments_count"],
            data_quality=data_quality,
        )

    async def get_card_status(
        self, user_id: uuid.UUID, card_id: uuid.UUID
    ) -> "CreditCardStatusRead":
        card = await self.repo.get_by_id(card_id)
        if not card or card.user_id != user_id or not card.is_active:
            raise CreditCardNotFoundError()

        return await self._calculate_card_status(card)

    async def get_credit_summary(self, user_id: uuid.UUID) -> "CreditSummaryRead":
        from app.credit.schemas import CreditSummaryRead

        cards = await self.repo.get_all_for_user(user_id)

        statuses = []
        for c in cards:
            statuses.append(await self.get_card_status(user_id, c.id))

        return CreditSummaryRead(
            total_credit_limit=sum((s.credit_limit for s in statuses), Decimal("0.00")),
            total_debt=sum((s.total_debt for s in statuses), Decimal("0.00")),
            total_available_credit=sum((s.available_credit for s in statuses), Decimal("0.00")),
            total_monthly_cc_payment=sum(
                (s.next_payment_estimate for s in statuses), Decimal("0.00")
            ),
            total_payment_required=sum((s.payment_required for s in statuses), Decimal("0.00")),
            total_next_payment_estimate=sum(
                (s.next_payment_estimate for s in statuses), Decimal("0.00")
            ),
            cards=statuses,
        )

    async def list_installments(
        self, user_id: uuid.UUID, card_id: uuid.UUID
    ) -> list[CreditCardInstallmentRead]:
        card = await self.repo.get_by_id(card_id)
        if not card or card.user_id != user_id or not card.is_active:
            raise CreditCardNotFoundError()
        installments = await self.repo.list_installments(card_id, user_id)
        return [CreditCardInstallmentRead.model_validate(i) for i in installments]

    async def list_statements(
        self, user_id: uuid.UUID, card_id: uuid.UUID
    ) -> list[CreditCardStatementRead]:
        card = await self.repo.get_by_id(card_id)
        if not card or card.user_id != user_id:
            raise CreditCardNotFoundError()
        statements = await self.repo.list_statements(card_id)
        return [CreditCardStatementRead.model_validate(s) for s in statements]

    async def get_statement(
        self, user_id: uuid.UUID, card_id: uuid.UUID, period: str
    ) -> CreditCardStatementRead | None:
        card = await self.repo.get_by_id(card_id)
        if not card or card.user_id != user_id:
            raise CreditCardNotFoundError()
        statement = await self.repo.get_statement(card_id, period)
        if not statement:
            return None
        return CreditCardStatementRead.model_validate(statement)
