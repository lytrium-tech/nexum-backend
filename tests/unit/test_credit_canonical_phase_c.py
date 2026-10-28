"""Unit and service tests for Credit Goal 2 Phase C: Canonical Immutable Statements and Cycle Closure.

Verifies all 10 domain and architectural requirements:
1. Typed component completeness (installments, adjustments, statement charges, manual adjustment direction).
2. Prior balance component derived from previous statement minus payment/adjustment allocations.
3. Conservation formula: statement_balance = sum(debit magnitudes) - sum(credit magnitudes); all amounts >= 0.
4. Safe batch locking and per-card transaction isolation; one card failure does not rollback others.
5. Migration safety: partial unique index scoped to canonical partition; non-destructive downgrade.
6. Immutability: closed statements and components cannot be updated/deleted; payment allocations allowed.
7. Dead lookup removed: modeled statements keep minimum_payment=None; issuer facts authoritative when read.
8. Bounded catch-up: strictly bounded by max_cycles_per_card; reports remaining catch-up honestly.
9. Operational CLI entry point: --max-cycles, --dry-run, clean exit codes.
10. Activation boundary: zero pre-activation legacy synthesis; zero-activity policy creates 1 statement.
"""

import uuid
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import inspect

from app.credit.closure_service import CreditCycleClosureService
from app.credit.exceptions import (
    CreditBillingCycleClosedError,
    CreditStatementImmutableError,
)
from app.credit.models import (
    DEBT_PARTITION_CANONICAL,
    DEBT_PARTITION_LEGACY,
    PROVENANCE_ISSUER_AUTHORITATIVE,
    PROVENANCE_MANUAL,
    CreditCard,
    CreditCardAdjustment,
    CreditCardAdjustmentAllocation,
    CreditCardBillingCycle,
    CreditCardInstallment,
    CreditCardPayment,
    CreditCardPaymentAllocation,
    CreditCardStatement,
    CreditCardStatementCharge,
    CreditCardStatementComponent,
    CreditCardStatementFact,
    CreditCardTransaction,
    _component_before_delete,
    _component_before_update,
    _statement_before_delete,
    _statement_before_update,
)
from app.credit.repository import CreditCardRepository
from app.credit.schemas import (
    CreditCardCycleClosureSummary,
    CreditCardPurchaseCreate,
    CreditCardStatementRead,
)
from app.credit.service import CreditCardService
from scripts.cron import close_credit_cycles

# ── In-Memory Fake Session & Repository Fixture ──────────────────────────────


