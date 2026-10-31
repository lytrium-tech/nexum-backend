import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.credit.interest import (
    ESTIMATOR_VERSION,
    PrincipalChange,
    build_interest_bearing_principal_timeline,
    daily_interest_rate,
    estimate_interest,
    monthly_rate_decimal,
)
from app.credit.models import CreditCard
from app.credit.repository import CreditCardRepository
from app.credit.schemas import (
    CreditCardCreate,
    CreditCardOpeningBalanceCreate,
    CreditCardPurchaseCreate,
)
from app.credit.service import CreditCardService

# ── PARTE 5 & Rate Semantics ────────────────────────────────────────────────


def test_monthly_rate_uses_percentage_points_once_and_legacy_alias():
    assert monthly_rate_decimal(Decimal("2.11")) == Decimal("0.0211")
    card = CreditCardCreate(
        name="Card",
        bank="Bank",
        credit_limit=1000,
        cutoff_day=15,
        due_day=25,
        currency="COP",
        monthly_interest_rate=Decimal("2.11"),
    )
    assert card.monthly_interest_rate_pct == Decimal("2.11")
    assert card.model_dump(by_alias=True)["monthly_interest_rate_pct"] == Decimal("2.11")
    assert CreditCardPurchaseCreate(amount=100).zero_interest is False
    assert CreditCardPurchaseCreate(amount=100, zero_interest=True).zero_interest is True
    with pytest.raises(ValidationError):
        CreditCardCreate(
            name="Card",
            bank="Bank",
            credit_limit=1000,
            cutoff_day=15,
            due_day=25,
            currency="COP",
            monthly_interest_rate_pct=Decimal("-0.01"),
        )


def test_daily_interest_estimator_is_versioned_and_deterministic():
    start = date(2026, 1, 1)
    changes = [PrincipalChange(start, Decimal("1000"))]
    one_day = estimate_interest(changes, Decimal("2.11"), start, date(2026, 1, 2))
    assert one_day == Decimal("0.70")
    assert estimate_interest(changes, Decimal("2.11"), start, date(2026, 1, 2)) == one_day
    assert ESTIMATOR_VERSION == "nexum_interest_v1"
    assert estimate_interest(changes, Decimal("0"), start, date(2026, 1, 3)) == Decimal("0.00")
    assert estimate_interest(changes, Decimal("-1.0"), start, date(2026, 1, 3)) == Decimal("0.00")


# ── PARTE 15 Matrix: Baseline, Payment, Refund, Reversals ───────────────────


def test_interest_baseline_and_timeline():
    start = date(2026, 10, 1)
    end = date(2026, 11, 1)  # 31 days [start, end)
    rate = Decimal("2.11")
    purchase = PrincipalChange(
        effective_date=start, amount=Decimal("1000000"), event_type="purchase"
    )

    baseline = estimate_interest([purchase], rate, start, end)
    dr = daily_interest_rate(rate)
    expected = (Decimal("1000000") * 31 * dr).quantize(Decimal("0.01"))
    assert baseline == expected
    assert baseline == Decimal("21584.00")


def test_payment_partially_allocated_reduces_interest_mathematically():
    start = date(2026, 10, 1)
    end = date(2026, 11, 1)
    rate = Decimal("2.11")
    purchase = PrincipalChange(
        effective_date=start, amount=Decimal("1000000"), event_type="purchase"
    )
    # Payment on Oct 10: 300,000 allocated to principal
    payment = PrincipalChange(
        effective_date=date(2026, 10, 10),
        amount=Decimal("-300000"),
        event_type="payment",
        order_priority=40,
    )

    baseline = estimate_interest([purchase], rate, start, end)
    with_payment = estimate_interest([purchase, payment], rate, start, end)

    assert with_payment < baseline
    # Days 1-9 (9 days) at 1,000,000; Days 10-31 (22 days) at 700,000
    dr = daily_interest_rate(rate)
    expected = ((Decimal("1000000") * 9 + Decimal("700000") * 22) * dr).quantize(Decimal("0.01"))
    assert with_payment == expected


def test_partial_payment_only_principal_allocation_reduces_projection():
    start = date(2026, 10, 1)
    end = date(2026, 10, 15)
    rate = Decimal("2.11")
    purchase = PrincipalChange(start, Decimal("500000"), event_type="purchase")
    # Partial payment allocated: 200,000 to principal
    partial_payment = PrincipalChange(date(2026, 10, 5), Decimal("-200000"), event_type="payment")
    interest = estimate_interest([purchase, partial_payment], rate, start, end)
    dr = daily_interest_rate(rate)
    # 4 days @ 500,000 + 10 days @ 300,000
    expected = ((Decimal("500000") * 4 + Decimal("300000") * 10) * dr).quantize(Decimal("0.01"))
    assert interest == expected


def test_payment_allocated_only_to_interest_or_charge_does_not_reduce_principal():
    start = date(2026, 10, 1)
    end = date(2026, 10, 15)
    rate = Decimal("2.11")
    purchase = PrincipalChange(start, Decimal("500000"), event_type="purchase")
    # Payment had 50,000 assessed interest allocation -> 0 principal delta
    baseline = estimate_interest([purchase], rate, start, end)
    interest_only_alloc = PrincipalChange(date(2026, 10, 5), Decimal("0"), event_type="payment")
    with_alloc = estimate_interest([purchase, interest_only_alloc], rate, start, end)
    assert with_alloc == baseline


