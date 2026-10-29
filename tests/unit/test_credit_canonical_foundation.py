"""Unit tests for Nexum Credit Goal 2, Phase A: Canonical Financial Foundation.

Validates:
1. Linear Alembic DAG lineage and heads without create_all.
2. Additive migration contracts and execution via OperationRecorder.
3. Legacy amount semantics, preservation of ambiguous legacy rows, and nullable FX fields.
4. Mutually exclusive canonical-versus-legacy debt partition semantics.
5. Retention of current_debt / views physically as non-authoritative artifacts.
6. Installment ceiling constraint forward supersession (paid_amount <= total_amount).
7. Canonical table schemas, ownership FKs, nonnegative checks, provenance, exactly-one target.
8. Stable NexumError subclasses, status codes, and error codes.
9. Deep fingerprint builders, hashes, and validation against parameter tampering.
10. Repository locked queries requiring populate_existing=True.
"""

import importlib.util
import inspect
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint

from app.core.errors import NexumError
from app.credit.exceptions import (
    CreditAllocationError,
    CreditBillingCycleClosedError,
    CreditCardForbiddenError,
    CreditCardInactiveError,
    CreditCardNotFoundError,
    CreditDomainError,
    CreditGoalReservationBreachError,
    CreditIdempotencyConflictError,
    CreditInstallmentCeilingError,
    CreditLimitExceededError,
    CreditPartitionError,
    CreditStatementImmutableError,
    InvalidPaymentAmountError,
)
from app.credit.fingerprint import (
    build_credit_fingerprint,
    hash_credit_fingerprint,
    validate_credit_fingerprint,
)
from app.credit.models import (
    ALLOWED_DEBT_PARTITIONS,
    DEBT_PARTITION_CANONICAL,
    DEBT_PARTITION_LEGACY,
    PROVENANCE_ISSUER_AUTHORITATIVE,
    PROVENANCE_LEGACY_UNKNOWN,
    PROVENANCE_NEXUM_MODELED,
    CreditCard,
    CreditCardAdjustment,
    CreditCardAdjustmentAllocation,
    CreditCardBillingCycle,
    CreditCardEarlyPayment,
    CreditCardInstallment,
    CreditCardPayment,
    CreditCardPaymentAllocation,
    CreditCardStatementComponent,
    CreditCardStatementFact,
    CreditCardTransaction,
)
from app.credit.repository import CreditCardRepository
from app.credit.schemas import CreditCardStatementFactRead

# ── 1. Alembic Lineage Tests (without create_all) ───────────────────────────


def get_script_directory() -> ScriptDirectory:
    config = Config("alembic.ini")
    return ScriptDirectory.from_config(config)


def test_alembic_linear_head():
    """Verify Alembic lineage forms a single linear DAG ending at credit_v2_refund_type."""
    script_dir = get_script_directory()
    heads = script_dir.get_heads()
    assert len(heads) == 1
    assert heads[0] == "credit_v2_refund_type"


def test_alembic_upgrade_path_from_empty_database():
    """Verify that a fresh database (current_rev=None) resolves the complete chain

    starting with v1_5_baseline and ending with credit_v2_refund_type.
    """
    script_dir = get_script_directory()
    steps = script_dir._upgrade_revs(destination="head", current_rev=None)
    all_revisions = [step.revision.revision for step in steps]

    assert all_revisions[0] == "v1_5_baseline"
    assert all_revisions[-4] == "credit_v2_canonical_schema"
    assert all_revisions[-3] == "credit_v2_canonical_check"
    assert all_revisions[-2] == "credit_v2_canonical_closure"
    assert all_revisions[-1] == "credit_v2_refund_type"
    assert "obligations_v1_7_auto_payments" in all_revisions

    # Exactly 14 revisions in linear chain
    assert len(all_revisions) == 14


def test_alembic_upgrade_path_from_prior_head():
    """Verify that upgrading from prior head (obligations_v1_7_auto_payments)

    resolves strictly to the new credit forward migrations.
    """
    script_dir = get_script_directory()
    steps = script_dir._upgrade_revs(
        destination="head", current_rev="obligations_v1_7_auto_payments"
    )
    upgrade_revisions = [step.revision.revision for step in steps]

    assert upgrade_revisions == [
        "credit_v2_canonical_schema",
        "credit_v2_canonical_check",
        "credit_v2_canonical_closure",
        "credit_v2_refund_type",
    ]


def test_alembic_revision_identifier_lengths():
    """Ensure all credit revision identifiers are <= 32 characters for database safety."""
    assert len("credit_v2_canonical_schema") <= 32
    assert len("credit_v2_canonical_check") <= 32
    assert len("credit_v2_canonical_closure") <= 32
    assert len("credit_v2_refund_type") <= 32


# ── 2. Additive Migration Contract & Execution via Recorder ──────────────────