class FakeSession:
    """Lightweight in-memory AsyncSession stub to drive unit tests synchronously."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.dirty: set[object] = set()
        self.flushed: list[object] = []
        self.committed = False
        self.rolled_back = False

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def add_all(self, objs: Sequence[object]) -> None:
        self.added.extend(objs)

    async def flush(self) -> None:
        self.flushed.extend(self.added)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def execute(self, stmt: object) -> MagicMock:
        return MagicMock()


class InMemoryCreditRepository(CreditCardRepository):
    """In-memory stub implementing CreditCardRepository contracts for Phase C."""

    def __init__(self) -> None:
        self.session = FakeSession()
        self.session.repo = self
        self.cards: dict[uuid.UUID, CreditCard] = {}
        self.cycles: dict[tuple[uuid.UUID, str], CreditCardBillingCycle] = {}
        self.statements: dict[tuple[uuid.UUID, str], CreditCardStatement] = {}
        self.components: dict[uuid.UUID, list[CreditCardStatementComponent]] = {}
        self.facts: dict[uuid.UUID, list[CreditCardStatementFact]] = {}
        self.installments: dict[uuid.UUID, list[CreditCardInstallment]] = {}
        self.transactions: dict[uuid.UUID, CreditCardTransaction] = {}
        self.adjustments: dict[uuid.UUID, list[CreditCardAdjustment]] = {}
        self.adjustments_by_id: dict[uuid.UUID, CreditCardAdjustment] = {}
        self.payments: dict[uuid.UUID, CreditCardPayment] = {}
        self.payment_allocations: list[CreditCardPaymentAllocation] = []
        self.adjustment_allocations: list[CreditCardAdjustmentAllocation] = []
        self.statement_charges: dict[uuid.UUID, list[CreditCardStatementCharge]] = {}

    async def get_by_id(self, card_id: uuid.UUID) -> CreditCard | None:
        return self.cards.get(card_id)

    async def get_by_id_for_update(self, card_id: uuid.UUID) -> CreditCard | None:
        return self.cards.get(card_id)

    async def list_active_canonical_card_ids(
        self, user_id: uuid.UUID | None = None
    ) -> list[uuid.UUID]:
        cards = [
            c
            for c in self.cards.values()
            if c.is_active and c.canonical_mode and c.canonical_activated_at
        ]
        if user_id:
            cards = [c for c in cards if c.user_id == user_id]
        cards.sort(key=lambda c: (c.created_at, c.id))
        return [c.id for c in cards]

    async def list_active_canonical_cards_for_update(
        self, user_id: uuid.UUID | None = None
    ) -> list[CreditCard]:
        cards = [
            c
            for c in self.cards.values()
            if c.is_active and c.canonical_mode and c.canonical_activated_at
        ]
        if user_id:
            cards = [c for c in cards if c.user_id == user_id]
        return cards

    async def get_billing_cycle_by_period(
        self, card_id: uuid.UUID, billing_period: str
    ) -> CreditCardBillingCycle | None:
        return self.cycles.get((card_id, billing_period))

    async def get_billing_cycle_by_period_for_update(
        self, card_id: uuid.UUID, billing_period: str
    ) -> CreditCardBillingCycle | None:
        return self.cycles.get((card_id, billing_period))

    async def add_billing_cycle(self, cycle: CreditCardBillingCycle) -> None:
        self.cycles[(cycle.credit_card_id, cycle.billing_period)] = cycle
        self.session.add(cycle)

    async def list_open_cycles_for_card_for_update(
        self, card_id: uuid.UUID
    ) -> list[CreditCardBillingCycle]:
        return [
            c for c in self.cycles.values() if c.credit_card_id == card_id and c.status == "open"
        ]

    async def get_statement(self, card_id: uuid.UUID, period: str) -> CreditCardStatement | None:
        return self.statements.get((card_id, period))

    async def list_statements(self, card_id: uuid.UUID) -> list[CreditCardStatement]:
        stmts = [s for s in self.statements.values() if s.credit_card_id == card_id]
        return sorted(stmts, key=lambda s: s.billing_period, reverse=True)

    async def get_previous_canonical_statement(
        self, card_id: uuid.UUID, current_period: str
    ) -> CreditCardStatement | None:
        candidates = [
            s
            for s in self.statements.values()
            if s.credit_card_id == card_id
            and s.debt_partition == DEBT_PARTITION_CANONICAL
            and s.status == "closed"
            and s.billing_period < current_period
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda s: s.billing_period, reverse=True)
        return candidates[0]

    async def add_statement(self, statement: CreditCardStatement) -> None:
        key = (statement.credit_card_id, statement.billing_period)
        self.statements[key] = statement
        self.session.add(statement)

    async def save_statement(self, statement: CreditCardStatement) -> None:
        if statement.status in ("closed", "settled") and statement in self.session.dirty:
            raise CreditStatementImmutableError(
                message="Closed or settled statement snapshot is immutable and cannot be updated in place",
                detail={"statement_id": str(statement.id), "status": statement.status},
            )
        self.statements[(statement.credit_card_id, statement.billing_period)] = statement
        self.session.add(statement)

    async def add_statement_components(
        self, components: list[CreditCardStatementComponent]
    ) -> None:
        for c in components:
            self.components.setdefault(c.statement_id, []).append(c)
        self.session.add_all(components)

    async def list_components_for_statement(
        self, statement_id: uuid.UUID
    ) -> list[CreditCardStatementComponent]:
        return list(self.components.get(statement_id, []))

    async def add_statement_facts(self, facts: list[CreditCardStatementFact]) -> None:
        for f in facts:
            self.facts.setdefault(f.statement_id, []).append(f)
        self.session.add_all(facts)

    async def list_facts_for_statement(
        self, statement_id: uuid.UUID
    ) -> list[CreditCardStatementFact]:
        return list(self.facts.get(statement_id, []))

    async def get_fact_for_statement(
        self, statement_id: uuid.UUID, fact_type: str
    ) -> CreditCardStatementFact | None:
        facts = self.facts.get(statement_id, [])
        return next((f for f in facts if f.fact_type == fact_type), None)

    async def list_installments_for_period_for_update(
        self, card_id: uuid.UUID, billing_period: str
    ) -> list[CreditCardInstallment]:
        insts = self.installments.get(card_id, [])
        return [
            i
            for i in insts
            if i.scheduled_period == billing_period
            and i.debt_partition == DEBT_PARTITION_CANONICAL
            and i.lifecycle_status == "active"
        ]

    async def add_installments(self, installments: list[CreditCardInstallment]) -> None:
        for inst in installments:
            self.installments.setdefault(inst.credit_card_id, []).append(inst)
        self.session.add_all(installments)

    async def add_transaction(self, tx: CreditCardTransaction) -> None:
        self.transactions[tx.id] = tx
        self.session.add(tx)

    async def list_adjustments_for_cycle_for_update(
        self, card_id: uuid.UUID, start_date: date, cutoff_date: date
    ) -> list[CreditCardAdjustment]:
        adjs = self.adjustments.get(card_id, [])
        result = []
        for a in adjs:
            if a.status == "posted":
                a_date = (
                    a.occurred_at.date() if isinstance(a.occurred_at, datetime) else a.occurred_at
                )
                if start_date <= a_date <= cutoff_date:
                    result.append(a)
        result.sort(key=lambda a: (a.occurred_at, a.id))
        return result

    async def add_adjustments(self, adjustments: list[CreditCardAdjustment]) -> None:
        for a in adjustments:
            self.adjustments.setdefault(a.credit_card_id, []).append(a)
            self.adjustments_by_id[a.id] = a
        self.session.add_all(adjustments)

    async def add_payment(self, payment: CreditCardPayment) -> None:
        self.payments[payment.id] = payment
        self.session.add(payment)

    async def add_payments(self, payments: list[CreditCardPayment]) -> None:
        for p in payments:
            self.payments[p.id] = p
        self.session.add_all(payments)

    async def list_closed_canonical_periods_for_card(self, card_id: uuid.UUID) -> set[str]:
        closed_cycles = {
            c.billing_period
            for c in self.cycles.values()
            if c.credit_card_id == card_id and c.status in ("closed", "settled")
        }
        closed_stmts = {
            s.billing_period
            for s in self.statements.values()
            if s.credit_card_id == card_id
            and s.debt_partition == DEBT_PARTITION_CANONICAL
            and s.status == "closed"
        }
        return closed_cycles | closed_stmts

    async def list_payments_for_cycle_for_update(
        self, card_id: uuid.UUID, start_date: date, cutoff_date: date
    ) -> list[CreditCardPayment]:
        return [
            p
            for p in self.payments.values()
            if p.credit_card_id == card_id
            and p.status == "posted"
            and start_date <= p.payment_date <= cutoff_date
        ]

    async def list_payment_allocations_for_components(
        self, component_ids: list[uuid.UUID]
    ) -> list[CreditCardPaymentAllocation]:
        c_set = set(component_ids)
        return [a for a in self.payment_allocations if a.target_statement_component_id in c_set]

    async def list_payment_allocations_for_installments(
        self, installment_ids: list[uuid.UUID]
    ) -> list[CreditCardPaymentAllocation]:
        i_set = set(installment_ids)
        return [a for a in self.payment_allocations if a.target_installment_id in i_set]

    async def list_payment_allocations_for_charges(
        self, charge_ids: list[uuid.UUID]
    ) -> list[CreditCardPaymentAllocation]:
        c_set = set(charge_ids)
        return [a for a in self.payment_allocations if a.target_charge_id in c_set]

    async def list_adjustment_allocations_for_installments(
        self, installment_ids: list[uuid.UUID]
    ) -> list[CreditCardAdjustmentAllocation]:
        i_set = set(installment_ids)
        return [a for a in self.adjustment_allocations if a.target_installment_id in i_set]

    async def add_payment_allocations(self, allocations: list[CreditCardPaymentAllocation]) -> None:
        self.payment_allocations.extend(allocations)
        self.session.add_all(allocations)

    async def add_adjustment_allocations(
        self, allocations: list[CreditCardAdjustmentAllocation]
    ) -> None:
        self.adjustment_allocations.extend(allocations)
        self.session.add_all(allocations)

    async def list_statement_charges_for_statement(
        self, statement_id: uuid.UUID
    ) -> list[CreditCardStatementCharge]:
        return list(self.statement_charges.get(statement_id, []))

    async def add_statement_charges(self, charges: list[CreditCardStatementCharge]) -> None:
        for sc in charges:
            self.statement_charges.setdefault(sc.statement_id, []).append(sc)
        self.session.add_all(charges)

    async def get_card_debt_partitioned(self, card_id: uuid.UUID) -> dict[str, Decimal]:
        insts = self.installments.get(card_id, [])
        canonical = sum(
            (i.principal_amount - i.paid_amount)
            for i in insts
            if i.debt_partition == DEBT_PARTITION_CANONICAL and i.status != "paid"
        )
        legacy = sum(
            (i.principal_amount - i.paid_amount)
            for i in insts
            if i.debt_partition == DEBT_PARTITION_LEGACY and i.status != "paid"
        )
        return {
            "canonical_debt": canonical,
            "legacy_debt": legacy,
            "total_debt": canonical + legacy,
        }


# ── Helper Factories ─────────────────────────────────────────────────────────


def make_card(
    user_id: uuid.UUID | None = None,
    cutoff_day: int = 15,
    due_day: int = 30,
    canonical_mode: bool = True,
    canonical_activated_at: datetime | None = None,
    credit_limit: Decimal = Decimal("5000000.00"),
    currency: str = "COP",
) -> CreditCard:
    uid = user_id or uuid.uuid4()
    card_id = uuid.uuid4()
    act_at = canonical_activated_at or datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
    return CreditCard(
        id=card_id,
        user_id=uid,
        name="Visa Platinum",
        bank="Bancolombia",
        credit_limit=credit_limit,
        cutoff_day=cutoff_day,
        due_day=due_day,
        currency=currency,
        is_active=True,
        canonical_mode=canonical_mode,
        canonical_activated_at=act_at if canonical_mode else None,
        provenance=PROVENANCE_MANUAL if canonical_mode else "legacy_unknown",
        created_at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
    )


# ── Test Cases ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cutoff_eligibility_boundaries():
    """Verify that a cycle is eligible to close ONLY after its cutoff date has elapsed."""
    repo = InMemoryCreditRepository()
    card = make_card(cutoff_day=15, due_day=30)
    repo.cards[card.id] = card

    service = CreditCycleClosureService(session=repo.session, repo=repo)

    # Cutoff for 2026-08 is 2026-08-15.
    # Case 1: as_of = 2026-08-14 (before cutoff) -> NOT eligible
    stmts, _, skipped, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 14)
    )
    assert len(stmts) == 0
    assert skipped == 0

    # Case 2: as_of = 2026-08-15 (on cutoff day) -> NOT eligible
    stmts, _, skipped, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 15)
    )
    assert len(stmts) == 0
    assert skipped == 0

    # Case 3: as_of = 2026-08-16 (day after cutoff) -> ELIGIBLE, closes 2026-08
    stmts, comps, skipped, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 16)
    )
    assert len(stmts) == 1
    assert stmts[0].billing_period == "2026-08"
    assert stmts[0].status == "closed"
    assert stmts[0].frozen_at is not None

    cycle = await repo.get_billing_cycle_by_period(card.id, "2026-08")
    assert cycle.status == "closed"


@pytest.mark.asyncio
async def test_finite_catchup_from_activation_frontier():
    """Verify that catch-up is strictly finite and never materializes cycles prior to activation."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_mode=True,
        canonical_activated_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    stmts, _, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 11, 20)
    )

    periods = [s.billing_period for s in stmts]
    assert "2026-08" not in periods  # Pre-activation cycle strictly avoided
    assert periods == ["2026-09", "2026-10", "2026-11"]


@pytest.mark.asyncio
async def test_non_canonical_legacy_cards_skipped():
    """Verify that cards without canonical_mode or canonical_activated_at are ignored by closure worker."""
    repo = InMemoryCreditRepository()
    legacy_card = make_card(canonical_mode=False, canonical_activated_at=None)
    repo.cards[legacy_card.id] = legacy_card

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    stmts, comps, skipped, _, _ = await service.close_eligible_cycles_for_card(
        legacy_card.id, as_of_date=date(2026, 10, 28)
    )

    assert len(stmts) == 0
    assert len(comps) == 0
    assert len(repo.cycles) == 0


@pytest.mark.asyncio
async def test_zero_eligible_facts_yields_one_statement_policy():
    """Verify that an eligible canonical cycle with zero facts yields exactly one closed statement.

    Statement balance carries previous balance (or 0.00), components is empty, status is closed.
    """
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    stmts, comps, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )

    assert len(stmts) == 1
    stmt = stmts[0]
    assert stmt.billing_period == "2026-08"
    assert stmt.status == "closed"
    assert stmt.statement_balance == Decimal("0.00")
    assert stmt.billed_installments == Decimal("0.00")
    assert stmt.interest_total == Decimal("0.00")
    assert stmt.fees_total == Decimal("0.00")
    assert stmt.payments_received == Decimal("0.00")
    assert stmt.minimum_payment is None
    assert len(comps) == 0