def test_refund_reduces_projection_from_effective_date():
    start = date(2026, 10, 1)
    end = date(2026, 11, 1)
    rate = Decimal("2.11")
    purchase = PrincipalChange(start, Decimal("1000000"), event_type="purchase")
    # Refund of 100,000 on Oct 15
    refund = PrincipalChange(
        date(2026, 10, 15), Decimal("-100000"), event_type="refund", order_priority=50
    )

    baseline = estimate_interest([purchase], rate, start, end)
    with_refund = estimate_interest([purchase, refund], rate, start, end)
    assert with_refund < baseline

    # 14 days @ 1,000,000 + 17 days @ 900,000
    dr = daily_interest_rate(rate)
    expected = ((Decimal("1000000") * 14 + Decimal("900000") * 17) * dr).quantize(Decimal("0.01"))
    assert with_refund == expected


def test_refund_reversal_restores_principal_from_reversal_date():
    start = date(2026, 10, 1)
    end = date(2026, 10, 21)
    rate = Decimal("2.11")
    purchase = PrincipalChange(start, Decimal("1000000"), event_type="purchase")
    refund = PrincipalChange(
        date(2026, 10, 5), Decimal("-200000"), event_type="refund", order_priority=50
    )
    # Reversal on Oct 10 restores the 200,000 principal
    refund_reversal = PrincipalChange(
        date(2026, 10, 10), Decimal("200000"), event_type="reversal", order_priority=30
    )

    interest = estimate_interest([purchase, refund, refund_reversal], rate, start, end)
    dr = daily_interest_rate(rate)
    # Oct 1-4 (4 days) @ 1,000,000
    # Oct 5-9 (5 days) @ 800,000
    # Oct 10-20 (11 days) @ 1,000,000 (restored!)
    expected = (
        (Decimal("1000000") * 4 + Decimal("800000") * 5 + Decimal("1000000") * 11) * dr
    ).quantize(Decimal("0.01"))
    assert interest == expected


def test_payment_reversal_restores_principal_from_reversal_date():
    start = date(2026, 10, 1)
    end = date(2026, 10, 21)
    rate = Decimal("2.11")
    purchase = PrincipalChange(start, Decimal("1000000"), event_type="purchase")
    payment = PrincipalChange(
        date(2026, 10, 5), Decimal("-300000"), event_type="payment", order_priority=40
    )
    payment_reversal = PrincipalChange(
        date(2026, 10, 12), Decimal("300000"), event_type="reversal", order_priority=30
    )

    interest = estimate_interest([purchase, payment, payment_reversal], rate, start, end)
    dr = daily_interest_rate(rate)
    # Oct 1-4 (4 days) @ 1,000,000
    # Oct 5-11 (7 days) @ 700,000
    # Oct 12-20 (9 days) @ 1,000,000
    expected = (
        (Decimal("1000000") * 4 + Decimal("700000") * 7 + Decimal("1000000") * 9) * dr
    ).quantize(Decimal("0.01"))
    assert interest == expected


def test_zero_interest_generates_zero_interest():
    start = date(2026, 10, 1)
    end = date(2026, 10, 15)
    rate = Decimal("2.11")
    zero_purchase = PrincipalChange(start, Decimal("1000000"), zero_interest=True)
    assert estimate_interest([zero_purchase], rate, start, end) == Decimal("0.00")


def test_one_installment_without_zero_interest_is_interest_bearing():
    start = date(2026, 10, 1)
    end = date(2026, 10, 15)
    rate = Decimal("2.11")
    one_inst = PrincipalChange(start, Decimal("1000000"), zero_interest=False)
    interest = estimate_interest([one_inst], rate, start, end)
    assert interest > Decimal("0.00")


def test_multiple_purchases_combine_correctly_on_timeline():
    start = date(2026, 10, 1)
    end = date(2026, 10, 20)
    rate = Decimal("2.11")
    p1 = PrincipalChange(date(2026, 10, 1), Decimal("600000"), event_type="purchase")
    p2 = PrincipalChange(date(2026, 10, 10), Decimal("400000"), event_type="purchase")

    interest = estimate_interest([p1, p2], rate, start, end)
    dr = daily_interest_rate(rate)
    # Oct 1-9 (9 days) @ 600,000 + Oct 10-19 (10 days) @ 1,000,000
    expected = ((Decimal("600000") * 9 + Decimal("1000000") * 10) * dr).quantize(Decimal("0.01"))
    assert interest == expected


def test_purchase_after_payment_does_not_affect_prior_days():
    start = date(2026, 10, 1)
    end = date(2026, 10, 20)
    p1 = PrincipalChange(date(2026, 10, 1), Decimal("500000"), event_type="purchase")
    pay = PrincipalChange(date(2026, 10, 5), Decimal("-200000"), event_type="payment")
    p2 = PrincipalChange(date(2026, 10, 15), Decimal("300000"), event_type="purchase")

    timeline = build_interest_bearing_principal_timeline([p1, pay, p2], start, end)
    dict_timeline = dict(timeline)
    assert dict_timeline[date(2026, 10, 1)] == Decimal("500000")
    assert dict_timeline[date(2026, 10, 4)] == Decimal("500000")
    assert dict_timeline[date(2026, 10, 5)] == Decimal("300000")
    assert dict_timeline[date(2026, 10, 14)] == Decimal("300000")
    assert dict_timeline[date(2026, 10, 15)] == Decimal("600000")


# ── PARTE 16 Edge Cases ──────────────────────────────────────────────────────


def test_edge_cases_same_day_events_deterministic_ordering():
    start = date(2026, 10, 10)
    end = date(2026, 10, 12)
    # On same day: purchase of 500,000 and payment of 300,000
    purchase = PrincipalChange(start, Decimal("500000"), event_type="purchase", order_priority=20)
    payment = PrincipalChange(start, Decimal("-300000"), event_type="payment", order_priority=40)

    # Regardless of input ordering, additions come first so balance doesn't falsely clamp
    timeline1 = build_interest_bearing_principal_timeline([payment, purchase], start, end)
    timeline2 = build_interest_bearing_principal_timeline([purchase, payment], start, end)
    assert timeline1 == timeline2
    assert timeline1[0][1] == Decimal("200000")


