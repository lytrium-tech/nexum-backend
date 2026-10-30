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
    CreditFXRateUnavailableError,
    CreditGoalReservationBreachError,
    CreditIdempotencyConflictError,
    CreditLifecycleInvalidityError,
    CreditLimitExceededError,
    InvalidPaymentAmountError,
)
from app.credit.fingerprint import (
    build_credit_fingerprint,
    hash_credit_fingerprint,
)
from app.credit.models import (
    ALLOCATION_SOURCE_NEXUM_MODELED,
    ALLOCATION_SOURCE_USER_DIRECTED,
    DEBT_PARTITION_CANONICAL,
    PROVENANCE_ISSUER_AUTHORITATIVE,
    PROVENANCE_LEGACY_UNKNOWN,
    PROVENANCE_MANUAL,
    PROVENANCE_NEXUM_MODELED,
    CreditCard,
    CreditCardAdjustment,
    CreditCardAdjustmentAllocation,
    CreditCardBillingCycle,
    CreditCardEarlyPayment,
    CreditCardInstallment,
    CreditCardPayment,
    CreditCardPaymentAllocation,
    CreditCardStatement,
    CreditCardStatementCharge,
    CreditCardStatementComponent,
    CreditCardTransaction,
)
from app.credit.repository import CreditCardRepository
from app.credit.schemas import (
    CreditCardAdjustmentCreate,
    CreditCardAdjustmentResult,
    CreditCardCreate,
    CreditCardCycleClosureSummary,
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
    CreditCommandReconciliationRead,
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
from app.goals.repository import GoalRepository
from app.ledger.enums import Direction, EventType
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate
from app.ledger.service import LedgerService

logger = logging.getLogger(__name__)


class CreditCardService:
    def __init__(self, session: AsyncSession, fx_provider: FxRateProvider | None = None):
        self.session = session
        self.repo = CreditCardRepository(session)
        self.ledger_service = LedgerService(LedgerRepository(session))
        self.ledger_repo = self.ledger_service.repository
        self.account_repo = AccountRepository(session)
        self.goal_repo = GoalRepository(session) if session is not None else None
        self.fx_provider = fx_provider or get_default_fx_provider()

    def _get_financial_date(self, as_of: datetime | date | None = None) -> date:
        if as_of is None:
            return financial_date()
        if isinstance(as_of, datetime):
            if as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=UTC)
            return financial_date(as_of)
        return as_of

    async def _get_fx_info(
        self, source_currency: str, target_currency: str, *, persist_snapshot: bool
    ) -> dict:
        fx_service = FXService(
            provider=self.fx_provider,
            session=self.session if persist_snapshot else None,
        )
        try:
            if persist_snapshot:
                snapshot = await fx_service.get_rate_snapshot(
                    source_currency, target_currency, commit=False
                )
                return {
                    "fx_rate": snapshot.rate,
                    "rate_source": snapshot.source,
                    "rate_timestamp": snapshot.retrieved_at,
                    "fx_rate_snapshot_id": snapshot.id,
                    "from_currency": source_currency.upper(),
                    "to_currency": target_currency.upper(),
                }

            latest = await fx_service.get_latest_rate(source_currency, target_currency)
            return {
                "fx_rate": latest.rates[target_currency.upper()],
                "rate_source": latest.provider,
                "rate_timestamp": latest.fetched_at,
                "fx_rate_snapshot_id": None,
                "from_currency": source_currency.upper(),
                "to_currency": target_currency.upper(),
            }
        except (ValidationError, httpx.HTTPError, ValueError, InvalidOperation) as exc:
            raise CreditFXRateUnavailableError() from exc

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
                    else get_cycle_dates_for_period(
                        inst_year, inst_month, card.cutoff_day, card.due_day
                    )[2]
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
            if (
                installments_total > 1
                and card.monthly_interest_rate
                and card.monthly_interest_rate > 0
            ):
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

    async def _get_goal_reserved_amount(self, account_id: uuid.UUID, user_id: uuid.UUID) -> Decimal:
        repo = getattr(self, "goal_repo", None) or GoalRepository(self.session)
        val = await repo.calculate_reserved_by_account(account_id, user_id)
        if not isinstance(val, Decimal):
            raise CreditGoalReservationBreachError(
                message="Goal reservation engine returned invalid non-decimal value",
                detail={"account_id": str(account_id), "returned_type": type(val).__name__},
            )
        if not val.is_finite() or val < Decimal("0.00"):
            raise CreditGoalReservationBreachError(
                message="Inconsistent goal reservation state for account",
                detail={"account_id": str(account_id), "reserved_amount": str(val)},
            )
        return val

    async def _verify_spendable_balance(
        self, account, user_id: uuid.UUID, source_amount: Decimal
    ) -> Decimal:
        if account.balance is None:
            raise ValidationError(message="Account balance is invalid")
        if account.balance < source_amount:
            from app.cash.exceptions import InsufficientFundsError

            raise InsufficientFundsError(message="Insufficient funds in account")

        reserved = await self._get_goal_reserved_amount(account.id, user_id)
        if not reserved.is_finite() or reserved < Decimal("0.00"):
            raise CreditGoalReservationBreachError(
                message="Inconsistent goal reservation state for account",
                detail={"account_id": str(account.id), "reserved_amount": str(reserved)},
            )

        spendable_balance = account.balance - reserved
        if source_amount > spendable_balance:
            raise CreditGoalReservationBreachError(
                message="Payment amount violates spendable balance and goal reservations",
                detail={
                    "account_id": str(account.id),
                    "account_balance": str(account.balance),
                    "goal_reserved_amount": str(reserved),
                    "available_balance": str(spendable_balance),
                    "required_amount": str(source_amount),
                },
            )
        return spendable_balance

    async def _check_operation_idempotency(
        self,
        *,
        user_id: uuid.UUID,
        command_id: uuid.UUID,
        operation_type: str,
        credit_card_id: uuid.UUID,
        account_id: uuid.UUID | str | None = None,
        booked_amount: Decimal | None = None,
        booked_currency: str | None = None,
        target_id: uuid.UUID | None = None,
        original_amount: Decimal | None = None,
        original_currency: str | None = None,
        fx_rate: Decimal | None = None,
        adjustment_type: str | None = None,
        direction: str | None = None,
        provenance: str | None = None,
        allocation_source: str | None = None,
    ):
        ledger_repo = getattr(self.ledger_service, "repository", None) or getattr(
            self, "ledger_repo", None
        )
        if ledger_repo is None:
            return None

        # Fail closed: database/read failures must propagate, not permit second mutation
        existing_event = await ledger_repo.get_by_command_id(command_id)
        if not existing_event:
            return None

        # Malformed collision state: fail closed
        event_type = getattr(existing_event, "event_type", None)
        event_user_id = getattr(existing_event, "user_id", None)
        if not isinstance(event_type, str) or event_user_id is None:
            raise CreditIdempotencyConflictError(
                message="Malformed existing event for command_id",
                detail={"command_id": str(command_id)},
            )

        # 1. User check
        if str(event_user_id) != str(user_id):
            raise CreditIdempotencyConflictError(
                message="Idempotency key collision across different users",
                detail={"command_id": str(command_id)},
            )

        # 2. Event type check
        expected_event_type = (
            EventType.CREDIT_CARD_PAYMENT
            if operation_type in ("credit_card_payment", "credit_card_pay_early")
            else EventType.MANUAL_ADJUSTMENT
        )
        if existing_event.event_type != expected_event_type:
            raise CreditIdempotencyConflictError(
                message=f"Idempotency key collision: event type mismatch ({existing_event.event_type} vs {expected_event_type})",
                detail={
                    "command_id": str(command_id),
                    "existing_type": existing_event.event_type,
                    "expected_type": expected_event_type,
                },
            )

        # 3. Specific operation type check (standard payment and early payment are NOT interchangeable)
        meta = existing_event.metadata_ or {}
        existing_op = meta.get("operation_type") or (
            "credit_card_pay_early"
            if meta.get("is_early_payment") or meta.get("purchase_transaction_id")
            else existing_event.event_type
        )
        if existing_op != operation_type:
            raise CreditIdempotencyConflictError(
                message=f"Idempotency key collision: operation type mismatch ({existing_op} vs {operation_type})",
                detail={
                    "command_id": str(command_id),
                    "existing_operation": existing_op,
                    "requested_operation": operation_type,
                },
            )

        # 4. Card match
        if meta.get("credit_card_id") != str(credit_card_id):
            raise CreditIdempotencyConflictError(
                message="Idempotency key collision across different credit cards",
                detail={
                    "command_id": str(command_id),
                    "existing_card": meta.get("credit_card_id"),
                    "target_card": str(credit_card_id),
                },
            )

        # 5. Account match for payments or account-affecting adjustments
        if account_id is not None or operation_type in (
            "credit_card_payment",
            "credit_card_pay_early",
        ):
            existing_account_id = getattr(existing_event, "account_id", None) or meta.get(
                "account_id"
            )
            if not existing_account_id:
                raise CreditIdempotencyConflictError(
                    message="Malformed existing event: missing account_id in event or metadata",
                    detail={"command_id": str(command_id)},
                )
            if account_id is not None and str(existing_account_id) != str(account_id):
                raise CreditIdempotencyConflictError(
                    message="Idempotency key collision across different accounts",
                    detail={
                        "command_id": str(command_id),
                        "existing_account": str(existing_account_id),
                        "target_account": str(account_id),
                    },
                )
        elif getattr(existing_event, "account_id", None) or meta.get("account_id"):
            raise CreditIdempotencyConflictError(
                message="Idempotency key collision: existing event affects account but requested operation is accountless",
                detail={"command_id": str(command_id)},
            )

        # 6. Target check for early payments and adjustments
        if operation_type == "credit_card_pay_early" and target_id is not None:
            if meta.get("purchase_transaction_id") != str(target_id):
                raise CreditIdempotencyConflictError(
                    message="Idempotency key collision across different target purchases",
                    detail={
                        "command_id": str(command_id),
                        "existing_target": meta.get("purchase_transaction_id"),
                        "target_id": str(target_id),
                    },
                )
        if operation_type == "credit_card_adjustment" and target_id is not None:
            if meta.get("target_transaction_id") != str(target_id):
                raise CreditIdempotencyConflictError(
                    message="Idempotency key collision across different target transactions",
                    detail={
                        "command_id": str(command_id),
                        "existing_target": meta.get("target_transaction_id"),
                        "target_id": str(target_id),
                    },
                )

        # 7. Adjustment semantics check
        if operation_type == "credit_card_adjustment":
            if adjustment_type:
                if "adjustment_type" not in meta:
                    raise CreditIdempotencyConflictError(
                        message="Malformed existing adjustment event: missing adjustment_type in metadata",
                        detail={"command_id": str(command_id)},
                    )
                if meta["adjustment_type"] != adjustment_type:
                    raise CreditIdempotencyConflictError(
                        message="Idempotency key collision with conflicting adjustment type",
                        detail={
                            "command_id": str(command_id),
                            "existing_adj_type": meta.get("adjustment_type"),
                            "requested_adj_type": adjustment_type,
                        },
                    )
            if direction:
                if "direction" not in meta:
                    raise CreditIdempotencyConflictError(
                        message="Malformed existing adjustment event: missing direction in metadata",
                        detail={"command_id": str(command_id)},
                    )
                if meta["direction"] != direction:
                    raise CreditIdempotencyConflictError(
                        message="Idempotency key collision with conflicting adjustment direction",
                        detail={
                            "command_id": str(command_id),
                            "existing_direction": meta.get("direction"),
                            "requested_direction": direction,
                        },
                    )
            if provenance:
                if "provenance" not in meta:
                    raise CreditIdempotencyConflictError(
                        message="Malformed existing adjustment event: missing provenance in metadata",
                        detail={"command_id": str(command_id)},
                    )
                if meta["provenance"] != provenance:
                    raise CreditIdempotencyConflictError(
                        message="Idempotency key collision with conflicting adjustment provenance",
                        detail={
                            "command_id": str(command_id),
                            "existing_provenance": meta.get("provenance"),
                            "requested_provenance": provenance,
                        },
                    )
            if allocation_source:
                if "allocation_source" not in meta:
                    raise CreditIdempotencyConflictError(
                        message="Malformed existing adjustment event: missing allocation_source in metadata",
                        detail={"command_id": str(command_id)},
                    )
                if meta["allocation_source"] != allocation_source:
                    raise CreditIdempotencyConflictError(
                        message="Idempotency key collision with conflicting adjustment allocation_source",
                        detail={
                            "command_id": str(command_id),
                            "existing_allocation_source": meta.get("allocation_source"),
                            "requested_allocation_source": allocation_source,
                        },
                    )

        # 8. Amounts and currencies
        if original_amount is not None and Decimal(str(existing_event.amount)) != Decimal(
            str(original_amount)
        ):
            raise CreditIdempotencyConflictError(
                message="Idempotency key collision with conflicting source amount",
                detail={
                    "command_id": str(command_id),
                    "existing_amount": str(existing_event.amount),
                    "requested_amount": str(original_amount),
                },
            )

        if original_currency is not None and existing_event.currency != original_currency:
            raise CreditIdempotencyConflictError(
                message="Idempotency key collision with conflicting source currency",
                detail={
                    "command_id": str(command_id),
                    "existing_currency": existing_event.currency,
                    "requested_currency": original_currency,
                },
            )

        if booked_amount is not None:
            if "applied_amount" not in meta:
                raise CreditIdempotencyConflictError(
                    message="Malformed existing event: missing applied_amount in metadata",
                    detail={"command_id": str(command_id)},
                )
            if Decimal(str(meta["applied_amount"])) != Decimal(str(booked_amount)):
                raise CreditIdempotencyConflictError(
                    message="Idempotency key collision with conflicting booked amount",
                    detail={
                        "command_id": str(command_id),
                        "existing_applied": meta.get("applied_amount"),
                        "requested_booked": str(booked_amount),
                    },
                )

        if booked_currency is not None:
            if "applied_currency" not in meta:
                raise CreditIdempotencyConflictError(
                    message="Malformed existing event: missing applied_currency in metadata",
                    detail={"command_id": str(command_id)},
                )
            if meta["applied_currency"] != booked_currency:
                raise CreditIdempotencyConflictError(
                    message="Idempotency key collision with conflicting booked currency",
                    detail={
                        "command_id": str(command_id),
                        "existing_currency": meta.get("applied_currency"),
                        "requested_currency": booked_currency,
                    },
                )

        return existing_event

    async def _apply_payment_to_waterfall(
        self,
        user_id: uuid.UUID,
        card: CreditCard,
        amount: Decimal,
        payment: CreditCardPayment | None = None,
        installments: list[CreditCardInstallment] | None = None,
        charges: list[CreditCardStatementCharge] | None = None,
    ) -> list[CreditCardPaymentAllocation]:
        remaining = amount
        allocations: list[CreditCardPaymentAllocation] = []
        order_counter = 1

        if installments is None:
            installments = await self.repo.list_pending_installments_for_update(card.id, user_id)
        if charges is None:
            charges = await self.repo.list_unpaid_statement_charges_for_update(card.id)

        current_date = self._get_financial_date()
        current_period = current_date.strftime("%Y-%m")

        # Partition installments deterministically (F-14 / F-19)
        def _inst_sort_key(x):
            sp = getattr(x, "scheduled_period", "") or ""
            in_num = getattr(x, "installment_number", 0) or 0
            ca = getattr(x, "created_at", None) or datetime.min
            x_id = str(getattr(x, "id", ""))
            return (sp, in_num, ca, x_id)

        def _charge_sort_key(x):
            ca = getattr(x, "created_at", None) or datetime.min
            x_id = str(getattr(x, "id", ""))
            return (ca, x_id)

        past_due = [i for i in installments if getattr(i, "scheduled_period", "") < current_period]
        past_due.sort(key=_inst_sort_key)

        current_insts = [
            i for i in installments if getattr(i, "scheduled_period", "") == current_period
        ]
        current_insts.sort(key=_inst_sort_key)

        installment_ids = {i.id for i in installments if getattr(i, "id", None)}
        filtered_charges = [
            c
            for c in charges
            if getattr(c, "status", "") != "paid"
            and (getattr(c, "reference_id", None) is None or c.reference_id not in installment_ids)
        ]
        filtered_charges.sort(key=_charge_sort_key)

        # Future installments: nearest due date -> oldest purchase/event -> UUID
        future_insts = [
            i for i in installments if getattr(i, "scheduled_period", "") > current_period
        ]
        future_insts.sort(key=_inst_sort_key)

        inst_adj_credits: dict[uuid.UUID, Decimal] = {}
        for i in installments:
            if getattr(i, "id", None):
                adj_allocs = await self.repo.list_credit_adjustment_allocations_for_installment(
                    i.id
                )
                inst_adj_credits[i.id] = sum(a.allocated_amount for a in adj_allocs)

        def _get_principal(inst: CreditCardInstallment) -> Decimal:
            val = getattr(inst, "principal_amount", None)
            if val is not None:
                return val
            tot = getattr(inst, "total_amount", Decimal("0.00")) or Decimal("0.00")
            intr = getattr(inst, "interest_amount", Decimal("0.00")) or Decimal("0.00")
            return tot - intr

        def _get_interest(inst: CreditCardInstallment) -> Decimal:
            val = getattr(inst, "assessed_interest_amount", None)
            if val is not None:
                return val
            return getattr(inst, "interest_amount", Decimal("0.00")) or Decimal("0.00")

        def allocate_installment(
            inst: CreditCardInstallment,
            principal_part: Decimal,
            interest_part: Decimal,
            fee_part: Decimal = Decimal("0.00"),
        ) -> None:
            nonlocal remaining, order_counter
            total_part = principal_part + interest_part + fee_part
            if remaining <= Decimal("0.00") or total_part <= Decimal("0.00"):
                return
            applied = min(remaining, total_part)
            if applied <= Decimal("0.00"):
                return

            applied_interest = min(applied, interest_part)
            rem_applied = applied - applied_interest
            applied_fee = min(rem_applied, fee_part)
            rem_applied -= applied_fee
            applied_principal = min(rem_applied, principal_part)

            if payment is not None:
                alloc = CreditCardPaymentAllocation(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    credit_card_id=card.id,
                    payment_id=payment.id,
                    target_installment_id=inst.id,
                    target_charge_id=None,
                    target_statement_component_id=None,
                    allocated_amount=applied,
                    allocated_principal=applied_principal,
                    allocated_interest=applied_interest,
                    allocated_fees=applied_fee,
                    allocation_order=order_counter,
                    allocation_source=ALLOCATION_SOURCE_NEXUM_MODELED,
                    provenance=PROVENANCE_NEXUM_MODELED,
                )
                allocations.append(alloc)
                order_counter += 1

            inst.paid_amount = (inst.paid_amount or Decimal("0.00")) + applied
            remaining -= applied

            tot_covered = (inst.paid_amount or Decimal("0.00")) + inst_adj_credits.get(
                inst.id, Decimal("0.00")
            )
            if tot_covered >= inst.total_amount:
                inst.status = "paid"
            elif inst.paid_amount > Decimal("0.00"):
                inst.status = "partial"

        def allocate_charge(charge: CreditCardStatementCharge, charge_amount: Decimal) -> None:
            nonlocal remaining, order_counter
            if remaining <= Decimal("0.00") or charge_amount <= Decimal("0.00"):
                return
            applied = min(remaining, charge_amount)
            if applied <= Decimal("0.00"):
                return

            charge_type = getattr(charge, "charge_type", "fee")
            charge_type_str = str(charge_type)
            applied_interest = applied if charge_type_str == "interest" else Decimal("0.00")
            applied_fee = applied if charge_type_str != "interest" else Decimal("0.00")

            if payment is not None:
                alloc = CreditCardPaymentAllocation(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    credit_card_id=card.id,
                    payment_id=payment.id,
                    target_installment_id=None,
                    target_charge_id=charge.id,
                    target_statement_component_id=None,
                    allocated_amount=applied,
                    allocated_principal=Decimal("0.00"),
                    allocated_interest=applied_interest,
                    allocated_fees=applied_fee,
                    allocation_order=order_counter,
                    allocation_source=ALLOCATION_SOURCE_NEXUM_MODELED,
                    provenance=PROVENANCE_NEXUM_MODELED,
                )
                allocations.append(alloc)
                order_counter += 1

            charge.paid_amount = (charge.paid_amount or Decimal("0.00")) + applied
            remaining -= applied

            if charge.paid_amount >= charge.amount:
                charge.status = "paid"
            elif charge.paid_amount > Decimal("0.00"):
                charge.status = "partial"

        # Tier 1: Past Due / Overdue Installments
        for inst in past_due:
            inst_interest = _get_interest(inst)
            inst_principal = max(
                Decimal("0.00"),
                _get_principal(inst) - inst_adj_credits.get(inst.id, Decimal("0.00")),
            )
            interest_unpaid = max(
                Decimal("0.00"), inst_interest - (inst.paid_amount or Decimal("0.00"))
            )
            if interest_unpaid > Decimal("0.00"):
                allocate_installment(
                    inst, principal_part=Decimal("0.00"), interest_part=interest_unpaid
                )

            paid_after_interest = max(
                Decimal("0.00"), (inst.paid_amount or Decimal("0.00")) - inst_interest
            )
            principal_unpaid = max(Decimal("0.00"), inst_principal - paid_after_interest)
            if principal_unpaid > Decimal("0.00"):
                allocate_installment(
                    inst, principal_part=principal_unpaid, interest_part=Decimal("0.00")
                )

        # Tier 2: Statement Charges (assessed fees, taxes, closed statement components)
        for charge in filtered_charges:
            charge_unpaid = max(
                Decimal("0.00"), charge.amount - (charge.paid_amount or Decimal("0.00"))
            )
            if charge_unpaid > Decimal("0.00"):
                allocate_charge(charge, charge_unpaid)

        # Tier 3: Current Period Installments
        # Interest first
        for inst in current_insts:
            inst_interest = _get_interest(inst)
            interest_unpaid = max(
                Decimal("0.00"), inst_interest - (inst.paid_amount or Decimal("0.00"))
            )
            if interest_unpaid > Decimal("0.00"):
                allocate_installment(
                    inst, principal_part=Decimal("0.00"), interest_part=interest_unpaid
                )

        # Principal second
        for inst in current_insts:
            inst_interest = _get_interest(inst)
            inst_principal = max(
                Decimal("0.00"),
                _get_principal(inst) - inst_adj_credits.get(inst.id, Decimal("0.00")),
            )
            paid_after_interest = max(
                Decimal("0.00"), (inst.paid_amount or Decimal("0.00")) - inst_interest
            )
            principal_unpaid = max(Decimal("0.00"), inst_principal - paid_after_interest)
            if principal_unpaid > Decimal("0.00"):
                allocate_installment(
                    inst, principal_part=principal_unpaid, interest_part=Decimal("0.00")
                )

        # Tier 4: Future Principal Prepayment
        is_canonical = getattr(card, "canonical_mode", False)
        for inst in future_insts:
            inst_tot = getattr(inst, "installments_total", 1) or 1
            if not is_canonical and inst_tot > 1:
                # Legacy cards preserve overpayment wall for multi-installment debt
                continue

            inst_principal = max(
                Decimal("0.00"),
                _get_principal(inst) - inst_adj_credits.get(inst.id, Decimal("0.00")),
            )
            principal_unpaid = max(
                Decimal("0.00"), inst_principal - (inst.paid_amount or Decimal("0.00"))
            )
            if principal_unpaid > Decimal("0.00"):
                allocate_installment(
                    inst, principal_part=principal_unpaid, interest_part=Decimal("0.00")
                )
                if (inst.paid_amount or Decimal("0.00")) + inst_adj_credits.get(
                    inst.id, Decimal("0.00")
                ) >= inst.total_amount:
                    inst.status = "paid"

        if remaining > Decimal("0.00"):
            raise InvalidPaymentAmountError(
                message="Voluntary overpayment above total payable debt is not permitted"
            )

        return allocations

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

        disallowed_keys = {
            "canonical_mode",
            "canonical_activated_at",
            "provenance",
            "lifecycle_status",
        }
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
        raw_orig_currency = (
            payload.original_currency or payload.currency or preliminary_card.currency
        )
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
                if preliminary_card.provenance
                in (PROVENANCE_ISSUER_AUTHORITATIVE, PROVENANCE_MANUAL)
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
                from app.credit.exceptions import CreditFXRateUnavailableError

                raise CreditFXRateUnavailableError()

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
                    "fx_rate_snapshot_id": str(fx_rate_snapshot_id)
                    if fx_rate_snapshot_id
                    else None,
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
                if not transaction:
                    raise CreditDomainError(
                        f"Inconsistent state: event {event.id} exists for command {command_id} but CreditCardTransaction is missing"
                    )
                retry_lifecycle = transaction.lifecycle_status
                return CreditCardPurchaseResult(
                    status="idempotent_retry",
                    event_id=event.id,
                    transaction_id=transaction.id,
                    amount=event.amount,
                    current_debt=estimated_debt,
                    available_credit=estimated_available,
                    estimated_current_debt=estimated_debt,
                    estimated_available_credit=estimated_available,
                    cycle_id=transaction.cycle_id
                    if (transaction and retry_lifecycle == "posted")
                    else None,
                    billing_period=transaction.period
                    if (transaction and retry_lifecycle == "posted")
                    else None,
                    original_amount=transaction.original_amount if transaction else original_amount,
                    original_currency=transaction.original_currency
                    if transaction
                    else original_currency,
                    booked_amount=transaction.booked_amount if transaction else booked_amount,
                    booked_currency=transaction.booked_currency if transaction else booked_currency,
                    fx_rate=transaction.fx_rate if transaction else fx_rate,
                    fx_rate_snapshot_id=transaction.fx_rate_snapshot_id
                    if transaction
                    else fx_rate_snapshot_id,
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

                existing_cycle = await self.repo.get_billing_cycle_by_period_for_update(
                    card.id, billing_period
                )
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
        cmd_uuid = self._normalize_command_id(command_id)

        # 1. Deep Idempotency Guard (F-16) - Check before external FX quote to prevent rate-drift false conflicts
        temp_account = await self.account_repo.get_by_id(payload.account_id)
        source_currency = getattr(temp_account, "currency", None) if temp_account else None

        existing_event = await self._check_operation_idempotency(
            user_id=user_id,
            command_id=cmd_uuid,
            operation_type="credit_card_payment",
            credit_card_id=card_id,
            account_id=payload.account_id,
            original_amount=payload.amount,
            original_currency=source_currency,
        )
        if existing_event:
            card = await self.repo.get_by_id(card_id)
            if not card:
                raise CreditDomainError(
                    f"Inconsistent state: card {card_id} missing on idempotent retry"
                )
            account = await self.account_repo.get_by_id(payload.account_id)
            if not account:
                raise CreditDomainError(
                    f"Inconsistent state: account {payload.account_id} missing on idempotent retry"
                )
            transaction = await self.repo.get_transaction_by_event(existing_event.id)
            if not transaction:
                raise CreditDomainError(
                    f"Inconsistent state: transaction for event {existing_event.id} missing on idempotent retry"
                )
            debt_data = await self.repo.get_card_debt_partitioned(card_id)
            total_debt = debt_data["total_debt"]
            return CreditCardPaymentResult(
                status="idempotent_retry",
                event_id=existing_event.id,
                transaction_id=transaction.id,
                amount=existing_event.amount,
                current_debt=total_debt,
                available_credit=card.credit_limit - total_debt,
                estimated_current_debt=total_debt,
                account_balance=account.balance,
            )

        # 2. External FX pre-fetching before acquiring database write locks (F-14 / F-17)
        temp_card = await self.repo.get_by_id(card_id)
        pre_fx_info = None
        acc_curr = source_currency
        card_curr = getattr(temp_card, "currency", None)
        if isinstance(acc_curr, str) and isinstance(card_curr, str) and acc_curr != card_curr:
            pre_fx_info = await self._get_fx_info(
                acc_curr, card_curr, persist_snapshot=True
            )

        # 3. GLOBAL LOCK ORDER: Account -> CreditCard -> Debt components (F-14)
        account = await self.account_repo.get_by_id_for_update(payload.account_id)
        if not account:
            from app.core.errors import NotFoundError

            raise NotFoundError(message="Cuenta no encontrada.")

        if account.user_id != user_id:
            from app.accounts.exceptions import AccountForbiddenError

            raise AccountForbiddenError()

        if not account.is_active:
            from app.credit.exceptions import CreditAccountInactiveError

            raise CreditAccountInactiveError()

        card = await self.repo.get_by_id_for_update(card_id)
        if not card or card.user_id != user_id:
            raise CreditCardNotFoundError()
        if not card.is_active:
            raise CreditCardInactiveError()

        source_currency = account.currency
        target_currency = card.currency

        if not source_currency or not target_currency or source_currency == target_currency:
            applied_amount = payload.amount
            source_amount = payload.amount
            fx_rate = Decimal("1.0")
            rate_source = "internal"
            rate_timestamp = None
            is_estimated = False
        else:
            if (
                not pre_fx_info
                or pre_fx_info.get("from_currency") != source_currency
                or pre_fx_info.get("to_currency") != target_currency
            ):
                raise CreditDomainError(
                    "Missing or drifted pre-fetched FX quote under locks; operation must be retried"
                )
            fx_rate = pre_fx_info["fx_rate"]
            rate_source = pre_fx_info["rate_source"]
            rate_timestamp = pre_fx_info["rate_timestamp"]
            fx_rate_snapshot_id = pre_fx_info["fx_rate_snapshot_id"]
            is_estimated = True
            source_amount = payload.amount
            applied_amount = round_to_minimum_unit(payload.amount * fx_rate, target_currency)

        # 4. Check Authoritative Goal Reservations under Account lock (F-13)
        await self._verify_spendable_balance(account, user_id, source_amount)

        # 5. Lock debt components in deterministic order & calculate total payable debt (F-19)
        installments = await self.repo.list_pending_installments_for_update(card.id, user_id)
        charges = await self.repo.list_unpaid_statement_charges_for_update(card.id)

        installment_ids = {i.id for i in installments if getattr(i, "id", None)}
        payable_charges = [
            c
            for c in charges
            if getattr(c, "status", "") != "paid"
            and (getattr(c, "reference_id", None) is None or c.reference_id not in installment_ids)
        ]

        current_date = self._get_financial_date()
        current_period = current_date.strftime("%Y-%m")
        is_canonical = getattr(card, "canonical_mode", False)

        inst_adj_credits: dict[uuid.UUID, Decimal] = {}
        for i in installments:
            if getattr(i, "id", None):
                adj_allocs = await self.repo.list_credit_adjustment_allocations_for_installment(
                    i.id
                )
                inst_adj_credits[i.id] = sum(a.allocated_amount for a in adj_allocs)

        total_payable_debt = Decimal("0.00")
        for chg in payable_charges:
            chg_amt = getattr(chg, "amount", Decimal("0.00")) or Decimal("0.00")
            chg_paid = getattr(chg, "paid_amount", Decimal("0.00")) or Decimal("0.00")
            total_payable_debt += max(Decimal("0.00"), chg_amt - chg_paid)
        for inst in installments:
            inst_p = getattr(inst, "scheduled_period", "") or ""
            inst_princ = max(
                Decimal("0.00"),
                (getattr(inst, "principal_amount", Decimal("0.00")) or Decimal("0.00"))
                - inst_adj_credits.get(inst.id, Decimal("0.00")),
            )
            inst_paid = getattr(inst, "paid_amount", Decimal("0.00")) or Decimal("0.00")
            inst_assessed = getattr(inst, "assessed_interest_amount", None)
            inst_legacy = getattr(inst, "interest_amount", Decimal("0.00")) or Decimal("0.00")
            inst_interest = inst_assessed if inst_assessed is not None else inst_legacy
            if inst_p <= current_period:
                total_payable_debt += max(
                    Decimal("0.00"),
                    (inst_princ + inst_interest) - inst_paid,
                )
            else:
                inst_total = getattr(inst, "installments_total", 1) or 1
                if is_canonical or inst_total == 1:
                    total_payable_debt += max(
                        Decimal("0.00"),
                        inst_princ - inst_paid,
                    )

        # Voluntary overpayment guard
        if applied_amount > total_payable_debt:
            raise InvalidPaymentAmountError(
                message="Payment amount exceeds total payable debt",
                detail={
                    "applied_amount": str(applied_amount),
                    "total_payable_debt": str(total_payable_debt),
                },
            )

        # 6. Record Ledger Event
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
                "operation_type": "credit_card_payment",
                "credit_card_id": str(card.id),
                "account_id": str(account.id),
                "fx_rate": str(fx_rate),
                "rate_source": rate_source,
                "rate_timestamp": rate_timestamp.isoformat() if rate_timestamp else None,
                "is_estimated": is_estimated,
                "applied_amount": str(applied_amount),
                "applied_currency": target_currency,
            },
        )
        event_payload.command_id = cmd_uuid
        result = await self.ledger_service.record_event(event_payload)
        event, is_retry = result.event, result.idempotent

        if is_retry:
            transaction = await self.repo.get_transaction_by_event(event.id)
            if not transaction:
                raise CreditDomainError(
                    f"Inconsistent state: transaction for event {event.id} missing on idempotent retry"
                )
            debt_data = await self.repo.get_card_debt_partitioned(card.id)
            total_debt = debt_data["total_debt"]
            return CreditCardPaymentResult(
                status="idempotent_retry",
                event_id=event.id,
                transaction_id=transaction.id,
                amount=event.amount,
                current_debt=total_debt,
                available_credit=card.credit_limit - total_debt,
                estimated_current_debt=total_debt,
                account_balance=account.balance,
            )

        # 7. Debit Account: Account debit strictly equals Ledger source amount
        account.balance -= source_amount

        # 8. Create CreditCardTransaction
        transaction = CreditCardTransaction(
            user_id=user_id,
            credit_card_id=card.id,
            type="payment",
            amount=applied_amount,
            account_id=payload.account_id,
            event_id=event.id,
            occurred_at=self._get_financial_date(),
            fx_rate=fx_rate if is_estimated else None,
            fx_rate_snapshot_id=fx_rate_snapshot_id if is_estimated else None,
            lifecycle_status="posted",
            provenance=PROVENANCE_NEXUM_MODELED,
            debt_partition=getattr(card, "debt_partition", None) or DEBT_PARTITION_CANONICAL,
            metadata_={
                "source_amount": str(source_amount),
                "source_currency": source_currency,
                "applied_amount": str(applied_amount),
                "applied_currency": target_currency,
                "fx_rate": str(fx_rate),
                "rate_source": rate_source,
                "rate_timestamp": rate_timestamp.isoformat() if rate_timestamp else None,
                "fx_rate_snapshot_id": str(fx_rate_snapshot_id)
                if is_estimated
                else None,
                "is_estimated": is_estimated,
            },
        )
        await self.repo.add_transaction(transaction)

        # 9. Create Canonical CreditCardPayment
        fp = build_credit_fingerprint(
            user_id=user_id,
            operation_type="credit_card_payment",
            credit_card_id=card.id,
            booked_amount=applied_amount,
            booked_currency=target_currency,
            original_amount=source_amount,
            original_currency=source_currency,
            fx_rate=fx_rate,
            payment_date=self._get_financial_date(),
        )
        fp_hash = hash_credit_fingerprint(fp)

        payment = CreditCardPayment(
            id=uuid.uuid4(),
            user_id=user_id,
            credit_card_id=card.id,
            account_id=payload.account_id,
            payment_transaction_id=transaction.id,
            event_id=event.id,
            booked_amount=applied_amount,
            booked_currency=target_currency,
            source_amount=source_amount,
            source_currency=source_currency,
            fx_rate=fx_rate if is_estimated else None,
            fx_rate_snapshot_id=fx_rate_snapshot_id if is_estimated else None,
            principal_applied=Decimal("0.00"),
            interest_applied=Decimal("0.00"),
            fee_applied=Decimal("0.00"),
            unallocated_amount=Decimal("0.00"),
            payment_date=self._get_financial_date(),
            status="posted",
            provenance=PROVENANCE_NEXUM_MODELED,
            allocation_source=ALLOCATION_SOURCE_NEXUM_MODELED,
            command_id=cmd_uuid,
            command_fingerprint=fp_hash,
        )
        # 10. Apply Waterfall and derive the immutable allocation totals before
        # persisting the payment: PostgreSQL enforces payment conservation.
        allocations = await self._apply_payment_to_waterfall(
            user_id=user_id,
            card=card,
            amount=applied_amount,
            payment=payment,
            installments=installments,
            charges=payable_charges,
        )

        payment.principal_applied = sum(a.allocated_principal for a in allocations)
        payment.interest_applied = sum(a.allocated_interest for a in allocations)
        payment.fee_applied = sum(a.allocated_fees for a in allocations)
        payment.unallocated_amount = Decimal("0.00")

        # Payment conservation assertion
        assert payment.booked_amount == (
            payment.principal_applied + payment.interest_applied + payment.fee_applied
        )

        await self.repo.add_payment(payment)
        await self.repo.add_payment_allocations(allocations)
        await self.session.flush()

        # 11. Calculate fresh new debt and available credit
        new_debt = total_payable_debt - applied_amount
        new_available = card.credit_limit - new_debt

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
        cmd_uuid = self._normalize_command_id(command_id)

        # 1. Deep Idempotency Guard (F-16) - Check before external FX quote to prevent rate-drift false conflicts
        temp_account = await self.account_repo.get_by_id(payload.account_id)
        source_currency = getattr(temp_account, "currency", None) if temp_account else None

        existing_event = await self._check_operation_idempotency(
            user_id=user_id,
            command_id=cmd_uuid,
            operation_type="credit_card_pay_early",
            credit_card_id=card_id,
            account_id=payload.account_id,
            target_id=purchase_id,
            original_amount=payload.amount,
            original_currency=source_currency,
        )
        if existing_event:
            card = await self.repo.get_by_id(card_id)
            if not card:
                raise CreditDomainError(
                    f"Inconsistent state: card {card_id} missing on idempotent retry"
                )
            account = await self.account_repo.get_by_id(payload.account_id)
            if not account:
                raise CreditDomainError(
                    f"Inconsistent state: account {payload.account_id} missing on idempotent retry"
                )
            early_payment = await self.repo.get_early_payment_by_event(existing_event.id)
            if not early_payment:
                raise CreditDomainError(
                    f"Inconsistent state: early payment for event {existing_event.id} missing on idempotent retry"
                )
            debt_data = await self.repo.get_card_debt_partitioned(card_id)
            total_debt = debt_data["total_debt"]
            return CreditCardEarlyPaymentResult(
                status="idempotent_retry",
                event_id=existing_event.id,
                early_payment_id=early_payment.id,
                amount=existing_event.amount,
                current_debt=total_debt,
                available_credit=card.credit_limit - total_debt,
                account_balance=account.balance,
            )

        # 2. External FX pre-fetch
        temp_card = await self.repo.get_by_id(card_id)
        pre_fx_info = None
        acc_curr = source_currency
        card_curr = getattr(temp_card, "currency", None)
        if isinstance(acc_curr, str) and isinstance(card_curr, str) and acc_curr != card_curr:
            pre_fx_info = await self._get_fx_info(
                acc_curr, card_curr, persist_snapshot=True
            )

        # 3. GLOBAL LOCK ORDER: Account -> CreditCard -> Target installments (F-14)
        account = await self.account_repo.get_by_id_for_update(payload.account_id)
        if not account:
            from app.core.errors import NotFoundError

            raise NotFoundError(message="Cuenta no encontrada.")

        if account.user_id != user_id:
            from app.accounts.exceptions import AccountForbiddenError

            raise AccountForbiddenError()

        if not account.is_active:
            from app.credit.exceptions import CreditAccountInactiveError

            raise CreditAccountInactiveError()

        card = await self.repo.get_by_id_for_update(card_id)
        if not card or card.user_id != user_id:
            raise CreditCardNotFoundError()
        if not card.is_active:
            raise CreditCardInactiveError()

        transaction = await self.repo.get_transaction_by_id(purchase_id)
        if not transaction or transaction.credit_card_id != card.id:
            raise CreditDomainError("Purchase not found or does not belong to this card")

        all_installments = await self.repo.list_installments_for_transaction_for_update(purchase_id)
        installments = [i for i in all_installments if i.status != "paid"]

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

        # Deterministic sorting: due date / scheduled period -> installment number -> created_at -> id
        def _early_inst_sort_key(x):
            due = getattr(x, "scheduled_due_date", None) or getattr(x, "scheduled_period", "") or ""
            num = getattr(x, "installment_number", 0) or 0
            ca = getattr(x, "created_at", None) or datetime.min
            return (due, num, ca, str(x.id))

        future_installments.sort(key=_early_inst_sort_key)

        inst_adj_credits: dict[uuid.UUID, Decimal] = {}
        for i in future_installments:
            if getattr(i, "id", None):
                adj_allocs = await self.repo.list_credit_adjustment_allocations_for_installment(
                    i.id
                )
                inst_adj_credits[i.id] = sum(a.allocated_amount for a in adj_allocs)

        total_remaining_principal = sum(
            max(
                Decimal("0.00"),
                (i.principal_amount or Decimal("0.00"))
                - (i.paid_amount or Decimal("0.00"))
                - inst_adj_credits.get(i.id, Decimal("0.00")),
            )
            for i in future_installments
        )
        if total_remaining_principal <= Decimal("0.00"):
            raise CreditDomainError("PAY_EARLY_NOT_ELIGIBLE: Remaining principal is zero.")

        source_currency = account.currency
        target_currency = card.currency

        if not source_currency or not target_currency or source_currency == target_currency:
            fx_rate = Decimal("1.0")
            rate_source = "internal"
            rate_timestamp = None
            is_estimated = False
        else:
            if (
                not pre_fx_info
                or pre_fx_info.get("from_currency") != source_currency
                or pre_fx_info.get("to_currency") != target_currency
            ):
                raise CreditDomainError(
                    "Missing or drifted pre-fetched FX quote under locks; operation must be retried"
                )
            fx_rate = pre_fx_info["fx_rate"]
            rate_source = pre_fx_info["rate_source"]
            rate_timestamp = pre_fx_info["rate_timestamp"]
            fx_rate_snapshot_id = pre_fx_info["fx_rate_snapshot_id"]
            is_estimated = True

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

        if applied_amount > total_remaining_principal:
            raise InvalidPaymentAmountError(
                message="Early payment amount exceeds remaining principal"
            )

        # 4. Check Authoritative Goal Reservations (F-13)
        await self._verify_spendable_balance(account, user_id, source_amount)

        # 5. Record Ledger Event
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
                "operation_type": "credit_card_pay_early",
                "credit_card_id": str(card.id),
                "account_id": str(account.id),
                "purchase_transaction_id": str(purchase_id),
                "fx_rate": str(fx_rate),
                "rate_source": rate_source,
                "rate_timestamp": rate_timestamp.isoformat() if rate_timestamp else None,
                "is_estimated": is_estimated,
                "applied_amount": str(applied_amount),
                "applied_currency": target_currency,
            },
        )
        event_payload.command_id = cmd_uuid
        result = await self.ledger_service.record_event(event_payload)
        event, is_retry = result.event, result.idempotent

        if is_retry:
            early_payment = await self.repo.get_early_payment_by_event(event.id)
            if not early_payment:
                raise CreditDomainError(
                    f"Inconsistent state: early payment for event {event.id} missing on idempotent retry"
                )
            debt_data = await self.repo.get_card_debt_partitioned(card.id)
            total_debt = debt_data["total_debt"]
            return CreditCardEarlyPaymentResult(
                status="idempotent_retry",
                event_id=event.id,
                early_payment_id=early_payment.id,
                amount=event.amount,
                current_debt=total_debt,
                available_credit=card.credit_limit - total_debt,
                account_balance=account.balance,
            )

        # 6. Debit Account: Account debit strictly equals Ledger source amount
        account.balance -= source_amount

        # 7. Create CreditCardTransaction (Payment)
        payment_tx = CreditCardTransaction(
            user_id=user_id,
            credit_card_id=card.id,
            type="payment",
            amount=applied_amount,
            account_id=payload.account_id,
            event_id=event.id,
            occurred_at=self._get_financial_date(),
            fx_rate=fx_rate if is_estimated else None,
            fx_rate_snapshot_id=fx_rate_snapshot_id if is_estimated else None,
            lifecycle_status="posted",
            provenance=PROVENANCE_NEXUM_MODELED,
            debt_partition=getattr(card, "debt_partition", None) or DEBT_PARTITION_CANONICAL,
            metadata_={
                "source_amount": str(source_amount),
                "source_currency": source_currency,
                "applied_amount": str(applied_amount),
                "applied_currency": target_currency,
                "purchase_transaction_id": str(purchase_id),
                "fx_rate": str(fx_rate),
                "rate_source": rate_source,
                "rate_timestamp": rate_timestamp.isoformat() if rate_timestamp else None,
                "fx_rate_snapshot_id": str(fx_rate_snapshot_id)
                if is_estimated
                else None,
                "is_estimated": is_estimated,
            },
        )
        await self.repo.add_transaction(payment_tx)

        # 8. Create Canonical CreditCardPayment
        fp = build_credit_fingerprint(
            user_id=user_id,
            operation_type="credit_card_pay_early",
            credit_card_id=card.id,
            target_id=purchase_id,
            booked_amount=applied_amount,
            booked_currency=target_currency,
            original_amount=source_amount,
            original_currency=source_currency,
            fx_rate=fx_rate,
            payment_date=self._get_financial_date(),
        )
        fp_hash = hash_credit_fingerprint(fp)

        payment = CreditCardPayment(
            id=uuid.uuid4(),
            user_id=user_id,
            credit_card_id=card.id,
            account_id=payload.account_id,
            payment_transaction_id=payment_tx.id,
            event_id=event.id,
            booked_amount=applied_amount,
            booked_currency=target_currency,
            source_amount=source_amount,
            source_currency=source_currency,
            fx_rate=fx_rate if is_estimated else None,
            fx_rate_snapshot_id=fx_rate_snapshot_id if is_estimated else None,
            principal_applied=applied_amount,
            interest_applied=Decimal("0.00"),
            fee_applied=Decimal("0.00"),
            unallocated_amount=Decimal("0.00"),
            payment_date=self._get_financial_date(),
            status="posted",
            provenance=PROVENANCE_NEXUM_MODELED,
            allocation_source=ALLOCATION_SOURCE_USER_DIRECTED,
            command_id=cmd_uuid,
            command_fingerprint=fp_hash,
        )

        # 9. Create Payment Allocations for the purchase's installments (F-19)
        rem_alloc = applied_amount
        allocations = []
        for idx, inst in enumerate(future_installments, start=1):
            if rem_alloc <= Decimal("0.00"):
                break
            inst_unpaid = max(
                Decimal("0.00"),
                (inst.principal_amount or Decimal("0.00"))
                - (inst.paid_amount or Decimal("0.00"))
                - inst_adj_credits.get(inst.id, Decimal("0.00")),
            )
            if inst_unpaid > Decimal("0.00"):
                alloc_amt = min(rem_alloc, inst_unpaid)
                alloc = CreditCardPaymentAllocation(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    credit_card_id=card.id,
                    payment_id=payment.id,
                    target_installment_id=inst.id,
                    target_charge_id=None,
                    target_statement_component_id=None,
                    allocated_amount=alloc_amt,
                    allocated_principal=alloc_amt,
                    allocated_interest=Decimal("0.00"),
                    allocated_fees=Decimal("0.00"),
                    allocation_order=idx,
                    allocation_source=ALLOCATION_SOURCE_USER_DIRECTED,
                    provenance=PROVENANCE_NEXUM_MODELED,
                )
                allocations.append(alloc)
                inst.paid_amount = (inst.paid_amount or Decimal("0.00")) + alloc_amt
                tot_covered = (inst.paid_amount or Decimal("0.00")) + inst_adj_credits.get(
                    inst.id, Decimal("0.00")
                )
                if tot_covered >= inst.total_amount:
                    inst.status = "paid"
                elif inst.paid_amount > Decimal("0.00"):
                    inst.status = "partial"
                rem_alloc -= alloc_amt

        # Payment conservation assertion
        assert sum(a.allocated_amount for a in allocations) == applied_amount
        for a in allocations:
            assert (
                a.allocated_principal + a.allocated_interest + a.allocated_fees
                == a.allocated_amount
            )

        await self.repo.add_payment(payment)
        await self.repo.add_payment_allocations(allocations)

        # 10. Legacy CreditCardEarlyPayment row for compatibility
        early_payment = CreditCardEarlyPayment(
            user_id=user_id,
            credit_card_id=card.id,
            purchase_transaction_id=purchase_id,
            payment_transaction_id=payment_tx.id,
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

        await self.session.flush()

        debt_data = await self.repo.get_card_debt_partitioned(card.id)
        new_debt = debt_data["total_debt"]

        return CreditCardEarlyPaymentResult(
            status="success",
            event_id=event.id,
            early_payment_id=early_payment.id,
            amount=source_amount,
            current_debt=new_debt,
            available_credit=card.credit_limit - new_debt,
            account_balance=account.balance,
        )

    async def preview_early_payment(
        self,
        user_id: uuid.UUID,
        card_id: uuid.UUID,
        purchase_id: uuid.UUID,
        payload: CreditCardEarlyPaymentPreviewCreate,
    ) -> PaymentPreviewResult:
        # READ-ONLY: No FOR UPDATE, no transaction mutation, no external call under locks (F-17)
        card = await self.repo.get_by_id(card_id)
        if not card or card.user_id != user_id or not card.is_active:
            raise CreditCardNotFoundError()

        transaction = await self.repo.get_transaction_by_id(purchase_id)
        if not transaction or transaction.credit_card_id != card.id:
            raise CreditDomainError("Purchase not found or does not belong to this card")

        all_installments = await self.repo.list_installments_for_transaction(purchase_id)
        installments = [i for i in all_installments if i.status != "paid"]

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
            max(
                Decimal("0.00"),
                (i.principal_amount or Decimal("0.00")) - (i.paid_amount or Decimal("0.00")),
            )
            for i in future_installments
        )
        if total_remaining_principal <= Decimal("0.00"):
            raise CreditDomainError("PAY_EARLY_NOT_ELIGIBLE: Remaining principal is zero.")

        account = await self.account_repo.get_by_id(payload.account_id)
        if not account:
            from app.core.errors import NotFoundError

            raise NotFoundError(message="Cuenta no encontrada.")

        if account.user_id != user_id:
            from app.accounts.exceptions import AccountForbiddenError

            raise AccountForbiddenError()

        if not account.is_active:
            from app.credit.exceptions import CreditAccountInactiveError

            raise CreditAccountInactiveError()

        source_currency = account.currency
        target_currency = card.currency

        if not source_currency or not target_currency or source_currency == target_currency:
            fx_rate = Decimal("1.0")
            rate_source = "internal"
            is_estimated = False
        else:
            fx_info = await self._get_fx_info(
                source_currency, target_currency, persist_snapshot=False
            )

            fx_rate = fx_info["fx_rate"]
            rate_source = fx_info["rate_source"]
            is_estimated = True

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

    async def create_adjustment(
        self,
        user_id: uuid.UUID,
        card_id: uuid.UUID,
        payload: CreditCardAdjustmentCreate,
        command_id: uuid.UUID | str,
    ) -> CreditCardAdjustmentResult:
        cmd_uuid = self._normalize_command_id(command_id)

        # 0. Check provenance / allocation_source constraints (Item 6)
        if payload.provenance == "issuer_authoritative":
            raise CreditDomainError(
                "Cannot claim issuer_authoritative provenance on manual adjustment endpoint"
            )
        if payload.allocation_source == "issuer_authoritative":
            raise CreditDomainError(
                "Cannot claim issuer_authoritative allocation_source on manual adjustment endpoint"
            )

        # 1. Read-only transaction check to determine target semantics and account association
        preview_tx = await self.repo.get_transaction_by_id(payload.original_transaction_id)
        if not preview_tx or preview_tx.credit_card_id != card_id:
            raise CreditDomainError(
                "Original transaction not found or does not belong to this card"
            )

        temp_card = await self.repo.get_by_id(card_id)
        if not temp_card or temp_card.user_id != user_id:
            raise CreditCardNotFoundError()
        if not temp_card.is_active:
            raise CreditCardInactiveError()
        card_currency = temp_card.currency or "COP"

        account = None
        target_account_id = None
        payment_info = None

        if preview_tx.type == "payment":
            if payload.adjustment_type != "reversal":
                raise CreditLifecycleInvalidityError(
                    f"Payment target only supports adjustment_type='reversal', got '{payload.adjustment_type}'"
                )
            if payload.direction != "debit":
                raise CreditLifecycleInvalidityError(
                    f"Payment reversal requires direction='debit', got '{payload.direction}'"
                )

            payment_info = await self.repo.get_payment_by_transaction_id(preview_tx.id)
            if not payment_info:
                raise CreditLifecycleInvalidityError(
                    "Payment reversal requires an existing canonical payment record"
                )

            # Full amount conservation check (Item 1 & 2)
            if payload.amount != payment_info.booked_amount:
                raise InvalidPaymentAmountError(
                    message=f"Payment reversal must be exact full amount ({payment_info.booked_amount}), got {payload.amount}",
                    detail={
                        "payment_booked_amount": str(payment_info.booked_amount),
                        "reversal_requested_amount": str(payload.amount),
                    },
                )

            target_account_id = payment_info.account_id or preview_tx.account_id
            if not target_account_id:
                raise CreditLifecycleInvalidityError(
                    "Payment reversal requires a canonical payment with an associated account"
                )

            source_amount = payment_info.source_amount or payment_info.booked_amount
            source_currency = payment_info.source_currency or card_currency

        elif preview_tx.type == "purchase":
            if payload.adjustment_type not in ("refund", "chargeback", "reversal"):
                raise CreditLifecycleInvalidityError(
                    f"Purchase target does not support adjustment_type='{payload.adjustment_type}'"
                )
            if payload.direction != "credit":
                raise CreditLifecycleInvalidityError(
                    f"Purchase adjustments require direction='credit', got '{payload.direction}'"
                )
            target_account_id = None
            source_amount = payload.original_amount or payload.amount
            source_currency = payload.original_currency or card_currency
        else:
            raise CreditLifecycleInvalidityError(
                f"Unsupported transaction type '{preview_tx.type}' for adjustment"
            )

        # 2. Deep Idempotency Guard (F-16) - MUST check BEFORE locks and BEFORE lifecycle rejection
        existing_event = await self._check_operation_idempotency(
            user_id=user_id,
            command_id=cmd_uuid,
            operation_type="credit_card_adjustment",
            credit_card_id=card_id,
            account_id=target_account_id,
            booked_amount=payload.amount,
            booked_currency=card_currency,
            target_id=payload.original_transaction_id,
            original_amount=source_amount,
            original_currency=source_currency,
            adjustment_type=payload.adjustment_type,
            direction=payload.direction,
            provenance=payload.provenance,
            allocation_source=payload.allocation_source,
        )
        if existing_event:
            existing_adj = await self.repo.get_adjustment_by_command_id(user_id, cmd_uuid)
            if not existing_adj:
                raise CreditDomainError(
                    f"Inconsistent state: event {existing_event.id} exists for command {cmd_uuid} but CreditCardAdjustment is missing"
                )
            debt_data = await self.repo.get_card_debt_partitioned(card_id)
            total_debt = debt_data["total_debt"]
            return CreditCardAdjustmentResult(
                status="idempotent_retry",
                adjustment_id=existing_adj.id,
                event_id=existing_event.id,
                amount=payload.amount,
                current_debt=total_debt,
                available_credit=temp_card.credit_limit - total_debt,
                adjustment_type=payload.adjustment_type,
                direction=payload.direction,
                provenance=existing_adj.provenance,
                allocation_source=existing_adj.allocation_source,
            )

        # 3. Lifecycle Status Validation for NEW command
        if preview_tx.type == "payment":
            if payment_info.status == "reversed" or preview_tx.lifecycle_status == "reversed":
                raise CreditLifecycleInvalidityError(
                    "Payment transaction has already been reversed"
                )
        elif preview_tx.type == "purchase":
            if preview_tx.lifecycle_status == "reversed":
                raise CreditLifecycleInvalidityError(
                    "Purchase transaction has already been reversed"
                )

        # 4. Strict global lock hierarchy (F-14): Account -> Card -> components
        if preview_tx.type == "payment":
            # Strict global lock hierarchy: Lock Account FIRST!
            account = await self.account_repo.get_by_id_for_update(target_account_id)
            if not account:
                from app.core.errors import NotFoundError

                raise NotFoundError(message=f"Account {target_account_id} not found")
            if account.user_id != user_id:
                from app.accounts.exceptions import AccountForbiddenError

                raise AccountForbiddenError()

            if not account.is_active:
                from app.credit.exceptions import CreditAccountInactiveError

                raise CreditAccountInactiveError()
        else:
            account = None

        # Lock Card SECOND (strictly after Account if Account is locked)
        card = await self.repo.get_by_id_for_update(card_id)
        if not card or card.user_id != user_id:
            raise CreditCardNotFoundError()
        if not card.is_active:
            raise CreditCardInactiveError()

        # Lock original transaction FOR UPDATE
        original_tx = await self.repo.get_transaction_by_id_for_update(
            payload.original_transaction_id
        )
        if not original_tx or original_tx.credit_card_id != card.id:
            raise CreditDomainError(
                "Original transaction not found or does not belong to this card"
            )

        # Under locks: revalidate that locked payment still points to that exact same Account and lifecycle hasn't drifted
        if original_tx.type == "payment":
            if original_tx.lifecycle_status == "reversed":
                raise CreditLifecycleInvalidityError(
                    "Payment transaction has already been reversed"
                )
            locked_payment = await self.repo.get_payment_by_transaction_id(original_tx.id)
            if not locked_payment:
                raise CreditLifecycleInvalidityError("Payment record missing under lock")
            if locked_payment.status == "reversed":
                raise CreditLifecycleInvalidityError("Payment has already been reversed")
            if (locked_payment.account_id or original_tx.account_id) != target_account_id:
                raise CreditDomainError(
                    "Payment associated account changed between preview and write lock"
                )
        elif original_tx.type == "purchase":
            if original_tx.lifecycle_status == "reversed":
                raise CreditLifecycleInvalidityError(
                    "Purchase transaction has already been reversed"
                )

        fp = build_credit_fingerprint(
            user_id=user_id,
            operation_type="credit_card_adjustment",
            credit_card_id=card.id,
            target_id=original_tx.id,
            booked_amount=payload.amount,
            booked_currency=card.currency,
            original_amount=source_amount,
            original_currency=source_currency,
            payment_date=self._get_financial_date(),
        )
        fp_hash = hash_credit_fingerprint(fp)

        # 3. Handle by original transaction type
        if original_tx.type == "purchase":
            if original_tx.lifecycle_status == "reversed":
                raise CreditLifecycleInvalidityError(
                    "Purchase transaction has already been reversed"
                )
            existing_adjustments = await self.repo.list_adjustments_for_transaction(original_tx.id)
            total_prior_refunds = sum(
                a.booked_amount
                for a in existing_adjustments
                if a.status == "posted" and a.direction == "credit"
            )
            purchase_amount = original_tx.booked_amount or original_tx.amount
            if total_prior_refunds + payload.amount > purchase_amount:
                raise InvalidPaymentAmountError(
                    message="Refund amount exceeds remaining purchase amount",
                    detail={
                        "purchase_amount": str(purchase_amount),
                        "total_prior_refunds": str(total_prior_refunds),
                        "requested_refund": str(payload.amount),
                    },
                )

            # Record Ledger FinancialEvent with EventType.MANUAL_ADJUSTMENT and Direction.NEUTRAL
            event_payload = LedgerEventCreate(
                user_id=user_id,
                account_id=None,
                category_id=None,
                event_type=EventType.MANUAL_ADJUSTMENT,
                direction=Direction.NEUTRAL,
                amount=payload.amount,
                currency=card.currency,
                description=payload.reason or f"Credit card adjustment: {payload.adjustment_type}",
                source="credit_adjustment",
                source_message_id=payload.source_message_id,
                occurred_at=datetime.now(UTC),
                metadata={
                    "operation_type": "credit_card_adjustment",
                    "command_id": str(cmd_uuid),
                    "command_fingerprint": fp_hash,
                    "credit_card_id": str(card.id),
                    "target_transaction_id": str(original_tx.id),
                    "adjustment_type": payload.adjustment_type,
                    "direction": payload.direction,
                    "provenance": payload.provenance,
                    "allocation_source": payload.allocation_source,
                    "applied_amount": str(payload.amount),
                    "applied_currency": card.currency,
                },
            )
            event_payload.command_id = cmd_uuid
            ledger_result = await self.ledger_service.record_event(event_payload)
            event = ledger_result.event

            # Create immutable CreditCardAdjustment
            adjustment = CreditCardAdjustment(
                id=uuid.uuid4(),
                user_id=user_id,
                credit_card_id=card.id,
                original_transaction_id=original_tx.id,
                event_id=event.id,
                adjustment_type=payload.adjustment_type,
                direction=payload.direction,
                booked_amount=payload.amount,
                booked_currency=card.currency,
                status="posted",
                provenance=payload.provenance,
                allocation_source=payload.allocation_source,
                command_id=cmd_uuid,
                command_fingerprint=fp_hash,
                reason=payload.reason,
                occurred_at=self._get_financial_date(),
            )
            await self.repo.add_adjustment(adjustment)

            # Allocate refund to installments without mutating original principal or total
            installments = await self.repo.list_installments_for_transaction_for_update(
                original_tx.id
            )
            rem_refund = payload.amount
            allocations = []
            order_idx = 1

            for inst in reversed(installments):
                if rem_refund <= Decimal("0.00"):
                    break
                prior_adj_allocs = (
                    await self.repo.list_credit_adjustment_allocations_for_installment(inst.id)
                )
                prior_adj = sum(a.allocated_amount for a in prior_adj_allocs)
                unpaid_inst = max(
                    Decimal("0.00"),
                    inst.principal_amount - (inst.paid_amount or Decimal("0.00")) - prior_adj,
                )
                if unpaid_inst > Decimal("0.00"):
                    reduce_amt = min(rem_refund, unpaid_inst)
                    alloc = CreditCardAdjustmentAllocation(
                        id=uuid.uuid4(),
                        user_id=user_id,
                        credit_card_id=card.id,
                        adjustment_id=adjustment.id,
                        target_installment_id=inst.id,
                        target_transaction_id=None,
                        target_charge_id=None,
                        allocated_amount=reduce_amt,
                        allocated_principal=reduce_amt,
                        allocated_interest=Decimal("0.00"),
                        allocated_fees=Decimal("0.00"),
                        allocation_order=order_idx,
                        allocation_source=payload.allocation_source,
                        provenance=payload.provenance,
                    )
                    allocations.append(alloc)
                    order_idx += 1
                    rem_refund -= reduce_amt

                    # Item 10: Completion based on full payable total (principal + assessed real interest)
                    inst_full_payable = inst.principal_amount + (
                        inst.assessed_interest_amount or Decimal("0.00")
                    )
                    total_settled = (inst.paid_amount or Decimal("0.00")) + prior_adj + reduce_amt
                    if total_settled >= inst_full_payable:
                        inst.status = (
                            "cancelled"
                            if (inst.paid_amount or Decimal("0.00")) == Decimal("0.00")
                            and inst_full_payable == prior_adj + reduce_amt
                            else "paid"
                        )
                    else:
                        inst.status = (
                            "partial"
                            if (inst.paid_amount or Decimal("0.00")) > Decimal("0.00")
                            else "pending"
                        )

            # Excess refund on already-paid purchase creates traceable card credit
            if rem_refund > Decimal("0.00"):
                alloc = CreditCardAdjustmentAllocation(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    credit_card_id=card.id,
                    adjustment_id=adjustment.id,
                    target_installment_id=None,
                    target_transaction_id=original_tx.id,
                    target_charge_id=None,
                    allocated_amount=rem_refund,
                    allocated_principal=rem_refund,
                    allocated_interest=Decimal("0.00"),
                    allocated_fees=Decimal("0.00"),
                    allocation_order=order_idx,
                    allocation_source=payload.allocation_source,
                    provenance=payload.provenance,
                )
                allocations.append(alloc)

                credit_tx = CreditCardTransaction(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    credit_card_id=card.id,
                    type="refund",
                    amount=rem_refund,
                    booked_amount=rem_refund,
                    booked_currency=card.currency,
                    occurred_at=self._get_financial_date(),
                    lifecycle_status="posted",
                    provenance=payload.provenance,
                    debt_partition=DEBT_PARTITION_CANONICAL,
                    metadata_={
                        "adjustment_id": str(adjustment.id),
                        "original_transaction_id": str(original_tx.id),
                        "is_card_credit": True,
                    },
                )
                await self.repo.add_transaction(credit_tx)

            await self.repo.add_adjustment_allocations(allocations)

            if total_prior_refunds + payload.amount == purchase_amount:
                original_tx.lifecycle_status = "reversed"

        elif original_tx.type == "payment":
            if original_tx.lifecycle_status == "reversed":
                raise CreditLifecycleInvalidityError(
                    "Payment transaction has already been reversed"
                )
            payment = await self.repo.get_payment_by_transaction_id(original_tx.id)
            if payment and payment.status == "reversed":
                raise CreditLifecycleInvalidityError("Payment has already been reversed")

            reversal_source_amount = (
                payment.source_amount if payment and payment.source_amount else payload.amount
            )
            reversal_source_currency = (
                payment.source_currency
                if payment and payment.source_currency
                else (account.currency if account else card.currency)
            )

            if payment:
                allocations = await self.repo.list_allocations_for_payment(payment.id)
                for a in allocations:
                    if a.target_installment_id:
                        inst = await self.repo.get_installment_by_id_for_update(
                            a.target_installment_id
                        )
                        if inst:
                            inst.paid_amount = max(
                                Decimal("0.00"),
                                (inst.paid_amount or Decimal("0.00")) - a.allocated_amount,
                            )
                            inst.status = (
                                "partial" if inst.paid_amount > Decimal("0.00") else "pending"
                            )
                    elif a.target_charge_id:
                        charge = await self.repo.get_charge_by_id_for_update(a.target_charge_id)
                        if charge:
                            charge.paid_amount = max(
                                Decimal("0.00"),
                                (charge.paid_amount or Decimal("0.00")) - a.allocated_amount,
                            )
                            charge.status = (
                                "partial" if charge.paid_amount > Decimal("0.00") else "pending"
                            )

                payment.status = "reversed"

                if payment.account_id and reversal_source_amount:
                    if not account:
                        raise CreditDomainError(
                            "Inconsistent state: Account lock was not acquired for payment reversal"
                        )
                    account.balance += reversal_source_amount

            original_tx.lifecycle_status = "reversed"

            event_payload = LedgerEventCreate(
                user_id=user_id,
                account_id=account.id if account else None,
                category_id=None,
                event_type=EventType.MANUAL_ADJUSTMENT,
                direction=Direction.INFLOW if account else Direction.NEUTRAL,
                amount=reversal_source_amount,
                currency=reversal_source_currency,
                description=payload.reason or "Payment reversal",
                source="credit_adjustment",
                source_message_id=payload.source_message_id,
                occurred_at=datetime.now(UTC),
                metadata={
                    "operation_type": "credit_card_adjustment",
                    "command_id": str(cmd_uuid),
                    "command_fingerprint": fp_hash,
                    "credit_card_id": str(card.id),
                    "account_id": str(account.id) if account else None,
                    "target_transaction_id": str(original_tx.id),
                    "adjustment_type": payload.adjustment_type,
                    "direction": payload.direction,
                    "provenance": payload.provenance,
                    "allocation_source": payload.allocation_source,
                    "source_amount": str(reversal_source_amount),
                    "source_currency": reversal_source_currency,
                    "applied_amount": str(payload.amount),
                    "applied_currency": card.currency,
                },
            )
            event_payload.command_id = cmd_uuid
            ledger_result = await self.ledger_service.record_event(event_payload)
            event = ledger_result.event

            adjustment = CreditCardAdjustment(
                id=uuid.uuid4(),
                user_id=user_id,
                credit_card_id=card.id,
                original_transaction_id=original_tx.id,
                event_id=event.id,
                adjustment_type=payload.adjustment_type,
                direction=payload.direction,
                booked_amount=payload.amount,
                booked_currency=card.currency,
                original_amount=reversal_source_amount,
                original_currency=reversal_source_currency,
                status="posted",
                provenance=payload.provenance,
                allocation_source=payload.allocation_source,
                command_id=cmd_uuid,
                command_fingerprint=fp_hash,
                reason=payload.reason,
                occurred_at=self._get_financial_date(),
            )
            await self.repo.add_adjustment(adjustment)
        else:
            raise CreditDomainError(
                f"Unsupported transaction type for adjustment: {original_tx.type}"
            )

        await self.session.flush()

        debt_data = await self.repo.get_card_debt_partitioned(card.id)
        current_debt = debt_data["total_debt"]
        available_credit = card.credit_limit - current_debt

        return CreditCardAdjustmentResult(
            status="success",
            adjustment_id=adjustment.id,
            event_id=event.id,
            amount=payload.amount,
            current_debt=current_debt,
            available_credit=available_credit,
            adjustment_type=payload.adjustment_type,
            direction=payload.direction,
            provenance=payload.provenance,
            allocation_source=payload.allocation_source,
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
        self, user_id: uuid.UUID, card_id: uuid.UUID, auto_catchup: bool = False
    ) -> list[CreditCardStatementRead]:
        card = await self.repo.get_by_id(card_id)
        if not card or card.user_id != user_id:
            raise CreditCardNotFoundError()

        if auto_catchup and card.canonical_mode and card.canonical_activated_at:
            from app.credit.closure_service import CreditCycleClosureService

            closure_service = CreditCycleClosureService(self.session, self.repo)
            await closure_service.close_eligible_cycles_for_card(card.id)

        statements = await self.repo.list_statements(card_id)
        return [await self._statement_to_read_dto(s) for s in statements]

    async def get_statement(
        self, user_id: uuid.UUID, card_id: uuid.UUID, period: str, auto_catchup: bool = False
    ) -> CreditCardStatementRead | None:
        card = await self.repo.get_by_id(card_id)
        if not card or card.user_id != user_id:
            raise CreditCardNotFoundError()

        statement = await self.repo.get_statement(card_id, period)
        if not statement and auto_catchup and card.canonical_mode and card.canonical_activated_at:
            from app.credit.closure_service import CreditCycleClosureService

            closure_service = CreditCycleClosureService(self.session, self.repo)
            await closure_service.close_eligible_cycles_for_card(card.id)
            statement = await self.repo.get_statement(card_id, period)

        if not statement:
            return None
        return await self._statement_to_read_dto(statement)

    async def _statement_to_read_dto(
        self, statement: CreditCardStatement
    ) -> CreditCardStatementRead:
        components = await self.repo.list_components_for_statement(statement.id)
        facts = await self.repo.list_facts_for_statement(statement.id)
        min_fact = next((f for f in facts if f.fact_type == "minimum_payment_due"), None)
        minimum_payment = min_fact.amount if min_fact else statement.minimum_payment

        from app.credit.schemas import (
            CreditCardStatementComponentRead,
            CreditCardStatementFactRead,
        )

        return CreditCardStatementRead(
            id=statement.id,
            user_id=statement.user_id,
            credit_card_id=statement.credit_card_id,
            billing_period=statement.billing_period,
            cycle_id=statement.cycle_id,
            billing_period_start=statement.billing_period_start,
            cutoff_date=statement.cutoff_date,
            due_date=statement.due_date,
            previous_balance=statement.previous_balance,
            new_purchases=statement.new_purchases,
            billed_installments=statement.billed_installments,
            fees_total=statement.fees_total,
            interest_total=statement.interest_total,
            payments_received=statement.payments_received,
            statement_balance=statement.statement_balance,
            minimum_payment=minimum_payment,
            status=statement.status,
            currency=statement.currency,
            assessed_interest=statement.assessed_interest,
            estimated_interest=statement.estimated_interest,
            provenance=statement.provenance,
            debt_partition=statement.debt_partition,
            frozen_at=statement.frozen_at,
            created_at=statement.created_at,
            updated_at=statement.updated_at,
            components=[CreditCardStatementComponentRead.model_validate(c) for c in components],
            facts=[CreditCardStatementFactRead.model_validate(f) for f in facts],
        )

    async def close_eligible_cycles(
        self,
        as_of_date: date | None = None,
        batch_size: int = 50,
        user_id: uuid.UUID | None = None,
        card_id: uuid.UUID | None = None,
    ) -> CreditCardCycleClosureSummary:
        from app.credit.closure_service import CreditCycleClosureService

        closure_service = CreditCycleClosureService(self.session, self.repo)
        return await closure_service.close_all_eligible_cycles(
            as_of_date=as_of_date,
            batch_size=batch_size,
            user_id=user_id,
            card_id=card_id,
        )

    async def close_eligible_cycles_for_card(
        self,
        card_id: uuid.UUID,
        as_of_date: date | None = None,
        max_cycles_per_card: int = 24,
    ) -> tuple[list[CreditCardStatement], list[CreditCardStatementComponent], int, bool, int]:
        from app.credit.closure_service import CreditCycleClosureService

        closure_service = CreditCycleClosureService(self.session, self.repo)
        return await closure_service.close_eligible_cycles_for_card(
            card_id=card_id,
            as_of_date=as_of_date,
            max_cycles_per_card=max_cycles_per_card,
        )

    async def reconcile_command(
        self, user_id: uuid.UUID, command_id: uuid.UUID
    ) -> CreditCommandReconciliationRead:
        """Narrow, authenticated, read-only Credit command reconciliation.

        Authoritatively resolves the status of a client command_id against
        financial_events and its linked canonical Credit resource.

        Guarantees:
        - Query-only: strictly zero mutations, no UnitOfWork, no FOR UPDATE, no autoflush writes.
        - Owner-scoped: authenticated user scope; foreign-owner commands return indistinguishable unknown.
        - Two provable outcomes only:
          * 'confirmed': visible committed FinancialEvent owned by user AND matching canonical Credit resource.
          * 'unknown': command cannot be proven as committed and owner-visible (never issued, in-flight,
            rolled back, foreign owner, non-Credit operation, or incomplete/corrupt resource link).
        - 'unknown' is NEVER authoritative absence and NEVER means safe to reissue.
        """
        unknown_result = CreditCommandReconciliationRead(
            command_id=command_id,
            status="unknown",
            operation_type=None,
            event_id=None,
            resource_id=None,
            transaction_id=None,
            payment_id=None,
            early_payment_id=None,
            adjustment_id=None,
        )

        ledger_repo = getattr(self.ledger_service, "repository", None) or getattr(self, "ledger_repo", None)
        if ledger_repo is None:
            return unknown_result

        # Missing rows are deliberately ambiguous: the command may still be uncommitted.
        event = await ledger_repo.get_by_command_id_for_user(command_id, user_id)
        if not event or str(getattr(event, "user_id", None)) != str(user_id):
            return unknown_result
        event_type = getattr(event, "event_type", None)
        event_type = getattr(event_type, "value", event_type)
        event_id = getattr(event, "id", None)
        if not event_type or not event_id:
            return unknown_result
        meta = getattr(event, "metadata_", None) or {}
        card_id = meta.get("credit_card_id")

        # Map only a committed event and its complete, owner-scoped canonical resource.
        if event_type == "credit_card_purchase":
            tx = await self.repo.get_transaction_by_event_for_user(event_id, user_id)
            if (
                tx is not None
                and str(tx.user_id) == str(user_id)
                and getattr(tx, "type", None) == "purchase"
                and card_id is not None
                and str(tx.credit_card_id) == str(card_id)
            ):
                return CreditCommandReconciliationRead(
                    command_id=command_id,
                    status="confirmed",
                    operation_type="purchase",
                    event_id=event_id,
                    resource_id=tx.id,
                    transaction_id=tx.id,
                    payment_id=None,
                    early_payment_id=None,
                    adjustment_id=None,
                )
            return unknown_result

        if event_type == EventType.CREDIT_CARD_PAYMENT.value:
            operation_type = meta.get("operation_type")
            payment = await self.repo.get_payment_by_event_for_user(event_id, user_id)
            if (
                payment is None
                or str(payment.user_id) != str(user_id)
                or card_id is None
                or str(payment.credit_card_id) != str(card_id)
            ):
                return unknown_result
            payment_tx = await self.repo.get_transaction_by_event_for_user(event_id, user_id)
            if (
                payment_tx is None
                or str(payment_tx.user_id) != str(user_id)
                or payment_tx.type != "payment"
                or payment_tx.id != payment.payment_transaction_id
                or payment_tx.credit_card_id != payment.credit_card_id
            ):
                return unknown_result
            if operation_type == "credit_card_pay_early":
                early_pay = await self.repo.get_early_payment_by_event_for_user(event_id, user_id)
                if (
                    early_pay is not None
                    and str(early_pay.user_id) == str(user_id)
                    and early_pay.credit_card_id == payment.credit_card_id
                    and str(payment.command_id) == str(command_id)
                    and early_pay.payment_transaction_id == payment_tx.id
                    and early_pay.purchase_transaction_id is not None
                    and str(early_pay.purchase_transaction_id)
                    == str(meta.get("purchase_transaction_id"))
                ):
                    return CreditCommandReconciliationRead(
                        command_id=command_id,
                        status="confirmed",
                        operation_type="pay_early",
                        event_id=event_id,
                        resource_id=early_pay.id,
                        transaction_id=None,
                        payment_id=None,
                        early_payment_id=early_pay.id,
                        adjustment_id=None,
                    )
                return unknown_result
            if await self.repo.get_early_payment_by_event_for_user(event_id, user_id) is not None:
                return unknown_result
            if operation_type == "credit_card_payment" and str(payment.credit_card_id) == str(card_id):
                if str(payment.command_id) == str(command_id):
                    return CreditCommandReconciliationRead(
                        command_id=command_id,
                        status="confirmed",
                        operation_type="payment",
                        event_id=event_id,
                        resource_id=payment.id,
                        transaction_id=None,
                        payment_id=payment.id,
                        early_payment_id=None,
                        adjustment_id=None,
                    )
            return unknown_result

        if event_type == EventType.MANUAL_ADJUSTMENT.value:
            if meta.get("operation_type") == "credit_card_adjustment":
                adj = await self.repo.get_adjustment_by_event_for_user(event_id, user_id)
                if (
                    adj is not None
                    and str(adj.user_id) == str(user_id)
                    and card_id is not None
                    and str(adj.credit_card_id) == str(card_id)
                    and adj.adjustment_type == meta.get("adjustment_type")
                    and str(adj.command_id) == str(command_id)
                ):
                    return CreditCommandReconciliationRead(
                        command_id=command_id,
                        status="confirmed",
                        operation_type="adjustment",
                        event_id=event_id,
                        resource_id=adj.id,
                        transaction_id=None,
                        payment_id=None,
                        early_payment_id=None,
                        adjustment_id=adj.id,
                    )
            return unknown_result

        # Non-credit event or unrecognized mapping
        return unknown_result