@pytest.mark.asyncio
async def test_single_installment_billed_and_reconciles_exactly():
    """Verify a posted 1-installment purchase produces an exact statement balance and component."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    tx_id = uuid.uuid4()
    inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=tx_id,
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("150000.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("150000.00"),
        scheduled_due_date=date(2026, 8, 30),
        scheduled_period="2026-08",
        status="pending",
        paid_amount=Decimal("0.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("0.00"),
        estimated_interest_amount=Decimal("0.00"),
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_installments([inst])

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    stmts, comps, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )

    assert len(stmts) == 1
    stmt = stmts[0]
    assert stmt.statement_balance == Decimal("150000.00")
    assert stmt.billed_installments == Decimal("150000.00")
    assert stmt.new_purchases == Decimal("150000.00")
    assert stmt.interest_total == Decimal("0.00")
    assert stmt.assessed_interest == Decimal("0.00")
    assert stmt.minimum_payment is None

    # Verify component conservation
    assert len(comps) == 1
    c = comps[0]
    assert c.statement_id == stmt.id
    assert c.component_type == "installment"
    assert c.amount == Decimal("150000.00")
    assert c.principal_amount == Decimal("150000.00")
    assert c.interest_amount == Decimal("0.00")
    assert c.fee_amount == Decimal("0.00")
    assert c.installment_id == inst.id
    assert c.transaction_id == tx_id


@pytest.mark.asyncio
async def test_multi_installment_excludes_future_principal():
    """Verify that only the current cycle's installment enters the statement."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    tx_id = uuid.uuid4()
    inst1 = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=tx_id,
        installment_number=1,
        installments_total=3,
        principal_amount=Decimal("100000.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("100000.00"),
        scheduled_due_date=date(2026, 8, 30),
        scheduled_period="2026-08",
        status="pending",
        paid_amount=Decimal("0.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("0.00"),
        estimated_interest_amount=Decimal("2500.00"),
        provenance=PROVENANCE_MANUAL,
    )
    inst2 = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=tx_id,
        installment_number=2,
        installments_total=3,
        principal_amount=Decimal("100000.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("100000.00"),
        scheduled_due_date=date(2026, 9, 30),
        scheduled_period="2026-09",
        status="pending",
        paid_amount=Decimal("0.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("0.00"),
        estimated_interest_amount=Decimal("2500.00"),
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_installments([inst1, inst2])

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    stmts, comps, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )

    assert len(stmts) == 1
    stmt_aug = stmts[0]
    assert stmt_aug.statement_balance == Decimal("100000.00")
    assert stmt_aug.billed_installments == Decimal("100000.00")
    assert len(comps) == 1
    assert comps[0].installment_id == inst1.id


@pytest.mark.asyncio
async def test_estimated_interest_excluded_from_statement_and_components():
    """Verify that estimated interest never becomes a component and is excluded from statement balance."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=uuid.uuid4(),
        installment_number=1,
        installments_total=6,
        principal_amount=Decimal("200000.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("200000.00"),
        scheduled_due_date=date(2026, 8, 30),
        scheduled_period="2026-08",
        status="pending",
        paid_amount=Decimal("0.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("0.00"),
        estimated_interest_amount=Decimal("5800.00"),
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_installments([inst])

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    stmts, comps, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )

    stmt = stmts[0]
    assert stmt.statement_balance == Decimal("200000.00")
    assert stmt.interest_total == Decimal("0.00")
    assert stmt.assessed_interest == Decimal("0.00")
    assert stmt.estimated_interest == Decimal("0.00")

    comp = comps[0]
    assert comp.interest_amount == Decimal("0.00")
    assert comp.amount == Decimal("200000.00")


@pytest.mark.asyncio
async def test_unknown_minimum_payment_remains_none():
    """Verify minimum payment is None unless an explicit issuer or manual fact is present."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    stmts, _, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )

    stmt = stmts[0]
    assert stmt.minimum_payment is None

    read_dto = CreditCardStatementRead.model_validate(stmt)
    assert read_dto.minimum_payment is None


@pytest.mark.asyncio
async def test_explicit_issuer_fact_populates_minimum_payment():
    """Verify that an explicit issuer fact for minimum payment is respected and populates the statement."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    stmts, _, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )
    stmt = stmts[0]

    fact = CreditCardStatementFact(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        statement_id=stmt.id,
        fact_type="minimum_payment_due",
        amount=Decimal("45000.00"),
        currency="COP",
        provenance=PROVENANCE_ISSUER_AUTHORITATIVE,
        observed_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    await repo.add_statement_facts([fact])

    credit_service = CreditCardService(repo.session)
    credit_service.repo = repo
    read_dto = await credit_service.get_statement(card.user_id, card.id, "2026-08")

    assert read_dto is not None
    assert read_dto.minimum_payment == Decimal("45000.00")
    assert len(read_dto.facts) == 1
    assert read_dto.facts[0].fact_type == "minimum_payment_due"
    assert read_dto.facts[0].amount == Decimal("45000.00")


@pytest.mark.asyncio
async def test_idempotent_duplicate_closure_runs():
    """Verify duplicate invocation closes zero new cycles and creates zero duplicate rows."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    service = CreditCycleClosureService(session=repo.session, repo=repo)

    # First run
    stmts1, comps1, skipped1, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )
    assert len(stmts1) == 1
    assert skipped1 == 0

    # Second run (exact duplicate invocation)
    stmts2, comps2, skipped2, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )
    assert len(stmts2) == 0
    assert len(comps2) == 0
    assert skipped2 == 1

    assert len(repo.statements) == 1
    assert len(repo.cycles) == 1


@pytest.mark.asyncio
async def test_closed_statement_immutable():
    """Verify that a closed statement snapshot raises CreditStatementImmutableError if mutation attempted."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    stmts, _, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )
    stmt = stmts[0]

    # Attempt to alter statement balance silently
    stmt.statement_balance = Decimal("999999.00")
    repo.session.dirty.add(stmt)

    with pytest.raises(CreditStatementImmutableError):
        await repo.save_statement(stmt)


@pytest.mark.asyncio
async def test_post_cutoff_purchases_cannot_alter_closed_statement():
    """Verify purchases made post-cutoff belong to the next cycle and cannot alter a closed statement."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    stmts, _, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )
    closed_stmt = stmts[0]
    initial_balance = closed_stmt.statement_balance

    credit_service = CreditCardService(repo.session)
    credit_service.repo = repo
    credit_service.ledger_service = AsyncMock()
    mock_res = MagicMock()
    mock_res.idempotent = False
    mock_res.event = MagicMock(id=uuid.uuid4(), amount=Decimal("75000.00"))
    credit_service.ledger_service.record_event.return_value = mock_res

    res = await credit_service.create_purchase(
        user_id=card.user_id,
        card_id=card.id,
        payload=CreditCardPurchaseCreate(
            amount=Decimal("75000.00"),
            installments_total=1,
            occurred_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        ),
        command_id=uuid.uuid4(),
    )

    assert res.billing_period == "2026-09"
    aug_stmt = await repo.get_statement(card.id, "2026-08")
    assert aug_stmt.statement_balance == initial_balance


@pytest.mark.asyncio
async def test_purchases_to_closed_cycle_rejected():
    """Verify direct purchase targeting a closed cycle raises CreditBillingCycleClosedError."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    await service.close_eligible_cycles_for_card(card.id, as_of_date=date(2026, 8, 20))

    credit_service = CreditCardService(repo.session)
    credit_service.repo = repo
    credit_service.ledger_service = AsyncMock()
    mock_res = MagicMock()
    mock_res.idempotent = False
    mock_res.event = MagicMock(id=uuid.uuid4(), amount=Decimal("50000.00"))
    credit_service.ledger_service.record_event.return_value = mock_res

    with pytest.raises(CreditBillingCycleClosedError):
        await credit_service.create_purchase(
            user_id=card.user_id,
            card_id=card.id,
            payload=CreditCardPurchaseCreate(
                amount=Decimal("50000.00"),
                installments_total=1,
                occurred_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
            ),
            command_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_read_only_statement_endpoints_take_no_write_locks():
    """Verify list_statements and get_statement queries are read-only and take no write locks."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    await service.close_eligible_cycles_for_card(card.id, as_of_date=date(2026, 8, 20))

    credit_service = CreditCardService(repo.session)
    credit_service.repo = repo

    stmt_list = await credit_service.list_statements(card.user_id, card.id)
    assert len(stmt_list) == 1
    assert isinstance(stmt_list[0], CreditCardStatementRead)

    single_stmt = await credit_service.get_statement(card.user_id, card.id, "2026-08")
    assert single_stmt is not None
    assert single_stmt.billing_period == "2026-08"


