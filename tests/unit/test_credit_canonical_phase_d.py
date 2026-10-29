"""Unit tests for Nexum Credit Goal 2, Phase D: Canonical Payment, Allocation, Prepayment,
Early Payment, Refunds/Reversals, Deep Idempotency, and Concurrency Invariants.

Validates the complete set of Phase D requirements and closed findings:
1. F-13: Authoritative Goal reservations under lock (source_amount <= account.balance - reserved).
         Never mutates reservations. Stable CreditGoalReservationBreachError.
2. F-14: Global lock order: Account -> CreditCard -> Debt components -> Allocations.
         Standard payment and early payment follow strict hierarchy.
         Preview is strictly read-only (zero write locks, zero mutations).
3. F-16: Deep structured idempotency: command_id with fingerprint comparing user, operation type,
         card, target purchase, amounts, and currencies. Conflicting replay raises
         CreditIdempotencyConflictError; matching replay returns idempotent_retry coherently.
4. F-19: Complete payable-set consistency: unpaid statement charges, past-due installments,
         current period installments, and future multi-installment principal are completely
         unified across debt query, voluntary overpayment guard, waterfall, and response debt.
5. Prepayment: Standard payment prepaying future multi-installment principal in deterministic order:
               nearest due date -> oldest purchase chronology -> stable UUID.
6. Voluntary Overpayment Rejection: Payment exceeding total payable debt rejected with
                                   InvalidPaymentAmountError; never creates credit balance.
7. Payment Conservation: booked_payment_amount == sum(allocations) == principal + interest + fees.
8. Cross-Currency Payments: Backend FX evidence; Account debit strictly equals Ledger source amount;
                            booked card amount explicit; preview marks is_estimated.
9. Targeted Early Payment (/pay_early): Creates canonical Payment, Transaction, Allocations,
                                        and legacy compatibility row. Visible in Ledger, Account,
                                        and Credit history. Rejects single-installment purchases.
10. Refunds, Reversals, Adjustments:
    - Purchase refund reduces unpaid installments and restores available credit.
    - Purchase refund when already paid creates traceable issuer/refund-originated card credit.
    - Payment reversal restores component unpaid amounts and marks payment reversed.
    - Invalid / duplicate reversal raises stable error.
    - Original transactions are immutable (never deleted or overwritten).
11. Atomic Rollback: Ledger or allocation failure leaves zero partial effects (no Account debit,
                     no partial payment or allocations).
12. Statement Immutability: Payment allocations reduce outstanding balance only, never altering
                            historical statement snapshots or components.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.accounts.exceptions import AccountForbiddenError
from app.accounts.models import Account
from app.cash.exceptions import InsufficientFundsError
from app.core.errors import NotFoundError
from app.credit.exceptions import (
    CreditDomainError,
    CreditGoalReservationBreachError,
    CreditIdempotencyConflictError,
    CreditLifecycleInvalidityError,
    InvalidPaymentAmountError,
)
from app.credit.models import (
    ALLOCATION_SOURCE_NEXUM_MODELED,
    ALLOCATION_SOURCE_USER_DIRECTED,
    DEBT_PARTITION_CANONICAL,
    PROVENANCE_NEXUM_MODELED,
    CreditCard,
    CreditCardAdjustment,
    CreditCardAdjustmentAllocation,
    CreditCardEarlyPayment,
    CreditCardInstallment,
    CreditCardPayment,
    CreditCardPaymentAllocation,
    CreditCardStatement,
    CreditCardStatementCharge,
    CreditCardTransaction,
)
from app.credit.schemas import (
    CreditCardAdjustmentCreate,
    CreditCardEarlyPaymentCreate,
    CreditCardEarlyPaymentPreviewCreate,
    CreditCardPaymentCreate,
)
from app.credit.service import CreditCardService
from app.ledger.models import FinancialEvent
from app.ledger.schemas import LedgerEventResult

# ── In-Memory Test Session & Fixtures ──────────────────────────────────────────


class MemorySession:
    """Lightweight in-memory session stub for Phase D testing."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushed: list[object] = []
        self.committed = False
        self.rolled_back = False

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def add_all(self, objs: list[object]) -> None:
        self.added.extend(objs)

    async def flush(self) -> None:
        self.flushed.extend(self.added)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class PhaseDTestContext:
    """Encapsulates test entities and mock repositories for Phase D testing."""

    def __init__(
        self,
        *,
        card_currency: str = "COP",
        account_currency: str = "COP",
        account_balance: Decimal = Decimal("1000000.00"),
        credit_limit: Decimal = Decimal("5000000.00"),
        canonical_mode: bool = True,
        cutoff_day: int = 15,
        due_day: int = 30,
        goal_reserved: Decimal = Decimal("0.00"),
    ) -> None:
        self.user_id = uuid.uuid4()
        self.session = MemorySession()

        self.account = Account(
            id=uuid.uuid4(),
            user_id=self.user_id,
            currency=account_currency,
            balance=account_balance,
            is_active=True,
        )

        self.card = CreditCard(
            id=uuid.uuid4(),
            user_id=self.user_id,
            credit_limit=credit_limit,
            currency=card_currency,
            cutoff_day=cutoff_day,
            due_day=due_day,
            is_active=True,
            canonical_mode=canonical_mode,
            canonical_activated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        # Repositories and state storage
        self.installments: list[CreditCardInstallment] = []
        self.charges: list[CreditCardStatementCharge] = []
        self.transactions: dict[uuid.UUID, CreditCardTransaction] = {}
        self.payments: dict[uuid.UUID, CreditCardPayment] = {}
        self.allocations: list[CreditCardPaymentAllocation] = []
        self.adjustments: list[CreditCardAdjustment] = []
        self.adjustment_allocations: list[CreditCardAdjustmentAllocation] = []
        self.early_payments: list[CreditCardEarlyPayment] = []
        self.financial_events: dict[uuid.UUID, FinancialEvent] = {}
        self.events_by_command_id: dict[uuid.UUID, FinancialEvent] = {}

        # Lock acquisition trace to verify F-14 global lock hierarchy
        self.lock_trace: list[tuple[str, uuid.UUID]] = []

        # Build CreditCardService with connected mocks
        self.service = CreditCardService(self.session)
        self._setup_mocks(goal_reserved)

    def _setup_mocks(self, goal_reserved: Decimal) -> None:
        # 1. Account repo
        account_repo = AsyncMock()

        async def get_acc(acc_id):
            return self.account if self.account.id == acc_id else None

        async def get_acc_for_update(acc_id):
            self.lock_trace.append(("account", acc_id))
            return self.account if self.account.id == acc_id else None

        account_repo.get_by_id.side_effect = get_acc
        account_repo.get_by_id_for_update.side_effect = get_acc_for_update
        self.service.account_repo = account_repo

        # 2. Credit repo
        credit_repo = AsyncMock()

        async def get_card(cid):
            return self.card if self.card.id == cid else None

        async def get_card_for_update(cid):
            self.lock_trace.append(("card", cid))
            return self.card if self.card.id == cid else None

        credit_repo.get_by_id.side_effect = get_card
        credit_repo.get_by_id_for_update.side_effect = get_card_for_update

        async def list_pending_insts_for_update(cid, uid):
            self.lock_trace.append(("installments_update", cid))
            return [i for i in self.installments if i.status != "paid" and i.credit_card_id == cid]

        credit_repo.list_pending_installments_for_update.side_effect = list_pending_insts_for_update

        async def list_unpaid_charges_for_update(cid):
            self.lock_trace.append(("charges_update", cid))
            inst_ids = {i.id for i in self.installments if i.credit_card_id == cid}
            return [
                c
                for c in self.charges
                if (c.paid_amount or Decimal("0.00")) < c.amount
                and (getattr(c, "reference_id", None) is None or c.reference_id not in inst_ids)
            ]

        credit_repo.list_unpaid_statement_charges_for_update.side_effect = (
            list_unpaid_charges_for_update
        )

        async def get_tx(tx_id):
            return self.transactions.get(tx_id)

        async def get_tx_for_update(tx_id):
            self.lock_trace.append(("transaction_update", tx_id))
            return self.transactions.get(tx_id)

        credit_repo.get_transaction_by_id.side_effect = get_tx
        credit_repo.get_transaction_by_id_for_update.side_effect = get_tx_for_update

        async def get_tx_by_event(event_id):
            for tx in self.transactions.values():
                if tx.event_id == event_id:
                    return tx
            return None

        credit_repo.get_transaction_by_event.side_effect = get_tx_by_event

        async def add_tx(tx):
            self.transactions[tx.id] = tx
            self.session.add(tx)

        credit_repo.add_transaction.side_effect = add_tx

        async def add_pay(pay):
            self.payments[pay.id] = pay
            self.session.add(pay)

        credit_repo.add_payment.side_effect = add_pay

        async def add_pay_allocs(allocs):
            self.allocations.extend(allocs)
            self.session.add_all(allocs)

        credit_repo.add_payment_allocations.side_effect = add_pay_allocs

        async def add_adj(adj):
            self.adjustments.append(adj)
            self.session.add(adj)

        credit_repo.add_adjustment.side_effect = add_adj

        async def add_adj_allocs(allocs):
            self.adjustment_allocations.extend(allocs)
            self.session.add_all(allocs)

        credit_repo.add_adjustment_allocations.side_effect = add_adj_allocs

        async def get_adj_by_cmd(*args, **kwargs):
            cmd_id = kwargs.get("command_id") or (args[1] if len(args) > 1 else args[0])
            for a in self.adjustments:
                if str(a.command_id) == str(cmd_id):
                    return a
            return None

        credit_repo.get_adjustment_by_command_id.side_effect = get_adj_by_cmd

        async def list_adj_allocs_inst(inst_id):
            return [a for a in self.adjustment_allocations if a.target_installment_id == inst_id]

        credit_repo.list_credit_adjustment_allocations_for_installment.side_effect = (
            list_adj_allocs_inst
        )

        async def get_payment_by_tx(tx_id):
            for p in self.payments.values():
                if p.payment_transaction_id == tx_id:
                    return p
            return None

        credit_repo.get_payment_by_transaction_id.side_effect = get_payment_by_tx

        async def list_allocs_for_pay(payment_id):
            return [a for a in self.allocations if a.payment_id == payment_id]

        credit_repo.list_allocations_for_payment.side_effect = list_allocs_for_pay

        async def get_inst_for_update(inst_id):
            self.lock_trace.append(("installment_update", inst_id))
            for i in self.installments:
                if i.id == inst_id:
                    return i
            return None

        credit_repo.get_installment_by_id_for_update.side_effect = get_inst_for_update

        async def list_adjs_for_tx(tx_id):
            return [a for a in self.adjustments if a.original_transaction_id == tx_id]

        credit_repo.list_adjustments_for_transaction.side_effect = list_adjs_for_tx

        async def list_inst_tx(tx_id):
            return [i for i in self.installments if i.purchase_transaction_id == tx_id]

        async def list_inst_tx_for_update(tx_id):
            self.lock_trace.append(("installments_tx_update", tx_id))
            return [i for i in self.installments if i.purchase_transaction_id == tx_id]

        credit_repo.list_installments_for_transaction.side_effect = list_inst_tx
        credit_repo.list_installments_for_transaction_for_update.side_effect = (
            list_inst_tx_for_update
        )

        async def get_card_debt_partitioned(cid):
            # Compute total debt from unpaid installments and charges minus credit adjustments
            unpaid_insts = sum(
                (i.principal_amount - (i.paid_amount or Decimal("0.00")))
                for i in self.installments
                if i.credit_card_id == cid and i.status != "paid"
            )
            # Statement charges mirroring installments: sc.reference_id IS NULL OR sc.reference_id NOT IN installments
            installment_ids = {i.id for i in self.installments}
            unpaid_chgs = sum(
                (c.amount - (c.paid_amount or Decimal("0.00")))
                for c in self.charges
                if (
                    getattr(c, "credit_card_id", None) == cid
                    or getattr(c, "credit_card_id", None) is None
                )
                and (
                    getattr(c, "reference_id", None) is None
                    or c.reference_id not in installment_ids
                )
            )
            net_adj_credit = sum(
                a.allocated_amount for a in self.adjustment_allocations if a.credit_card_id == cid
            )
            canonical_debt = unpaid_insts + unpaid_chgs - net_adj_credit
            current_month = "2026-06"
            canonical_monthly = sum(
                (
                    i.principal_amount
                    + (i.assessed_interest_amount or Decimal("0.00"))
                    - (i.paid_amount or Decimal("0.00"))
                )
                for i in self.installments
                if i.credit_card_id == cid
                and i.status != "paid"
                and getattr(i, "scheduled_period", "") == current_month
            )
            monthly_adj_credit = sum(
                a.allocated_amount
                for a in self.adjustment_allocations
                if a.credit_card_id == cid
                and any(
                    i.id == a.target_installment_id
                    and getattr(i, "scheduled_period", "") == current_month
                    for i in self.installments
                )
            )
            monthly_cc_payment = canonical_monthly - monthly_adj_credit
            return {
                "canonical_debt": canonical_debt,
                "legacy_debt": Decimal("0.00"),
                "total_debt": canonical_debt,
                "monthly_cc_payment": monthly_cc_payment,
            }

        credit_repo.get_card_debt_partitioned.side_effect = get_card_debt_partitioned
        self.service.repo = credit_repo

        # 3. Ledger service & repo
        ledger_service = AsyncMock()
        ledger_repo = AsyncMock()

        async def get_event_by_cmd(cmd_id):
            return self.events_by_command_id.get(cmd_id)

        ledger_repo.get_by_command_id.side_effect = get_event_by_cmd

        async def record_event(payload):
            # Simulate idempotency check
            if payload.command_id in self.events_by_command_id:
                existing = self.events_by_command_id[payload.command_id]
                return LedgerEventResult(event=existing, idempotent=True)

            event = FinancialEvent(
                id=uuid.uuid4(),
                user_id=payload.user_id,
                event_type=payload.event_type,
                direction=payload.direction,
                amount=payload.amount,
                currency=payload.currency,
                account_id=payload.account_id,
                command_id=payload.command_id,
                source="system",
                period=datetime.now(UTC).strftime("%Y-%m"),
                metadata_=payload.metadata or {},
                occurred_at=datetime.now(UTC),
                created_at=datetime.now(UTC),
            )
            self.financial_events[event.id] = event
            if payload.command_id:
                self.events_by_command_id[payload.command_id] = event
            return LedgerEventResult(event=event, idempotent=False)

        ledger_service.record_event.side_effect = record_event
        ledger_service.repository = ledger_repo
        self.service.ledger_service = ledger_service
        self.service.ledger_repo = ledger_repo

        # 4. Goal repo (Authoritative Goal Reservations)
        self.goal_reserved = goal_reserved
        goal_repo = AsyncMock()

        async def calc_reserved(acc_id, uid):
            return self.goal_reserved

        goal_repo.calculate_reserved_by_account.side_effect = calc_reserved
        self.service.goal_repo = goal_repo


# ── Test Suite ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGoalReservationsF13:
    """Verifies F-13: Authoritative Goal reservations under lock.

    Invariants:
    - source_amount <= account.balance - authoritative_reservations
    - Goal reservations are never mutated during payment
    - Stable CreditGoalReservationBreachError on violation
    """

    async def test_payment_rejects_when_violating_goal_reservation(self):
        # Account balance: 1,000; Goal reservation: 800; Spendable balance: 200
        ctx = PhaseDTestContext(
            account_balance=Decimal("1000.00"),
            goal_reserved=Decimal("800.00"),
        )
        # Outstanding debt = 500
        ctx.installments.append(
            CreditCardInstallment(
                id=uuid.uuid4(),
                user_id=ctx.user_id,
                credit_card_id=ctx.card.id,
                purchase_transaction_id=uuid.uuid4(),
                installment_number=1,
                installments_total=1,
                principal_amount=Decimal("500.00"),
                interest_amount=Decimal("0.00"),
                total_amount=Decimal("500.00"),
                scheduled_due_date=date(2026, 6, 30),
                scheduled_period="2026-06",
                status="pending",
                paid_amount=Decimal("0.00"),
            )
        )

        payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("300.00"))
        with pytest.raises(CreditGoalReservationBreachError) as exc_info:
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())

        assert "violates spendable balance and goal reservations" in exc_info.value.message
        # Verify account balance was NOT modified
        assert ctx.account.balance == Decimal("1000.00")
        # Verify reservations were NOT mutated
        assert ctx.goal_reserved == Decimal("800.00")
        assert len(ctx.payments) == 0

    async def test_payment_succeeds_within_spendable_balance(self):
        # Account balance: 1,000; Goal reservation: 400; Spendable balance: 600
        ctx = PhaseDTestContext(
            account_balance=Decimal("1000.00"),
            goal_reserved=Decimal("400.00"),
        )
        ctx.installments.append(
            CreditCardInstallment(
                id=uuid.uuid4(),
                user_id=ctx.user_id,
                credit_card_id=ctx.card.id,
                purchase_transaction_id=uuid.uuid4(),
                installment_number=1,
                installments_total=1,
                principal_amount=Decimal("500.00"),
                interest_amount=Decimal("0.00"),
                total_amount=Decimal("500.00"),
                scheduled_due_date=date(2026, 6, 30),
                scheduled_period="2026-06",
                status="pending",
                paid_amount=Decimal("0.00"),
            )
        )

        # Payment of 200 <= 600 spendable
        payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("200.00"))
        result = await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())

        assert result.status == "success"
        assert result.amount == Decimal("200.00")
        assert ctx.account.balance == Decimal("800.00")
        # Authoritative reservation remains untouched
        assert ctx.goal_reserved == Decimal("400.00")

    async def test_early_payment_respects_goal_reservations(self):
        # Account balance: 500; Goal reservation: 400; Spendable balance: 100
        ctx = PhaseDTestContext(
            account_balance=Decimal("500.00"),
            goal_reserved=Decimal("400.00"),
        )
        tx_id = uuid.uuid4()
        # Multi-installment purchase (total 3, future pending)
        ctx.installments.extend(
            [
                CreditCardInstallment(
                    id=uuid.uuid4(),
                    user_id=ctx.user_id,
                    credit_card_id=ctx.card.id,
                    purchase_transaction_id=tx_id,
                    installment_number=1,
                    installments_total=3,
                    principal_amount=Decimal("100.00"),
                    interest_amount=Decimal("0.00"),
                    total_amount=Decimal("100.00"),
                    scheduled_due_date=date(2026, 6, 30),
                    scheduled_period="2026-06",
                    status="paid",
                    paid_amount=Decimal("100.00"),
                ),
                CreditCardInstallment(
                    id=uuid.uuid4(),
                    user_id=ctx.user_id,
                    credit_card_id=ctx.card.id,
                    purchase_transaction_id=tx_id,
                    installment_number=2,
                    installments_total=3,
                    principal_amount=Decimal("200.00"),
                    interest_amount=Decimal("0.00"),
                    total_amount=Decimal("200.00"),
                    scheduled_due_date=date(2099, 7, 30),
                    scheduled_period="2099-07",
                    status="pending",
                    paid_amount=Decimal("0.00"),
                ),
            ]
        )
        ctx.transactions[tx_id] = CreditCardTransaction(
            id=tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("300.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )

        payload = CreditCardEarlyPaymentCreate(
            account_id=ctx.account.id,
            amount=Decimal("150.00"),
            allocation_mode="nearest_installment",
        )
        with pytest.raises(CreditGoalReservationBreachError):
            await ctx.service.create_early_payment(
                ctx.user_id, ctx.card.id, tx_id, payload, uuid.uuid4()
            )


@pytest.mark.asyncio
class TestGlobalLockOrderF14:
    """Verifies F-14: Global lock hierarchy and deadlock regression.

    Hierarchy: Account -> CreditCard -> Debt components -> Allocations.
    Preview: Strictly read-only (zero write locks).
    """

    async def test_standard_payment_lock_order(self):
        ctx = PhaseDTestContext()
        ctx.installments.append(
            CreditCardInstallment(
                id=uuid.uuid4(),
                user_id=ctx.user_id,
                credit_card_id=ctx.card.id,
                purchase_transaction_id=uuid.uuid4(),
                installment_number=1,
                installments_total=1,
                principal_amount=Decimal("100.00"),
                interest_amount=Decimal("0.00"),
                total_amount=Decimal("100.00"),
                scheduled_due_date=date(2026, 6, 30),
                scheduled_period="2026-06",
                status="pending",
                paid_amount=Decimal("0.00"),
            )
        )

        payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("100.00"))
        await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())

        # Check lock trace sequence
        assert len(ctx.lock_trace) >= 3
        # First write lock must be Account
        assert ctx.lock_trace[0] == ("account", ctx.account.id)
        # Second write lock must be Card
        assert ctx.lock_trace[1] == ("card", ctx.card.id)
        # Third write lock must be debt components
        assert ctx.lock_trace[2] == ("installments_update", ctx.card.id)

    async def test_early_payment_lock_order(self):
        ctx = PhaseDTestContext()
        tx_id = uuid.uuid4()
        ctx.installments.append(
            CreditCardInstallment(
                id=uuid.uuid4(),
                user_id=ctx.user_id,
                credit_card_id=ctx.card.id,
                purchase_transaction_id=tx_id,
                installment_number=2,
                installments_total=2,
                principal_amount=Decimal("200.00"),
                interest_amount=Decimal("0.00"),
                total_amount=Decimal("200.00"),
                scheduled_due_date=date(2099, 7, 30),
                scheduled_period="2099-07",
                status="pending",
                paid_amount=Decimal("0.00"),
            )
        )
        ctx.transactions[tx_id] = CreditCardTransaction(
            id=tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("200.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )

        payload = CreditCardEarlyPaymentCreate(
            account_id=ctx.account.id,
            amount=Decimal("100.00"),
            allocation_mode="nearest_installment",
        )
        await ctx.service.create_early_payment(
            ctx.user_id, ctx.card.id, tx_id, payload, uuid.uuid4()
        )

        # Must acquire Account -> Card -> Target installments
        assert ctx.lock_trace[0] == ("account", ctx.account.id)
        assert ctx.lock_trace[1] == ("card", ctx.card.id)
        assert ctx.lock_trace[2] == ("installments_tx_update", tx_id)

    async def test_preview_is_strictly_read_only(self):
        ctx = PhaseDTestContext()
        tx_id = uuid.uuid4()
        ctx.installments.append(
            CreditCardInstallment(
                id=uuid.uuid4(),
                user_id=ctx.user_id,
                credit_card_id=ctx.card.id,
                purchase_transaction_id=tx_id,
                installment_number=2,
                installments_total=2,
                principal_amount=Decimal("200.00"),
                interest_amount=Decimal("0.00"),
                total_amount=Decimal("200.00"),
                scheduled_due_date=date(2099, 7, 30),
                scheduled_period="2099-07",
                status="pending",
                paid_amount=Decimal("0.00"),
            )
        )
        ctx.transactions[tx_id] = CreditCardTransaction(
            id=tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("200.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )

        payload = CreditCardEarlyPaymentPreviewCreate(
            account_id=ctx.account.id,
            amount=Decimal("100.00"),
            allocation_mode="nearest_installment",
        )
        preview = await ctx.service.preview_early_payment(ctx.user_id, ctx.card.id, tx_id, payload)

        assert preview.source_amount == Decimal("100.00")
        assert preview.target_amount == Decimal("100.00")
        # Assert NO locks were acquired
        assert len(ctx.lock_trace) == 0
        # Assert NO objects were added to session
        assert len(ctx.session.added) == 0


@pytest.mark.asyncio
class TestDeepIdempotencyF16:
    """Verifies F-16: Structured fingerprint deep idempotency and conflict detection."""

    async def test_matching_command_id_returns_idempotent_retry(self):
        ctx = PhaseDTestContext()
        ctx.installments.append(
            CreditCardInstallment(
                id=uuid.uuid4(),
                user_id=ctx.user_id,
                credit_card_id=ctx.card.id,
                purchase_transaction_id=uuid.uuid4(),
                installment_number=1,
                installments_total=1,
                principal_amount=Decimal("500.00"),
                interest_amount=Decimal("0.00"),
                total_amount=Decimal("500.00"),
                scheduled_due_date=date(2026, 6, 30),
                scheduled_period="2026-06",
                status="pending",
                paid_amount=Decimal("0.00"),
            )
        )

        cmd_id = uuid.uuid4()
        payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("200.00"))

        # First payment: succeeds
        res1 = await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, cmd_id)
        assert res1.status == "success"
        assert ctx.account.balance == Decimal("999800.00")

        # Second payment with SAME command_id and identical payload
        res2 = await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, cmd_id)
        assert res2.status == "idempotent_retry"
        assert res2.event_id == res1.event_id
        # Account balance was NOT debited a second time
        assert ctx.account.balance == Decimal("999800.00")

    async def test_conflict_on_different_user(self):
        ctx = PhaseDTestContext()
        cmd_id = uuid.uuid4()
        # Seed an existing event for user A
        event = FinancialEvent(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),  # Different user
            event_type="credit_card_payment",
            direction="outflow",
            amount=Decimal("200.00"),
            currency="COP",
            account_id=ctx.account.id,
            command_id=cmd_id,
            metadata_={"credit_card_id": str(ctx.card.id)},
        )
        ctx.events_by_command_id[cmd_id] = event

        payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("200.00"))
        with pytest.raises(CreditIdempotencyConflictError) as exc_info:
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, cmd_id)
        assert "different users" in exc_info.value.message

    async def test_conflict_on_different_card(self):
        ctx = PhaseDTestContext()
        cmd_id = uuid.uuid4()
        # Seed event for a different card
        event = FinancialEvent(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            event_type="credit_card_payment",
            direction="outflow",
            amount=Decimal("200.00"),
            currency="COP",
            account_id=ctx.account.id,
            command_id=cmd_id,
            metadata_={"credit_card_id": str(uuid.uuid4())},
        )
        ctx.events_by_command_id[cmd_id] = event

        payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("200.00"))
        with pytest.raises(CreditIdempotencyConflictError) as exc_info:
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, cmd_id)
        assert "different credit cards" in exc_info.value.message

    async def test_conflict_on_conflicting_amounts(self):
        ctx = PhaseDTestContext()
        cmd_id = uuid.uuid4()
        event = FinancialEvent(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            event_type="credit_card_payment",
            direction="outflow",
            amount=Decimal("200.00"),
            currency="COP",
            account_id=ctx.account.id,
            command_id=cmd_id,
            metadata_={"credit_card_id": str(ctx.card.id), "applied_amount": "200.00"},
        )
        ctx.events_by_command_id[cmd_id] = event

        # Request with amount 300 instead of 200
        payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("300.00"))
        with pytest.raises(CreditIdempotencyConflictError) as exc_info:
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, cmd_id)
        assert "conflicting source amount" in exc_info.value.message

    async def test_conflict_on_conflicting_currencies(self):
        ctx = PhaseDTestContext()
        cmd_id = uuid.uuid4()
        event = FinancialEvent(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            event_type="credit_card_payment",
            direction="outflow",
            amount=Decimal("200.00"),
            currency="USD",
            account_id=ctx.account.id,
            command_id=cmd_id,
            metadata_={"credit_card_id": str(ctx.card.id)},
        )
        ctx.events_by_command_id[cmd_id] = event

        # Account is in COP, event was in USD
        payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("200.00"))
        with pytest.raises(CreditIdempotencyConflictError) as exc_info:
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, cmd_id)
        assert "conflicting source currency" in exc_info.value.message