def test_edge_case_negative_principal_clamped_to_zero():
    start = date(2026, 10, 1)
    end = date(2026, 10, 3)
    # Erroneous or extreme negative change clamps daily balance to 0, never negative
    excess_payment = PrincipalChange(start, Decimal("-500000"), event_type="payment")
    timeline = build_interest_bearing_principal_timeline([excess_payment], start, end)
    assert all(balance == Decimal("0.00") for _d, balance in timeline)


def test_edge_case_event_on_period_end_is_excluded_from_half_open():
    start = date(2026, 10, 1)
    end = date(2026, 10, 5)
    rate = Decimal("2.11")
    event_start = PrincipalChange(start, Decimal("100000"))
    event_end = PrincipalChange(
        end, Decimal("500000")
    )  # On period_end: should NOT affect [start, end)
    interest_without = estimate_interest([event_start], rate, start, end)
    interest_with = estimate_interest([event_start, event_end], rate, start, end)
    assert interest_without == interest_with


def test_edge_case_february_and_leap_year():
    rate = Decimal("2.11")
    # Non-leap year Feb: 28 days
    start = date(2025, 2, 1)
    end = date(2025, 3, 1)
    changes = [PrincipalChange(start, Decimal("1000000"))]
    timeline = build_interest_bearing_principal_timeline(changes, start, end)
    assert len(timeline) == 28
    feb_interest = estimate_interest(changes, rate, start, end)
    dr = daily_interest_rate(rate)
    assert feb_interest == (Decimal("1000000") * 28 * dr).quantize(Decimal("0.01"))


# ── PARTE 8 & 9: Next Payment Chronology & Opening Balance ──────────────────


async def test_next_payment_chronology_overdue_due_today_upcoming():
    session = AsyncMock()
    repo = CreditCardRepository(session)
    today = date.today()

    # Case 1: Overdue exists -> picks oldest overdue
    row_overdue_old = (
        today - timedelta(days=10),
        Decimal("100"),
        Decimal("0"),
        Decimal("0"),
        "manual",
        False,
    )
    row_overdue_new = (
        today - timedelta(days=2),
        Decimal("100"),
        Decimal("0"),
        Decimal("0"),
        "manual",
        False,
    )
    row_future = (
        today + timedelta(days=5),
        Decimal("100"),
        Decimal("0"),
        Decimal("0"),
        "manual",
        False,
    )
    session.execute.return_value = SimpleNamespace(
        fetchall=lambda: [row_overdue_new, row_overdue_old, row_future]
    )
    repo.get_by_id = AsyncMock(return_value=None)
    proj = await repo.get_next_payment_projection(uuid.uuid4())
    assert proj["status"] == "overdue"
    assert proj["due_date"] == today - timedelta(days=10)

    # Case 2: No overdue, due today exists -> picks due today
    row_today = (today, Decimal("200"), Decimal("0"), Decimal("0"), "manual", False)
    session.execute.return_value = SimpleNamespace(fetchall=lambda: [row_today, row_future])
    proj2 = await repo.get_next_payment_projection(uuid.uuid4())
    assert proj2["status"] == "due_today"
    assert proj2["due_date"] == today

    # Case 3: No overdue, none today -> picks nearest future
    row_future2 = (
        today + timedelta(days=15),
        Decimal("100"),
        Decimal("0"),
        Decimal("0"),
        "manual",
        False,
    )
    session.execute.return_value = SimpleNamespace(fetchall=lambda: [row_future2, row_future])
    proj3 = await repo.get_next_payment_projection(uuid.uuid4())
    assert proj3["status"] == "upcoming"
    assert proj3["due_date"] == today + timedelta(days=5)


def test_opening_balance_known_composition_must_conserve_total():
    known = CreditCardOpeningBalanceCreate(
        amount=Decimal("1200"),
        currency="COP",
        effective_date=date.today(),
        composition_known=True,
        principal_amount=Decimal("1000"),
        assessed_interest_amount=Decimal("200"),
    )
    assert known.principal_amount + known.assessed_interest_amount == known.amount
    with pytest.raises(ValidationError):
        CreditCardOpeningBalanceCreate(
            amount=Decimal("1200"),
            currency="COP",
            effective_date=date.today(),
            composition_known=True,
            principal_amount=Decimal("900"),
            assessed_interest_amount=Decimal("200"),
        )