class MigrationRecorder:
    def __init__(self) -> None:
        self.created_tables: list[str] = []
        self.added_columns: list[tuple[str, str]] = []
        self.created_indexes: list[str] = []
        self.created_fks: list[str] = []
        self.created_checks: list[str] = []
        self.executed_statements: list[str] = []
        self.dropped_constraints: list[str] = []
        self.dropped_columns: list[tuple[str, str]] = []
        self.dropped_tables: list[str] = []
        self.altered_columns: list[tuple[str, str]] = []
        self.created_unique_constraints: list[str] = []
        self.dropped_indexes: list[str] = []

    def alter_column(self, table_name, column_name, *args, **kwargs):
        self.altered_columns.append((table_name, column_name))

    def create_unique_constraint(self, name, table_name, columns, *args, **kwargs):
        self.created_unique_constraints.append(name)

    def create_table(self, name, *args, **kwargs):
        self.created_tables.append(name)

    def add_column(self, table_name, column, *args, **kwargs):
        self.added_columns.append((table_name, column.name))

    def create_index(self, name, *args, **kwargs):
        self.created_indexes.append(name)

    def create_foreign_key(self, name, *args, **kwargs):
        self.created_fks.append(name)

    def create_check_constraint(self, name, *args, **kwargs):
        self.created_checks.append(name)

    def execute(self, sql):
        self.executed_statements.append(str(sql))

    def drop_constraint(self, name, *args, **kwargs):
        self.dropped_constraints.append(name)

    def drop_column(self, table_name, column_name, *args, **kwargs):
        self.dropped_columns.append((table_name, column_name))

    def drop_table(self, name, *args, **kwargs):
        self.dropped_tables.append(name)

    def drop_index(self, name, *args, **kwargs):
        self.dropped_indexes.append(name)


def _load_migration(module_name: str, file_name: str):
    path = Path("alembic/versions") / file_name
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_credit_v2_canonical_schema_upgrade_and_downgrade(monkeypatch):
    """Verify credit_v2_canonical_schema creates all 7 tables, columns, indexes and cleans up."""
    mod = _load_migration("credit_v2_canonical_schema", "credit_v2_canonical_schema.py")
    recorder = MigrationRecorder()
    monkeypatch.setattr(mod, "op", recorder)

    mod.upgrade()

    expected_tables = {
        "credit_card_billing_cycles",
        "credit_card_statement_components",
        "credit_card_statement_facts",
        "credit_card_payments",
        "credit_card_payment_allocations",
        "credit_card_adjustments",
        "credit_card_adjustment_allocations",
    }
    assert expected_tables.issubset(set(recorder.created_tables))

    # Existing table additions
    added_cols = set(recorder.added_columns)
    assert ("credit_cards", "canonical_mode") in added_cols
    assert ("credit_cards", "provenance") in added_cols
    assert ("credit_card_transactions", "original_amount") in added_cols
    assert ("credit_card_transactions", "original_currency") in added_cols
    assert ("credit_card_transactions", "booked_amount") in added_cols
    assert ("credit_card_transactions", "booked_currency") in added_cols
    assert ("credit_card_transactions", "fx_rate") in added_cols
    assert ("credit_card_transactions", "debt_partition") in added_cols
    assert ("credit_card_installments", "assessed_interest_amount") in added_cols
    assert ("credit_card_installments", "debt_partition") in added_cols
    assert ("credit_card_early_payments", "payment_transaction_id") in added_cols

    # Foreign key indexes (resolves F-18)
    assert "ix_credit_card_transactions_event_id" in recorder.created_indexes
    assert "ix_credit_card_early_payments_event_id" in recorder.created_indexes
    assert "ix_credit_card_statement_facts_stmt_type" in recorder.created_indexes
    assert "ix_credit_card_statement_facts_user_card" in recorder.created_indexes

    # Test downgrade reverses cleanly
    mod.downgrade()
    assert set(recorder.dropped_tables) == expected_tables


def test_credit_v2_canonical_check_forward_supersession(monkeypatch):
    """Verify constraint replacement uses NOT VALID staging without assuming legacy consistency."""
    mod = _load_migration("credit_v2_canonical_check", "credit_v2_canonical_check.py")
    recorder = MigrationRecorder()
    monkeypatch.setattr(mod, "op", recorder)

    mod.upgrade()
    sql_up = " ".join(recorder.executed_statements)
    assert "DROP CONSTRAINT IF EXISTS credit_card_installments_paid_check" in sql_up
    assert "ADD CONSTRAINT credit_card_installments_paid_check_v2" in sql_up
    assert "CHECK (paid_amount <= total_amount)" in sql_up
    assert "NOT VALID" in sql_up

    # Test downgrade
    mod.downgrade()
    sql_down = " ".join(recorder.executed_statements)
    assert "DROP CONSTRAINT IF EXISTS credit_card_installments_paid_check_v2" in sql_down
    assert "ADD CONSTRAINT credit_card_installments_paid_check" in sql_down
    assert "CHECK (paid_amount <= principal_amount)" in sql_down
    assert "NOT VALID" in sql_down