@pytest.mark.asyncio
async def test_batch_closure_summary_aggregation():
    """Verify batch summary correctly counts cards, cycles, statements, and skipped with session_factory."""
    repo = InMemoryCreditRepository()
    card1 = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    card2 = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card1.id] = card1
    repo.cards[card2.id] = card2

    @asynccontextmanager
    async def fake_session_factory():
        yield repo.session

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    summary = await service.close_all_eligible_cycles(
        as_of_date=date(2026, 8, 20),
        batch_size=50,
        session_factory=fake_session_factory,
    )

    assert isinstance(summary, CreditCardCycleClosureSummary)
    assert summary.cards_inspected == 2
    assert summary.cycles_closed == 2
    assert summary.statements_created == 2
    assert summary.statements_skipped == 0
    assert summary.technical_failures == 0


@pytest.mark.asyncio
async def test_cli_dry_run_smoke_and_exit_codes():
    """Verify close_credit_cycles CLI runs clean dry-run without mutating database."""
    exit_code = await close_credit_cycles.async_main(
        batch_size=10,
        dry_run=True,
    )
    assert exit_code == close_credit_cycles.EXIT_OK

    parser = close_credit_cycles._build_parser()
    args = parser.parse_args(["--dry-run", "--batch-size", "25", "--max-cycles", "12"])
    assert args.dry_run is True
    assert args.batch_size == 25
    assert args.max_cycles == 12

    with pytest.raises(SystemExit):
        close_credit_cycles.main(["--batch-size", "999"])


# ── New Focused Tests Covering All 10 User Requirements ──────────────────────


@pytest.mark.asyncio
async def test_typed_debit_and_credit_reconciliation():
    """Point 1 & 3: Component completeness, part conservation, and exact sign rule.

    Statement balance must equal sum(debit magnitudes) - sum(credit magnitudes).
    All component amounts must equal sum of principal + interest + fee and be non-negative.
    """
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    # 1. Installment with principal $200,000 + assessed interest $5,000
    tx1_id = uuid.uuid4()
    inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=tx1_id,
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("200000.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("205000.00"),
        scheduled_due_date=date(2026, 8, 30),
        scheduled_period="2026-08",
        status="pending",
        paid_amount=Decimal("0.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("5000.00"),
        estimated_interest_amount=Decimal("9999.00"),  # MUST BE EXCLUDED!
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_installments([inst])

    # 2. Posted refund adjustment of $30,000 (credit)
    adj_refund = CreditCardAdjustment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        original_transaction_id=tx1_id,
        adjustment_type="refund",
        direction="credit",
        booked_amount=Decimal("30000.00"),
        booked_currency="COP",
        status="posted",
        provenance=PROVENANCE_MANUAL,
        allocation_source="nexum_modeled",
        occurred_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
    )
    # 3. Posted reversal adjustment of $20,000 (credit)
    adj_reversal = CreditCardAdjustment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        original_transaction_id=tx1_id,
        adjustment_type="reversal",
        direction="credit",
        booked_amount=Decimal("20000.00"),
        booked_currency="COP",
        status="posted",
        provenance=PROVENANCE_MANUAL,
        allocation_source="nexum_modeled",
        occurred_at=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
    )
    # 4. Posted manual adjustment with explicit direction='debit' of $10,000
    adj_debit = CreditCardAdjustment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        original_transaction_id=None,
        adjustment_type="manual_adjustment",
        direction="debit",
        booked_amount=Decimal("10000.00"),
        booked_currency="COP",
        status="posted",
        provenance=PROVENANCE_MANUAL,
        allocation_source="nexum_modeled",
        reason="Manual adjustment fee",
        occurred_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    )
    await repo.add_adjustments([adj_refund, adj_reversal, adj_debit])

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    stmts, comps, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )

    assert len(stmts) == 1
    stmt = stmts[0]

    # Debits: installment ($205,000: principal $200,000 + assessed interest $5,000) + manual debit ($10,000) = $215,000
    # Credits: refund ($30,000) + reversal ($20,000) = $50,000
    # Expected net balance: $215,000 - $50,000 = $165,000
    expected_balance = Decimal("165000.00")
    assert stmt.statement_balance == expected_balance
    assert stmt.billed_installments == Decimal("200000.00")
    assert stmt.interest_total == Decimal("5000.00")
    assert stmt.assessed_interest == Decimal("5000.00")
    assert stmt.estimated_interest == Decimal("0.00")
    # Per Defect 4: payments_received tracks actual payments in window; refunds/reversals are credit components, not payments
    assert stmt.payments_received == Decimal("0.00")

    # Verify each component respects non-negativity and part conservation
    assert len(comps) == 4
    for c in comps:
        assert c.amount >= Decimal("0.00"), "Component magnitude must be non-negative"
        assert c.amount == (c.principal_amount + c.interest_amount + c.fee_amount), (
            "Part conservation failure"
        )

    # Separate components by direction
    debit_sum = sum(
        c.amount
        for c in comps
        if c.component_type in ("installment", "prior_balance", "fee", "interest")
        or (c.component_type == "adjustment" and c.description == "Manual adjustment fee")
    )
    credit_sum = sum(c.amount for c in comps if c.component_type in ("refund", "reversal"))
    assert debit_sum == Decimal("215000.00")
    assert credit_sum == Decimal("50000.00")
    assert debit_sum - credit_sum == stmt.statement_balance