async def test_opening_balance_is_idempotent_ledger_fact_not_purchase():
    service = CreditCardService(AsyncMock())
    service.repo = AsyncMock()
    service.ledger_service = AsyncMock()
    service.ledger_service.repository.get_by_command_id.return_value = None
    service.ledger_repo = None
    user_id, card_id, event_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    card = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("1000"),
        currency="COP",
        cutoff_day=15,
        due_day=5,
        is_active=True,
    )
    service.repo.get_by_id_for_update.return_value = card
    service.repo.get_card_financial_state.return_value = {
        "modeled_debt": Decimal("1200"),
        "available_credit": Decimal("-200"),
    }
    event = SimpleNamespace(id=event_id)
    service.ledger_service.record_event.return_value = SimpleNamespace(
        event=event, idempotent=False
    )
    payload = CreditCardOpeningBalanceCreate(
        amount=Decimal("1200"),
        currency="COP",
        effective_date=date.today(),
        composition_known=False,
    )

    result = await service.create_opening_balance(user_id, card_id, payload, uuid.uuid4())

    transaction = service.repo.add_transaction.await_args.args[0]
    installment = service.repo.add_installments.await_args.args[0][0]
    event_payload = service.ledger_service.record_event.await_args.args[0]
    assert transaction.type == "opening_balance"
    assert event_payload.event_type == "opening_balance"
    assert transaction.metadata_["provenance"] == "manual_input"
    assert transaction.metadata_["composition_known"] is False
    assert transaction.metadata_["interest_projection_assumption"]
    assert installment.purchase_transaction_id == transaction.id
    assert installment.principal_amount == Decimal("0.00")
    assert installment.opening_balance_amount == Decimal("1200")
    assert result.available_credit == Decimal("-200")

    installment.scheduled_period = date.today().strftime("%Y-%m")
    installment.total_amount = Decimal("1200")
    service.repo.list_credit_adjustment_allocations_for_installment.return_value = []
    await service._apply_payment_to_waterfall(
        user_id, card, Decimal("100"), installments=[installment], charges=[]
    )
    assert installment.paid_amount == Decimal("100")
    assert installment.status == "partial"


# ── Repository SQL & Schema Contract Tests ───────────────────────────────────


def test_credit_schema_columns_contract():
    """Verify ORM models match column names used in repository queries."""
    from app.credit.models import (
        CreditCardAdjustment,
        CreditCardAdjustmentAllocation,
        CreditCardInstallment,
        CreditCardPayment,
        CreditCardPaymentAllocation,
        CreditCardTransaction,
    )

    # CreditCardTransaction contract
    assert hasattr(CreditCardTransaction, "occurred_at")
    assert hasattr(CreditCardTransaction, "lifecycle_status")
    assert hasattr(CreditCardTransaction, "zero_interest")
    assert hasattr(CreditCardTransaction, "booked_amount")
    assert hasattr(CreditCardTransaction, "metadata_")
    assert not hasattr(CreditCardTransaction, "transaction_date")

    # CreditCardPayment contract
    assert hasattr(CreditCardPayment, "payment_date")
    assert hasattr(CreditCardPayment, "status")
    assert hasattr(CreditCardPayment, "payment_transaction_id")
    assert hasattr(CreditCardPayment, "booked_amount")

    # CreditCardPaymentAllocation contract
    assert hasattr(CreditCardPaymentAllocation, "allocated_principal")
    assert hasattr(CreditCardPaymentAllocation, "target_installment_id")
    assert hasattr(CreditCardPaymentAllocation, "payment_id")

    # CreditCardAdjustment contract
    assert hasattr(CreditCardAdjustment, "adjustment_type")
    assert hasattr(CreditCardAdjustment, "direction")
    assert hasattr(CreditCardAdjustment, "occurred_at")
    assert hasattr(CreditCardAdjustment, "status")
    assert hasattr(CreditCardAdjustment, "original_transaction_id")

    # CreditCardAdjustmentAllocation contract
    assert hasattr(CreditCardAdjustmentAllocation, "allocated_principal")
    assert hasattr(CreditCardAdjustmentAllocation, "target_installment_id")
    assert hasattr(CreditCardAdjustmentAllocation, "target_transaction_id")

    # CreditCardInstallment contract
    assert hasattr(CreditCardInstallment, "purchase_transaction_id")
    assert hasattr(CreditCardInstallment, "opening_balance_amount")
    assert hasattr(CreditCardInstallment, "principal_amount")
    assert hasattr(CreditCardInstallment, "assessed_interest_amount")
    assert hasattr(CreditCardInstallment, "paid_amount")


def test_get_card_principal_changes_sql_compiles():
    """Verify repository SQL queries compile validly against PostgreSQL dialect."""
    import inspect

    repo = CreditCardRepository(AsyncMock())
    src = inspect.getsource(repo.get_card_principal_changes)
    assert "occurred_at" in src
    assert "lifecycle_status IN ('posted', 'reversed')" in src
    assert "transaction_date" not in src

    # Verify query for next_payment_projection joins posted payments and nets adjustments
    src_np = inspect.getsource(repo.get_next_payment_projection)
    assert "p.status = 'posted'" in src_np
    assert "a.status = 'posted'" in src_np