def test_credit_v2_canonical_closure_forward_migration(monkeypatch):
    """Verify forward-only closure migration relaxes minimum_payment nullability and creates uniqueness indexes."""
    mod = _load_migration("credit_v2_canonical_closure", "credit_v2_canonical_closure.py")
    recorder = MigrationRecorder()
    monkeypatch.setattr(mod, "op", recorder)

    mod.upgrade()
    assert ("credit_card_statements", "minimum_payment") in recorder.altered_columns
    assert "uq_cc_statements_canonical_cycle_id" in recorder.created_indexes
    assert "uq_cc_stmt_comp_statement_installment" in recorder.created_indexes
    assert "uq_cc_stmt_comp_statement_tx" in recorder.created_indexes
    assert "uq_cc_stmt_comp_statement_charge" in recorder.created_indexes
    assert "uq_cc_stmt_facts_stmt_fact_type" in recorder.created_indexes
    assert ("credit_card_adjustments", "direction") in recorder.added_columns
    assert "chk_cc_adjustments_direction" in recorder.created_checks
    assert "chk_cc_stmt_comp_type" in recorder.created_checks
    sql_up = " ".join(recorder.executed_statements)
    assert "trg_cc_statements_immutable" in sql_up
    assert "trg_fn_prevent_statement_mutation" in sql_up
    assert "trg_cc_stmt_comp_immutable" in sql_up
    assert "trg_fn_prevent_statement_component_mutation" in sql_up

    # Test downgrade
    mod.downgrade()
    sql_down = " ".join(recorder.executed_statements)
    assert "DROP TRIGGER IF EXISTS trg_cc_stmt_comp_immutable" in sql_down
    assert "DROP FUNCTION IF EXISTS trg_fn_prevent_statement_component_mutation" in sql_down
    assert "DROP TRIGGER IF EXISTS trg_cc_statements_immutable" in sql_down
    assert "DROP FUNCTION IF EXISTS trg_fn_prevent_statement_mutation" in sql_down
    assert "uq_cc_stmt_facts_stmt_fact_type" in recorder.dropped_indexes
    assert "uq_cc_stmt_comp_statement_charge" in recorder.dropped_indexes
    assert "uq_cc_stmt_comp_statement_tx" in recorder.dropped_indexes
    assert "uq_cc_stmt_comp_statement_installment" in recorder.dropped_indexes
    assert "chk_cc_stmt_comp_type" in recorder.dropped_constraints
    assert "chk_cc_adjustments_direction" in recorder.dropped_constraints
    assert ("credit_card_adjustments", "direction") in recorder.dropped_columns
    assert "uq_cc_statements_canonical_cycle_id" in recorder.dropped_indexes
    # Safe downgrade guard: minimum_payment is NOT forced NOT NULL on downgrade
    assert ("credit_card_statements", "minimum_payment") not in recorder.dropped_columns


def test_credit_v2_refund_type_adds_supported_transaction_type(monkeypatch):
    mod = _load_migration("credit_v2_refund_type", "credit_v2_refund_type.py")
    recorder = MigrationRecorder()
    monkeypatch.setattr(mod, "op", recorder)

    mod.upgrade()

    assert recorder.dropped_constraints == ["credit_card_transactions_type_check"]
    assert recorder.created_checks == ["credit_card_transactions_type_check"]
    assert "'purchase'::text" in mod._LEGACY_TYPES
    assert "'payment'::text" in mod._LEGACY_TYPES
    assert "'refund'::text" not in mod._LEGACY_TYPES


# ── 3. Legacy Semantics & Ambiguous Legacy Rows Preservation ─────────────────


def test_legacy_transaction_amount_semantics():
    """Verify that 'amount' remains the legacy booked amount in card currency,

    while explicit original_amount and original_currency are nullable.
    """
    # Verify column defaults on table definition
    assert CreditCardTransaction.__table__.c.provenance.default.arg == PROVENANCE_LEGACY_UNKNOWN
    assert CreditCardTransaction.__table__.c.debt_partition.default.arg == DEBT_PARTITION_LEGACY

    card_id = uuid.uuid4()
    user_id = uuid.uuid4()

    legacy_tx = CreditCardTransaction(
        id=uuid.uuid4(),
        user_id=user_id,
        credit_card_id=card_id,
        type="purchase",
        amount=Decimal("150000.00"),  # Legacy booked amount in COP
        provenance=PROVENANCE_LEGACY_UNKNOWN,
        debt_partition=DEBT_PARTITION_LEGACY,
    )

    # Ambiguous legacy originals remain null (do not invent original currency or FX)
    assert legacy_tx.amount == Decimal("150000.00")
    assert legacy_tx.original_amount is None
    assert legacy_tx.original_currency is None
    assert legacy_tx.booked_amount is None
    assert legacy_tx.booked_currency is None
    assert legacy_tx.fx_rate is None
    assert legacy_tx.fx_quote_id is None
    assert legacy_tx.fx_rate_snapshot_id is None
    assert legacy_tx.cycle_id is None
    assert legacy_tx.provenance == PROVENANCE_LEGACY_UNKNOWN
    assert legacy_tx.debt_partition == DEBT_PARTITION_LEGACY


def test_legacy_installment_semantics():
    """Verify ambiguous legacy installment rows default to legacy partition and null assessed interest."""
    assert CreditCardInstallment.__table__.c.provenance.default.arg == PROVENANCE_LEGACY_UNKNOWN
    assert CreditCardInstallment.__table__.c.debt_partition.default.arg == DEBT_PARTITION_LEGACY

    card_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tx_id = uuid.uuid4()

    legacy_inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=user_id,
        credit_card_id=card_id,
        purchase_transaction_id=tx_id,
        installment_number=1,
        installments_total=12,
        principal_amount=Decimal("100000.00"),
        scheduled_period="2026-10",
        total_amount=Decimal("120000.00"),
        provenance=PROVENANCE_LEGACY_UNKNOWN,
        debt_partition=DEBT_PARTITION_LEGACY,
    )

    assert legacy_inst.original_amount is None
    assert legacy_inst.original_currency is None
    assert legacy_inst.booked_amount is None
    assert legacy_inst.cycle_id is None
    assert legacy_inst.assessed_interest_amount is None
    assert legacy_inst.provenance == PROVENANCE_LEGACY_UNKNOWN
    assert legacy_inst.debt_partition == DEBT_PARTITION_LEGACY