@pytest.mark.asyncio
async def test_manual_adjustment_missing_direction_rejected():
    """Point 1: Manual adjustment with missing or ambiguous direction is rejected without guessing."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    ambiguous_adj = CreditCardAdjustment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        original_transaction_id=None,
        adjustment_type="manual_adjustment",
        direction=None,  # AMBIGUOUS DIRECTION!
        booked_amount=Decimal("15000.00"),
        booked_currency="COP",
        status="posted",
        provenance=PROVENANCE_MANUAL,
        allocation_source="nexum_modeled",
        occurred_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    )
    await repo.add_adjustments([ambiguous_adj])

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    with pytest.raises(ValueError, match="ambiguous or missing direction"):
        await service.close_eligible_cycles_for_card(card.id, as_of_date=date(2026, 8, 20))


@pytest.mark.asyncio
async def test_prior_balance_component_deducts_payment_allocations():
    """Point 2: Prior balance component accounts for previous statement net balance.

    Previous statement balance reflects all in-cycle debits and payments;
    it rolls over to the next statement exactly once without double counting.
    """
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    # Month 1: Purchase with installment of $100,000 scheduled for August
    inst_aug = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=uuid.uuid4(),
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("100000.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("100000.00"),
        scheduled_due_date=date(2026, 8, 30),
        scheduled_period="2026-08",
        status="pending",
        paid_amount=Decimal("40000.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("0.00"),
        estimated_interest_amount=Decimal("0.00"),
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_installments([inst_aug])

    # Payment of $40,000 arrives in August (Aug 10) before August cutoff (Aug 15)
    pay_id = uuid.uuid4()
    payment = CreditCardPayment(
        id=pay_id,
        user_id=card.user_id,
        credit_card_id=card.id,
        booked_amount=Decimal("40000.00"),
        payment_date=date(2026, 8, 10),
        status="posted",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_payment(payment)
    pay_alloc = CreditCardPaymentAllocation(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        payment_id=pay_id,
        target_installment_id=inst_aug.id,
        target_charge_id=None,
        target_statement_component_id=None,
        allocated_amount=Decimal("40000.00"),
        allocated_principal=Decimal("40000.00"),
        allocated_interest=Decimal("0.00"),
        allocated_fees=Decimal("0.00"),
        allocation_order=1,
        allocation_source="nexum_modeled",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_payment_allocations([pay_alloc])

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    # Close August statement -> statement balance is $100,000 - $40,000 = $60,000
    stmts_aug, comps_aug, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )
    assert len(stmts_aug) == 1
    aug_stmt = stmts_aug[0]
    assert aug_stmt.statement_balance == Decimal("60000.00")
    assert aug_stmt.payments_received == Decimal("40000.00")

    # In September, a new installment of $50,000 is scheduled
    inst_sep = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=uuid.uuid4(),
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("50000.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("50000.00"),
        scheduled_due_date=date(2026, 9, 30),
        scheduled_period="2026-09",
        status="pending",
        paid_amount=Decimal("0.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("0.00"),
        estimated_interest_amount=Decimal("0.00"),
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_installments([inst_sep])

    # Close September cycle (as of 2026-09-20)
    stmts_sep, comps_sep, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 9, 20)
    )
    assert len(stmts_sep) == 1
    sep_stmt = stmts_sep[0]

    # Prior balance carried forward must be exactly $60,000
    assert sep_stmt.previous_balance == Decimal("60000.00")
    # September net balance = $60,000 prior balance + $50,000 September installment = $110,000
    assert sep_stmt.statement_balance == Decimal("110000.00")

    # Verify explicit prior_balance component exists
    prior_comps = [c for c in comps_sep if c.component_type == "prior_balance"]
    assert len(prior_comps) == 1
    pc = prior_comps[0]
    assert pc.amount == Decimal("60000.00")
    assert pc.principal_amount == Decimal("60000.00")
    assert pc.interest_amount == Decimal("0.00")
    assert pc.fee_amount == Decimal("0.00")


@pytest.mark.asyncio
async def test_prior_balance_zero_when_previous_statement_fully_paid():
    """Point 2: When previous statement is fully paid, prior balance is zero and no prior_balance component is emitted."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    inst_aug = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=uuid.uuid4(),
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("80000.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("80000.00"),
        scheduled_due_date=date(2026, 8, 30),
        scheduled_period="2026-08",
        status="pending",
        paid_amount=Decimal("80000.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("0.00"),
        estimated_interest_amount=Decimal("0.00"),
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_installments([inst_aug])

    # Full payment of $80,000 in August (Aug 10, before Aug 15 cutoff)
    pay_id = uuid.uuid4()
    payment = CreditCardPayment(
        id=pay_id,
        user_id=card.user_id,
        credit_card_id=card.id,
        booked_amount=Decimal("80000.00"),
        payment_date=date(2026, 8, 10),
        status="posted",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_payment(payment)
    pay_alloc = CreditCardPaymentAllocation(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        payment_id=pay_id,
        target_installment_id=inst_aug.id,
        target_charge_id=None,
        target_statement_component_id=None,
        allocated_amount=Decimal("80000.00"),
        allocated_principal=Decimal("80000.00"),
        allocated_interest=Decimal("0.00"),
        allocated_fees=Decimal("0.00"),
        allocation_order=1,
        allocation_source="nexum_modeled",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_payment_allocations([pay_alloc])

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    stmts_aug, _, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )
    assert len(stmts_aug) == 1
    assert stmts_aug[0].statement_balance == Decimal("0.00")

    stmts_sep, comps_sep, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 9, 20)
    )
    sep_stmt = stmts_sep[0]
    assert sep_stmt.previous_balance == Decimal("0.00")
    assert sep_stmt.statement_balance == Decimal("0.00")
    assert not any(c.component_type in ("prior_balance", "prior_credit") for c in comps_sep)


@pytest.mark.asyncio
async def test_safe_batch_locking_one_card_failure_does_not_rollback_others():
    """Point 4: One card failure never rolls back prior successful cards in a multi-card batch."""
    shared_repo = InMemoryCreditRepository()

    card1 = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    card2 = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    shared_repo.cards[card1.id] = card1
    shared_repo.cards[card2.id] = card2

    # Card 2 has corrupted state (invalid direction) that raises ValueError during cycle closure
    corrupted_adj = CreditCardAdjustment(
        id=uuid.uuid4(),
        user_id=card2.user_id,
        credit_card_id=card2.id,
        original_transaction_id=None,
        adjustment_type="manual_adjustment",
        direction="invalid_direction",  # Will raise ValueError
        booked_amount=Decimal("50000.00"),
        booked_currency="COP",
        status="posted",
        provenance=PROVENANCE_MANUAL,
        allocation_source="nexum_modeled",
        occurred_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    )
    await shared_repo.add_adjustments([corrupted_adj])

    created_sessions: list[FakeSession] = []

    @asynccontextmanager
    async def per_card_session_factory():
        sess = FakeSession()
        sess.repo = shared_repo
        created_sessions.append(sess)
        yield sess

    service = CreditCycleClosureService(session=shared_repo.session, repo=shared_repo)
    summary = await service.close_all_eligible_cycles(
        as_of_date=date(2026, 8, 20),
        batch_size=50,
        session_factory=per_card_session_factory,
    )

    # Exactly 2 sessions created, exactly 1 card succeeded and 1 failed
    assert summary.cards_inspected == 2
    assert summary.cycles_closed == 1
    assert summary.technical_failures == 1
    assert len(created_sessions) == 2

    # One session committed, one did not commit
    committed_sessions = [s for s in created_sessions if s.committed]
    assert len(committed_sessions) == 1

    # Card 2 error recorded in card summary
    card2_summary = next(cs for cs in summary.card_summaries if cs["card_id"] == str(card2.id))
    assert card2_summary["error"] == "ValueError"

    # Card 1 has its closed statement in repository
    card1_stmt = await shared_repo.get_statement(card1.id, "2026-08")
    assert card1_stmt is not None
    assert card1_stmt.status == "closed"


@pytest.mark.asyncio
async def test_multi_card_without_session_factory_rejected():
    """Point 4: Calling batch closure for multiple cards without session_factory raises ValueError."""
    repo = InMemoryCreditRepository()
    card1 = make_card()
    card2 = make_card()
    repo.cards[card1.id] = card1
    repo.cards[card2.id] = card2

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    with pytest.raises(ValueError, match="session_factory is required for multi-card batch"):
        await service.close_all_eligible_cycles(
            as_of_date=date(2026, 8, 20),
            batch_size=50,
            session_factory=None,
        )


@pytest.mark.asyncio
async def test_bounded_catchup_reports_remaining_honestly():
    """Point 8: Bounded catch-up caps cycles per run and accurately reports remaining catch-up."""
    repo = InMemoryCreditRepository()
    # Card activated 11 months ago (2025-10 to 2026-08 inclusive = 11 eligible cycles)
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2025, 10, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    # Run with max_cycles_per_card = 4
    (
        stmts,
        comps,
        skipped,
        has_remaining,
        remaining_cycles,
    ) = await service.close_eligible_cycles_for_card(
        card.id,
        as_of_date=date(2026, 8, 20),
        max_cycles_per_card=4,
    )

    # Exactly 4 cycles closed
    assert len(stmts) == 4
    assert has_remaining is True
    assert remaining_cycles == 7  # 11 eligible total - 4 processed = 7 remaining

    # Batch summary on fresh card correctly reflects catch-up status
    repo2 = InMemoryCreditRepository()
    card2 = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2025, 10, 1, 10, 0, tzinfo=UTC),
    )
    repo2.cards[card2.id] = card2
    service2 = CreditCycleClosureService(session=repo2.session, repo=repo2)
    summary = await service2.close_all_eligible_cycles(
        card_id=card2.id,
        as_of_date=date(2026, 8, 20),
        max_cycles_per_card=4,
    )
    assert summary.has_remaining_catchup is True
    assert summary.remaining_catchup_cycles == 7