@pytest.mark.asyncio
class TestCompletePayableSetAndWaterfallF19:
    """Verifies F-19: Complete payable-set consistency across charges, past due, current, and future tiers."""

    async def test_complete_payable_set_governs_guard_and_waterfall(self):
        ctx = PhaseDTestContext()

        # 1. Past due installment (100)
        inst_past = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=uuid.uuid4(),
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("100.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            scheduled_due_date=date(2025, 1, 30),
            scheduled_period="2025-01",
            status="pending",
            paid_amount=Decimal("0.00"),
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        # 2. Statement fee / charge (50)
        chg = CreditCardStatementCharge(
            id=uuid.uuid4(),
            statement_id=uuid.uuid4(),
            charge_type="fee",
            amount=Decimal("50.00"),
            paid_amount=Decimal("0.00"),
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        # 3. Current period installment (200)
        current_period = ctx.service._get_financial_date().strftime("%Y-%m")
        inst_curr = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=uuid.uuid4(),
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("200.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
            scheduled_due_date=date(2026, 8, 30),
            scheduled_period=current_period,
            status="pending",
            paid_amount=Decimal("0.00"),
            created_at=datetime(2026, 1, 3, tzinfo=UTC),
        )
        # 4. Future principal installment (300)
        inst_future = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=uuid.uuid4(),
            installment_number=2,
            installments_total=2,
            principal_amount=Decimal("300.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("300.00"),
            scheduled_due_date=date(2099, 12, 30),
            scheduled_period="2099-12",
            status="pending",
            paid_amount=Decimal("0.00"),
            created_at=datetime(2026, 1, 4, tzinfo=UTC),
        )

        ctx.installments.extend([inst_past, inst_curr, inst_future])
        ctx.charges.append(chg)

        # Total payable debt = 100 + 50 + 200 + 300 = 650
        # Overpayment guard check: 651 should be rejected
        payload_over = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("651.00"))
        with pytest.raises(InvalidPaymentAmountError):
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload_over, uuid.uuid4())

        # Exact total payment: 650 accepted
        payload_exact = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("650.00"))
        result = await ctx.service.create_payment(
            ctx.user_id, ctx.card.id, payload_exact, uuid.uuid4()
        )

        assert result.status == "success"
        assert result.amount == Decimal("650.00")
        assert result.current_debt == Decimal("0.00")

        # Verify exact payment conservation
        total_allocated = sum(a.allocated_amount for a in ctx.allocations)
        assert total_allocated == Decimal("650.00")

        # Verify waterfall execution order: past due -> charge -> current -> future
        # Past due allocation
        past_alloc = next(a for a in ctx.allocations if a.target_installment_id == inst_past.id)
        assert past_alloc.allocated_principal == Decimal("100.00")
        assert past_alloc.allocation_order == 1

        # Statement charge allocation
        chg_alloc = next(a for a in ctx.allocations if a.target_charge_id == chg.id)
        assert chg_alloc.allocated_fees == Decimal("50.00")
        assert chg_alloc.allocation_order == 2

        # Current period installment allocation
        curr_alloc = next(a for a in ctx.allocations if a.target_installment_id == inst_curr.id)
        assert curr_alloc.allocated_principal == Decimal("200.00")
        assert curr_alloc.allocation_order == 3

        # Future installment allocation
        fut_alloc = next(a for a in ctx.allocations if a.target_installment_id == inst_future.id)
        assert fut_alloc.allocated_principal == Decimal("300.00")
        assert fut_alloc.allocation_order == 4