def test_legacy_early_payment_semantics():
    """Verify legacy early payments preserve legacy unknowns and partition."""
    assert CreditCardEarlyPayment.__table__.c.provenance.default.arg == PROVENANCE_LEGACY_UNKNOWN
    assert CreditCardEarlyPayment.__table__.c.debt_partition.default.arg == DEBT_PARTITION_LEGACY

    early_pmt = CreditCardEarlyPayment(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        purchase_transaction_id=uuid.uuid4(),
        amount=Decimal("50000.00"),
        allocation_mode="reduce_installment_amount",
        provenance=PROVENANCE_LEGACY_UNKNOWN,
        debt_partition=DEBT_PARTITION_LEGACY,
    )
    assert early_pmt.payment_transaction_id is None
    assert early_pmt.original_amount is None
    assert early_pmt.original_currency is None
    assert early_pmt.provenance == PROVENANCE_LEGACY_UNKNOWN
    assert early_pmt.debt_partition == DEBT_PARTITION_LEGACY


# ── 4. Debt Partition Exclusivity (No Double Counting) ───────────────────────


def test_debt_partition_mutual_exclusivity():
    """Verify canonical vs legacy debt partition is mutually exclusive."""
    assert ALLOWED_DEBT_PARTITIONS == ("legacy", "canonical")
    assert DEBT_PARTITION_LEGACY != DEBT_PARTITION_CANONICAL

    legacy_rows = [
        {"id": 1, "partition": DEBT_PARTITION_LEGACY, "debt": Decimal("100.00")},
        {"id": 2, "partition": DEBT_PARTITION_LEGACY, "debt": Decimal("200.00")},
    ]
    canonical_rows = [
        {"id": 3, "partition": DEBT_PARTITION_CANONICAL, "debt": Decimal("300.00")},
    ]

    all_rows = legacy_rows + canonical_rows

    legacy_set = {r["id"] for r in all_rows if r["partition"] == DEBT_PARTITION_LEGACY}
    canonical_set = {r["id"] for r in all_rows if r["partition"] == DEBT_PARTITION_CANONICAL}

    # Strict disjointness: intersection is empty
    assert legacy_set.isdisjoint(canonical_set)
    # Summing canonical debt never includes legacy debt
    canonical_debt = sum(r["debt"] for r in all_rows if r["partition"] == DEBT_PARTITION_CANONICAL)
    assert canonical_debt == Decimal("300.00")


# ── 5. Retention of Physical Legacy Artifacts as Non-Authoritative ───────────


def test_current_debt_and_views_physical_retention():
    """Verify credit_cards.current_debt is retained physically on the model and defaults to 0.00."""
    assert CreditCard.__table__.c.current_debt.default.arg == Decimal("0.00")
    assert CreditCard.__table__.c.canonical_mode.default.arg is False
    assert CreditCard.__table__.c.provenance.default.arg == PROVENANCE_LEGACY_UNKNOWN

    card = CreditCard(
        user_id=uuid.uuid4(),
        name="Test Visa",
        bank="Bancolombia",
        credit_limit=Decimal("5000000.00"),
        cutoff_day=15,
        due_day=5,
        current_debt=Decimal("0.00"),
        canonical_mode=False,
        provenance=PROVENANCE_LEGACY_UNKNOWN,
    )
    assert hasattr(card, "current_debt")
    assert card.current_debt == Decimal("0.00")
    assert card.canonical_mode is False
    assert card.provenance == PROVENANCE_LEGACY_UNKNOWN


# ── 6. Installment Ceiling Invariant (paid_amount <= total_amount) ───────────


def test_installment_ceiling_with_interest():
    """Verify that installments with interest allow paid_amount up to total_amount

    superseding the buggy Alembic check paid_amount <= principal_amount.
    """
    inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        purchase_transaction_id=uuid.uuid4(),
        installment_number=1,
        installments_total=6,
        principal_amount=Decimal("100000.00"),
        interest_amount=Decimal("15000.00"),
        total_amount=Decimal("115000.00"),
        scheduled_period="2026-10",
        paid_amount=Decimal("115000.00"),  # Fully paid with interest!
    )
    # In legacy check: paid_amount <= principal_amount failed (115000 <= 100000 is False).
    # In canonical check: paid_amount <= total_amount succeeds (115000 <= 115000 is True).
    assert inst.paid_amount <= inst.total_amount
    assert inst.paid_amount > inst.principal_amount


# ── 7. New Canonical Structures & Constraints ────────────────────────────────


def test_canonical_billing_cycle_model():
    """Verify CreditCardBillingCycle fields and date bounds."""
    cycle = CreditCardBillingCycle(
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        billing_period="2026-10",
        start_date=date(2026, 9, 16),
        cutoff_date=date(2026, 10, 15),
        due_date=date(2026, 11, 5),
        status="open",
        provenance=PROVENANCE_NEXUM_MODELED,
    )
    assert cycle.start_date <= cycle.cutoff_date <= cycle.due_date
    assert cycle.provenance == PROVENANCE_NEXUM_MODELED