@pytest.mark.asyncio
async def test_get_card_principal_changes_fact_extraction():
    """Verify repository fact extraction converts DB rows into canonical PrincipalChange events."""
    from datetime import datetime

    card_id = uuid.uuid4()
    session = AsyncMock()
    repo = CreditCardRepository(session)

    # 1. Transactions mock
    tx_normal = (
        uuid.uuid4(),
        "purchase",
        datetime(2026, 10, 1, 12, 0),
        Decimal("1000000"),
        False,
        {},
        "posted",
    )
    tx_zero = (
        uuid.uuid4(),
        "purchase",
        datetime(2026, 10, 2, 12, 0),
        Decimal("500000"),
        True,
        {},
        "posted",
    )
    tx_opening_known = (
        uuid.uuid4(),
        "opening_balance",
        datetime(2026, 10, 1, 12, 0),
        Decimal("1200000"),
        False,
        {
            "composition_known": True,
            "principal_amount": Decimal("1000000"),
            "assessed_interest_amount": Decimal("200000"),
        },
        "posted",
    )
    tx_opening_unknown_no_assumption = (
        uuid.uuid4(),
        "opening_balance",
        datetime(2026, 10, 1, 12, 0),
        Decimal("800000"),
        False,
        {"composition_known": False},
        "posted",
    )
    tx_opening_unknown_with_assumption = (
        uuid.uuid4(),
        "opening_balance",
        datetime(2026, 10, 1, 12, 0),
        Decimal("800000"),
        False,
        {
            "composition_known": False,
            "interest_projection_assumption": "unknown_composition_treated_as_interest_bearing",
        },
        "posted",
    )

    # 2. Payments mock
    pa_posted = (uuid.uuid4(), date(2026, 10, 10), Decimal("300000"), "posted", None)
    pa_reversed = (
        uuid.uuid4(),
        date(2026, 10, 15),
        Decimal("200000"),
        "reversed",
        datetime(2026, 10, 25, 12, 0),
    )

    # 3. Adjustments mock (6 columns: aa.id, a.id, a.occurred_at, a.status, aa.allocated_principal, rev.occurred_at)
    aa_posted = (
        uuid.uuid4(),
        uuid.uuid4(),
        datetime(2026, 10, 12, 12, 0),
        "posted",
        Decimal("100000"),
        None,
    )
    aa_reversed = (
        uuid.uuid4(),
        uuid.uuid4(),
        datetime(2026, 10, 14, 12, 0),
        "reversed",
        Decimal("50000"),
        datetime(2026, 10, 20, 12, 0),
    )

    session.execute.side_effect = [
        SimpleNamespace(
            fetchall=lambda: [
                tx_normal,
                tx_zero,
                tx_opening_known,
                tx_opening_unknown_no_assumption,
                tx_opening_unknown_with_assumption,
            ]
        ),
        SimpleNamespace(fetchall=lambda: [pa_posted, pa_reversed]),
        SimpleNamespace(fetchall=lambda: [aa_posted, aa_reversed]),
    ]

    changes = await repo.get_card_principal_changes(card_id)

    # Assertions on fact extraction:
    # 1. Normal purchase: added
    p_norm = next(c for c in changes if c.identifier == str(tx_normal[0]))
    assert p_norm.amount == Decimal("1000000")
    assert p_norm.zero_interest is False
    assert p_norm.effective_date == date(2026, 10, 1)

    # 2. Zero interest purchase: marked zero_interest
    p_zero = next(c for c in changes if c.identifier == str(tx_zero[0]))
    assert p_zero.amount == Decimal("500000")
    assert p_zero.zero_interest is True

    # 3. Opening balance known composition: excludes assessed interest (1000000, not 1200000)
    p_op_known = next(c for c in changes if c.identifier == str(tx_opening_known[0]))
    assert p_op_known.amount == Decimal("1000000")

    # 4. Opening balance unknown without assumption: excluded completely!
    assert not any(c.identifier == str(tx_opening_unknown_no_assumption[0]) for c in changes)

    # 5. Opening balance unknown with assumption: added
    p_op_assumed = next(
        c for c in changes if c.identifier == str(tx_opening_unknown_with_assumption[0])
    )
    assert p_op_assumed.amount == Decimal("800000")

    # 6. Posted payment: -300000 on payment date
    pay_posted = next(c for c in changes if c.identifier == str(pa_posted[0]))
    assert pay_posted.amount == Decimal("-300000")
    assert pay_posted.effective_date == date(2026, 10, 10)

    # 7. Reversed payment: -200000 on payment date AND +200000 on reversal date
    pay_rev_hist = next(c for c in changes if c.identifier == str(pa_reversed[0]))
    assert pay_rev_hist.amount == Decimal("-200000")
    assert pay_rev_hist.effective_date == date(2026, 10, 15)

    pay_rev_rest = next(c for c in changes if c.identifier == f"{pa_reversed[0]}_reversal")
    assert pay_rev_rest.amount == Decimal("200000")
    assert pay_rev_rest.effective_date == date(2026, 10, 25)

    # 8. Refund: -100000 on refund date
    ref_posted = next(c for c in changes if c.identifier == str(aa_posted[0]))
    assert ref_posted.amount == Decimal("-100000")
    assert ref_posted.effective_date == date(2026, 10, 12)

    # 9. Reversed refund: -50000 on refund date AND +50000 on reversal date
    ref_rev_hist = next(c for c in changes if c.identifier == str(aa_reversed[0]))
    assert ref_rev_hist.amount == Decimal("-50000")
    assert ref_rev_hist.effective_date == date(2026, 10, 14)

    ref_rev_rest = next(c for c in changes if c.identifier == f"{aa_reversed[0]}_reversal")
    assert ref_rev_rest.amount == Decimal("50000")
    assert ref_rev_rest.effective_date == date(2026, 10, 20)


@pytest.mark.asyncio
async def test_get_installment_projection_zero_interest_and_unknown_opening():
    """Verify installment projection returns 0 estimated interest for zero-interest or unknown opening balance."""
    session = AsyncMock()
    service = CreditCardService(session)
    service.repo = AsyncMock()
    service.repo.get_installment_settlement.return_value = (
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
    )

    card_id, user_id, inst_id, tx_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    card = CreditCard(
        id=card_id,
        user_id=user_id,
        cutoff_day=15,
        due_day=5,
        currency="COP",
        monthly_interest_rate=Decimal("2.11"),
        is_active=True,
    )
    service.repo.get_by_id.return_value = card

    # Case 1: Zero interest installment
    inst = SimpleNamespace(
        id=inst_id,
        credit_card_id=card_id,
        purchase_transaction_id=tx_id,
        scheduled_due_date=date(2026, 11, 5),
        scheduled_period="2026-11",
        installment_number=1,
        opening_balance_amount=None,
        principal_amount=Decimal("100000"),
        paid_amount=Decimal("0.00"),
        assessed_interest_amount=Decimal("0.00"),
        status="pending",
    )
    tx_zero = SimpleNamespace(
        id=tx_id,
        type="purchase",
        zero_interest=True,
        occurred_at=datetime(2026, 10, 1, 12, 0),
        metadata_={},
    )
    service.repo.get_installment_by_id.return_value = inst
    service.repo.get_transaction_by_id.return_value = tx_zero

    proj = await service.get_installment_projection(user_id, card_id, inst_id)
    assert proj["estimated_interest"] == Decimal("0.00")

    # Case 2: Opening balance unknown without assumption
    tx_unknown = SimpleNamespace(
        id=tx_id,
        type="opening_balance",
        zero_interest=False,
        occurred_at=datetime(2026, 10, 1, 12, 0),
        metadata_={"composition_known": False},
    )
    service.repo.get_transaction_by_id.return_value = tx_unknown
    proj2 = await service.get_installment_projection(user_id, card_id, inst_id)
    assert proj2["estimated_interest"] == Decimal("0.00")