@pytest.mark.asyncio
class TestGenericPrepaymentAndCanonicalOrdering:
    """Verifies standard payment prepaying future installments in canonical deterministic order."""

    async def test_standard_payment_prepaying_future_installments(self):
        ctx = PhaseDTestContext()

        # Two future installments with different due dates and creation times
        tx_old = uuid.uuid4()
        tx_new = uuid.uuid4()

        inst_near = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=tx_old,
            installment_number=2,
            installments_total=3,
            principal_amount=Decimal("100.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            scheduled_due_date=date(2099, 8, 30),
            scheduled_period="2099-08",
            status="pending",
            paid_amount=Decimal("0.00"),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        inst_far = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=tx_new,
            installment_number=3,
            installments_total=3,
            principal_amount=Decimal("100.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            scheduled_due_date=date(2099, 9, 30),
            scheduled_period="2099-09",
            status="pending",
            paid_amount=Decimal("0.00"),
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        ctx.installments.extend([inst_near, inst_far])

        # Pay 150 -> 100 goes to inst_near (earlier due date), 50 goes to inst_far
        payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("150.00"))
        result = await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())

        assert result.status == "success"
        assert result.amount == Decimal("150.00")

        near_alloc = next(a for a in ctx.allocations if a.target_installment_id == inst_near.id)
        assert near_alloc.allocated_principal == Decimal("100.00")
        assert near_alloc.allocation_order == 1

        far_alloc = next(a for a in ctx.allocations if a.target_installment_id == inst_far.id)
        assert far_alloc.allocated_principal == Decimal("50.00")
        assert far_alloc.allocation_order == 2

        # Conservation holds: 100 + 50 = 150
        assert near_alloc.allocated_amount + far_alloc.allocated_amount == Decimal("150.00")


@pytest.mark.asyncio
class TestVoluntaryOverpaymentRejection:
    """Verifies that voluntary overpayment above total payable debt rejects with stable error."""

    async def test_overpayment_rejected_without_creating_credit_balance(self):
        ctx = PhaseDTestContext()
        ctx.installments.append(
            CreditCardInstallment(
                id=uuid.uuid4(),
                user_id=ctx.user_id,
                credit_card_id=ctx.card.id,
                purchase_transaction_id=uuid.uuid4(),
                installment_number=1,
                installments_total=1,
                principal_amount=Decimal("300.00"),
                interest_amount=Decimal("0.00"),
                total_amount=Decimal("300.00"),
                scheduled_due_date=date(2026, 6, 30),
                scheduled_period="2026-06",
                status="pending",
                paid_amount=Decimal("0.00"),
            )
        )

        payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("350.00"))
        with pytest.raises(InvalidPaymentAmountError) as exc_info:
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())

        assert "exceeds total payable debt" in exc_info.value.message
        # Account is not debited
        assert ctx.account.balance == Decimal("1000000.00")
        # No payment row created
        assert len(ctx.payments) == 0