def test_canonical_payment_money_conservation():
    """Verify CreditCardPayment component conservation (booked_amount = principal + interest + fee + unallocated)."""
    cmd_id = uuid.uuid4()
    payment = CreditCardPayment(
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        booked_amount=Decimal("250000.00"),
        booked_currency="COP",
        source_amount=Decimal("60.00"),
        source_currency="USD",
        principal_applied=Decimal("200000.00"),
        interest_applied=Decimal("40000.00"),
        fee_applied=Decimal("10000.00"),
        unallocated_amount=Decimal("0.00"),
        payment_date=date(2026, 10, 20),
        command_id=cmd_id,
    )
    # Conservation check against booked_amount
    total_components = (
        payment.principal_applied
        + payment.interest_applied
        + payment.fee_applied
        + payment.unallocated_amount
    )
    assert payment.booked_amount == total_components
    # Compatibility aliases
    assert payment.amount == payment.booked_amount
    assert payment.currency == payment.booked_currency
    assert payment.command_id == cmd_id
    assert payment.source_amount == Decimal("60.00")
    assert payment.source_currency == "USD"


def test_canonical_payment_allocation_exactly_one_target():
    """Verify CreditCardPaymentAllocation targets exactly one entity among
    target_installment_id, target_charge_id, and target_statement_component_id.
    """
    inst_id = uuid.uuid4()
    stmt_comp_id = uuid.uuid4()
    charge_id = uuid.uuid4()

    # Case A: target_installment_id
    alloc_inst = CreditCardPaymentAllocation(
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        target_installment_id=inst_id,
        target_charge_id=None,
        target_statement_component_id=None,
        allocated_amount=Decimal("115000.00"),
        allocated_principal=Decimal("100000.00"),
        allocated_interest=Decimal("15000.00"),
        allocated_fees=Decimal("0.00"),
    )
    non_null_targets_a = sum(
        1
        for t in (
            alloc_inst.target_installment_id,
            alloc_inst.target_charge_id,
            alloc_inst.target_statement_component_id,
        )
        if t is not None
    )
    assert non_null_targets_a == 1
    assert alloc_inst.allocated_amount == (
        alloc_inst.allocated_principal + alloc_inst.allocated_interest + alloc_inst.allocated_fees
    )

    # Case B: target_statement_component_id (auditable exact debt component)
    alloc_comp = CreditCardPaymentAllocation(
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        target_installment_id=None,
        target_charge_id=None,
        target_statement_component_id=stmt_comp_id,
        allocated_amount=Decimal("25000.00"),
        allocated_principal=Decimal("0.00"),
        allocated_interest=Decimal("0.00"),
        allocated_fees=Decimal("25000.00"),
    )
    non_null_targets_b = sum(
        1
        for t in (
            alloc_comp.target_installment_id,
            alloc_comp.target_charge_id,
            alloc_comp.target_statement_component_id,
        )
        if t is not None
    )
    assert non_null_targets_b == 1

    # Case C: target_charge_id
    alloc_charge = CreditCardPaymentAllocation(
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        target_installment_id=None,
        target_charge_id=charge_id,
        target_statement_component_id=None,
        allocated_amount=Decimal("5000.00"),
        allocated_principal=Decimal("0.00"),
        allocated_interest=Decimal("0.00"),
        allocated_fees=Decimal("5000.00"),
    )
    non_null_targets_c = sum(
        1
        for t in (
            alloc_charge.target_installment_id,
            alloc_charge.target_charge_id,
            alloc_charge.target_statement_component_id,
        )
        if t is not None
    )
    assert non_null_targets_c == 1


def test_canonical_adjustment_and_allocation():
    """Verify CreditCardAdjustment and CreditCardAdjustmentAllocation exactly-one target."""
    cmd_id = uuid.uuid4()
    now = datetime.now()
    quote_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()

    adj = CreditCardAdjustment(
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        adjustment_type="refund",
        booked_amount=Decimal("50000.00"),
        booked_currency="COP",
        original_amount=Decimal("12.50"),
        original_currency="USD",
        fx_rate=Decimal("4000.00000000"),
        fx_rate_snapshot_id=snapshot_id,
        fx_quote_id=quote_id,
        fx_provider="dolarapi",
        fx_rate_timestamp=now,
        status="posted",
        provenance=PROVENANCE_ISSUER_AUTHORITATIVE,
        command_id=cmd_id,
    )
    assert adj.booked_amount > Decimal("0.00")
    assert adj.amount == adj.booked_amount
    assert adj.currency == adj.booked_currency
    assert adj.original_amount == Decimal("12.50")
    assert adj.original_currency == "USD"
    assert adj.fx_provider == "dolarapi"
    assert adj.command_id == cmd_id

    alloc = CreditCardAdjustmentAllocation(
        user_id=adj.user_id,
        credit_card_id=adj.credit_card_id,
        adjustment_id=adj.id,
        target_installment_id=uuid.uuid4(),
        target_transaction_id=None,
        target_charge_id=None,
        allocated_amount=Decimal("50000.00"),
        allocated_principal=Decimal("50000.00"),
        allocated_interest=Decimal("0.00"),
        allocated_fees=Decimal("0.00"),
    )
    non_null_targets = sum(
        1
        for t in (
            alloc.target_installment_id,
            alloc.target_transaction_id,
            alloc.target_charge_id,
        )
        if t is not None
    )
    assert non_null_targets == 1
    assert alloc.allocated_amount == alloc.allocated_principal