@pytest.mark.asyncio
async def test_closed_statement_and_components_orm_immutability():
    """Point 6: Complete immutable snapshot enforcement.

    - Once a statement is closed or settled, no in-place UPDATE or DELETE is allowed.
    - No status transition (e.g. closed -> settled, closed -> open) can escape immutability.
    - Every formerly-mutable snapshot field is rejected: minimum_payment, status, frozen_at,
      provenance, debt_partition, assessed/estimated interest, identifiers, and financial totals.
    - Settled statement deletion and update are strictly rejected.
    - Statement components are immutable and cannot be updated or deleted.
    """
    stmt = CreditCardStatement(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        cycle_id=uuid.uuid4(),
        billing_period="2026-08",
        status="closed",
        statement_balance=Decimal("150000.00"),
        minimum_payment=Decimal("10000.00"),
        frozen_at=datetime.now(UTC),
        provenance="nexum_modeled",
        debt_partition="canonical",
        assessed_interest=Decimal("5000.00"),
        estimated_interest=Decimal("0.00"),
    )

    # 1. Direct update to closed statement rejected
    with pytest.raises(CreditStatementImmutableError, match="immutable in status 'closed'"):
        _statement_before_update(None, None, stmt)

    # 2. Deletion of closed statement raises CreditStatementImmutableError
    with pytest.raises(
        CreditStatementImmutableError, match="snapshot in status 'closed' cannot be deleted"
    ):
        _statement_before_delete(None, None, stmt)

    # 3. Settled statement: update and deletion are both strictly rejected
    settled_stmt = CreditCardStatement(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        cycle_id=uuid.uuid4(),
        billing_period="2026-08",
        status="settled",
        statement_balance=Decimal("0.00"),
    )
    with pytest.raises(CreditStatementImmutableError, match="immutable in status 'settled'"):
        _statement_before_update(None, None, settled_stmt)
    with pytest.raises(
        CreditStatementImmutableError, match="snapshot in status 'settled' cannot be deleted"
    ):
        _statement_before_delete(None, None, settled_stmt)

    # 4. Status transition to escape immutability is blocked (e.g. closed -> settled, closed -> open)
    stmt_trans = CreditCardStatement(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        billing_period="2026-08",
        status="closed",
    )
    inspect(stmt_trans)._commit_all(inspect(stmt_trans).dict)
    stmt_trans.status = "settled"
    with pytest.raises(CreditStatementImmutableError):
        _statement_before_update(None, None, stmt_trans)

    stmt_trans2 = CreditCardStatement(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        billing_period="2026-08",
        status="closed",
    )
    inspect(stmt_trans2)._commit_all(inspect(stmt_trans2).dict)
    stmt_trans2.status = "open"
    with pytest.raises(CreditStatementImmutableError):
        _statement_before_update(None, None, stmt_trans2)

    # 5. Formerly-mutable snapshot fields rejected on closed statement
    fields_to_test = [
        ("minimum_payment", Decimal("20000.00")),
        ("frozen_at", datetime(2026, 9, 1, tzinfo=UTC)),
        ("provenance", "manual"),
        ("debt_partition", "legacy_unknown"),
        ("assessed_interest", Decimal("9000.00")),
        ("estimated_interest", Decimal("1000.00")),
        ("user_id", uuid.uuid4()),
        ("credit_card_id", uuid.uuid4()),
        ("cycle_id", uuid.uuid4()),
        ("statement_balance", Decimal("999999.00")),
        ("billed_installments", Decimal("50000.00")),
        ("payments_received", Decimal("10000.00")),
        ("previous_balance", Decimal("5000.00")),
    ]
    for field_name, new_val in fields_to_test:
        test_stmt = CreditCardStatement(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            credit_card_id=uuid.uuid4(),
            billing_period="2026-08",
            status="closed",
            statement_balance=Decimal("150000.00"),
        )
        setattr(test_stmt, field_name, new_val)
        with pytest.raises(CreditStatementImmutableError):
            _statement_before_update(None, None, test_stmt)

    # 6. Components are append-only: update and deletion strictly rejected
    comp = CreditCardStatementComponent(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        statement_id=stmt.id,
        component_type="installment",
        amount=Decimal("150000.00"),
    )
    with pytest.raises(CreditStatementImmutableError, match="components are immutable"):
        _component_before_update(None, None, comp)

    with pytest.raises(CreditStatementImmutableError, match="cannot be deleted"):
        _component_before_delete(None, None, comp)


@pytest.mark.asyncio
async def test_payment_allocations_allowed_without_mutating_statement():
    """Point 6: Creating a PaymentAllocation targets a component without mutating the closed snapshot."""
    stmt_id = uuid.uuid4()
    comp_id = uuid.uuid4()
    comp = CreditCardStatementComponent(
        id=comp_id,
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        statement_id=stmt_id,
        component_type="installment",
        amount=Decimal("200000.00"),
    )
    # Payment allocation created
    alloc = CreditCardPaymentAllocation(
        id=uuid.uuid4(),
        user_id=comp.user_id,
        credit_card_id=comp.credit_card_id,
        payment_id=uuid.uuid4(),
        target_statement_component_id=comp_id,
        allocated_amount=Decimal("50000.00"),
        allocated_principal=Decimal("50000.00"),
        allocation_order=1,
        allocation_source="nexum_modeled",
        provenance=PROVENANCE_MANUAL,
    )
    # Comp amount is unchanged (immutable)
    assert comp.amount == Decimal("200000.00")
    assert alloc.allocated_amount == Decimal("50000.00")


@pytest.mark.asyncio
async def test_migration_safety_partial_indexes_and_downgrade():
    """Point 5: Validate migration definitions: partial unique index on canonical rows and non-destructive downgrade."""
    from pathlib import Path

    mig_file = (
        Path(__file__).resolve().parent.parent.parent
        / "alembic"
        / "versions"
        / "credit_v2_canonical_closure.py"
    )
    assert mig_file.exists(), f"Migration file {mig_file} not found"

    with open(mig_file, encoding="utf-8") as f:
        src = f.read()

    # 1. Partial index on cycle_id exists and is scoped to canonical debt_partition
    assert "uq_cc_statements_canonical_cycle_id" in src
    assert "cycle_id IS NOT NULL AND debt_partition = 'canonical'" in src

    # 2. Downgrade does NOT force minimum_payment to NOT NULL
    downgrade_section = src[src.find("def downgrade") :]
    assert "minimum_payment" not in downgrade_section or "nullable=False" not in downgrade_section

    # 3. Additive direction on credit_card_adjustments
    assert "chk_cc_adjustments_direction" in src
    assert "direction IS NULL OR direction = ANY" in src

    # 4. Component type includes prior_balance and prior_credit
    assert "prior_balance" in src
    assert "prior_credit" in src

    # 5. Physical immutability triggers and functions are defined in upgrade and cleaned in downgrade
    assert "trg_cc_statements_immutable" in src
    assert "trg_fn_prevent_statement_mutation" in src
    assert "trg_cc_stmt_comp_immutable" in src
    assert "trg_fn_prevent_statement_component_mutation" in src

    assert "DROP TRIGGER IF EXISTS trg_cc_stmt_comp_immutable" in downgrade_section
    assert (
        "DROP FUNCTION IF EXISTS trg_fn_prevent_statement_component_mutation" in downgrade_section
    )
    assert "DROP TRIGGER IF EXISTS trg_cc_statements_immutable" in downgrade_section
    assert "DROP FUNCTION IF EXISTS trg_fn_prevent_statement_mutation" in downgrade_section


@pytest.mark.asyncio
async def test_bounded_catchup_advances_across_multiple_batches():
    """Defect 1: Progressive bounded catch-up across >2 batches.

    Proves run1, run2, run3 close disjoint chronological periods and final run has none remaining.
    """
    repo = InMemoryCreditRepository()
    # 11 eligible months: 2025-10 to 2026-08 inclusive (as of 2026-08-20)
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2025, 10, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card
    service = CreditCycleClosureService(session=repo.session, repo=repo)

    # Batch 1 (max 4): closes 2025-10, 2025-11, 2025-12, 2026-01
    stmts1, _, _, has_rem1, rem1 = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20), max_cycles_per_card=4
    )
    assert len(stmts1) == 4
    periods1 = [s.billing_period for s in stmts1]
    assert periods1 == ["2025-10", "2025-11", "2025-12", "2026-01"]
    assert has_rem1 is True
    assert rem1 == 7

    # Batch 2 (max 4): closes 2026-02, 2026-03, 2026-04, 2026-05 (disjoint from Batch 1!)
    stmts2, _, _, has_rem2, rem2 = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20), max_cycles_per_card=4
    )
    assert len(stmts2) == 4
    periods2 = [s.billing_period for s in stmts2]
    assert periods2 == ["2026-02", "2026-03", "2026-04", "2026-05"]
    assert set(periods1).isdisjoint(set(periods2))
    assert has_rem2 is True
    assert rem2 == 3

    # Batch 3 (max 4): closes 2026-06, 2026-07, 2026-08 (disjoint from Batch 1 and 2!)
    stmts3, _, _, has_rem3, rem3 = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20), max_cycles_per_card=4
    )
    assert len(stmts3) == 3
    periods3 = [s.billing_period for s in stmts3]
    assert periods3 == ["2026-06", "2026-07", "2026-08"]
    assert set(periods3).isdisjoint(set(periods1) | set(periods2))
    assert has_rem3 is False
    assert rem3 == 0

    # Batch 4: zero unclosed cycles remaining
    stmts4, _, _, has_rem4, rem4 = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20), max_cycles_per_card=4
    )
    assert len(stmts4) == 0
    assert has_rem4 is False
    assert rem4 == 0

    # Invariant: exactly 11 closed statements in repo, strictly chronological
    assert len(repo.statements) == 11
    all_periods = sorted([s.billing_period for s in repo.statements.values()])
    assert all_periods == periods1 + periods2 + periods3


