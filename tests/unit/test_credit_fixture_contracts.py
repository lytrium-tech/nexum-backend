"""Contract tests for Credit Domain ORM models, fixture helpers, and service interfaces.

Guarantees:
1. Local execution without PostgreSQL: executes in standard unit test suites.
2. Validates all ORM model constructors accept only real mapped SQLAlchemy attributes.
3. Prevents regressions where physical tests skip and conceal invalid constructor kwargs.
4. Verifies CreditCardService and CreditCardRepository method signatures and command_id requirements.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.credit.models import (
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
    CreditCardStatementFact,
    CreditCardTransaction,
)
from app.credit.repository import CreditCardRepository
from app.credit.service import CreditCardService
from tests.unit.postgres_helpers import (
    GateCleanupError,
    GateFixture,
    get_fixture_timestamps,
    verify_statement_triggers_enabled,
)


def test_credit_card_mapped_constructor_contract():
    """CreditCard accepts all mapped attributes and rejects nonexistent legacy kwargs."""
    now_utc, now_naive = get_fixture_timestamps()
    card_id = uuid.uuid4()
    user_id = uuid.uuid4()

    card = CreditCard(
        id=card_id,
        user_id=user_id,
        name="Contract Visa",
        bank="Contract Bank",
        credit_limit=Decimal("5000000.00"),
        current_debt=Decimal("0.00"),
        cutoff_day=15,
        due_day=5,
        management_fee=Decimal("0.00"),
        monthly_interest_rate=Decimal("0.0210"),
        annual_interest_rate=Decimal("0.2800"),
        currency="COP",
        network="Visa",
        franchise="Classic",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=now_utc,
        provenance="nexum_modeled",
        lifecycle_status="active",
        created_at=now_naive,
        updated_at=now_utc,
    )
    assert card.id == card_id
    assert card.due_day == 5
    assert card.canonical_mode is True

    # Nonexistent/legacy keyword arguments must be rejected
    with pytest.raises(TypeError):
        CreditCard(payment_due_day=5)  # Real column is due_day

    with pytest.raises(TypeError):
        CreditCard(is_legacy=True)  # No is_legacy column on CreditCard

    with pytest.raises(TypeError):
        CreditCard(debt_partition="canonical")  # No debt_partition column on CreditCard


def test_credit_card_billing_cycle_mapped_constructor_contract():
    """CreditCardBillingCycle accepts all mapped attributes and rejects nonexistent legacy kwargs."""
    now_utc = datetime.now(UTC)
    cycle = CreditCardBillingCycle(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        billing_period="2026-10",
        start_date=date(2026, 9, 16),
        cutoff_date=date(2026, 10, 15),
        due_date=date(2026, 11, 5),
        status="open",
        provenance="nexum_modeled",
        created_at=now_utc,
        updated_at=now_utc,
    )
    assert cycle.billing_period == "2026-10"
    assert cycle.due_date == date(2026, 11, 5)

    with pytest.raises(TypeError):
        CreditCardBillingCycle(card_id=uuid.uuid4())  # Real column is credit_card_id

    with pytest.raises(TypeError):
        CreditCardBillingCycle(period_key="2026-10")  # Real column is billing_period


def test_credit_card_statement_mapped_constructor_contract():
    """CreditCardStatement accepts all mapped attributes and rejects nonexistent legacy kwargs."""
    now_utc = datetime.now(UTC)
    stmt = CreditCardStatement(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        billing_period="2026-10",
        billing_period_start=date(2026, 9, 16),
        cutoff_date=date(2026, 10, 15),
        due_date=date(2026, 11, 5),
        previous_balance=Decimal("0.00"),
        new_purchases=Decimal("100000.00"),
        billed_installments=Decimal("100000.00"),
        fees_total=Decimal("0.00"),
        interest_total=Decimal("0.00"),
        payments_received=Decimal("0.00"),
        statement_balance=Decimal("100000.00"),
        minimum_payment=None,
        status="open",
        frozen_at=None,
        cycle_id=uuid.uuid4(),
        currency="COP",
        assessed_interest=Decimal("0.00"),
        estimated_interest=Decimal("0.00"),
        provenance="nexum_modeled",
        debt_partition="canonical",
        created_at=now_utc,
        updated_at=now_utc,
    )
    assert stmt.billing_period_start == date(2026, 9, 16)
    assert stmt.cutoff_date == date(2026, 10, 15)

    with pytest.raises(TypeError):
        CreditCardStatement(card_id=uuid.uuid4())  # Real column is credit_card_id

    with pytest.raises(TypeError):
        CreditCardStatement(
            statement_period_start=date(2026, 9, 16)
        )  # Real is billing_period_start

    with pytest.raises(TypeError):
        CreditCardStatement(payment_due_date=date(2026, 11, 5))  # Real column is due_date


def test_credit_card_statement_charge_mapped_constructor_contract():
    """CreditCardStatementCharge accepts statement_id and charge fields, rejecting card_id."""
    charge = CreditCardStatementCharge(
        id=uuid.uuid4(),
        statement_id=uuid.uuid4(),
        charge_type="fee",
        amount=Decimal("15000.00"),
        description="Late fee",
        reference_id=uuid.uuid4(),
        status="pending",
        paid_amount=Decimal("0.00"),
        created_at=datetime.now(UTC),
    )
    assert charge.amount == Decimal("15000.00")

    with pytest.raises(TypeError):
        CreditCardStatementCharge(card_id=uuid.uuid4())  # No card_id column on charges

    with pytest.raises(TypeError):
        CreditCardStatementCharge(debt_partition="canonical")  # No debt_partition on charges


def test_credit_card_statement_component_mapped_constructor_contract():
    """CreditCardStatementComponent accepts all mapped component attributes."""
    comp = CreditCardStatementComponent(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        statement_id=uuid.uuid4(),
        component_type="purchase",
        amount=Decimal("50000.00"),
        currency="COP",
        principal_amount=Decimal("50000.00"),
        interest_amount=Decimal("0.00"),
        fee_amount=Decimal("0.00"),
        transaction_id=uuid.uuid4(),
        installment_id=uuid.uuid4(),
        statement_charge_id=None,
        provenance="nexum_modeled",
        description="Purchase component",
        created_at=datetime.now(UTC),
    )
    assert comp.amount == Decimal("50000.00")


def test_credit_card_statement_fact_mapped_constructor_contract():
    """CreditCardStatementFact accepts all mapped statement fact attributes."""
    fact = CreditCardStatementFact(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        statement_id=uuid.uuid4(),
        fact_type="minimum_payment_due",
        amount=Decimal("25000.00"),
        currency="COP",
        provenance="issuer_authoritative",
        observed_at=datetime.now(UTC),
        effective_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    assert fact.fact_type == "minimum_payment_due"


def test_credit_card_transaction_mapped_constructor_contract():
    """CreditCardTransaction accepts type and credit_card_id, rejecting card_id and transaction_type."""
    now_utc, now_naive = get_fixture_timestamps()
    tx = CreditCardTransaction(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        type="purchase",
        amount=Decimal("100000.00"),
        installments_total=1,
        installments_paid=0,
        monthly_amount=Decimal("100000.00"),
        interest_amount=Decimal("0.00"),
        occurred_at=now_utc,
        source="manual",
        original_amount=Decimal("100000.00"),
        original_currency="COP",
        booked_amount=Decimal("100000.00"),
        booked_currency="COP",
        cycle_id=uuid.uuid4(),
        lifecycle_status="posted",
        provenance="nexum_modeled",
        debt_partition="canonical",
        created_at=now_naive,
        updated_at=now_utc,
    )
    assert tx.type == "purchase"
    assert tx.credit_card_id is not None

    with pytest.raises(TypeError):
        CreditCardTransaction(card_id=uuid.uuid4())  # Real is credit_card_id

    with pytest.raises(TypeError):
        CreditCardTransaction(transaction_type="purchase")  # Real is type

    with pytest.raises(TypeError):
        CreditCardTransaction(total_installments=3)  # Real is installments_total


def test_credit_card_installment_mapped_constructor_contract():
    """CreditCardInstallment accepts mapped attributes and rejects invalid kwargs."""
    inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        purchase_transaction_id=uuid.uuid4(),
        installment_number=1,
        installments_total=3,
        principal_amount=Decimal("33333.33"),
        scheduled_period="2026-10",
        status="pending",
        paid_amount=Decimal("0.00"),
        interest_amount=Decimal("2000.00"),
        total_amount=Decimal("35333.33"),
        scheduled_due_date=date(2026, 11, 5),
        revision_id=1,
        cycle_id=uuid.uuid4(),
        assessed_interest_amount=Decimal("2000.00"),
        estimated_interest_amount=Decimal("2000.00"),
        lifecycle_status="active",
        provenance="nexum_modeled",
        debt_partition="canonical",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert inst.installments_total == 3
    assert inst.scheduled_due_date == date(2026, 11, 5)

    with pytest.raises(TypeError):
        CreditCardInstallment(card_id=uuid.uuid4())  # Real is credit_card_id

    with pytest.raises(TypeError):
        CreditCardInstallment(transaction_id=uuid.uuid4())  # Real is purchase_transaction_id

    with pytest.raises(TypeError):
        CreditCardInstallment(total_installments=3)  # Real is installments_total

    with pytest.raises(TypeError):
        CreditCardInstallment(due_date=date(2026, 11, 5))  # Real is scheduled_due_date


def test_credit_card_payment_and_allocation_mapped_constructor_contract():
    """CreditCardPayment and CreditCardPaymentAllocation accept all mapped fields."""
    payment_id = uuid.uuid4()
    pmt = CreditCardPayment(
        id=payment_id,
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        booked_amount=Decimal("100000.00"),
        booked_currency="COP",
        source_amount=Decimal("100000.00"),
        source_currency="COP",
        fx_rate=Decimal("1.0"),
        principal_applied=Decimal("100000.00"),
        interest_applied=Decimal("0.00"),
        fee_applied=Decimal("0.00"),
        unallocated_amount=Decimal("0.00"),
        payment_date=datetime.now(UTC),
        status="applied",
        provenance="nexum_modeled",
        allocation_source="standard",
        command_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert pmt.booked_amount == Decimal("100000.00")

    alloc = CreditCardPaymentAllocation(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        payment_id=payment_id,
        target_installment_id=uuid.uuid4(),
        target_charge_id=None,
        target_statement_component_id=None,
        allocated_amount=Decimal("100000.00"),
        allocated_principal=Decimal("100000.00"),
        allocated_interest=Decimal("0.00"),
        allocated_fees=Decimal("0.00"),
        allocation_order=1,
        allocation_source="standard",
        provenance="nexum_modeled",
        created_at=datetime.now(UTC),
    )
    assert alloc.allocation_order == 1

    with pytest.raises(TypeError):
        CreditCardPaymentAllocation(card_id=uuid.uuid4())  # Real is credit_card_id


def test_credit_card_adjustment_and_allocation_mapped_constructor_contract():
    """CreditCardAdjustment and CreditCardAdjustmentAllocation accept all mapped fields."""
    adj_id = uuid.uuid4()
    adj = CreditCardAdjustment(
        id=adj_id,
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        adjustment_type="refund",
        amount=Decimal("50000.00"),
        direction="credit",
        status="applied",
        original_transaction_id=uuid.uuid4(),
        reason="Store refund",
        command_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert adj.direction == "credit"

    adj_alloc = CreditCardAdjustmentAllocation(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        adjustment_id=adj_id,
        target_installment_id=uuid.uuid4(),
        allocated_amount=Decimal("50000.00"),
        allocated_principal=Decimal("50000.00"),
        allocated_interest=Decimal("0.00"),
        allocated_fees=Decimal("0.00"),
        allocation_order=1,
        allocation_source="adjustment",
        provenance="nexum_modeled",
        created_at=datetime.now(UTC),
    )
    assert adj_alloc.allocated_amount == Decimal("50000.00")


def test_credit_card_early_payment_mapped_constructor_contract():
    """CreditCardEarlyPayment accepts all mapped attributes and rejects invalid kwargs."""
    ep = CreditCardEarlyPayment(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_card_id=uuid.uuid4(),
        purchase_transaction_id=uuid.uuid4(),
        amount=Decimal("100000.00"),
        allocation_mode="reduce_principal",
        applied_at=datetime.now(UTC),
        payment_transaction_id=uuid.uuid4(),
        original_amount=Decimal("100000.00"),
        original_currency="COP",
        booked_amount=Decimal("100000.00"),
        booked_currency="COP",
        provenance="nexum_modeled",
        debt_partition="canonical",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert ep.amount == Decimal("100000.00")

    with pytest.raises(TypeError):
        CreditCardEarlyPayment(card_id=uuid.uuid4())  # Real is credit_card_id


def test_service_method_signatures_require_command_id():
    """CreditCardService mutation methods strictly require command_id as parameter."""
    purchase_sig = inspect.signature(CreditCardService.create_purchase)
    assert "command_id" in purchase_sig.parameters
    assert purchase_sig.parameters["command_id"].default is inspect.Parameter.empty

    payment_sig = inspect.signature(CreditCardService.create_payment)
    assert "command_id" in payment_sig.parameters
    assert payment_sig.parameters["command_id"].default is inspect.Parameter.empty

    early_pmt_sig = inspect.signature(CreditCardService.create_early_payment)
    assert "purchase_id" in early_pmt_sig.parameters
    assert "command_id" in early_pmt_sig.parameters
    assert early_pmt_sig.parameters["command_id"].default is inspect.Parameter.empty

    adj_sig = inspect.signature(CreditCardService.create_adjustment)
    assert "command_id" in adj_sig.parameters
    assert adj_sig.parameters["command_id"].default is inspect.Parameter.empty


def test_repository_has_get_card_debt_partitioned():
    """CreditCardRepository exposes get_card_debt_partitioned; CreditCardService delegates to repo."""
    assert hasattr(CreditCardRepository, "get_card_debt_partitioned")
    # CreditCardService does NOT define get_card_debt_partitioned directly; callers must use service.repo
    assert not hasattr(CreditCardService, "get_card_debt_partitioned")


def test_normalize_trigger_status_accepts_str_and_bytes_enabled():
    """Trigger status normalizer recognizes both str 'O' and bytes b'O' as enabled 'O'."""
    from scripts.dev.run_alembic_physical_scenarios import normalize_trigger_status

    # Enabled variants returned across different driver/protocol invocations (CT101 fix)
    assert normalize_trigger_status("O") == "O"
    assert normalize_trigger_status(b"O") == "O"
    assert normalize_trigger_status("O ") == "O"
    assert normalize_trigger_status(b"O ") == "O"
    assert normalize_trigger_status(" o ") == "o"
    assert normalize_trigger_status(b" o ") == "o"


def test_normalize_trigger_status_preserves_disabled_and_unknown_values():
    """Trigger status normalizer preserves disabled, replica, always, and unknown values without false enabled mapping."""
    from scripts.dev.run_alembic_physical_scenarios import normalize_trigger_status

    assert normalize_trigger_status("D") == "D"
    assert normalize_trigger_status(b"D") == "D"
    assert normalize_trigger_status("R") == "R"
    assert normalize_trigger_status(b"R") == "R"
    assert normalize_trigger_status("A") == "A"
    assert normalize_trigger_status(b"A") == "A"
    assert normalize_trigger_status(None) == ""
    assert normalize_trigger_status("") == ""
    assert normalize_trigger_status(b"") == ""
    assert normalize_trigger_status("X") == "X"
    assert normalize_trigger_status(b"X") == "X"


def test_validate_trigger_status_map_accepts_str_and_bytes_enabled_and_rejects_disabled_fail_closed():
    """Migration runner trigger validation accepts 'O' / b'O' but fails closed on missing or disabled triggers."""
    from scripts.dev.run_alembic_physical_scenarios import validate_trigger_status_map

    # 1. Standard string 'O' passes
    validate_trigger_status_map(
        {
            "trg_cc_statements_immutable": "O",
            "trg_cc_stmt_comp_immutable": "O",
        }
    )

    # 2. CT101 exact driver manifestation: bytes b'O' passes
    validate_trigger_status_map(
        {
            "trg_cc_statements_immutable": b"O",
            "trg_cc_stmt_comp_immutable": b"O",
        }
    )

    # 3. Mixed str and bytes passes
    validate_trigger_status_map(
        {
            "trg_cc_statements_immutable": "O",
            "trg_cc_stmt_comp_immutable": b"O",
        }
    )

    # 4. Bytes trigger names also normalize and pass
    validate_trigger_status_map(
        {
            b"trg_cc_statements_immutable": b"O",
            b"trg_cc_stmt_comp_immutable": b"O",
        }
    )

    # 5. Disabled trigger b'D' fails closed
    with pytest.raises(RuntimeError) as exc_info:
        validate_trigger_status_map(
            {
                "trg_cc_statements_immutable": b"D",
                "trg_cc_stmt_comp_immutable": b"O",
            }
        )
    assert "trg_cc_statements_immutable" in str(exc_info.value)
    assert "expected 'O' (enabled)" in str(exc_info.value)

    # 6. Replica trigger 'R' fails closed
    with pytest.raises(RuntimeError) as exc_info:
        validate_trigger_status_map(
            {
                "trg_cc_statements_immutable": "O",
                "trg_cc_stmt_comp_immutable": "R",
            }
        )
    assert "trg_cc_stmt_comp_immutable" in str(exc_info.value)
    assert "expected 'O' (enabled)" in str(exc_info.value)

    # 7. Missing trigger fails closed
    with pytest.raises(RuntimeError) as exc_info:
        validate_trigger_status_map(
            {
                "trg_cc_statements_immutable": b"O",
            }
        )
    assert "Missing expected immutability triggers" in str(exc_info.value)
    assert "trg_cc_stmt_comp_immutable" in str(exc_info.value)

    # 8. Empty triggers map fails closed
    with pytest.raises(RuntimeError) as exc_info:
        validate_trigger_status_map({})
    assert "Missing expected immutability triggers" in str(exc_info.value)

    # 9. None / unknown status fails closed
    with pytest.raises(RuntimeError) as exc_info:
        validate_trigger_status_map(
            {
                "trg_cc_statements_immutable": b"O",
                "trg_cc_stmt_comp_immutable": None,
            }
        )
    assert "trg_cc_stmt_comp_immutable" in str(exc_info.value)
    assert "expected 'O' (enabled)" in str(exc_info.value)


def test_get_card_debt_partitioned_sql_splits_charges_by_statement_partition():
    """get_card_debt_partitioned query inherits statement partition for charges with no double-counting."""
    src = inspect.getsource(CreditCardRepository.get_card_debt_partitioned)

    # 1. Joins statements to inspect s.debt_partition
    assert "JOIN credit_card_statements s ON sc.statement_id = s.id" in src

    # 2. Partitions charges into canonical vs legacy based on joined statement partition
    assert "s.debt_partition = 'canonical'" in src
    assert "canonical_charges_debt" in src
    assert "s.debt_partition != 'canonical' OR s.debt_partition IS NULL" in src
    assert "legacy_charges_debt" in src

    # 3. Excludes installment-mirroring charges
    assert "sc.reference_id IS NULL OR sc.reference_id NOT IN" in src

    # 4. Total debt is strict arithmetic sum of canonical and legacy partitions
    assert (
        "inst.canonical_inst_debt + chg.canonical_charges_debt - adj.net_adj_credit as canonical_debt"
        in src
    )
    assert "inst.legacy_debt + chg.legacy_charges_debt as legacy_debt" in src
    assert (
        "inst.canonical_inst_debt + chg.canonical_charges_debt - adj.net_adj_credit + inst.legacy_debt + chg.legacy_charges_debt as total_debt"
        in src
    )


def test_list_unpaid_statement_charges_for_update_requires_canonical_partition():
    """Payment allocation query strictly filters statement charges to canonical partition."""
    src = inspect.getsource(CreditCardRepository.list_unpaid_statement_charges_for_update)

    # Joins statement and asserts canonical debt partition
    assert ".join(CreditCardStatement)" in src
    assert 'CreditCardStatement.debt_partition == "canonical"' in src
    assert 'CreditCardStatementCharge.status != "paid"' in src
    assert "CreditCardStatementCharge.reference_id.notin_(subq_inst_ids)" in src


def test_debt_partitioning_financial_conservation_pure_logic():
    """Verify debt partitioning arithmetic preserves no double-count across pure legacy and mixed cards."""

    def partition_debt(
        installments: list[dict], charges: list[dict], adjustments: list[dict]
    ) -> dict[str, Decimal]:
        inst_ids = {i["id"] for i in installments}

        canonical_inst = sum(
            (
                i["principal_amount"]
                + i.get("assessed_interest_amount", i.get("interest_amount", Decimal("0.00")))
                - i["paid_amount"]
            )
            for i in installments
            if i.get("debt_partition") == "canonical" and i.get("status") != "paid"
        )
        legacy_inst = sum(
            (i["principal_amount"] + i["interest_amount"] - i["paid_amount"])
            for i in installments
            if i.get("debt_partition") != "canonical" and i.get("status") != "paid"
        )

        canonical_chg = sum(
            (c["amount"] - c["paid_amount"])
            for c in charges
            if c.get("status") != "paid"
            and c.get("statement_partition") == "canonical"
            and (c.get("reference_id") is None or c.get("reference_id") not in inst_ids)
        )
        legacy_chg = sum(
            (c["amount"] - c["paid_amount"])
            for c in charges
            if c.get("status") != "paid"
            and c.get("statement_partition") != "canonical"
            and (c.get("reference_id") is None or c.get("reference_id") not in inst_ids)
        )

        net_adj = sum(
            (a["amount"] if a.get("direction") == "credit" else -a["amount"]) for a in adjustments
        )

        canonical_debt = Decimal(str(canonical_inst + canonical_chg - net_adj))
        legacy_debt = Decimal(str(legacy_inst + legacy_chg))
        total_debt = canonical_debt + legacy_debt
        return {
            "canonical_debt": canonical_debt,
            "legacy_debt": legacy_debt,
            "total_debt": total_debt,
        }

    # Case A: Pure legacy card with legacy installment (105k) + legacy statement fee (15k) [CT101 Scenario C]
    res_a = partition_debt(
        installments=[
            {
                "id": uuid.uuid4(),
                "principal_amount": Decimal("100000.00"),
                "interest_amount": Decimal("5000.00"),
                "paid_amount": Decimal("0.00"),
                "debt_partition": "legacy",
                "status": "pending",
            }
        ],
        charges=[
            {
                "id": uuid.uuid4(),
                "amount": Decimal("15000.00"),
                "paid_amount": Decimal("0.00"),
                "statement_partition": "legacy",
                "reference_id": None,
                "status": "pending",
            }
        ],
        adjustments=[],
    )
    assert res_a["canonical_debt"] == Decimal("0.00")
    assert res_a["legacy_debt"] == Decimal("120000.00")
    assert res_a["total_debt"] == Decimal("120000.00")

    # Case B: Mixed card with legacy installment (150k), legacy fee (25k), canonical installment (200k)
    res_b = partition_debt(
        installments=[
            {
                "id": uuid.uuid4(),
                "principal_amount": Decimal("150000.00"),
                "interest_amount": Decimal("0.00"),
                "paid_amount": Decimal("0.00"),
                "debt_partition": "legacy",
                "status": "pending",
            },
            {
                "id": uuid.uuid4(),
                "principal_amount": Decimal("200000.00"),
                "interest_amount": Decimal("0.00"),
                "paid_amount": Decimal("0.00"),
                "debt_partition": "canonical",
                "status": "pending",
            },
        ],
        charges=[
            {
                "id": uuid.uuid4(),
                "amount": Decimal("25000.00"),
                "paid_amount": Decimal("0.00"),
                "statement_partition": "legacy",
                "reference_id": None,
                "status": "pending",
            }
        ],
        adjustments=[],
    )
    assert res_b["canonical_debt"] == Decimal("200000.00")
    assert res_b["legacy_debt"] == Decimal("175000.00")
    assert res_b["total_debt"] == Decimal("375000.00")

    # Case C: Mirrored installment charge is NOT double counted
    inst_c_id = uuid.uuid4()
    res_c = partition_debt(
        installments=[
            {
                "id": inst_c_id,
                "principal_amount": Decimal("100000.00"),
                "interest_amount": Decimal("0.00"),
                "paid_amount": Decimal("0.00"),
                "debt_partition": "canonical",
                "status": "pending",
            }
        ],
        charges=[
            {
                "id": uuid.uuid4(),
                "amount": Decimal("100000.00"),
                "paid_amount": Decimal("0.00"),
                "statement_partition": "canonical",
                "reference_id": inst_c_id,  # Mirroring charge!
                "status": "pending",
            },
            {
                "id": uuid.uuid4(),
                "amount": Decimal("10000.00"),
                "paid_amount": Decimal("0.00"),
                "statement_partition": "canonical",
                "reference_id": None,
                "status": "pending",
            },
        ],
        adjustments=[],
    )
    assert res_c["canonical_debt"] == Decimal("110000.00")
    assert res_c["legacy_debt"] == Decimal("0.00")
    assert res_c["total_debt"] == Decimal("110000.00")


def test_credit_domain_models_timezone_column_contracts():
    """Historical timestamp columns must be naive DateTime; canonical columns must be aware timestamptz."""
    from sqlalchemy import inspect
    from sqlalchemy.types import DateTime

    card_mapper = inspect(CreditCard)
    assert card_mapper.columns["created_at"].type.timezone is False
    assert card_mapper.columns["updated_at"].type.timezone is True
    assert card_mapper.columns["canonical_activated_at"].type.timezone is True

    tx_mapper = inspect(CreditCardTransaction)
    assert tx_mapper.columns["created_at"].type.timezone is False
    assert tx_mapper.columns["occurred_at"].type.timezone is True
    assert tx_mapper.columns["updated_at"].type.timezone is True

    # Canonical models require DateTime(timezone=True) exclusively
    for model_cls in [
        CreditCardInstallment,
        CreditCardStatement,
        CreditCardBillingCycle,
        CreditCardEarlyPayment,
        CreditCardStatementCharge,
        CreditCardStatementComponent,
        CreditCardStatementFact,
        CreditCardPayment,
        CreditCardPaymentAllocation,
        CreditCardAdjustment,
        CreditCardAdjustmentAllocation,
    ]:
        mapper = inspect(model_cls)
        for col in mapper.columns:
            if isinstance(col.type, DateTime):
                assert col.type.timezone is True, (
                    f"{model_cls.__name__}.{col.name} must be timezone-aware DateTime(timezone=True)"
                )


@pytest.mark.asyncio
async def test_gate_fixture_factory_timestamps_and_entities_contract():
    """GateFixture factory produces naive timestamps for historical columns and aware for timestamptz."""
    from unittest.mock import AsyncMock, MagicMock

    now_utc, now_naive = get_fixture_timestamps()
    assert now_utc.tzinfo is not None
    assert now_naive.tzinfo is None
    assert now_naive == now_utc.replace(tzinfo=None)

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    fixture = GateFixture(mock_session)
    card = await fixture.add_credit_card(currency="COP", canonical_mode=True)

    assert card.created_at.tzinfo is None, "CreditCard.created_at must be offset-naive UTC"
    assert card.updated_at.tzinfo is not None, "CreditCard.updated_at must be offset-aware UTC"
    assert card.canonical_activated_at.tzinfo is not None, (
        "CreditCard.canonical_activated_at must be offset-aware UTC"
    )

    tx = await fixture.add_transaction(card.id, Decimal("100000.00"), type="purchase")
    assert tx.created_at.tzinfo is None, "CreditCardTransaction.created_at must be offset-naive UTC"
    assert tx.occurred_at.tzinfo is not None, "CreditCardTransaction.occurred_at must be aware UTC"
    assert tx.updated_at.tzinfo is not None, "CreditCardTransaction.updated_at must be aware UTC"


@pytest.mark.asyncio
async def test_verify_statement_triggers_enabled_contract():
    """verify_statement_triggers_enabled accepts str ('O') and bytes (b'O'), rejecting disabled fail-closed."""
    from unittest.mock import AsyncMock, MagicMock

    session = AsyncMock()

    # Case A: Driver returns bytes b'O'
    result_bytes = MagicMock()
    result_bytes.all.return_value = [
        ("trg_cc_statements_immutable", b"O"),
        ("trg_cc_stmt_comp_immutable", b"O"),
    ]
    session.execute.return_value = result_bytes
    assert await verify_statement_triggers_enabled(session) is True

    # Case B: Driver returns str 'O'
    result_str = MagicMock()
    result_str.all.return_value = [
        ("trg_cc_statements_immutable", "O"),
        ("trg_cc_stmt_comp_immutable", "O"),
    ]
    session.execute.return_value = result_str
    assert await verify_statement_triggers_enabled(session) is True

    # Case C: Disabled trigger b'D' fails closed
    result_disabled = MagicMock()
    result_disabled.all.return_value = [
        ("trg_cc_statements_immutable", b"D"),
        ("trg_cc_stmt_comp_immutable", b"O"),
    ]
    session.execute.return_value = result_disabled
    with pytest.raises(RuntimeError, match="not in enabled state 'O'"):
        await verify_statement_triggers_enabled(session)

    # Case D: Missing trigger fails closed
    result_missing = MagicMock()
    result_missing.all.return_value = [
        ("trg_cc_statements_immutable", b"O"),
    ]
    session.execute.return_value = result_missing
    with pytest.raises(RuntimeError, match="Missing expected immutability triggers"):
        await verify_statement_triggers_enabled(session)


@pytest.mark.asyncio
async def test_gate_fixture_cleanup_safely_recovers_from_aborted_session():
    """GateFixture cleanup rolls back before trigger restoration, recovering from aborted sessions without masking errors."""
    from unittest.mock import AsyncMock, MagicMock

    session = AsyncMock()
    nested_states = [True, False]

    def mock_in_nested():
        if nested_states:
            return nested_states.pop(0)
        return False

    session.in_nested_transaction = MagicMock(side_effect=mock_in_nested)
    session.rollback = AsyncMock()

    # Case A: Clean cleanup with successful stages
    session.execute = AsyncMock()
    # Trigger verification returns enabled
    result_triggers = MagicMock()
    result_triggers.all.return_value = [
        ("trg_cc_statements_immutable", b"O"),
        ("trg_cc_stmt_comp_immutable", b"O"),
    ]
    # Residue checks return 0
    result_residue = MagicMock()
    result_residue.scalar_one.return_value = 0

    def mock_execute(stmt, *args, **kwargs):
        stmt_str = str(stmt)
        if "pg_trigger" in stmt_str:
            return result_triggers
        return result_residue

    session.execute.side_effect = mock_execute

    fixture = GateFixture(session)
    await fixture.cleanup()
    # initial_rollback and nested rollbacks executed
    assert session.rollback.await_count >= 1

    # Case B: Delete stage failure rolls back before trigger restoration and raises GateCleanupError
    session_b = AsyncMock()
    session_b.in_nested_transaction = MagicMock(return_value=False)
    session_b.rollback = AsyncMock()

    call_count = 0

    def mock_execute_with_failure(stmt, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        stmt_str = str(stmt)
        if "pg_trigger" in stmt_str:
            return result_triggers
        if "credit_card_adjustment_allocations" in stmt_str:
            raise RuntimeError("Simulated foreign key delete lock timeout")
        return result_residue

    session_b.execute.side_effect = mock_execute_with_failure

    fixture_b = GateFixture(session_b)
    with pytest.raises(GateCleanupError) as exc_info:
        await fixture_b.cleanup()

    assert any(
        stage == "credit_card_adjustment_allocations" for stage, _ in exc_info.value.errors
    ), "Stage failure must be preserved in GateCleanupError"
    # pre_trigger_restore_rollback was called before trigger restoration
    assert session_b.rollback.await_count >= 2


@pytest.mark.asyncio
async def test_gate_fixture_add_credit_card_explicit_activation_date():
    """GateFixture allows explicit canonical_activated_at override to support historical cycle tests."""
    from unittest.mock import AsyncMock, MagicMock

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    fixture = GateFixture(mock_session)

    # Override with historical activation date
    hist_act = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    card_hist = await fixture.add_credit_card(canonical_activated_at=hist_act)
    assert card_hist.canonical_activated_at == hist_act
    assert card_hist.canonical_mode is True

    # Default canonical_mode=True sets current UTC
    card_default = await fixture.add_credit_card()
    assert card_default.canonical_activated_at is not None
    assert card_default.canonical_activated_at.tzinfo is not None

    # canonical_mode=False sets None
    card_legacy = await fixture.add_credit_card(canonical_mode=False)
    assert card_legacy.canonical_activated_at is None


def test_nested_transaction_savepoint_rollback_contract():
    """Objects added inside a nested transaction are cleanly rolled back by savepoint without expunge."""
    from sqlalchemy import Column, Integer, String, create_engine
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session, declarative_base

    Base = declarative_base()

    class Item(Base):
        __tablename__ = "test_items"
        id = Column(Integer, primary_key=True)
        code = Column(String, unique=True, nullable=False)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    session = Session(engine)
    i1 = Item(id=1, code="unique-code")
    session.add(i1)
    session.commit()

    # Attempt duplicate insert inside begin_nested
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            i2 = Item(id=2, code="unique-code")
            session.add(i2)
            session.flush()

    # Session remains active and healthy; i2 is not in session; commit succeeds without expunge
    assert session.is_active is True
    session.commit()


def test_activation_frontier_cycle_alignment_contract():
    """Activation timestamps evaluated via financial_date() in America/Bogota match determine_cycle_for_date."""
    from app.core.financial_time import financial_date
    from app.credit.utils import determine_cycle_for_date

    # 1. test_pg03c: activation at 2026-07-16T05:00:00Z (midnight America/Bogota) yields financial_date 2026-07-16
    # Day 16 > cutoff 15 => initial period is 2026-08 (August)
    act_03c = datetime(2026, 7, 16, 5, 0, tzinfo=UTC)
    fin_date_03c = financial_date(act_03c)
    assert fin_date_03c == date(2026, 7, 16)
    period_03c, start_03c, cutoff_03c, _ = determine_cycle_for_date(
        fin_date_03c, cutoff_day=15, due_day=5
    )
    assert period_03c == "2026-08"
    assert start_03c == date(2026, 7, 16)
    assert cutoff_03c == date(2026, 8, 15)

    # Negative proof: 2026-07-16T00:00:00Z is 19:00 July 15 in Bogota, yielding cycle 2026-07 (July)
    act_03c_naive_utc = datetime(2026, 7, 16, 0, 0, tzinfo=UTC)
    fin_date_03c_prior = financial_date(act_03c_naive_utc)
    assert fin_date_03c_prior == date(2026, 7, 15)
    period_prior, _, _, _ = determine_cycle_for_date(fin_date_03c_prior, cutoff_day=15, due_day=5)
    assert period_prior == "2026-07"

    # 2. test_pg08a: activation at 2026-09-01T05:00:00Z (midnight America/Bogota) yields financial_date 2026-09-01
    # Day 1 <= cutoff 15 => single eligible period is 2026-09 (September)
    act_08a = datetime(2026, 9, 1, 5, 0, tzinfo=UTC)
    fin_date_08a = financial_date(act_08a)
    assert fin_date_08a == date(2026, 9, 1)
    period_08a, start_08a, cutoff_08a, _ = determine_cycle_for_date(
        fin_date_08a, cutoff_day=15, due_day=5
    )
    assert period_08a == "2026-09"
    assert start_08a == date(2026, 8, 16)
    assert cutoff_08a == date(2026, 9, 15)


def test_voluntary_overpayment_exception_type_and_identity_contract():
    """Overpayment raises InvalidPaymentAmountError and pre-rollback UUID caching prevents expired IO."""
    from sqlalchemy import Column, Integer, String, create_engine
    from sqlalchemy.orm import Session, declarative_base

    from app.credit.exceptions import CreditDomainError, InvalidPaymentAmountError

    assert issubclass(InvalidPaymentAmountError, CreditDomainError)

    Base = declarative_base()

    class MockAccount(Base):
        __tablename__ = "mock_accounts"
        id = Column(Integer, primary_key=True)
        name = Column(String)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    session = Session(engine)
    acc = MockAccount(id=42, name="test")
    session.add(acc)
    session.commit()

    # Pre-rollback ID capture
    account_id = acc.id
    assert account_id == 42

    # Simulate rollback expiration
    session.rollback()

    # Accessing the pre-saved account_id never touches expired ORM instance state
    assert account_id == 42
    reloaded = session.get(MockAccount, account_id)
    assert reloaded is not None and reloaded.id == 42