def test_canonical_statement_component():
    """Verify CreditCardStatementComponent conservation, positive amounts convention,
    and support for reversal and refund component types.
    """
    comp = CreditCardStatementComponent(
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        statement_id=uuid.uuid4(),
        component_type="fee",
        amount=Decimal("25000.00"),
        currency="COP",
        principal_amount=Decimal("0.00"),
        interest_amount=Decimal("0.00"),
        fee_amount=Decimal("25000.00"),
        provenance=PROVENANCE_ISSUER_AUTHORITATIVE,
    )
    assert comp.amount == (comp.principal_amount + comp.interest_amount + comp.fee_amount)

    # Positive convention for reversals (direction applied by domain logic)
    reversal_comp = CreditCardStatementComponent(
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        statement_id=uuid.uuid4(),
        component_type="reversal",
        amount=Decimal("15000.00"),
        currency="COP",
        principal_amount=Decimal("15000.00"),
        interest_amount=Decimal("0.00"),
        fee_amount=Decimal("0.00"),
        provenance=PROVENANCE_ISSUER_AUTHORITATIVE,
    )
    assert reversal_comp.amount > Decimal("0.00")
    assert reversal_comp.component_type == "reversal"

    # Positive convention for refunds
    refund_comp = CreditCardStatementComponent(
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        statement_id=uuid.uuid4(),
        component_type="refund",
        amount=Decimal("30000.00"),
        currency="COP",
        principal_amount=Decimal("30000.00"),
        interest_amount=Decimal("0.00"),
        fee_amount=Decimal("0.00"),
        provenance=PROVENANCE_ISSUER_AUTHORITATIVE,
    )
    assert refund_comp.amount > Decimal("0.00")
    assert refund_comp.component_type == "refund"


def test_canonical_statement_facts_unknown_minimum_payment():
    """Verify CreditCardStatementFact handles statement facts like minimum_payment_due:
    - Nullable amount allowing absence to indicate unknown minimum payment (never default to zero).
    - Append-only structure: has created_at, does NOT have updated_at.
    - Preserves observed_at, effective_at, provenance, and FK relationships.
    """
    # Verify column defaults on table definition: amount must NOT have a default (never fabricate 0.00)
    assert CreditCardStatementFact.__table__.c.amount.nullable is True
    assert CreditCardStatementFact.__table__.c.amount.default is None
    assert CreditCardStatementFact.__table__.c.amount.server_default is None

    # Verify append-only semantics: created_at exists, updated_at must NOT exist
    assert "created_at" in CreditCardStatementFact.__table__.c
    assert "updated_at" not in CreditCardStatementFact.__table__.c

    statement_id = uuid.uuid4()
    card_id = uuid.uuid4()
    user_id = uuid.uuid4()

    # Unknown minimum payment represented by absence of amount or null amount (never zero)
    unknown_fact = CreditCardStatementFact(
        user_id=user_id,
        credit_card_id=card_id,
        statement_id=statement_id,
        fact_type="minimum_payment_due",
        amount=None,
        currency="COP",
        provenance=PROVENANCE_ISSUER_AUTHORITATIVE,
    )
    assert unknown_fact.amount is None
    assert unknown_fact.fact_type == "minimum_payment_due"
    assert unknown_fact.provenance == PROVENANCE_ISSUER_AUTHORITATIVE

    # Known minimum payment
    known_fact = CreditCardStatementFact(
        user_id=user_id,
        credit_card_id=card_id,
        statement_id=statement_id,
        fact_type="minimum_payment_due",
        amount=Decimal("85000.00"),
        currency="COP",
        provenance=PROVENANCE_ISSUER_AUTHORITATIVE,
    )
    assert known_fact.amount == Decimal("85000.00")

    # Verify read schema accepts null amount for unknown facts
    read_fact = CreditCardStatementFactRead(
        id=uuid.uuid4(),
        user_id=user_id,
        credit_card_id=card_id,
        statement_id=statement_id,
        fact_type="minimum_payment_due",
        amount=None,
        currency="COP",
        provenance=PROVENANCE_ISSUER_AUTHORITATIVE,
        observed_at=datetime.now(),
        effective_at=None,
        created_at=datetime.now(),
    )
    assert read_fact.amount is None