@pytest.mark.asyncio
async def test_list_installments_zero_interest_and_unknown_opening():
    """Verify list_installments returns 0 estimated interest for zero-interest and unknown opening balance."""
    session = AsyncMock()
    service = CreditCardService(session)
    service.repo = AsyncMock()

    card_id, user_id = uuid.uuid4(), uuid.uuid4()
    card = CreditCard(
        id=card_id,
        user_id=user_id,
        cutoff_day=15,
        due_day=5,
        currency="COP",
        monthly_interest_rate=Decimal("2.11"),
        is_active=True,
    )
    service.repo.get_by_id.return_value = card
    service.repo.get_card_principal_changes.return_value = []

    tx_zero_id = uuid.uuid4()
    tx_unknown_id = uuid.uuid4()
    inst1 = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        credit_card_id=card_id,
        purchase_transaction_id=tx_zero_id,
        scheduled_due_date=date(2026, 11, 5),
        scheduled_period="2026-11",
        installment_number=1,
        installments_total=1,
        opening_balance_amount=None,
        principal_amount=Decimal("100000"),
        paid_amount=Decimal("0.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("100000"),
        assessed_interest_amount=Decimal("0.00"),
        status="pending",
        provenance="nexum_modeled",
        debt_partition="canonical",
        lifecycle_status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    inst2 = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        credit_card_id=card_id,
        purchase_transaction_id=tx_unknown_id,
        scheduled_due_date=date(2026, 11, 5),
        scheduled_period="2026-11",
        installment_number=1,
        installments_total=1,
        opening_balance_amount=Decimal("200000"),
        principal_amount=Decimal("0.00"),
        paid_amount=Decimal("0.00"),
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("200000"),
        assessed_interest_amount=Decimal("0.00"),
        status="pending",
        provenance="nexum_modeled",
        debt_partition="canonical",
        lifecycle_status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    service.repo.list_installments.return_value = [inst1, inst2]

    tx_zero = SimpleNamespace(
        id=tx_zero_id,
        type="purchase",
        zero_interest=True,
        occurred_at=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
        metadata_={},
    )
    tx_unknown = SimpleNamespace(
        id=tx_unknown_id,
        type="opening_balance",
        zero_interest=False,
        occurred_at=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
        metadata_={"composition_known": False},
    )
    service.repo.get_transaction_by_id.side_effect = lambda tid: (
        tx_zero if tid == tx_zero_id else tx_unknown
    )

    reads = await service.list_installments(user_id, card_id)
    assert len(reads) == 2
    assert reads[0].estimated_interest_amount == Decimal("0.00")
    assert reads[1].estimated_interest_amount == Decimal("0.00")


# ── Local Adapter Integration Tests (PARTE 5 Requirements) ───────────────────


@pytest.mark.asyncio
async def test_adapter_full_refund_preserves_historical_principal_before_refund():
    """Verify reversed purchase/opening is extracted on-read, preserving history before refund."""
    card_id = uuid.uuid4()
    purchase_id = uuid.uuid4()
    refund_alloc_id = uuid.uuid4()
    session = AsyncMock()
    repo = CreditCardRepository(session)

    # Purchase has lifecycle_status='reversed' because of full refund
    tx_full_refunded = (
        purchase_id,
        "purchase",
        datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
        Decimal("500000"),
        False,
        {},
        "reversed",
    )
    # Refund on Oct 15
    aa_full_refund = (
        refund_alloc_id,
        uuid.uuid4(),
        datetime(2026, 10, 15, 10, 0, tzinfo=UTC),
        "posted",
        Decimal("500000"),
        None,
    )

    session.execute.side_effect = [
        SimpleNamespace(fetchall=lambda: [tx_full_refunded]),
        SimpleNamespace(fetchall=lambda: []),
        SimpleNamespace(fetchall=lambda: [aa_full_refund]),
    ]

    changes = await repo.get_card_principal_changes(card_id, transaction_id=purchase_id)
    assert len(changes) == 2

    c_purchase = next(c for c in changes if c.identifier == str(purchase_id))
    assert c_purchase.amount == Decimal("500000")
    assert c_purchase.effective_date == date(2026, 10, 1)

    c_refund = next(c for c in changes if c.identifier == str(refund_alloc_id))
    assert c_refund.amount == Decimal("-500000")
    assert c_refund.effective_date == date(2026, 10, 15)

    timeline = dict(
        build_interest_bearing_principal_timeline(changes, date(2026, 10, 1), date(2026, 11, 1))
    )
    # Oct 1 to Oct 14: 500,000 principal
    for day in range(1, 15):
        assert timeline[date(2026, 10, day)] == Decimal("500000")
    # Oct 15 onwards: 0 principal
    for day in range(15, 32):
        assert timeline[date(2026, 10, day)] == Decimal("0.00")


@pytest.mark.asyncio
async def test_adapter_mixed_zero_interest_allocations():
    """Verify payments allocated to zero-interest installments do not reduce interest-bearing principal."""
    card_id = uuid.uuid4()
    tx_normal_id, tx_zero_id = uuid.uuid4(), uuid.uuid4()
    pa1_id = uuid.uuid4()
    session = AsyncMock()
    repo = CreditCardRepository(session)

    tx_normal = (
        tx_normal_id,
        "purchase",
        datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
        Decimal("100000"),
        False,
        {},
        "posted",
    )
    tx_zero = (
        tx_zero_id,
        "purchase",
        datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
        Decimal("200000"),
        True,
        {},
        "posted",
    )

    # In actual query, pa2 is excluded because target installment belongs to zero_interest tx!
    # Mock returns only pa1 (normal target) matching the SQL WHERE COALESCE(t.zero_interest, false) IS FALSE
    pa_normal_target = (pa1_id, date(2026, 10, 10), Decimal("40000"), "posted", None)

    session.execute.side_effect = [
        SimpleNamespace(fetchall=lambda: [tx_normal, tx_zero]),
        SimpleNamespace(fetchall=lambda: [pa_normal_target]),
        SimpleNamespace(fetchall=lambda: []),
    ]

    changes = await repo.get_card_principal_changes(card_id)
    assert len(changes) == 3

    # Both purchases are extracted
    c_norm = next(c for c in changes if c.identifier == str(tx_normal_id))
    assert c_norm.amount == Decimal("100000")
    assert c_norm.zero_interest is False

    c_zero = next(c for c in changes if c.identifier == str(tx_zero_id))
    assert c_zero.amount == Decimal("200000")
    assert c_zero.zero_interest is True

    # Payment reduces only normal principal
    c_pay = next(c for c in changes if c.identifier == str(pa1_id))
    assert c_pay.amount == Decimal("-40000")

    timeline = dict(
        build_interest_bearing_principal_timeline(changes, date(2026, 10, 1), date(2026, 10, 20))
    )
    # Zero interest purchase is completely excluded from interest-bearing exposure
    assert timeline[date(2026, 10, 5)] == Decimal("100000")
    assert timeline[date(2026, 10, 15)] == Decimal("60000")


@pytest.mark.asyncio
async def test_adapter_fee_only_payment_reversal_creates_no_principal_changes():
    """Verify fee-only payments and their reversals never fabricate principal deltas."""
    card_id = uuid.uuid4()
    session = AsyncMock()
    repo = CreditCardRepository(session)

    # When payment is 100% fee, pa.allocated_principal = 0, so pay_query returns empty list
    session.execute.side_effect = [
        SimpleNamespace(fetchall=lambda: []),
        SimpleNamespace(fetchall=lambda: []),
        SimpleNamespace(fetchall=lambda: []),
    ]

    changes = await repo.get_card_principal_changes(card_id)
    assert len(changes) == 0


@pytest.mark.asyncio
async def test_adapter_two_partial_refunds_reverse_only_one():
    """Verify reversing only one partial refund links exact adjustment and does not restore unreversed refunds."""
    card_id = uuid.uuid4()
    purchase_id = uuid.uuid4()
    adj1_id, adj2_id = uuid.uuid4(), uuid.uuid4()
    alloc1_id, alloc2_id = uuid.uuid4(), uuid.uuid4()
    session = AsyncMock()
    repo = CreditCardRepository(session)

    tx_purchase = (
        purchase_id,
        "purchase",
        datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
        Decimal("1000000"),
        False,
        {},
        "posted",
    )

    # Refund 1: 200,000 on Oct 10, posted (NOT reversed)
    # Refund 2: 300,000 on Oct 15, reversed on Oct 25
    aa1 = (
        alloc1_id,
        adj1_id,
        datetime(2026, 10, 10, 10, 0, tzinfo=UTC),
        "posted",
        Decimal("200000"),
        None,
    )
    aa2 = (
        alloc2_id,
        adj2_id,
        datetime(2026, 10, 15, 10, 0, tzinfo=UTC),
        "reversed",
        Decimal("300000"),
        datetime(2026, 10, 25, 14, 0, tzinfo=UTC),
    )

    session.execute.side_effect = [
        SimpleNamespace(fetchall=lambda: [tx_purchase]),
        SimpleNamespace(fetchall=lambda: []),
        SimpleNamespace(fetchall=lambda: [aa1, aa2]),
    ]

    changes = await repo.get_card_principal_changes(card_id)
    assert len(changes) == 4  # purchase + refund1 + refund2 + reversal_of_refund2

    c_p = next(c for c in changes if c.identifier == str(purchase_id))
    c_ref1 = next(c for c in changes if c.identifier == str(alloc1_id))
    c_ref2 = next(c for c in changes if c.identifier == str(alloc2_id))
    c_rev2 = next(c for c in changes if c.identifier == f"{alloc2_id}_reversal")

    assert c_p.amount == Decimal("1000000")
    assert c_ref1.amount == Decimal("-200000")
    assert c_ref2.amount == Decimal("-300000")
    assert c_rev2.amount == Decimal("300000")
    assert c_rev2.effective_date == date(2026, 10, 25)

    # Assert refund 1 has NO reversal change
    assert not any(c.identifier == f"{alloc1_id}_reversal" for c in changes)

    timeline = dict(
        build_interest_bearing_principal_timeline(changes, date(2026, 10, 1), date(2026, 10, 30))
    )
    assert timeline[date(2026, 10, 5)] == Decimal("1000000")
    assert timeline[date(2026, 10, 12)] == Decimal("800000")
    assert timeline[date(2026, 10, 20)] == Decimal("500000")
    assert timeline[date(2026, 10, 26)] == Decimal("800000")


@pytest.mark.asyncio
async def test_adapter_exact_reversal_date_no_fallback_to_payment_or_updated_at():
    """Verify reversal uses exact compensating fact date without falling back to payment date or updated_at."""
    card_id = uuid.uuid4()
    pa_id = uuid.uuid4()
    session = AsyncMock()
    repo = CreditCardRepository(session)

    # Payment on Oct 5 reversed on Oct 22
    pa_reversed_with_fact = (
        pa_id,
        date(2026, 10, 5),
        Decimal("250000"),
        "reversed",
        datetime(2026, 10, 22, 16, 0, tzinfo=UTC),
    )

    session.execute.side_effect = [
        SimpleNamespace(fetchall=lambda: []),
        SimpleNamespace(fetchall=lambda: [pa_reversed_with_fact]),
        SimpleNamespace(fetchall=lambda: []),
    ]

    changes = await repo.get_card_principal_changes(card_id)
    assert len(changes) == 2

    c_pay = next(c for c in changes if c.identifier == str(pa_id))
    c_rev = next(c for c in changes if c.identifier == f"{pa_id}_reversal")

    assert c_pay.effective_date == date(2026, 10, 5)
    assert c_pay.amount == Decimal("-250000")

    assert c_rev.effective_date == date(2026, 10, 22)
    assert c_rev.amount == Decimal("250000")

    # Now verify fail-safe: if reversed payment has NO compensating reversal fact, do NOT fabricate restoration!
    pa_no_fact = (pa_id, date(2026, 10, 5), Decimal("250000"), "reversed", None)
    session.execute.side_effect = [
        SimpleNamespace(fetchall=lambda: []),
        SimpleNamespace(fetchall=lambda: [pa_no_fact]),
        SimpleNamespace(fetchall=lambda: []),
    ]

    changes_no_fact = await repo.get_card_principal_changes(card_id)
    assert len(changes_no_fact) == 1
    assert changes_no_fact[0].identifier == str(pa_id)


@pytest.mark.asyncio
async def test_adapter_refund_reversal_service_integration():
    """Verify service.create_adjustment reverses a refund, persists compensating allocations, and blocks double reversal."""
    session = AsyncMock()
    service = CreditCardService(session)
    service.repo = AsyncMock()
    service.ledger_service = AsyncMock()
    service.ledger_service.repository.get_by_command_id.return_value = None
    service.ledger_repo = None

    user_id, card_id, orig_tx_id, adj_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    inst_id = uuid.uuid4()
    card = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("1000000"),
        currency="COP",
        is_active=True,
    )
    service.repo.get_by_id.return_value = card
    service.repo.get_by_id_for_update.return_value = card
    service.repo.get_card_debt_partitioned.return_value = {"total_debt": Decimal("500000")}

    # Original posted refund adjustment
    refund_adj = SimpleNamespace(
        id=adj_id,
        credit_card_id=card_id,
        original_transaction_id=orig_tx_id,
        direction="credit",
        status="posted",
        booked_amount=Decimal("100000"),
        booked_currency="COP",
    )
    # Target transaction
    orig_tx = SimpleNamespace(
        id=orig_tx_id, credit_card_id=card_id, type="purchase", lifecycle_status="reversed"
    )

    orig_alloc = SimpleNamespace(
        id=uuid.uuid4(),
        target_installment_id=inst_id,
        target_transaction_id=None,
        target_charge_id=None,
        allocated_amount=Decimal("100000"),
        allocated_principal=Decimal("100000"),
        allocated_interest=Decimal("0.00"),
        allocated_fees=Decimal("0.00"),
        allocation_order=1,
    )
    inst = SimpleNamespace(id=inst_id, paid_amount=Decimal("0.00"), status="cancelled")

    service.repo.get_transaction_by_id.return_value = None  # Not a transaction ID
    service.repo.get_adjustment_by_id.return_value = refund_adj
    service.repo.get_adjustment_for_update.return_value = refund_adj
    service.repo.get_transaction_by_id_for_update.return_value = orig_tx
    service.repo.list_allocations_for_adjustment.return_value = [orig_alloc]
    service.repo.get_installment_by_id_for_update.return_value = inst
    service.ledger_service.record_event.return_value = SimpleNamespace(
        event=SimpleNamespace(id=uuid.uuid4())
    )

    from app.credit.exceptions import CreditLifecycleInvalidityError
    from app.credit.schemas import CreditCardAdjustmentCreate

    payload = CreditCardAdjustmentCreate(
        original_transaction_id=adj_id,
        amount=Decimal("100000"),
        adjustment_type="reversal",
        direction="debit",
    )

    cmd_id = uuid.uuid4()
    result = await service.create_adjustment(user_id, card_id, payload, cmd_id)

    assert result.status == "success"
    assert refund_adj.status == "reversed"
    assert orig_tx.lifecycle_status == "posted"  # Restored from reversed to posted
    assert inst.status == "pending"  # Restored from cancelled

    # Verify compensating allocation was added
    alloc_args = service.repo.add_adjustment_allocations.await_args[0][0]
    assert len(alloc_args) == 1
    comp_alloc = alloc_args[0]
    assert comp_alloc.target_installment_id == inst_id
    assert comp_alloc.allocated_principal == Decimal("100000")

    # Verify second attempt to reverse the same adjustment raises CreditLifecycleInvalidityError (block double reversal)
    with pytest.raises(CreditLifecycleInvalidityError, match="already been reversed"):
        await service.create_adjustment(user_id, card_id, payload, uuid.uuid4())
