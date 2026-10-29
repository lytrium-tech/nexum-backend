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


def test_credit_card_mapped_constructor_contract():
    """CreditCard accepts all mapped attributes and rejects nonexistent legacy kwargs."""
    now_utc = datetime.now(UTC)
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
        created_at=now_utc,
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
        occurred_at=datetime.now(UTC),
        source="manual",
        original_amount=Decimal("100000.00"),
        original_currency="COP",
        booked_amount=Decimal("100000.00"),
        booked_currency="COP",
        cycle_id=uuid.uuid4(),
        lifecycle_status="posted",
        provenance="nexum_modeled",
        debt_partition="canonical",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
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