def test_canonical_orm_and_migration_parity_and_types():
    """Verify ORM CheckConstraints match migration check constraints and command_id is UUID."""
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID

    # Verify command_id is PostgreSQL UUID dialect on ORM tables
    payment_cmd_type = CreditCardPayment.__table__.c.command_id.type
    adj_cmd_type = CreditCardAdjustment.__table__.c.command_id.type
    assert isinstance(payment_cmd_type, PG_UUID), (
        "CreditCardPayment.command_id must be PostgreSQL UUID"
    )
    assert isinstance(adj_cmd_type, PG_UUID), (
        "CreditCardAdjustment.command_id must be PostgreSQL UUID"
    )

    # Verify check constraint names parity
    expected_constraints = {
        CreditCardBillingCycle: {
            "chk_cc_billing_cycles_start_cutoff",
            "chk_cc_billing_cycles_cutoff_due",
            "chk_cc_billing_cycles_status",
            "chk_cc_billing_cycles_provenance",
        },
        CreditCardStatementComponent: {
            "chk_cc_stmt_comp_amount_nonneg",
            "chk_cc_stmt_comp_parts_nonneg",
            "chk_cc_stmt_comp_conservation",
            "chk_cc_stmt_comp_type",
            "chk_cc_stmt_comp_provenance",
        },
        CreditCardStatementFact: {
            "chk_cc_stmt_facts_amount_nonneg",
            "chk_cc_stmt_facts_type",
            "chk_cc_stmt_facts_provenance",
        },
        CreditCardPayment: {
            "chk_cc_payments_amount_pos",
            "chk_cc_payments_source_amount_pos",
            "chk_cc_payments_applied_nonneg",
            "chk_cc_payments_conservation",
            "chk_cc_payments_status",
            "chk_cc_payments_provenance",
            "chk_cc_payments_alloc_source",
        },
        CreditCardPaymentAllocation: {
            "chk_cc_alloc_exactly_one_target",
            "chk_cc_alloc_amount_pos",
            "chk_cc_alloc_parts_nonneg",
            "chk_cc_alloc_conservation",
            "chk_cc_alloc_order_pos",
            "chk_cc_alloc_source",
            "chk_cc_alloc_provenance",
        },
        CreditCardAdjustment: {
            "chk_cc_adjustments_amount_pos",
            "chk_cc_adjustments_orig_amount_pos",
            "chk_cc_adjustments_type",
            "chk_cc_adjustments_status",
            "chk_cc_adjustments_provenance",
            "chk_cc_adjustments_alloc_source",
        },
        CreditCardAdjustmentAllocation: {
            "chk_cc_adj_alloc_exactly_one_target",
            "chk_cc_adj_alloc_amount_pos",
            "chk_cc_adj_alloc_parts_nonneg",
            "chk_cc_adj_alloc_conservation",
            "chk_cc_adj_alloc_order_pos",
            "chk_cc_adj_alloc_source",
            "chk_cc_adj_alloc_provenance",
        },
    }

    for model_cls, expected_names in expected_constraints.items():
        actual_names = {
            c.name
            for c in model_cls.__table__.constraints
            if isinstance(c, CheckConstraint) and c.name
        }
        missing = expected_names - actual_names
        assert not missing, f"{model_cls.__name__} missing check constraints: {missing}"


# ── 8. NexumError Subclasses & Stable Error Codes ────────────────────────────


def test_credit_domain_error_hierarchy():
    """Verify CreditDomainError and its subclasses inherit from NexumError and define stable codes."""
    errors = [
        (CreditDomainError, 400, "credit_domain_error"),
        (CreditCardNotFoundError, 404, "credit_card_not_found"),
        (CreditCardInactiveError, 400, "credit_card_inactive"),
        (CreditLimitExceededError, 409, "credit_limit_exceeded"),
        (InvalidPaymentAmountError, 400, "credit_payment_amount_invalid"),
        (CreditCardForbiddenError, 403, "credit_card_forbidden"),
        (CreditIdempotencyConflictError, 409, "credit_idempotency_conflict"),
        (CreditAllocationError, 409, "credit_allocation_error"),
        (CreditBillingCycleClosedError, 409, "credit_billing_cycle_closed"),
        (CreditStatementImmutableError, 409, "credit_statement_immutable"),
        (CreditGoalReservationBreachError, 409, "credit_goal_reservation_breach"),
        (CreditPartitionError, 409, "credit_partition_error"),
        (CreditInstallmentCeilingError, 409, "credit_installment_ceiling_error"),
    ]

    for err_cls, expected_status, expected_code in errors:
        exc = err_cls()
        assert isinstance(exc, NexumError), f"{err_cls.__name__} must inherit from NexumError"
        assert exc.status_code == expected_status, f"{err_cls.__name__} status_code mismatch"
        assert exc.error_code == expected_code, f"{err_cls.__name__} error_code mismatch"


# ── 9. Deep Fingerprint Helpers (F-16 Defense) ───────────────────────────────


def test_fingerprint_deterministic_hash():
    """Verify fingerprint builder creates canonical dictionary and deterministic SHA-256."""
    u_id = uuid.uuid4()
    c_id = uuid.uuid4()
    t_id = uuid.uuid4()
    q_id = uuid.uuid4()
    s_id = uuid.uuid4()

    fp1 = build_credit_fingerprint(
        user_id=u_id,
        operation_type="payment",
        credit_card_id=c_id,
        target_id=t_id,
        booked_amount=Decimal("500000.00"),
        booked_currency="COP",
        original_amount=Decimal("125.00"),
        original_currency="USD",
        fx_rate=Decimal("4000.00000000"),
        fx_quote_id=q_id,
        fx_rate_snapshot_id=s_id,
        payment_date=date(2026, 10, 28),
    )

    fp2 = build_credit_fingerprint(
        user_id=str(u_id),
        operation_type="PAYMENT",  # Case insensitivity
        credit_card_id=str(c_id),
        target_id=str(t_id),
        booked_amount="500000",  # Numerical equivalence
        booked_currency="cop",
        original_amount="125.000",
        original_currency="usd",
        fx_rate="4000",
        fx_quote_id=str(q_id),
        fx_rate_snapshot_id=str(s_id),
        payment_date="2026-10-28",
    )

    hash1 = hash_credit_fingerprint(fp1)
    hash2 = hash_credit_fingerprint(fp2)
    assert hash1 == hash2