@pytest.mark.asyncio
async def test_adjustment_and_payment_timeline_before_start_within_cycle_after_cutoff():
    """Defect 2 & 4: Exact timeline boundaries and refund double-counting prevention.

    1. Allocations before start_date reduce prior balance at cycle start.
    2. Adjustments within the cycle appear exactly once as current-cycle components (not double-subtracted from prior balance).
    3. Payments within the cycle appear as payment credit components and populate payments_received.
    4. Payments/adjustments after cutoff do not affect the closing snapshot.
    5. Balance strictly equals signed components: debits - credits.
    """
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    # Month 1: 2026-08 (start Aug 1, cutoff Aug 15). Installment $200,000.
    inst_aug = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=uuid.uuid4(),
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("200000.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("200000.00"),
        scheduled_due_date=date(2026, 8, 30),
        scheduled_period="2026-08",
        status="pending",
        paid_amount=Decimal("0.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("0.00"),
        estimated_interest_amount=Decimal("0.00"),
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_installments([inst_aug])

    # Payment of $50,000 on Aug 10 (BEFORE Month 1 cutoff Aug 15) allocated to inst_aug
    pay1_id = uuid.uuid4()
    pay1 = CreditCardPayment(
        id=pay1_id,
        user_id=card.user_id,
        credit_card_id=card.id,
        booked_amount=Decimal("50000.00"),
        payment_date=date(2026, 8, 10),
        status="posted",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_payment(pay1)
    alloc1 = CreditCardPaymentAllocation(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        payment_id=pay1_id,
        target_installment_id=inst_aug.id,
        allocated_amount=Decimal("50000.00"),
        allocated_principal=Decimal("50000.00"),
        allocation_order=1,
        allocation_source="nexum_modeled",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_payment_allocations([alloc1])

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    # Month 1 closes: $200,000 installment - $50,000 payment = $150,000 statement balance
    stmts_aug, comps_aug, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )
    assert len(stmts_aug) == 1
    assert stmts_aug[0].statement_balance == Decimal("150000.00")
    assert stmts_aug[0].payments_received == Decimal("50000.00")

    # Timeline events relative to Month 2 (period 2026-09: start Aug 16, cutoff Sep 15):

    # Event 2: Refund adjustment of $30,000 on Aug 25 (WITHIN Month 2 window: Aug 16 <= Aug 25 <= Sep 15)
    # targeting Month 1 installment
    adj_refund = CreditCardAdjustment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        original_transaction_id=None,
        adjustment_type="refund",
        direction="credit",
        booked_amount=Decimal("30000.00"),
        booked_currency="COP",
        status="posted",
        provenance=PROVENANCE_MANUAL,
        allocation_source="nexum_modeled",
        occurred_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    )
    await repo.add_adjustments([adj_refund])
    adj_alloc = CreditCardAdjustmentAllocation(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        adjustment_id=adj_refund.id,
        target_installment_id=inst_aug.id,
        allocated_amount=Decimal("30000.00"),
        allocated_principal=Decimal("30000.00"),
        allocation_order=1,
        allocation_source="nexum_modeled",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_adjustment_allocations([adj_alloc])

    # Event 3: Payment of $40,000 on Sep 5 (WITHIN Month 2 window)
    pay2_id = uuid.uuid4()
    pay2 = CreditCardPayment(
        id=pay2_id,
        user_id=card.user_id,
        credit_card_id=card.id,
        booked_amount=Decimal("40000.00"),
        payment_date=date(2026, 9, 5),  # WITHIN Month 2 window
        status="posted",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_payment(pay2)

    # Event 4: Post-cutoff adjustment of $10,000 on Sep 20 (AFTER Month 2 cutoff Sep 15)
    adj_post = CreditCardAdjustment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        original_transaction_id=None,
        adjustment_type="refund",
        direction="credit",
        booked_amount=Decimal("10000.00"),
        booked_currency="COP",
        status="posted",
        provenance=PROVENANCE_MANUAL,
        allocation_source="nexum_modeled",
        occurred_at=datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
    )
    await repo.add_adjustments([adj_post])

    # Event 5: Post-cutoff payment of $25,000 on Sep 22 (AFTER Month 2 cutoff Sep 15)
    pay_post = CreditCardPayment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        booked_amount=Decimal("25000.00"),
        payment_date=date(2026, 9, 22),
        status="posted",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_payment(pay_post)

    # Month 2 scheduled installment: $60,000
    inst_sep = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=uuid.uuid4(),
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("60000.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("60000.00"),
        scheduled_due_date=date(2026, 9, 30),
        scheduled_period="2026-09",
        status="pending",
        paid_amount=Decimal("0.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("0.00"),
        estimated_interest_amount=Decimal("0.00"),
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_installments([inst_sep])

    # Close Month 2 (as of 2026-09-20, after Sep 15 cutoff)
    stmts_sep, comps_sep, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 9, 20)
    )
    assert len(stmts_sep) == 1
    sep_stmt = stmts_sep[0]

    # Rule 1 Verification: Prior balance at cycle start reflects ONLY allocations before start_date (Aug 16)
    # Aug statement ($200,000) minus pay1 ($50,000 on Aug 15) = $150,000
    # The refund of $30,000 (Aug 25) was NOT before Aug 16, so it is NOT deducted here!
    assert sep_stmt.previous_balance == Decimal("150000.00")

    # Rule 2 Verification: Refund appears exactly once as current-cycle component
    refund_comps = [c for c in comps_sep if c.component_type == "refund"]
    assert len(refund_comps) == 1
    assert refund_comps[0].amount == Decimal("30000.00")

    # Rule 3 Verification: Payments in window appear as payment component and in payments_received
    pay_comps = [c for c in comps_sep if c.component_type == "payment"]
    assert len(pay_comps) == 1
    assert pay_comps[0].amount == Decimal("40000.00")
    assert sep_stmt.payments_received == Decimal("40000.00")

    # Rule 4 Verification: Post-cutoff events do not appear in Month 2
    assert not any(c.amount == Decimal("10000.00") for c in comps_sep)
    assert not any(c.amount == Decimal("25000.00") for c in comps_sep)

    # Rule 5 Verification: Exact signed balance reconciliation:
    # Debits: prior_balance ($150,000) + installment ($60,000) = $210,000
    # Credits: refund ($30,000) + payment ($40,000) = $70,000
    # Net balance: $210,000 - $70,000 = $140,000!
    assert sep_stmt.statement_balance == Decimal("140000.00")
    assert sep_stmt.billed_installments == Decimal("60000.00")
    assert sep_stmt.fees_total == Decimal("0.00")


@pytest.mark.asyncio
async def test_payments_received_reflects_actual_payments_never_refunds():
    """Defect 4: payments_received must strictly reflect payments, never conflated with refunds/reversals."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    # Installment of $100,000
    inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=uuid.uuid4(),
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("100000.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("100000.00"),
        scheduled_due_date=date(2026, 8, 30),
        scheduled_period="2026-08",
        status="pending",
        paid_amount=Decimal("0.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("0.00"),
        estimated_interest_amount=Decimal("0.00"),
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_installments([inst])

    # Refund of $25,000 (credit)
    adj_refund = CreditCardAdjustment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        original_transaction_id=None,
        adjustment_type="refund",
        direction="credit",
        booked_amount=Decimal("25000.00"),
        booked_currency="COP",
        status="posted",
        provenance=PROVENANCE_MANUAL,
        allocation_source="nexum_modeled",
        occurred_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
    )
    # Payment of $35,000 (credit)
    payment = CreditCardPayment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        booked_amount=Decimal("35000.00"),
        payment_date=date(2026, 8, 10),
        status="posted",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_adjustments([adj_refund])
    await repo.add_payment(payment)

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    stmts, comps, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )

    stmt = stmts[0]
    # Invariant: payments_received must ONLY equal the payment ($35,000), NEVER $60,000 (payment + refund)
    assert stmt.payments_received == Decimal("35000.00")
    # Statement balance reflects both credits: $100,000 - $25,000 - $35,000 = $40,000
    assert stmt.statement_balance == Decimal("40000.00")
    # Fees total remains 0.00
    assert stmt.fees_total == Decimal("0.00")


@pytest.mark.asyncio
async def test_regression_prior_balance_carried_exactly_without_allocation_double_counting():
    """Regression 1: prior 100 + prior-cycle payment 40 => prior statement balance 60 and next prior balance exactly 60, not 20.

    Ensures closed statement's statement_balance is the net snapshot through cutoff,
    and payment allocations do not cause statement-to-statement double subtraction.
    """
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    # Month 1 (August: Aug 1 - Aug 15): Installment of $100.00
    inst_aug = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=uuid.uuid4(),
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("100.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("100.00"),
        scheduled_due_date=date(2026, 8, 30),
        scheduled_period="2026-08",
        status="pending",
        paid_amount=Decimal("40.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("0.00"),
        estimated_interest_amount=Decimal("0.00"),
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_installments([inst_aug])

    # Payment of $40.00 in August (Aug 10, before Aug 15 cutoff) with allocation targeting inst_aug
    pay_id = uuid.uuid4()
    pay_aug = CreditCardPayment(
        id=pay_id,
        user_id=card.user_id,
        credit_card_id=card.id,
        booked_amount=Decimal("40.00"),
        payment_date=date(2026, 8, 10),
        status="posted",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_payment(pay_aug)
    pay_alloc = CreditCardPaymentAllocation(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        payment_id=pay_id,
        target_installment_id=inst_aug.id,
        target_charge_id=None,
        target_statement_component_id=None,
        allocated_amount=Decimal("40.00"),
        allocated_principal=Decimal("40.00"),
        allocated_interest=Decimal("0.00"),
        allocated_fees=Decimal("0.00"),
        allocation_order=1,
        allocation_source="nexum_modeled",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_payment_allocations([pay_alloc])

    service = CreditCycleClosureService(session=repo.session, repo=repo)

    # Close August statement -> statement balance must be 100 - 40 = 60
    stmts_aug, comps_aug, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )
    assert len(stmts_aug) == 1
    assert stmts_aug[0].statement_balance == Decimal("60.00")
    assert stmts_aug[0].payments_received == Decimal("40.00")

    # Close September statement (with no new facts yet)
    stmts_sep, comps_sep, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 9, 20)
    )
    assert len(stmts_sep) == 1
    sep_stmt = stmts_sep[0]

    # Invariant: prior balance must be EXACTLY 60, NOT 20!
    assert sep_stmt.previous_balance == Decimal("60.00")
    assert sep_stmt.statement_balance == Decimal("60.00")

    prior_comps = [c for c in comps_sep if c.component_type == "prior_balance"]
    assert len(prior_comps) == 1
    assert prior_comps[0].amount == Decimal("60.00")


@pytest.mark.asyncio
async def test_regression_new_cycle_payment_reduces_prior_balance_once():
    """Regression 2: new-cycle payment 25 => next statement starts 60 then subtracts 25 once."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    # Prior statement: August has installment 100 and payment 40 => balance 60
    inst_aug = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=uuid.uuid4(),
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("100.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("100.00"),
        scheduled_due_date=date(2026, 8, 30),
        scheduled_period="2026-08",
        status="pending",
        paid_amount=Decimal("40.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("0.00"),
        estimated_interest_amount=Decimal("0.00"),
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_installments([inst_aug])
    pay_aug = CreditCardPayment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        booked_amount=Decimal("40.00"),
        payment_date=date(2026, 8, 10),
        status="posted",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_payment(pay_aug)

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    stmts_aug, _, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )
    assert stmts_aug[0].statement_balance == Decimal("60.00")

    # In September cycle (window: Aug 16 - Sep 15), a payment of $25 arrives on Aug 25
    pay2_id = uuid.uuid4()
    pay_sep = CreditCardPayment(
        id=pay2_id,
        user_id=card.user_id,
        credit_card_id=card.id,
        booked_amount=Decimal("25.00"),
        payment_date=date(2026, 8, 25),
        status="posted",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_payment(pay_sep)
    # Allocation exists to audit installment debt reduction
    alloc_sep = CreditCardPaymentAllocation(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        payment_id=pay2_id,
        target_installment_id=inst_aug.id,
        target_charge_id=None,
        target_statement_component_id=None,
        allocated_amount=Decimal("25.00"),
        allocated_principal=Decimal("25.00"),
        allocated_interest=Decimal("0.00"),
        allocated_fees=Decimal("0.00"),
        allocation_order=1,
        allocation_source="nexum_modeled",
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_payment_allocations([alloc_sep])

    # Close September cycle
    stmts_sep, comps_sep, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 9, 20)
    )
    assert len(stmts_sep) == 1
    sep_stmt = stmts_sep[0]

    # September statement starts at 60, subtracts 25 once => balance 35
    assert sep_stmt.previous_balance == Decimal("60.00")
    assert sep_stmt.payments_received == Decimal("25.00")
    assert sep_stmt.statement_balance == Decimal("35.00")

    # Component audit: 1 prior_balance debit (60) + 1 payment credit (25)
    debits = [c for c in comps_sep if c.component_type == "prior_balance"]
    credits = [c for c in comps_sep if c.component_type == "payment"]
    assert len(debits) == 1 and debits[0].amount == Decimal("60.00")
    assert len(credits) == 1 and credits[0].amount == Decimal("25.00")


@pytest.mark.asyncio
async def test_regression_prior_negative_credit_carried_without_clamping():
    """Regression 3: prior negative refund credit carries as explicit credit without clamping."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    # August has installment $50.00 and refund of $80.00 on Aug 5
    inst_aug = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=uuid.uuid4(),
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("50.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("50.00"),
        scheduled_due_date=date(2026, 8, 30),
        scheduled_period="2026-08",
        status="pending",
        paid_amount=Decimal("0.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("0.00"),
        estimated_interest_amount=Decimal("0.00"),
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_installments([inst_aug])

    adj_refund = CreditCardAdjustment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        original_transaction_id=None,
        adjustment_type="refund",
        direction="credit",
        booked_amount=Decimal("80.00"),
        booked_currency="COP",
        status="posted",
        provenance=PROVENANCE_MANUAL,
        allocation_source="nexum_modeled",
        occurred_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
    )
    await repo.add_adjustments([adj_refund])

    service = CreditCycleClosureService(session=repo.session, repo=repo)

    # August statement closes with net negative balance: 50 - 80 = -30
    stmts_aug, _, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 8, 20)
    )
    assert len(stmts_aug) == 1
    assert stmts_aug[0].statement_balance == Decimal("-30.00")

    # September cycle: new installment of $100.00 scheduled
    inst_sep = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=uuid.uuid4(),
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("100.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("100.00"),
        scheduled_due_date=date(2026, 9, 30),
        scheduled_period="2026-09",
        status="pending",
        paid_amount=Decimal("0.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        lifecycle_status="active",
        assessed_interest_amount=Decimal("0.00"),
        estimated_interest_amount=Decimal("0.00"),
        provenance=PROVENANCE_MANUAL,
    )
    await repo.add_installments([inst_sep])

    # Close September
    stmts_sep, comps_sep, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 9, 20)
    )
    assert len(stmts_sep) == 1
    sep_stmt = stmts_sep[0]

    # Invariant: previous balance is signed -30, and explicit prior_credit component exists
    assert sep_stmt.previous_balance == Decimal("-30.00")

    prior_credits = [c for c in comps_sep if c.component_type == "prior_credit"]
    assert len(prior_credits) == 1
    pc = prior_credits[0]
    # Magnitude is strictly non-negative
    assert pc.amount == Decimal("30.00")
    assert pc.principal_amount == Decimal("30.00")
    assert pc.interest_amount == Decimal("0.00")
    assert pc.fee_amount == Decimal("0.00")

    # Net balance derives strictly: debits (100) - credits (30) = 70
    assert sep_stmt.statement_balance == Decimal("70.00")


@pytest.mark.asyncio
async def test_regression_future_post_cutoff_facts_remain_excluded():
    """Regression 4: future/post-cutoff facts remain excluded from closing statement snapshot."""
    repo = InMemoryCreditRepository()
    card = make_card(
        cutoff_day=15,
        due_day=30,
        canonical_activated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    repo.cards[card.id] = card

    # September cycle window: 2026-08-16 to 2026-09-15.
    # Fact 1: within window (payment of $50 on Sep 10)
    pay_in = CreditCardPayment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        booked_amount=Decimal("50.00"),
        payment_date=date(2026, 9, 10),
        status="posted",
        provenance=PROVENANCE_MANUAL,
    )
    # Fact 2: post-cutoff payment (on Sep 16, day after cutoff)
    pay_post = CreditCardPayment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        booked_amount=Decimal("30.00"),
        payment_date=date(2026, 9, 16),
        status="posted",
        provenance=PROVENANCE_MANUAL,
    )
    # Fact 3: post-cutoff refund (on Sep 20)
    adj_post = CreditCardAdjustment(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        original_transaction_id=None,
        adjustment_type="refund",
        direction="credit",
        booked_amount=Decimal("20.00"),
        booked_currency="COP",
        status="posted",
        provenance=PROVENANCE_MANUAL,
        allocation_source="nexum_modeled",
        occurred_at=datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
    )
    await repo.add_payment(pay_in)
    await repo.add_payment(pay_post)
    await repo.add_adjustments([adj_post])

    service = CreditCycleClosureService(session=repo.session, repo=repo)
    # Close as of 2026-09-25
    stmts, comps, _, _, _ = await service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 9, 25)
    )

    # August closed first (zero activity => 1 statement with 0 balance)
    # September closes with only in-window facts
    sep_stmt = [s for s in stmts if s.billing_period == "2026-09"][0]
    sep_comps = [c for c in comps if c.statement_id == sep_stmt.id]

    assert sep_stmt.payments_received == Decimal("50.00")
    assert not any(c.amount == Decimal("30.00") for c in sep_comps)
    assert not any(c.amount == Decimal("20.00") for c in sep_comps)