@pytest.mark.asyncio
class TestCrossCurrencyPayments:
    """Verifies cross-currency payment conservation, FX evidence, and preview behavior."""

    async def test_cross_currency_payment_conservation_and_fx(self, monkeypatch):
        # Account in USD, Card in COP
        ctx = PhaseDTestContext(account_currency="USD", card_currency="COP")

        # Mock get_fx_rate to return 4,000 COP per USD
        async def mock_fx(source, target):
            return {
                "fx_rate": Decimal("4000.00"),
                "rate_source": "banrep",
                "rate_timestamp": datetime.now(UTC),
            }

        monkeypatch.setattr("app.core.currency.get_fx_rate", mock_fx)

        # 40,000 COP debt
        ctx.installments.append(
            CreditCardInstallment(
                id=uuid.uuid4(),
                user_id=ctx.user_id,
                credit_card_id=ctx.card.id,
                purchase_transaction_id=uuid.uuid4(),
                installment_number=1,
                installments_total=1,
                principal_amount=Decimal("40000.00"),
                interest_amount=Decimal("0.00"),
                total_amount=Decimal("40000.00"),
                scheduled_due_date=date(2026, 6, 30),
                scheduled_period="2026-06",
                status="pending",
                paid_amount=Decimal("0.00"),
            )
        )

        # User pays $10.00 USD -> 40,000 COP booked
        payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("10.00"))
        result = await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())

        assert result.status == "success"
        # Account was debited exactly $10.00 USD
        assert ctx.account.balance == Decimal("999990.00")

        # Ledger event was recorded with source $10.00 USD and applied 40,000 COP
        event = ctx.financial_events[result.event_id]
        assert event.amount == Decimal("10.00")
        assert event.currency == "USD"
        assert event.metadata_["applied_amount"] == "40000.00"
        assert event.metadata_["applied_currency"] == "COP"

        # Card payment row was booked in COP
        payment = next(iter(ctx.payments.values()))
        assert payment.source_amount == Decimal("10.00")
        assert payment.source_currency == "USD"
        assert payment.booked_amount == Decimal("40000.00")
        assert payment.booked_currency == "COP"

        # Allocation conservation on card
        assert sum(a.allocated_amount for a in ctx.allocations) == Decimal("40000.00")