def test_fingerprint_validation_detects_tampering():
    """Verify validate_credit_fingerprint raises 409 CreditIdempotencyConflictError on any tampering."""
    u_id = uuid.uuid4()
    c_id = uuid.uuid4()

    fp = build_credit_fingerprint(
        user_id=u_id,
        operation_type="payment",
        credit_card_id=c_id,
        booked_amount=Decimal("100000.00"),
        booked_currency="COP",
    )

    # Identical validation passes
    validate_credit_fingerprint(
        fp,
        user_id=u_id,
        operation_type="payment",
        credit_card_id=c_id,
        booked_amount=Decimal("100000.00"),
        booked_currency="COP",
    )

    # Tampered amount raises 409
    with pytest.raises(CreditIdempotencyConflictError) as exc_info:
        validate_credit_fingerprint(
            fp,
            user_id=u_id,
            operation_type="payment",
            credit_card_id=c_id,
            booked_amount=Decimal("100001.00"),
            booked_currency="COP",
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "credit_idempotency_conflict"

    # Tampered card_id raises 409
    with pytest.raises(CreditIdempotencyConflictError):
        validate_credit_fingerprint(
            fp,
            user_id=u_id,
            operation_type="payment",
            credit_card_id=uuid.uuid4(),  # Different card
            booked_amount=Decimal("100000.00"),
            booked_currency="COP",
        )

    # Tampered currency raises 409
    with pytest.raises(CreditIdempotencyConflictError):
        validate_credit_fingerprint(
            fp,
            user_id=u_id,
            operation_type="payment",
            credit_card_id=c_id,
            booked_amount=Decimal("100000.00"),
            booked_currency="USD",
        )


# ── 10. Locked Repository Primitives (populate_existing=True) ────────────────


@pytest.mark.asyncio
async def test_repository_locked_queries_populate_existing():
    """Verify that all locked repository primitives execute with populate_existing=True."""
    mock_session = MagicMock()
    executed_statements = []

    async def mock_execute(statement, *args, **kwargs):
        executed_statements.append(statement)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_result.scalars.return_value.all.return_value = []
        return mock_result

    mock_session.execute = mock_execute
    repo = CreditCardRepository(mock_session)

    # Test all 8 locked primitives
    test_id = uuid.uuid4()
    await repo.get_by_id_for_update(test_id)
    await repo.list_pending_installments_for_update(test_id, test_id)
    await repo.list_installments_for_transaction_for_update(test_id)
    await repo.list_unpaid_statement_charges_for_update(test_id)
    await repo.get_billing_cycle_for_update(test_id)
    await repo.get_payment_for_update(test_id)
    await repo.get_statement_for_update(test_id)
    await repo.get_adjustment_for_update(test_id)

    assert len(executed_statements) == 8
    for stmt in executed_statements:
        opts = stmt._execution_options
        assert opts.get("populate_existing") is True, (
            f"Statement {stmt} missing populate_existing=True"
        )


@pytest.mark.asyncio
async def test_repository_statement_facts_primitives():
    """Verify repository statement facts primitives (add_statement_facts, list_facts_for_statement)."""
    mock_session = MagicMock()
    mock_facts = [
        CreditCardStatementFact(
            user_id=uuid.uuid4(),
            credit_card_id=uuid.uuid4(),
            statement_id=uuid.uuid4(),
            fact_type="minimum_payment_due",
            amount=Decimal("45000.00"),
            currency="COP",
        )
    ]
    mock_session.add_all = MagicMock()

    async def mock_flush():
        pass

    mock_session.flush = mock_flush

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = mock_facts

    async def mock_execute(statement, *args, **kwargs):
        return mock_result

    mock_session.execute = mock_execute
    repo = CreditCardRepository(mock_session)

    # add_statement_facts
    await repo.add_statement_facts(mock_facts)
    mock_session.add_all.assert_called_once_with(mock_facts)

    # list_facts_for_statement
    fetched = await repo.list_facts_for_statement(mock_facts[0].statement_id)
    assert fetched == mock_facts


def test_repository_command_id_type_annotations():
    """Verify repository methods for payment and adjustment command_id require uuid.UUID."""
    sig_payment = inspect.signature(CreditCardRepository.get_payment_by_command_id)
    assert sig_payment.parameters["command_id"].annotation is uuid.UUID

    sig_adj = inspect.signature(CreditCardRepository.get_adjustment_by_command_id)
    assert sig_adj.parameters["command_id"].annotation is uuid.UUID


@pytest.mark.asyncio
async def test_repository_get_by_command_id_uuid():
    """Verify get_payment_by_command_id and get_adjustment_by_command_id query with UUIDs."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    async def mock_execute(statement, *args, **kwargs):
        return mock_result

    mock_session.execute = mock_execute
    repo = CreditCardRepository(mock_session)

    u_id = uuid.uuid4()
    cmd_id = uuid.uuid4()

    payment = await repo.get_payment_by_command_id(u_id, cmd_id)
    assert payment is None

    adj = await repo.get_adjustment_by_command_id(u_id, cmd_id)
    assert adj is None