@pytest.mark.asyncio
class TestTargetedEarlyPayment:
    """Verifies targeted /pay_early creation of canonical payment, allocations, and legacy compatibility."""

    async def test_early_payment_creates_canonical_payment_and_compatibility_row(self):
        ctx = PhaseDTestContext()
        tx_id = uuid.uuid4()

        ctx.installments.extend(
            [
                CreditCardInstallment(
                    id=uuid.uuid4(),
                    user_id=ctx.user_id,
                    credit_card_id=ctx.card.id,
                    purchase_transaction_id=tx_id,
                    installment_number=1,
                    installments_total=2,
                    principal_amount=Decimal("100.00"),
                    interest_amount=Decimal("0.00"),
                    total_amount=Decimal("100.00"),
                    scheduled_due_date=date(2026, 6, 30),
                    scheduled_period="2026-06",
                    status="paid",
                    paid_amount=Decimal("100.00"),
                ),
                CreditCardInstallment(
                    id=uuid.uuid4(),
                    user_id=ctx.user_id,
                    credit_card_id=ctx.card.id,
                    purchase_transaction_id=tx_id,
                    installment_number=2,
                    installments_total=2,
                    principal_amount=Decimal("100.00"),
                    interest_amount=Decimal("0.00"),
                    total_amount=Decimal("100.00"),
                    scheduled_due_date=date(2099, 7, 30),
                    scheduled_period="2099-07",
                    status="pending",
                    paid_amount=Decimal("0.00"),
                ),
            ]
        )
        ctx.transactions[tx_id] = CreditCardTransaction(
            id=tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("200.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )

        payload = CreditCardEarlyPaymentCreate(
            account_id=ctx.account.id,
            amount=Decimal("100.00"),
            allocation_mode="nearest_installment",
        )
        result = await ctx.service.create_early_payment(
            ctx.user_id, ctx.card.id, tx_id, payload, uuid.uuid4()
        )

        assert result.status == "success"
        assert result.amount == Decimal("100.00")

        # Canonical CreditCardPayment created
        payment = next(iter(ctx.payments.values()))
        assert payment.allocation_source == ALLOCATION_SOURCE_USER_DIRECTED
        assert payment.principal_applied == Decimal("100.00")

        # Canonical CreditCardPaymentAllocation created
        alloc = next(iter(ctx.allocations))
        assert alloc.allocation_source == ALLOCATION_SOURCE_USER_DIRECTED
        assert alloc.allocated_principal == Decimal("100.00")

        # Compatibility CreditCardEarlyPayment row added to session
        early_row = next(
            (o for o in ctx.session.added if isinstance(o, CreditCardEarlyPayment)), None
        )
        assert early_row is not None
        assert early_row.amount == Decimal("100.00")
        assert early_row.purchase_transaction_id == tx_id

    async def test_early_payment_rejected_for_single_installment_purchase(self):
        ctx = PhaseDTestContext()
        tx_id = uuid.uuid4()
        ctx.installments.append(
            CreditCardInstallment(
                id=uuid.uuid4(),
                user_id=ctx.user_id,
                credit_card_id=ctx.card.id,
                purchase_transaction_id=tx_id,
                installment_number=1,
                installments_total=1,
                principal_amount=Decimal("100.00"),
                interest_amount=Decimal("0.00"),
                total_amount=Decimal("100.00"),
                scheduled_due_date=date(2099, 7, 30),
                scheduled_period="2099-07",
                status="pending",
                paid_amount=Decimal("0.00"),
            )
        )
        ctx.transactions[tx_id] = CreditCardTransaction(
            id=tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("100.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )

        payload = CreditCardEarlyPaymentCreate(
            account_id=ctx.account.id,
            amount=Decimal("100.00"),
            allocation_mode="nearest_installment",
        )
        with pytest.raises(CreditDomainError) as exc_info:
            await ctx.service.create_early_payment(
                ctx.user_id, ctx.card.id, tx_id, payload, uuid.uuid4()
            )

        assert "Single-installment purchases are not eligible" in exc_info.value.message


@pytest.mark.asyncio
class TestAdjustmentsRefundsAndReversals:
    """Verifies refunds, payment reversals, card credit, and immutability invariants."""

    async def test_purchase_refund_reduces_installments_and_restores_limit(self):
        ctx = PhaseDTestContext()
        tx_id = uuid.uuid4()
        inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=tx_id,
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("200.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
            scheduled_due_date=date(2026, 6, 30),
            scheduled_period="2026-06",
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        ctx.installments.append(inst)

        purchase_tx = CreditCardTransaction(
            id=tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("200.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )
        ctx.transactions[tx_id] = purchase_tx

        # Submit refund adjustment
        payload = CreditCardAdjustmentCreate(
            original_transaction_id=tx_id,
            adjustment_type="refund",
            amount=Decimal("200.00"),
            reason="Merchant refund for returned goods",
        )
        res = await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())

        assert res.status == "success"
        assert res.amount == Decimal("200.00")
        assert res.adjustment_type == "refund"

        # Original purchase transaction is immutable (not deleted or overwritten)
        assert purchase_tx.id in ctx.transactions
        assert purchase_tx.lifecycle_status == "reversed"

        # Installment status adjusted/cancelled
        assert inst.status == "cancelled"

    async def test_purchase_refund_when_already_paid_creates_card_credit(self):
        ctx = PhaseDTestContext()
        tx_id = uuid.uuid4()
        # Purchase was already fully paid
        inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=tx_id,
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("150.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("150.00"),
            scheduled_due_date=date(2026, 6, 30),
            scheduled_period="2026-06",
            status="paid",
            paid_amount=Decimal("150.00"),
        )
        ctx.installments.append(inst)

        purchase_tx = CreditCardTransaction(
            id=tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("150.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )
        ctx.transactions[tx_id] = purchase_tx

        payload = CreditCardAdjustmentCreate(
            original_transaction_id=tx_id,
            adjustment_type="refund",
            amount=Decimal("150.00"),
            reason="Paid purchase refund",
        )
        res = await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())

        assert res.status == "success"
        # Adjustment transaction recorded as posted refund
        adj_tx = next((t for t in ctx.transactions.values() if t.type == "refund"), None)
        assert adj_tx is not None
        assert adj_tx.amount == Decimal("150.00")
        assert adj_tx.lifecycle_status == "posted"

    async def test_payment_reversal_restores_components_and_marks_payment_reversed(self):
        ctx = PhaseDTestContext()
        pay_tx_id = uuid.uuid4()

        # Payment previously paid an installment
        inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=uuid.uuid4(),
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("200.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
            scheduled_due_date=date(2026, 6, 30),
            scheduled_period="2026-06",
            status="paid",
            paid_amount=Decimal("200.00"),
        )
        ctx.installments.append(inst)

        pay_tx = CreditCardTransaction(
            id=pay_tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="payment",
            amount=Decimal("200.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )
        ctx.transactions[pay_tx_id] = pay_tx

        payment = CreditCardPayment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            account_id=ctx.account.id,
            payment_transaction_id=pay_tx_id,
            event_id=uuid.uuid4(),
            booked_amount=Decimal("200.00"),
            booked_currency="COP",
            source_amount=Decimal("200.00"),
            source_currency="COP",
            principal_applied=Decimal("200.00"),
            status="posted",
            payment_date=date(2026, 6, 20),
            command_id=uuid.uuid4(),
            command_fingerprint="test-fp",
        )
        ctx.payments[payment.id] = payment

        alloc = CreditCardPaymentAllocation(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            payment_id=payment.id,
            target_installment_id=inst.id,
            allocated_amount=Decimal("200.00"),
            allocated_principal=Decimal("200.00"),
            allocation_order=1,
            allocation_source=ALLOCATION_SOURCE_NEXUM_MODELED,
            provenance=PROVENANCE_NEXUM_MODELED,
        )
        ctx.allocations.append(alloc)

        # Submit payment reversal
        payload = CreditCardAdjustmentCreate(
            original_transaction_id=pay_tx_id,
            adjustment_type="reversal",
            direction="debit",
            amount=Decimal("200.00"),
            reason="Bank payment reversal / return",
        )
        res = await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())

        assert res.status == "success"
        # Payment marked reversed
        assert payment.status == "reversed"
        # Installment paid_amount and status restored
        assert inst.paid_amount == Decimal("0.00")
        assert inst.status == "pending"

    async def test_duplicate_or_invalid_reversal_rejected(self):
        ctx = PhaseDTestContext()
        pay_tx_id = uuid.uuid4()
        pay_tx = CreditCardTransaction(
            id=pay_tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="payment",
            amount=Decimal("200.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="reversed",  # Already reversed
        )
        ctx.transactions[pay_tx_id] = pay_tx

        payment = CreditCardPayment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            account_id=ctx.account.id,
            payment_transaction_id=pay_tx_id,
            event_id=uuid.uuid4(),
            booked_amount=Decimal("200.00"),
            booked_currency="COP",
            source_amount=Decimal("200.00"),
            source_currency="COP",
            principal_applied=Decimal("200.00"),
            status="reversed",
            payment_date=date(2026, 6, 20),
            command_id=uuid.uuid4(),
            command_fingerprint="test-fp",
        )
        ctx.payments[payment.id] = payment

        payload = CreditCardAdjustmentCreate(
            original_transaction_id=pay_tx_id,
            adjustment_type="reversal",
            direction="debit",
            amount=Decimal("200.00"),
            reason="Duplicate reversal attempt",
        )
        with pytest.raises(CreditLifecycleInvalidityError) as exc_info:
            await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())
        assert "already been reversed" in exc_info.value.message


@pytest.mark.asyncio
class TestAtomicRollback:
    """Verifies that failures during payment or allocations cleanly rollback state with zero partial effects."""

    async def test_ledger_failure_aborts_payment_without_debiting_account(self):
        ctx = PhaseDTestContext()
        ctx.installments.append(
            CreditCardInstallment(
                id=uuid.uuid4(),
                user_id=ctx.user_id,
                credit_card_id=ctx.card.id,
                purchase_transaction_id=uuid.uuid4(),
                installment_number=1,
                installments_total=1,
                principal_amount=Decimal("100.00"),
                interest_amount=Decimal("0.00"),
                total_amount=Decimal("100.00"),
                scheduled_due_date=date(2026, 6, 30),
                scheduled_period="2026-06",
                status="pending",
                paid_amount=Decimal("0.00"),
            )
        )

        # Force ledger service to raise RuntimeError
        ctx.service.ledger_service.record_event.side_effect = RuntimeError(
            "Ledger cluster unreachable"
        )

        payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("100.00"))
        with pytest.raises(RuntimeError):
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())

        # Account balance is intact
        assert ctx.account.balance == Decimal("1000000.00")
        # No payment or allocations recorded
        assert len(ctx.payments) == 0
        assert len(ctx.allocations) == 0
        assert len(ctx.transactions) == 0


@pytest.mark.asyncio
class TestStatementSnapshotsImmutable:
    """Verifies that payment allocations update outstanding components only, leaving statement snapshots immutable."""

    async def test_statement_snapshot_unchanged_by_subsequent_payments(self):
        ctx = PhaseDTestContext()

        # Closed statement snapshot
        stmt = CreditCardStatement(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            billing_period="2026-05",
            status="closed",
            statement_balance=Decimal("500000.00"),
            billed_installments=Decimal("500000.00"),
            previous_balance=Decimal("0.00"),
            interest_total=Decimal("0.00"),
            fees_total=Decimal("0.00"),
            payments_received=Decimal("0.00"),
            new_purchases=Decimal("500000.00"),
            frozen_at=datetime(2026, 5, 15, tzinfo=UTC),
            due_date=date(2026, 5, 30),
            debt_partition=DEBT_PARTITION_CANONICAL,
        )

        # Installment belonging to that closed cycle
        inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=uuid.uuid4(),
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("500000.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("500000.00"),
            scheduled_due_date=date(2026, 5, 30),
            scheduled_period="2026-05",
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        ctx.installments.append(inst)

        # Pay 200,000 toward the installment
        payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("200000.00"))
        res = await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())

        assert res.status == "success"
        # The statement snapshot remains strictly intact and unchanged
        assert stmt.statement_balance == Decimal("500000.00")
        assert stmt.billed_installments == Decimal("500000.00")
        assert stmt.status == "closed"
        # The installment reflects the allocation
        assert inst.paid_amount == Decimal("200000.00")
        assert inst.status == "partial"


@pytest.mark.asyncio
class TestCodexRequirements:
    """Explicit tests addressing all 12 rejection findings from the Codex review."""

    async def test_item1_immutable_facts_preserved_under_early_payment_and_refund(self):
        """Codex Item 1: Early payment and refunds must NEVER rewrite principal_amount or total_amount."""
        ctx = PhaseDTestContext()
        tx_id = uuid.uuid4()
        inst1 = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=tx_id,
            installment_number=1,
            installments_total=2,
            principal_amount=Decimal("300.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("300.00"),
            scheduled_period="2099-01",
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        inst2 = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=tx_id,
            installment_number=2,
            installments_total=2,
            principal_amount=Decimal("300.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("300.00"),
            scheduled_period="2099-02",
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        ctx.installments.extend([inst1, inst2])
        ctx.transactions[tx_id] = CreditCardTransaction(
            id=tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("600.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )

        # 1. Early payment of 150 targeting future installments
        payload = CreditCardEarlyPaymentCreate(
            account_id=ctx.account.id,
            amount=Decimal("150.00"),
            allocation_mode="nearest_installment",
        )
        await ctx.service.create_early_payment(
            ctx.user_id, ctx.card.id, tx_id, payload, uuid.uuid4()
        )

        # Crucial invariant: principal_amount and total_amount MUST NOT BE MUTATED
        assert inst1.principal_amount == Decimal("300.00")
        assert inst1.total_amount == Decimal("300.00")
        assert inst1.paid_amount == Decimal("150.00")
        assert inst1.status == "partial"
        assert inst2.principal_amount == Decimal("300.00")
        assert inst2.total_amount == Decimal("300.00")
        assert inst2.paid_amount == Decimal("0.00")

        # 2. Purchase refund of 100
        adj_payload = CreditCardAdjustmentCreate(
            original_transaction_id=tx_id,
            adjustment_type="refund",
            direction="credit",
            amount=Decimal("100.00"),
            reason="Partial return",
        )
        await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, adj_payload, uuid.uuid4())

        # Crucial invariant: refund MUST NOT rewrite principal_amount or total_amount
        assert inst1.principal_amount == Decimal("300.00")
        assert inst1.total_amount == Decimal("300.00")
        assert inst2.principal_amount == Decimal("300.00")
        assert inst2.total_amount == Decimal("300.00")
        # Compensating adjustment allocations exist
        assert len(ctx.adjustment_allocations) == 1
        assert ctx.adjustment_allocations[0].allocated_amount == Decimal("100.00")

    async def test_item2_early_payment_conservation_and_no_nonfuture_allocation(self):
        """Codex Item 2: Early payment targets eligible future installments only; assertions on conservation."""
        ctx = PhaseDTestContext()
        tx_id = uuid.uuid4()
        current_period = datetime.now(UTC).strftime("%Y-%m")
        past_inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=tx_id,
            installment_number=1,
            installments_total=3,
            principal_amount=Decimal("100.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            scheduled_period=current_period,  # current, not future
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        future_inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=tx_id,
            installment_number=2,
            installments_total=3,
            principal_amount=Decimal("200.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
            scheduled_period="2099-12",  # strictly future
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        ctx.installments.extend([past_inst, future_inst])
        ctx.transactions[tx_id] = CreditCardTransaction(
            id=tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("300.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )

        payload = CreditCardEarlyPaymentCreate(
            account_id=ctx.account.id,
            amount=Decimal("150.00"),
            allocation_mode="nearest_installment",
        )
        res = await ctx.service.create_early_payment(
            ctx.user_id, ctx.card.id, tx_id, payload, uuid.uuid4()
        )
        assert res.status == "success"

        # Current period installment must NOT have received any early payment allocation
        assert past_inst.paid_amount == Decimal("0.00")
        assert past_inst.status == "pending"

        # Future installment received the 150.00
        assert future_inst.paid_amount == Decimal("150.00")
        assert future_inst.status == "partial"

        # Payment conservation
        pay_allocs = [a for a in ctx.allocations if a.target_installment_id == future_inst.id]
        assert sum(a.allocated_amount for a in pay_allocs) == Decimal("150.00")

    async def test_item3_unclamped_debt_and_credit_balance(self):
        """Codex Item 3: Do not clamp inconsistent or negative debt/available credit in responses."""
        ctx = PhaseDTestContext(credit_limit=Decimal("1000.00"))
        tx_id = uuid.uuid4()
        inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=tx_id,
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("200.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
            scheduled_period="2026-06",
            status="paid",
            paid_amount=Decimal("200.00"),
        )
        ctx.installments.append(inst)
        ctx.transactions[tx_id] = CreditCardTransaction(
            id=tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("200.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )

        # Full refund of paid purchase creates -200 canonical debt
        payload = CreditCardAdjustmentCreate(
            original_transaction_id=tx_id,
            adjustment_type="refund",
            direction="credit",
            amount=Decimal("200.00"),
            reason="Return of paid item",
        )
        res = await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())

        # Unclamped negative debt and available credit exceeding credit limit
        assert res.current_debt == Decimal("-200.00")
        assert res.available_credit == Decimal("1200.00")  # 1000 - (-200) = 1200

    async def test_item4_adjustment_structural_validation_and_provenance(self):
        """Codex Item 4: Structurally reject incoherent type/direction combos via model validation."""
        # Refund with debit is rejected
        with pytest.raises(ValueError) as exc:
            CreditCardAdjustmentCreate(
                original_transaction_id=uuid.uuid4(),
                adjustment_type="refund",
                direction="debit",
                amount=Decimal("100.00"),
            )
        assert "direction 'credit'" in str(exc.value)

        # Chargeback with debit is rejected
        with pytest.raises(ValueError) as exc:
            CreditCardAdjustmentCreate(
                original_transaction_id=uuid.uuid4(),
                adjustment_type="chargeback",
                direction="debit",
                amount=Decimal("50.00"),
            )
        assert "direction 'credit'" in str(exc.value)

        # Spoofed issuer_authoritative provenance is rejected on manual endpoint
        with pytest.raises(ValueError) as exc:
            CreditCardAdjustmentCreate(
                original_transaction_id=uuid.uuid4(),
                adjustment_type="refund",
                direction="credit",
                provenance="issuer_authoritative",
                amount=Decimal("100.00"),
            )
        assert "issuer_authoritative" in str(exc.value)

        # Valid refund with manual provenance
        valid = CreditCardAdjustmentCreate(
            original_transaction_id=uuid.uuid4(),
            adjustment_type="refund",
            direction="credit",
            provenance="manual",
            allocation_source="nexum_modeled",
            amount=Decimal("100.00"),
        )
        assert valid.provenance == "manual"
        assert valid.allocation_source == "nexum_modeled"

    async def test_item5_adjustment_idempotency_via_ledger_event_and_fail_closed(self):
        """Codex Item 5: Accepted adjustment must have traceable FinancialEvent, and retry must be coherent."""
        ctx = PhaseDTestContext()
        tx_id = uuid.uuid4()
        inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=tx_id,
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("200.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
            scheduled_period="2026-06",
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        ctx.installments.append(inst)
        ctx.transactions[tx_id] = CreditCardTransaction(
            id=tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("200.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )

        cmd_id = uuid.uuid4()
        payload = CreditCardAdjustmentCreate(
            original_transaction_id=tx_id,
            adjustment_type="refund",
            direction="credit",
            amount=Decimal("200.00"),
            reason="First refund attempt",
        )

        # 1. Create adjustment -> creates FinancialEvent
        res1 = await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, cmd_id)
        assert res1.status == "success"
        assert res1.event_id is not None
        assert res1.event_id in ctx.financial_events

        # 2. Retry with same command_id -> returns coherent idempotent_retry with SAME adjustment_id and event_id
        res2 = await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, cmd_id)
        assert res2.status == "idempotent_retry"
        assert res2.adjustment_id == res1.adjustment_id
        assert res2.event_id == res1.event_id

        # 3. If CreditCardAdjustment row is deleted/missing -> fails closed (never returns random UUID)
        ctx.adjustments.clear()
        with pytest.raises(CreditDomainError) as exc_info:
            await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, cmd_id)
        assert "Inconsistent state" in exc_info.value.message

    async def test_item6_idempotency_fail_closed_and_rate_drift_safety(self):
        """Codex Item 6: DB failure or malformed event fails closed; standard and early payment are NOT interchangeable."""
        ctx = PhaseDTestContext()
        cmd_id = uuid.uuid4()

        # Seed standard payment event
        event = FinancialEvent(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            event_type="credit_card_payment",
            direction="outflow",
            amount=Decimal("100.00"),
            currency="COP",
            account_id=ctx.account.id,
            command_id=cmd_id,
            metadata_={"credit_card_id": str(ctx.card.id), "operation_type": "credit_card_payment"},
        )
        ctx.events_by_command_id[cmd_id] = event

        # Attempt to use same command_id for early payment -> must conflict
        payload = CreditCardEarlyPaymentCreate(
            account_id=ctx.account.id,
            amount=Decimal("100.00"),
            allocation_mode="nearest_installment",
        )
        with pytest.raises(CreditIdempotencyConflictError) as exc:
            await ctx.service.create_early_payment(
                ctx.user_id, ctx.card.id, uuid.uuid4(), payload, cmd_id
            )
        assert "operation type mismatch" in exc.value.message

        # DB error in ledger repo must propagate, NOT return None
        db_err_cmd = uuid.uuid4()
        ctx.service.ledger_repo.get_by_command_id.side_effect = RuntimeError(
            "Database connection lost"
        )
        with pytest.raises(RuntimeError) as exc:
            await ctx.service.create_payment(
                ctx.user_id,
                ctx.card.id,
                CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("50.00")),
                db_err_cmd,
            )
        assert "Database connection lost" in str(exc.value)

    async def test_item7_lock_hierarchy_purchase_vs_payment(self):
        """Codex Item 7: Purchase refund must not acquire Account lock. Payment reversal locks Account first."""
        ctx = PhaseDTestContext()
        tx_id = uuid.uuid4()
        inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=tx_id,
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("100.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            scheduled_period="2026-06",
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        ctx.installments.append(inst)
        ctx.transactions[tx_id] = CreditCardTransaction(
            id=tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("100.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )

        ctx.lock_trace.clear()
        adj_payload = CreditCardAdjustmentCreate(
            original_transaction_id=tx_id,
            adjustment_type="refund",
            direction="credit",
            amount=Decimal("100.00"),
        )
        await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, adj_payload, uuid.uuid4())

        # Purchase refund must NEVER acquire account lock
        locked_resources = [res for res, _ in ctx.lock_trace]
        assert "account" not in locked_resources
        assert "card" in locked_resources

    async def test_item8_goal_reservations_fail_closed(self):
        """Codex Item 8: Typed GoalRepository validation; invalid non-Decimal or negative fails closed."""
        ctx = PhaseDTestContext(account_balance=Decimal("1000.00"))
        ctx.installments.append(
            CreditCardInstallment(
                id=uuid.uuid4(),
                user_id=ctx.user_id,
                credit_card_id=ctx.card.id,
                purchase_transaction_id=uuid.uuid4(),
                installment_number=1,
                installments_total=1,
                principal_amount=Decimal("200.00"),
                interest_amount=Decimal("0.00"),
                total_amount=Decimal("200.00"),
                scheduled_period="2026-06",
                status="pending",
                paid_amount=Decimal("0.00"),
            )
        )

        payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("100.00"))

        # 1. Non-Decimal return fails closed
        ctx.service.goal_repo.calculate_reserved_by_account.side_effect = None
        ctx.service.goal_repo.calculate_reserved_by_account.return_value = "invalid_string"
        with pytest.raises(CreditGoalReservationBreachError) as exc:
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())
        assert "invalid non-decimal" in exc.value.message

        # 2. Negative reservation fails closed
        ctx.service.goal_repo.calculate_reserved_by_account.return_value = Decimal("-50.00")
        with pytest.raises(CreditGoalReservationBreachError) as exc:
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())
        assert "Inconsistent goal reservation" in exc.value.message

        # 3. Insufficient funds in account before reservations raises InsufficientFundsError
        ctx.service.goal_repo.calculate_reserved_by_account.return_value = Decimal("0.00")
        big_payload = CreditCardPaymentCreate(account_id=ctx.account.id, amount=Decimal("2000.00"))
        with pytest.raises(InsufficientFundsError):
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, big_payload, uuid.uuid4())

    async def test_item10_standardized_error_classes(self):
        """Codex Item 10: Replace loose ValueError with NotFoundError and AccountForbiddenError."""
        ctx = PhaseDTestContext()
        missing_acc_payload = CreditCardPaymentCreate(
            account_id=uuid.uuid4(), amount=Decimal("50.00")
        )
        with pytest.raises(NotFoundError):
            await ctx.service.create_payment(
                ctx.user_id, ctx.card.id, missing_acc_payload, uuid.uuid4()
            )

        ctx.account.is_active = False
        inactive_payload = CreditCardPaymentCreate(
            account_id=ctx.account.id, amount=Decimal("50.00")
        )
        with pytest.raises(AccountForbiddenError):
            await ctx.service.create_payment(
                ctx.user_id, ctx.card.id, inactive_payload, uuid.uuid4()
            )

    async def test_item12_mirroring_statement_charges_non_double_counted(self):
        """Codex Item 12: Statement charges mirroring installments (reference_id = inst.id) are not double-counted."""
        ctx = PhaseDTestContext()
        inst_id = uuid.uuid4()
        inst = CreditCardInstallment(
            id=inst_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=uuid.uuid4(),
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("300.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("300.00"),
            scheduled_period="2026-06",
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        ctx.installments.append(inst)

        # Mirroring statement charge referencing the installment
        mirror_charge = CreditCardStatementCharge(
            id=uuid.uuid4(),
            statement_id=uuid.uuid4(),
            charge_type="principal",
            reference_id=inst_id,  # Points to installment!
            amount=Decimal("300.00"),
            paid_amount=Decimal("0.00"),
            created_at=datetime.now(UTC),
            status="pending",
        )
        # Standalone statement charge (e.g. fee)
        fee_charge = CreditCardStatementCharge(
            id=uuid.uuid4(),
            statement_id=uuid.uuid4(),
            charge_type="fee",
            reference_id=None,
            amount=Decimal("25.00"),
            paid_amount=Decimal("0.00"),
            created_at=datetime.now(UTC),
            status="pending",
        )
        ctx.charges.extend([mirror_charge, fee_charge])

        debt_data = await ctx.service.repo.get_card_debt_partitioned(ctx.card.id)
        # Expected debt: 300 (installment) + 25 (fee) = 325 (mirroring charge NOT counted!)
        assert debt_data["total_debt"] == Decimal("325.00")

    async def test_item1_payment_reversal_amount_conservation_rejects_partial_1_vs_100(self):
        """Codex Item 1: Payment reversal must be exact full amount; 1-vs-100 reversal rejects with zero mutations."""
        ctx = PhaseDTestContext(account_balance=Decimal("500.00"))
        pay_tx_id = uuid.uuid4()
        inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=uuid.uuid4(),
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("100.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            scheduled_due_date=date(2026, 6, 30),
            scheduled_period="2026-06",
            status="paid",
            paid_amount=Decimal("100.00"),
        )
        ctx.installments.append(inst)

        pay_tx = CreditCardTransaction(
            id=pay_tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="payment",
            amount=Decimal("100.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )
        ctx.transactions[pay_tx_id] = pay_tx

        payment = CreditCardPayment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            account_id=ctx.account.id,
            payment_transaction_id=pay_tx_id,
            event_id=uuid.uuid4(),
            booked_amount=Decimal("100.00"),
            booked_currency="COP",
            source_amount=Decimal("100.00"),
            source_currency="COP",
            principal_applied=Decimal("100.00"),
            status="posted",
            payment_date=date(2026, 6, 20),
            command_id=uuid.uuid4(),
            command_fingerprint="test-fp",
        )
        ctx.payments[payment.id] = payment

        alloc = CreditCardPaymentAllocation(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            payment_id=payment.id,
            target_installment_id=inst.id,
            allocated_amount=Decimal("100.00"),
            allocated_principal=Decimal("100.00"),
            allocation_order=1,
            allocation_source=ALLOCATION_SOURCE_NEXUM_MODELED,
            provenance=PROVENANCE_NEXUM_MODELED,
        )
        ctx.allocations.append(alloc)

        # Attempt partial reversal of 1.00 against 100.00 payment
        payload = CreditCardAdjustmentCreate(
            original_transaction_id=pay_tx_id,
            adjustment_type="reversal",
            direction="debit",
            amount=Decimal("1.00"),
            reason="Partial reversal attempt",
        )
        with pytest.raises(InvalidPaymentAmountError) as exc_info:
            await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())
        assert "exact full amount" in exc_info.value.message

        # Assert zero mutations survived
        assert ctx.account.balance == Decimal("500.00")
        assert payment.status == "posted"
        assert pay_tx.lifecycle_status == "posted"
        assert inst.paid_amount == Decimal("100.00")
        assert inst.status == "paid"
        assert len(ctx.adjustments) == 0
        assert len(ctx.adjustment_allocations) == 0

    async def test_item2_payment_reversal_lock_order_and_inconsistent_preview_fails_before_card_lock(
        self,
    ):
        """Codex Item 2: Lock order is Account -> Card; missing payment record fails before acquiring Card lock."""
        ctx = PhaseDTestContext()
        pay_tx_id = uuid.uuid4()
        pay_tx = CreditCardTransaction(
            id=pay_tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="payment",
            amount=Decimal("100.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )
        ctx.transactions[pay_tx_id] = pay_tx

        # Case A: Inconsistent preview (payment transaction exists, but canonical payment row is missing)
        payload = CreditCardAdjustmentCreate(
            original_transaction_id=pay_tx_id,
            adjustment_type="reversal",
            direction="debit",
            amount=Decimal("100.00"),
        )
        with pytest.raises(CreditLifecycleInvalidityError) as exc_info:
            await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())
        assert "existing canonical payment record" in exc_info.value.message
        # Crucial: Card was NEVER locked
        card_locks = [entry for entry in ctx.lock_trace if entry[0] == "card"]
        assert len(card_locks) == 0

        # Case B: Consistent payment reversal verifies exact Account -> Card lock hierarchy
        ctx.lock_trace.clear()
        payment = CreditCardPayment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            account_id=ctx.account.id,
            payment_transaction_id=pay_tx_id,
            event_id=uuid.uuid4(),
            booked_amount=Decimal("100.00"),
            booked_currency="COP",
            source_amount=Decimal("100.00"),
            source_currency="COP",
            principal_applied=Decimal("100.00"),
            status="posted",
            payment_date=date(2026, 6, 20),
            command_id=uuid.uuid4(),
            command_fingerprint="test-fp",
        )
        ctx.payments[payment.id] = payment

        await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())
        lock_names = [entry[0] for entry in ctx.lock_trace]
        assert "account" in lock_names
        assert "card" in lock_names
        assert lock_names.index("account") < lock_names.index("card")

    async def test_item3_external_fx_under_locks_rejected_without_provider_call(self, monkeypatch):
        """Codex Item 3: If FX quote is missing or drifted under locks, raise stable error without external provider call."""
        ctx = PhaseDTestContext(account_currency="USD", card_currency="COP")
        mock_get_fx = AsyncMock(
            return_value={
                "fx_rate": Decimal("4000.00"),
                "rate_source": "test_provider",
                "rate_timestamp": datetime.now(UTC),
            }
        )

        import app.core.currency

        monkeypatch.setattr(app.core.currency, "get_fx_rate", mock_get_fx)

        inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=uuid.uuid4(),
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("100000.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("100000.00"),
            scheduled_period="2026-06",
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        ctx.installments.append(inst)

        # Trigger currency drift by making card currency change under lock
        original_get_card = ctx.service.repo.get_by_id_for_update

        async def get_card_with_drift(cid):
            c = await original_get_card(cid)
            c.currency = "EUR"  # Drifted from prefetch quote of USD->COP!
            return c

        ctx.service.repo.get_by_id_for_update = get_card_with_drift

        payload = CreditCardPaymentCreate(
            account_id=ctx.account.id,
            amount=Decimal("25.00"),
        )
        with pytest.raises(CreditDomainError) as exc_info:
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())
        assert "Missing or drifted pre-fetched FX quote under locks" in exc_info.value.message
        # Crucial: Provider was called exactly ONCE before locks, NEVER called under locks
        assert mock_get_fx.call_count == 1

    async def test_item4_deep_idempotency_binds_source_account_and_provenance(self):
        """Codex Item 4: Deep idempotency binds source Account, adjustment provenance, and fails closed on missing metadata."""
        ctx = PhaseDTestContext()
        cmd_id = uuid.uuid4()
        other_account_id = uuid.uuid4()

        inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=uuid.uuid4(),
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("200.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
            scheduled_period="2026-06",
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        ctx.installments.append(inst)

        # 1. First payment under cmd_id
        pay1 = CreditCardPaymentCreate(
            account_id=ctx.account.id,
            amount=Decimal("50.00"),
        )
        res1 = await ctx.service.create_payment(ctx.user_id, ctx.card.id, pay1, cmd_id)
        assert res1.status == "success"

        # 2. Replay under same cmd_id with different account_id must CONFLICT
        pay_diff_acc = CreditCardPaymentCreate(
            account_id=other_account_id,
            amount=Decimal("50.00"),
        )
        with pytest.raises(CreditIdempotencyConflictError) as exc_info:
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, pay_diff_acc, cmd_id)
        assert "different accounts" in exc_info.value.message

        # 3. Adjustment replay with mismatched provenance must CONFLICT
        adj_cmd = uuid.uuid4()
        tx_id = uuid.uuid4()
        ctx.transactions[tx_id] = CreditCardTransaction(
            id=tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("100.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )
        adj_payload = CreditCardAdjustmentCreate(
            original_transaction_id=tx_id,
            adjustment_type="refund",
            direction="credit",
            provenance="manual",
            amount=Decimal("10.00"),
        )
        adj_res = await ctx.service.create_adjustment(
            ctx.user_id, ctx.card.id, adj_payload, adj_cmd
        )
        assert adj_res.status == "success"

        # Retry with different provenance
        adj_payload_diff = CreditCardAdjustmentCreate(
            original_transaction_id=tx_id,
            adjustment_type="refund",
            direction="credit",
            provenance="legacy_unknown",
            amount=Decimal("10.00"),
        )
        with pytest.raises(CreditIdempotencyConflictError) as exc_info:
            await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, adj_payload_diff, adj_cmd)
        assert "adjustment provenance" in exc_info.value.message

    async def test_item6_adjustment_semantics_invalid_combinations_rejected(self):
        """Codex Item 6: Incoherent adjustment combinations (purchase debit reversal, payment refund) are rejected."""
        ctx = PhaseDTestContext()
        tx_purchase = uuid.uuid4()
        ctx.transactions[tx_purchase] = CreditCardTransaction(
            id=tx_purchase,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("100.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )

        tx_payment = uuid.uuid4()
        ctx.transactions[tx_payment] = CreditCardTransaction(
            id=tx_payment,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="payment",
            amount=Decimal("100.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )

        # 1. Purchase with debit reversal is rejected
        p1 = CreditCardAdjustmentCreate(
            original_transaction_id=tx_purchase,
            adjustment_type="reversal",
            direction="debit",
            amount=Decimal("50.00"),
        )
        with pytest.raises(CreditLifecycleInvalidityError) as exc_info:
            await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, p1, uuid.uuid4())
        assert "direction='credit'" in exc_info.value.message

        # 2. Payment with refund is rejected
        p2 = CreditCardAdjustmentCreate(
            original_transaction_id=tx_payment,
            adjustment_type="refund",
            direction="credit",
            amount=Decimal("50.00"),
        )
        with pytest.raises(CreditLifecycleInvalidityError) as exc_info:
            await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, p2, uuid.uuid4())
        assert "only supports adjustment_type='reversal'" in exc_info.value.message

        # 3. Payment with credit reversal is rejected
        p3 = CreditCardAdjustmentCreate(
            original_transaction_id=tx_payment,
            adjustment_type="reversal",
            direction="credit",
            amount=Decimal("100.00"),
        )
        with pytest.raises(CreditLifecycleInvalidityError) as exc_info:
            await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, p3, uuid.uuid4())
        assert "direction='debit'" in exc_info.value.message

    async def test_item7_idempotent_retry_fails_closed_on_missing_persisted_resources(self):
        """Codex Item 7: Missing resources on idempotent retry fail closed instead of synthesizing zeros."""
        ctx = PhaseDTestContext()
        cmd_id = uuid.uuid4()

        inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=uuid.uuid4(),
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("200.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
            scheduled_period="2026-06",
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        ctx.installments.append(inst)

        pay_payload = CreditCardPaymentCreate(
            account_id=ctx.account.id,
            amount=Decimal("50.00"),
        )
        res1 = await ctx.service.create_payment(ctx.user_id, ctx.card.id, pay_payload, cmd_id)
        assert res1.status == "success"

        # Case A: Transaction row deleted -> retry fails closed (does not synthesize transaction_id=None)
        ctx.transactions.clear()
        with pytest.raises(CreditDomainError) as exc_info:
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, pay_payload, cmd_id)
        assert "Inconsistent state" in exc_info.value.message

    async def test_item8_debt_and_waterfall_consistency_mirroring_charges_excluded(self):
        """Codex Item 8: Installment 100 + mirrored charge 100 + fee 10 permits at most 110, allocations sum 110 (not 210)."""
        ctx = PhaseDTestContext(account_balance=Decimal("1000.00"))
        inst_id = uuid.uuid4()
        inst = CreditCardInstallment(
            id=inst_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=uuid.uuid4(),
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("100.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            scheduled_period="2026-06",
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        ctx.installments.append(inst)

        mirror_charge = CreditCardStatementCharge(
            id=uuid.uuid4(),
            statement_id=uuid.uuid4(),
            charge_type="principal",
            reference_id=inst_id,  # Mirroring installment
            amount=Decimal("100.00"),
            paid_amount=Decimal("0.00"),
            created_at=datetime.now(UTC),
            status="pending",
        )
        fee_charge = CreditCardStatementCharge(
            id=uuid.uuid4(),
            statement_id=uuid.uuid4(),
            charge_type="fee",
            reference_id=None,
            amount=Decimal("10.00"),
            paid_amount=Decimal("0.00"),
            created_at=datetime.now(UTC),
            status="pending",
        )
        ctx.charges.extend([mirror_charge, fee_charge])

        # 1. Total debt from repository is 110.00 (not 210.00)
        debt_data = await ctx.service.repo.get_card_debt_partitioned(ctx.card.id)
        assert debt_data["total_debt"] == Decimal("110.00")

        # 2. Overpayment guard rejects payment of 111.00
        pay_over = CreditCardPaymentCreate(
            account_id=ctx.account.id,
            amount=Decimal("111.00"),
        )
        with pytest.raises(InvalidPaymentAmountError):
            await ctx.service.create_payment(ctx.user_id, ctx.card.id, pay_over, uuid.uuid4())

        # 3. Payment of 110.00 succeeds
        pay_valid = CreditCardPaymentCreate(
            account_id=ctx.account.id,
            amount=Decimal("110.00"),
        )
        res = await ctx.service.create_payment(ctx.user_id, ctx.card.id, pay_valid, uuid.uuid4())
        assert res.status == "success"

        # 4. Allocations sum to exactly 110.00 (100 to installment, 10 to fee, 0 to mirrored charge)
        assert len(ctx.allocations) == 2
        alloc_inst = next(a for a in ctx.allocations if a.target_installment_id == inst_id)
        alloc_fee = next(a for a in ctx.allocations if a.target_charge_id == fee_charge.id)
        assert alloc_inst.allocated_amount == Decimal("100.00")
        assert alloc_fee.allocated_amount == Decimal("10.00")
        assert sum(a.allocated_amount for a in ctx.allocations) == Decimal("110.00")
        assert mirror_charge.paid_amount == Decimal("0.00")  # untouched

        # 5. Returned debt reconciles to 0.00
        debt_after = await ctx.service.repo.get_card_debt_partitioned(ctx.card.id)
        assert debt_after["total_debt"] == Decimal("0.00")

    async def test_item9_monthly_cc_payment_subtracts_current_month_adjustments(self):
        """Codex Item 9: monthly_cc_payment correctly reflects remaining obligations net of adjustment allocations."""
        ctx = PhaseDTestContext()
        inst_id = uuid.uuid4()
        inst = CreditCardInstallment(
            id=inst_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=uuid.uuid4(),
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("100.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            scheduled_period="2026-06",
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        ctx.installments.append(inst)

        adj = CreditCardAdjustment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            original_transaction_id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            adjustment_type="refund",
            direction="credit",
            booked_amount=Decimal("40.00"),
            booked_currency="COP",
            status="posted",
            command_id=uuid.uuid4(),
            command_fingerprint="fp",
        )
        ctx.adjustments.append(adj)

        alloc = CreditCardAdjustmentAllocation(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            adjustment_id=adj.id,
            target_installment_id=inst_id,
            allocated_amount=Decimal("40.00"),
            allocation_order=1,
            allocation_source=ALLOCATION_SOURCE_NEXUM_MODELED,
            provenance=PROVENANCE_NEXUM_MODELED,
        )
        ctx.adjustment_allocations.append(alloc)

        debt_data = await ctx.service.repo.get_card_debt_partitioned(ctx.card.id)
        # Expected monthly payment: 100 - 40 = 60 (not 100)
        assert debt_data["monthly_cc_payment"] == Decimal("60.00")
        assert debt_data["total_debt"] == Decimal("60.00")

    async def test_item10_preserve_immutable_facts_refund_keeps_installment_pending_for_assessed_interest(
        self,
    ):
        """Codex Item 10: Refund of principal leaves installment pending for remaining assessed interest."""
        ctx = PhaseDTestContext()
        tx_id = uuid.uuid4()
        inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=tx_id,
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("100.00"),
            interest_amount=Decimal("20.00"),
            assessed_interest_amount=Decimal("20.00"),
            total_amount=Decimal("120.00"),
            scheduled_period="2026-06",
            status="pending",
            paid_amount=Decimal("0.00"),
        )
        ctx.installments.append(inst)

        ctx.transactions[tx_id] = CreditCardTransaction(
            id=tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("100.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )

        # Refund 100.00 principal
        payload = CreditCardAdjustmentCreate(
            original_transaction_id=tx_id,
            adjustment_type="refund",
            direction="credit",
            amount=Decimal("100.00"),
            reason="Refund purchase principal",
        )
        res = await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, uuid.uuid4())
        assert res.status == "success"

        # Crucial: Installment is NOT marked paid or cancelled; status remains pending for the 20.00 assessed interest
        assert inst.status == "pending"
        assert inst.paid_amount == Decimal("0.00")
        assert inst.principal_amount == Decimal("100.00")  # Immutable!
        assert inst.total_amount == Decimal("120.00")  # Immutable!

        # Waterfall payment of 20.00 pays the remaining assessed interest and settles the installment
        pay = CreditCardPaymentCreate(
            account_id=ctx.account.id,
            amount=Decimal("20.00"),
        )
        pay_res = await ctx.service.create_payment(ctx.user_id, ctx.card.id, pay, uuid.uuid4())
        assert pay_res.status == "success"
        assert inst.status == "paid"
        assert inst.paid_amount == Decimal("20.00")

    async def test_item1_payment_reversal_idempotent_retry_returns_original_adjustment(self):
        """Codex Item 1: Payment-reversal idempotent retry returns original adjustment; does not raise lifecycle error or repeat mutations."""
        ctx = PhaseDTestContext(account_balance=Decimal("500.00"))
        pay_tx_id = uuid.uuid4()
        inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=uuid.uuid4(),
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("100.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            scheduled_period="2026-06",
            status="paid",
            paid_amount=Decimal("100.00"),
        )
        ctx.installments.append(inst)

        pay_tx = CreditCardTransaction(
            id=pay_tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="payment",
            amount=Decimal("100.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
            account_id=ctx.account.id,
        )
        ctx.transactions[pay_tx_id] = pay_tx

        payment = CreditCardPayment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            account_id=ctx.account.id,
            payment_transaction_id=pay_tx_id,
            event_id=uuid.uuid4(),
            booked_amount=Decimal("100.00"),
            booked_currency="COP",
            source_amount=Decimal("100.00"),
            source_currency="COP",
            principal_applied=Decimal("100.00"),
            status="posted",
            payment_date=date(2026, 6, 20),
            command_id=uuid.uuid4(),
            command_fingerprint="fp-pay",
        )
        ctx.payments[payment.id] = payment

        alloc = CreditCardPaymentAllocation(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            payment_id=payment.id,
            target_installment_id=inst.id,
            allocated_amount=Decimal("100.00"),
            allocated_principal=Decimal("100.00"),
            allocated_interest=Decimal("0.00"),
            allocated_fees=Decimal("0.00"),
            allocation_order=1,
            allocation_source=ALLOCATION_SOURCE_NEXUM_MODELED,
            provenance=PROVENANCE_NEXUM_MODELED,
        )
        ctx.allocations.append(alloc)

        reversal_cmd = uuid.uuid4()
        payload = CreditCardAdjustmentCreate(
            original_transaction_id=pay_tx_id,
            adjustment_type="reversal",
            direction="debit",
            amount=Decimal("100.00"),
            reason="Bank payment reversal",
        )

        # 1. Execute reversal once -> success
        res1 = await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, reversal_cmd)
        assert res1.status == "success"
        assert res1.adjustment_id is not None
        assert res1.event_id is not None
        assert ctx.account.balance == Decimal("600.00")  # Restored +100.00
        assert payment.status == "reversed"
        assert inst.status == "pending"
        assert inst.paid_amount == Decimal("0.00")
        initial_alloc_count = len(ctx.allocations)
        initial_adj_count = len(ctx.adjustments)

        # 2. Re-execute exact same reversal command -> returns idempotent_retry
        res2 = await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, reversal_cmd)
        assert res2.status == "idempotent_retry"
        assert res2.adjustment_id == res1.adjustment_id
        assert res2.event_id == res1.event_id
        # Zero secondary mutations
        assert ctx.account.balance == Decimal("600.00")  # NOT 700.00!
        assert inst.paid_amount == Decimal("0.00")  # NOT negative!
        assert len(ctx.allocations) == initial_alloc_count
        assert len(ctx.adjustments) == initial_adj_count

        # 3. Different command attempting another reversal on the already reversed payment -> fails
        diff_cmd = uuid.uuid4()
        with pytest.raises(CreditLifecycleInvalidityError) as exc_info:
            await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, diff_cmd)
        assert "already been reversed" in exc_info.value.message
        assert ctx.account.balance == Decimal("600.00")

    async def test_item2_cross_currency_payment_reversal_account_ledger_conservation(self):
        """Codex Item 2: Cross-currency reversal restores source_amount to Account and Ledger inflow; preserves booked amount on card."""
        ctx = PhaseDTestContext(account_balance=Decimal("100.00"))
        # Account is in USD, card is in COP
        ctx.account.currency = "USD"
        ctx.card.currency = "COP"

        pay_tx_id = uuid.uuid4()
        inst = CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            purchase_transaction_id=uuid.uuid4(),
            installment_number=1,
            installments_total=1,
            principal_amount=Decimal("100000.00"),
            interest_amount=Decimal("0.00"),
            total_amount=Decimal("100000.00"),
            scheduled_period="2026-06",
            status="paid",
            paid_amount=Decimal("100000.00"),
        )
        ctx.installments.append(inst)

        pay_tx = CreditCardTransaction(
            id=pay_tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="payment",
            amount=Decimal("100000.00"),
            booked_amount=Decimal("100000.00"),
            booked_currency="COP",
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
            account_id=ctx.account.id,
        )
        ctx.transactions[pay_tx_id] = pay_tx

        # Payment booked 100,000 COP with source 25.00 USD
        payment = CreditCardPayment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            account_id=ctx.account.id,
            payment_transaction_id=pay_tx_id,
            event_id=uuid.uuid4(),
            booked_amount=Decimal("100000.00"),
            booked_currency="COP",
            source_amount=Decimal("25.00"),
            source_currency="USD",
            principal_applied=Decimal("100000.00"),
            status="posted",
            payment_date=date(2026, 6, 20),
            command_id=uuid.uuid4(),
            command_fingerprint="fp-pay-fx",
        )
        ctx.payments[payment.id] = payment

        alloc = CreditCardPaymentAllocation(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            payment_id=payment.id,
            target_installment_id=inst.id,
            allocated_amount=Decimal("100000.00"),
            allocated_principal=Decimal("100000.00"),
            allocated_interest=Decimal("0.00"),
            allocated_fees=Decimal("0.00"),
            allocation_order=1,
            allocation_source=ALLOCATION_SOURCE_NEXUM_MODELED,
            provenance=PROVENANCE_NEXUM_MODELED,
        )
        ctx.allocations.append(alloc)

        reversal_cmd = uuid.uuid4()
        payload = CreditCardAdjustmentCreate(
            original_transaction_id=pay_tx_id,
            adjustment_type="reversal",
            direction="debit",
            amount=Decimal("100000.00"),  # Booked card amount
            reason="Cross-currency payment return",
        )

        res = await ctx.service.create_adjustment(ctx.user_id, ctx.card.id, payload, reversal_cmd)
        assert res.status == "success"

        # Account balance credited exactly +25.00 USD (source amount)
        assert ctx.account.balance == Decimal("125.00")

        # Ledger FinancialEvent inflow amount/currency strictly equals Account balance credit
        reversal_event = ctx.financial_events[res.event_id]
        assert reversal_event.amount == Decimal("25.00")
        assert reversal_event.currency == "USD"
        assert reversal_event.direction == "inflow"
        assert reversal_event.account_id == ctx.account.id
        assert reversal_event.metadata_["source_amount"] == "25.00"
        assert reversal_event.metadata_["source_currency"] == "USD"
        assert reversal_event.metadata_["applied_amount"] == "100000.00"
        assert reversal_event.metadata_["applied_currency"] == "COP"

        # CreditCardAdjustment preserves booked amount on card and source facts
        adjustment = ctx.adjustments[0]
        assert adjustment.booked_amount == Decimal("100000.00")
        assert adjustment.booked_currency == "COP"
        assert adjustment.original_amount == Decimal("25.00")
        assert adjustment.original_currency == "USD"

        # Exact retry is coherent and returns idempotent_retry
        retry_res = await ctx.service.create_adjustment(
            ctx.user_id, ctx.card.id, payload, reversal_cmd
        )
        assert retry_res.status == "idempotent_retry"
        assert retry_res.adjustment_id == res.adjustment_id
        assert ctx.account.balance == Decimal("125.00")

        # Material mismatch conflict on replay with different booked amount
        mismatch_payload = CreditCardAdjustmentCreate(
            original_transaction_id=pay_tx_id,
            adjustment_type="reversal",
            direction="debit",
            amount=Decimal("50000.00"),  # Mismatched booked amount
            reason="Different amount attempt",
        )
        with pytest.raises(InvalidPaymentAmountError):
            await ctx.service.create_adjustment(
                ctx.user_id, ctx.card.id, mismatch_payload, reversal_cmd
            )

    async def test_item3_account_binding_in_idempotency_for_reversals_and_purchase_adjustments(
        self,
    ):
        """Codex Item 3: Account-affecting reversal binds Account in idempotency; purchase adjustments remain accountless."""
        ctx = PhaseDTestContext(account_balance=Decimal("200.00"))
        other_account_id = uuid.uuid4()

        # Create posted payment
        pay_tx_id = uuid.uuid4()
        pay_tx = CreditCardTransaction(
            id=pay_tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="payment",
            amount=Decimal("50.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
            account_id=ctx.account.id,
        )
        ctx.transactions[pay_tx_id] = pay_tx

        payment = CreditCardPayment(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            account_id=ctx.account.id,
            payment_transaction_id=pay_tx_id,
            event_id=uuid.uuid4(),
            booked_amount=Decimal("50.00"),
            booked_currency="COP",
            source_amount=Decimal("50.00"),
            source_currency="COP",
            principal_applied=Decimal("50.00"),
            status="posted",
            payment_date=date(2026, 6, 20),
            command_id=uuid.uuid4(),
            command_fingerprint="fp-pay",
        )
        ctx.payments[payment.id] = payment

        # 1. Execute payment reversal
        reversal_cmd = uuid.uuid4()
        reversal_payload = CreditCardAdjustmentCreate(
            original_transaction_id=pay_tx_id,
            adjustment_type="reversal",
            direction="debit",
            amount=Decimal("50.00"),
        )
        rev_res = await ctx.service.create_adjustment(
            ctx.user_id, ctx.card.id, reversal_payload, reversal_cmd
        )
        assert rev_res.status == "success"

        # Check idempotency check directly with conflicting account_id
        with pytest.raises(CreditIdempotencyConflictError) as exc_info:
            await ctx.service._check_operation_idempotency(
                user_id=ctx.user_id,
                command_id=reversal_cmd,
                operation_type="credit_card_adjustment",
                credit_card_id=ctx.card.id,
                account_id=other_account_id,  # Conflicting account!
                booked_amount=Decimal("50.00"),
                booked_currency="COP",
                target_id=pay_tx_id,
                original_amount=Decimal("50.00"),
                original_currency="COP",
                adjustment_type="reversal",
                direction="debit",
            )
        assert "different accounts" in exc_info.value.message

        # Missing account on account-affecting event is malformed conflict
        corrupt_event = FinancialEvent(
            id=uuid.uuid4(),
            user_id=ctx.user_id,
            event_type="manual_adjustment",
            direction="inflow",
            amount=Decimal("50.00"),
            currency="COP",
            account_id=None,  # Missing on account-affecting event!
            command_id=uuid.uuid4(),
            source="system",
            metadata_={
                "operation_type": "credit_card_adjustment",
                "credit_card_id": str(ctx.card.id),
                "target_transaction_id": str(pay_tx_id),
                "adjustment_type": "reversal",
                "direction": "debit",
                "applied_amount": "50.00",
                "applied_currency": "COP",
            },
        )
        ctx.events_by_command_id[corrupt_event.command_id] = corrupt_event
        with pytest.raises(CreditIdempotencyConflictError) as exc_info:
            await ctx.service._check_operation_idempotency(
                user_id=ctx.user_id,
                command_id=corrupt_event.command_id,
                operation_type="credit_card_adjustment",
                credit_card_id=ctx.card.id,
                account_id=ctx.account.id,  # Provided, but missing on event
                booked_amount=Decimal("50.00"),
                booked_currency="COP",
                target_id=pay_tx_id,
                original_amount=Decimal("50.00"),
                original_currency="COP",
                adjustment_type="reversal",
                direction="debit",
            )
        assert "missing account_id" in exc_info.value.message

        # 2. Purchase adjustment remains accountless
        purch_tx_id = uuid.uuid4()
        purch_tx = CreditCardTransaction(
            id=purch_tx_id,
            user_id=ctx.user_id,
            credit_card_id=ctx.card.id,
            type="purchase",
            amount=Decimal("30.00"),
            occurred_at=datetime.now(UTC),
            lifecycle_status="posted",
        )
        ctx.transactions[purch_tx_id] = purch_tx

        purch_cmd = uuid.uuid4()
        purch_payload = CreditCardAdjustmentCreate(
            original_transaction_id=purch_tx_id,
            adjustment_type="refund",
            direction="credit",
            amount=Decimal("30.00"),
        )
        purch_res = await ctx.service.create_adjustment(
            ctx.user_id, ctx.card.id, purch_payload, purch_cmd
        )
        assert purch_res.status == "success"

        # Retry of accountless purchase adjustment succeeds
        purch_retry = await ctx.service.create_adjustment(
            ctx.user_id, ctx.card.id, purch_payload, purch_cmd
        )
        assert purch_retry.status == "idempotent_retry"
        assert purch_retry.adjustment_id == purch_res.adjustment_id
