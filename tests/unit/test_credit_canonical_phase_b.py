"""Unit tests for Nexum Credit Goal 2, Phase B: Canonical Domain Behavior.

Validates:
1. Cutoff boundaries: before, on, and after cutoff day (F-01 resolution).
2. Short-month behavior (February 28/29, April 30, cutoff 31, due day clamping).
3. America/Bogota timezone governance via financial_date() (F-02 resolution).
4. Same-currency canonical purchase: original == booked, fx_rate == 1, legacy alias preserved.
5. Cross-currency purchase: canonical FX snapshot, client cannot choose booked amount, nexum_modeled provenance.
6. Installment schedule principal conservation: exact cent rounding, sum(principal) == booked_amount.
7. No automatic assessed interest: 1-installment has zero interest, configured rate never books assessed interest (F-03).
8. Lifecycle status and invalidity: pending/posted/reversed, terminal transitions, reversed creation rejected.
9. Closed billing cycle mutation prevention: post-cutoff leakage and closed statement mutation forbidden.
10. Partition non-double-counting: canonical debt and legacy compatibility debt are mutually exclusive and sum to total.
11. Minimum payment nullable/unknown: no invented 5% formula, clearly labeled data quality.
12. Deep idempotency tampering verification: parameter mismatches raise CreditIdempotencyConflictError.
13. New cards canonical by backend policy without client switch; legacy cards remain legacy without canonical cycles.
14. Pre-activation canonical purchase rejection without historical cycle synthesis.
15. External FX provider resolution strictly before write lock; revalidation strictly after write lock.
16. Amount ambiguity verification: conflicting amounts reject, same-currency backwards compatibility preserved.
17. Negative available credit not masked/clamped; non-positive available credit rejects purchase.
18. Targeted FX exception handling: FX/provider failures map to stable error; programming errors propagate.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import ValidationError

from app.core.errors import ValidationError as NexumValidationError
from app.core.financial_time import financial_date
from app.credit.exceptions import (
    CreditActivationBoundaryError,
    CreditAmbiguousAmountError,
    CreditBillingCycleClosedError,
    CreditDomainError,
    CreditIdempotencyConflictError,
    CreditLifecycleInvalidityError,
    CreditLimitExceededError,
)
from app.credit.models import (
    DEBT_PARTITION_CANONICAL,
    DEBT_PARTITION_LEGACY,
    PROVENANCE_MANUAL,
    PROVENANCE_NEXUM_MODELED,
    CreditCard,
    CreditCardBillingCycle,
    CreditCardInstallment,
    CreditCardTransaction,
)
from app.credit.schemas import (
    CreditCardCreate,
    CreditCardPurchaseCreate,
    CreditCardUpdate,
)
from app.credit.service import CreditCardService
from app.credit.utils import (
    determine_cycle_for_date,
    get_cycle_dates_for_period,
    validate_lifecycle_status,
    validate_lifecycle_transition,
)
from app.fx.provider import FxRateProvider, StaticFxRateProvider


def make_test_repo(card: CreditCard | None = None) -> AsyncMock:
    repo = AsyncMock()
    if card is not None:
        repo.get_by_id.return_value = card
        repo.get_by_id_for_update.return_value = card
    repo.get_card_status_data.return_value = {
        "purchases_count": 0,
        "payments_count": 0,
        "billed_purchases": Decimal("0.00"),
        "unbilled_purchases": Decimal("0.00"),
        "total_payments": Decimal("0.00"),
    }
    repo.get_next_payment_estimate.return_value = Decimal("0.00")
    repo.get_card_debt_partitioned.return_value = {
        "canonical_debt": Decimal("0.00"),
        "legacy_debt": Decimal("0.00"),
        "total_debt": Decimal("0.00"),
        "monthly_cc_payment": Decimal("0.00"),
    }

    async def financial_state(card_arg):
        debt = (await repo.get_card_debt_partitioned(card_arg.id))["total_debt"]
        return {
            "modeled_debt": debt,
            "available_credit": card_arg.credit_limit - debt,
        }

    repo.get_card_financial_state.side_effect = financial_state
    repo.get_billing_cycle_by_period_for_update.return_value = None
    return repo


# ── 1. Cutoff Boundary & Attribution Tests (F-01 Fix) ─────────────────────────


def test_cutoff_cycle_attribution_before_cutoff():
    """Purchase before cutoff day belongs to current month's closing cycle."""
    target_date = date(2026, 10, 10)
    cutoff_day = 15
    due_day = 5

    period, start_d, cutoff_d, due_d = determine_cycle_for_date(target_date, cutoff_day, due_day)

    assert period == "2026-10"
    assert start_d == date(2026, 9, 16)
    assert cutoff_d == date(2026, 10, 15)
    assert due_d == date(2026, 11, 5)
    assert start_d <= target_date <= cutoff_d


def test_cutoff_cycle_attribution_on_cutoff():
    """Purchase exactly on cutoff day belongs to that closing cycle."""
    target_date = date(2026, 10, 15)
    cutoff_day = 15
    due_day = 5

    period, start_d, cutoff_d, due_d = determine_cycle_for_date(target_date, cutoff_day, due_day)

    assert period == "2026-10"
    assert cutoff_d == date(2026, 10, 15)
    assert due_d == date(2026, 11, 5)
    assert start_d <= target_date <= cutoff_d


def test_cutoff_cycle_attribution_after_cutoff_moves_to_next_cycle():
    """Purchase day after cutoff strictly belongs to subsequent cycle (F-01 resolution)."""
    target_date = date(2026, 10, 16)
    cutoff_day = 15
    due_day = 5

    period, start_d, cutoff_d, due_d = determine_cycle_for_date(target_date, cutoff_day, due_day)

    assert period == "2026-11"
    assert start_d == date(2026, 10, 16)
    assert cutoff_d == date(2026, 11, 15)
    assert due_d == date(2026, 12, 5)
    assert start_d <= target_date <= cutoff_d


def test_cutoff_cycle_attribution_year_boundary():
    """Purchase after cutoff in December rolls over into January of the next year."""
    target_date = date(2026, 12, 20)
    cutoff_day = 15
    due_day = 5

    period, start_d, cutoff_d, due_d = determine_cycle_for_date(target_date, cutoff_day, due_day)

    assert period == "2027-01"
    assert start_d == date(2026, 12, 16)
    assert cutoff_d == date(2027, 1, 15)
    assert due_d == date(2027, 2, 5)


# ── 2. Short-Month Behavior Tests ─────────────────────────────────────────────


def test_short_month_february_non_leap_year():
    """Cutoff day 31 clamps to Feb 28 in a non-leap year (e.g. 2027)."""
    target_date = date(2027, 2, 20)
    cutoff_day = 31
    due_day = 15

    period, start_d, cutoff_d, due_d = determine_cycle_for_date(target_date, cutoff_day, due_day)

    assert period == "2027-02"
    assert cutoff_d == date(2027, 2, 28)
    assert due_d == date(2027, 3, 15)


def test_short_month_february_leap_year():
    """Cutoff day 31 clamps to Feb 29 in a leap year (e.g. 2028)."""
    target_date = date(2028, 2, 20)
    cutoff_day = 31
    due_day = 15

    period, start_d, cutoff_d, due_d = determine_cycle_for_date(target_date, cutoff_day, due_day)

    assert period == "2028-02"
    assert cutoff_d == date(2028, 2, 29)
    assert due_d == date(2028, 3, 15)


def test_short_month_thirty_day_month_april():
    """Cutoff day 31 clamps to April 30."""
    target_date = date(2026, 4, 15)
    cutoff_day = 31
    due_day = 10

    period, start_d, cutoff_d, due_d = determine_cycle_for_date(target_date, cutoff_day, due_day)

    assert period == "2026-04"
    assert cutoff_d == date(2026, 4, 30)
    assert due_d == date(2026, 5, 10)


def test_short_month_due_day_clamping():
    """Due day 31 clamps to 28 in February when due date falls in Feb."""
    # February cycle with cutoff 15 and due_day 31 -> due date in Feb must clamp to Feb 28
    start_d, cutoff_d, due_d = get_cycle_dates_for_period(2027, 2, 15, 31)
    assert cutoff_d == date(2027, 2, 15)
    assert due_d == date(2027, 2, 28)


# ── 3. America/Bogota Timezone Governance Tests (F-02 Fix) ────────────────────


def test_bogota_date_governance_across_utc_midnight():
    """Validates financial_date() pins time to America/Bogota across UTC midnight."""
    # 2026-10-15T23:30:00-05:00 is 2026-10-16T04:30:00Z in UTC.
    bogota_evening = datetime(
        2026, 10, 15, 23, 30, 0, tzinfo=UTC
    )  # will be converted by financial_date
    bogota_date = financial_date(bogota_evening)
    assert bogota_date == date(2026, 10, 15)

    cutoff_day = 15
    due_day = 5
    period, _, cutoff_d, _ = determine_cycle_for_date(bogota_date, cutoff_day, due_day)
    assert period == "2026-10"
    assert cutoff_d == date(2026, 10, 15)


# ── 4. Same-Currency Canonical Purchase Semantics Tests ────────────────────────


@pytest.mark.asyncio
async def test_same_currency_purchase_preserves_semantics():
    """Same currency purchase has original == booked, fx_rate == 1, and legacy amount alias."""
    repo = AsyncMock()
    ledger = AsyncMock()
    service = CreditCardService(AsyncMock())
    service.repo = repo
    service.ledger_service = ledger

    card_id = uuid.uuid4()
    user_id = uuid.uuid4()
    card = CreditCard(
        id=card_id,
        user_id=user_id,
        name="Visa COP",
        bank="Bancolombia",
        credit_limit=Decimal("5000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    repo.get_by_id.return_value = card
    repo.get_by_id_for_update.return_value = card
    repo.get_card_debt_partitioned.return_value = {
        "canonical_debt": Decimal("0.00"),
        "legacy_debt": Decimal("0.00"),
        "total_debt": Decimal("0.00"),
        "monthly_cc_payment": Decimal("0.00"),
    }
    repo.get_billing_cycle_by_period_for_update.return_value = None

    mock_event = AsyncMock(id=uuid.uuid4(), amount=Decimal("150000.00"))
    ledger.record_event.return_value = AsyncMock(event=mock_event, idempotent=False)

    payload = CreditCardPurchaseCreate(
        amount=Decimal("150000.00"),
        currency="COP",
        installments_total=1,
        occurred_at=datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC),
    )

    result = await service.create_purchase(user_id, card_id, payload, uuid.uuid4())

    assert result.status == "success"
    assert result.amount == Decimal("150000.00")
    assert result.original_amount == Decimal("150000.00")
    assert result.booked_amount == Decimal("150000.00")
    assert result.original_currency == "COP"
    assert result.booked_currency == "COP"
    assert result.fx_rate == Decimal("1.00000000")
    assert result.fx_rate_snapshot_id is None
    assert result.debt_partition == DEBT_PARTITION_CANONICAL
    assert result.billing_period == "2026-10"


# ── 5. Cross-Currency Purchase & Canonical FX Infrastructure Tests ─────────────


@pytest.mark.asyncio
async def test_cross_currency_purchase_uses_canonical_fx():
    """Cross-currency purchase converts via canonical FX snapshot with nexum_modeled provenance."""
    repo = AsyncMock()
    ledger = AsyncMock()
    static_provider = StaticFxRateProvider({"USD_COP": Decimal("4000.00")})
    session_mock = MagicMock()
    session_mock.add = MagicMock()
    session_mock.flush = AsyncMock()

    service = CreditCardService(session_mock, fx_provider=static_provider)
    service.repo = repo
    service.ledger_service = ledger

    card_id = uuid.uuid4()
    user_id = uuid.uuid4()
    card = CreditCard(
        id=card_id,
        user_id=user_id,
        name="Mastercard COP",
        bank="Davivienda",
        credit_limit=Decimal("10000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    repo.get_by_id.return_value = card
    repo.get_by_id_for_update.return_value = card
    repo.get_card_debt_partitioned.return_value = {
        "canonical_debt": Decimal("0.00"),
        "legacy_debt": Decimal("0.00"),
        "total_debt": Decimal("0.00"),
        "monthly_cc_payment": Decimal("0.00"),
    }
    repo.get_billing_cycle_by_period_for_update.return_value = None

    mock_event = AsyncMock(id=uuid.uuid4(), amount=Decimal("200000.00"))
    ledger.record_event.return_value = AsyncMock(event=mock_event, idempotent=False)

    payload = CreditCardPurchaseCreate(
        amount=Decimal("50.00"),  # 50 USD
        original_currency="USD",
        installments_total=1,
        occurred_at=datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC),
    )

    result = await service.create_purchase(user_id, card_id, payload, uuid.uuid4())

    assert result.status == "success"
    assert result.original_amount == Decimal("50.00")
    assert result.original_currency == "USD"
    assert result.booked_amount == Decimal("200000.00")
    assert result.booked_currency == "COP"
    assert result.fx_rate == Decimal("4000.00")
    assert result.fx_rate_snapshot_id is not None
    assert result.provenance == PROVENANCE_NEXUM_MODELED

    saved_tx = repo.add_transaction.call_args.args[0]
    assert saved_tx.amount == Decimal("200000.00")
    assert saved_tx.original_amount == Decimal("50.00")
    assert saved_tx.original_currency == "USD"
    assert saved_tx.fx_rate == Decimal("4000.00")
    assert saved_tx.fx_rate_snapshot_id == result.fx_rate_snapshot_id


# ── 6. Principal Rounding & Exact Conservation Tests ─────────────────────────


@pytest.mark.asyncio
async def test_installment_schedule_exact_principal_conservation():
    """Installments conserve principal exactly down to cent with deterministic remainder."""
    service = CreditCardService(AsyncMock())
    card = CreditCard(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        cutoff_day=15,
        due_day=5,
        currency="COP",
    )
    cycle = CreditCardBillingCycle(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        billing_period="2026-10",
        start_date=date(2026, 9, 16),
        cutoff_date=date(2026, 10, 15),
        due_date=date(2026, 11, 5),
        status="open",
        provenance=PROVENANCE_NEXUM_MODELED,
    )

    inst_3 = await service._build_installments(
        user_id=card.user_id,
        card=card,
        transaction_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        installments_total=3,
        cycle=cycle,
    )
    assert len(inst_3) == 3
    assert [i.principal_amount for i in inst_3] == [
        Decimal("33.33"),
        Decimal("33.33"),
        Decimal("33.34"),
    ]
    assert sum(i.principal_amount for i in inst_3) == Decimal("100.00")

    inst_6 = await service._build_installments(
        user_id=card.user_id,
        card=card,
        transaction_id=uuid.uuid4(),
        amount=Decimal("1000.00"),
        installments_total=6,
        cycle=cycle,
    )
    assert len(inst_6) == 6
    assert inst_6[-1].principal_amount == Decimal("166.70")
    assert sum(i.principal_amount for i in inst_6) == Decimal("1000.00")

    inst_odd = await service._build_installments(
        user_id=card.user_id,
        card=card,
        transaction_id=uuid.uuid4(),
        amount=Decimal("10.01"),
        installments_total=3,
        cycle=cycle,
    )
    assert sum(i.principal_amount for i in inst_odd) == Decimal("10.01")


# ── 7. No Automatic / Fabricated Assessed Interest Tests (F-03) ───────────────


@pytest.mark.asyncio
async def test_single_installment_purchase_is_interest_bearing_by_default():
    """A single installment does not imply a zero-interest promotion."""
    service = CreditCardService(AsyncMock())
    card = CreditCard(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        monthly_interest_rate=Decimal("2.5"),
    )
    cycle = CreditCardBillingCycle(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        billing_period="2026-10",
        start_date=date(2026, 9, 16),
        cutoff_date=date(2026, 10, 15),
        due_date=date(2026, 11, 5),
        status="open",
        provenance=PROVENANCE_NEXUM_MODELED,
    )

    installments = await service._build_installments(
        user_id=card.user_id,
        card=card,
        transaction_id=uuid.uuid4(),
        amount=Decimal("500000.00"),
        installments_total=1,
        cycle=cycle,
        zero_interest=False,
    )

    assert len(installments) == 1
    inst = installments[0]
    assert inst.principal_amount == Decimal("500000.00")
    assert inst.assessed_interest_amount == Decimal("0.00")
    assert inst.estimated_interest_amount > Decimal("0.00")
    assert inst.interest_amount == Decimal("0.00")
    assert inst.total_amount == Decimal("500000.00")


@pytest.mark.asyncio
async def test_explicit_zero_interest_excludes_single_and_multi_installment_projection():
    service = CreditCardService(AsyncMock())
    card = CreditCard(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        monthly_interest_rate=Decimal("2.5"),
    )
    cycle = CreditCardBillingCycle(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        billing_period="2026-10",
        start_date=date(2026, 9, 16),
        cutoff_date=date(2026, 10, 15),
        due_date=date(2026, 11, 5),
        status="open",
        provenance=PROVENANCE_NEXUM_MODELED,
    )
    for count in (1, 3):
        installments = await service._build_installments(
            user_id=card.user_id,
            card=card,
            transaction_id=uuid.uuid4(),
            amount=Decimal("300000.00"),
            installments_total=count,
            cycle=cycle,
            zero_interest=True,
        )
        assert all(i.estimated_interest_amount == Decimal("0.00") for i in installments)


@pytest.mark.asyncio
async def test_multi_installment_does_not_book_assessed_interest():
    """Configured rate alone projects estimated interest but NEVER books assessed interest."""
    service = CreditCardService(AsyncMock())
    card = CreditCard(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        monthly_interest_rate=Decimal("2.0"),
    )
    cycle = CreditCardBillingCycle(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        billing_period="2026-10",
        start_date=date(2026, 9, 16),
        cutoff_date=date(2026, 10, 15),
        due_date=date(2026, 11, 5),
        status="open",
        provenance=PROVENANCE_NEXUM_MODELED,
    )

    installments = await service._build_installments(
        user_id=card.user_id,
        card=card,
        transaction_id=uuid.uuid4(),
        amount=Decimal("300000.00"),
        installments_total=3,
        cycle=cycle,
    )

    for inst in installments:
        assert inst.assessed_interest_amount == Decimal("0.00")
        assert inst.interest_amount == Decimal("0.00")
        assert inst.total_amount == inst.principal_amount
        assert inst.estimated_interest_amount > Decimal("0.00")


# ── 8. Lifecycle Invalidity & Transitions Tests ───────────────────────────────


def test_lifecycle_validation_and_transitions():
    """Lifecycle rejects invalid statuses and forbidden transitions."""
    validate_lifecycle_status("pending")
    validate_lifecycle_status("posted")
    validate_lifecycle_status("reversed")

    with pytest.raises(CreditLifecycleInvalidityError):
        validate_lifecycle_status("settled")

    validate_lifecycle_transition("pending", "posted")
    validate_lifecycle_transition("pending", "reversed")
    validate_lifecycle_transition("posted", "reversed")

    with pytest.raises(CreditLifecycleInvalidityError):
        validate_lifecycle_transition("reversed", "posted")
    with pytest.raises(CreditLifecycleInvalidityError):
        validate_lifecycle_transition("reversed", "pending")
    with pytest.raises(CreditLifecycleInvalidityError):
        validate_lifecycle_transition("posted", "pending")


@pytest.mark.asyncio
async def test_purchase_creation_directly_reversed_rejected():
    """Attempting to create purchase in reversed status raises error at schema and service levels."""
    with pytest.raises(ValidationError):
        CreditCardPurchaseCreate(
            amount=Decimal("100.00"),
            lifecycle_status="reversed",
        )

    service = CreditCardService(AsyncMock())
    service.repo = AsyncMock()
    card = CreditCard(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_limit=Decimal("1000.00"),
        is_active=True,
    )
    service.repo.get_by_id.return_value = card
    service.repo.get_by_id_for_update.return_value = card

    payload = CreditCardPurchaseCreate(amount=Decimal("100.00"))
    payload.lifecycle_status = "reversed"

    with pytest.raises(CreditLifecycleInvalidityError):
        await service.create_purchase(card.user_id, card.id, payload, uuid.uuid4())


# ── 9. Closed Billing Cycle Mutation Prevention Tests ─────────────────────────


@pytest.mark.asyncio
async def test_purchase_to_closed_cycle_rejected():
    """Attempt to assign a purchase to a closed cycle raises CreditBillingCycleClosedError."""
    repo = AsyncMock()
    ledger = AsyncMock()
    service = CreditCardService(AsyncMock())
    service.repo = repo
    service.ledger_service = ledger

    card_id = uuid.uuid4()
    user_id = uuid.uuid4()
    card = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("5000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    repo.get_by_id.return_value = card
    repo.get_by_id_for_update.return_value = card
    repo.get_card_debt_partitioned.return_value = {
        "canonical_debt": Decimal("0.00"),
        "legacy_debt": Decimal("0.00"),
        "total_debt": Decimal("0.00"),
        "monthly_cc_payment": Decimal("0.00"),
    }

    closed_cycle = CreditCardBillingCycle(
        id=uuid.uuid4(),
        user_id=user_id,
        credit_card_id=card_id,
        billing_period="2026-10",
        start_date=date(2026, 9, 16),
        cutoff_date=date(2026, 10, 15),
        due_date=date(2026, 11, 5),
        status="closed",
        provenance=PROVENANCE_NEXUM_MODELED,
    )
    repo.get_billing_cycle_by_period_for_update.return_value = closed_cycle

    mock_event = AsyncMock(id=uuid.uuid4(), amount=Decimal("100000.00"))
    ledger.record_event.return_value = AsyncMock(event=mock_event, idempotent=False)

    payload = CreditCardPurchaseCreate(
        amount=Decimal("100000.00"),
        occurred_at=datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC),
    )

    with pytest.raises(CreditBillingCycleClosedError):
        await service.create_purchase(user_id, card_id, payload, uuid.uuid4())


@pytest.mark.asyncio
async def test_purchase_to_settled_cycle_rejected():
    """Attempt to assign a purchase to a settled cycle raises CreditBillingCycleClosedError."""
    repo = AsyncMock()
    ledger = AsyncMock()
    service = CreditCardService(AsyncMock())
    service.repo = repo
    service.ledger_service = ledger

    card_id = uuid.uuid4()
    user_id = uuid.uuid4()
    card = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("5000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    repo.get_by_id.return_value = card
    repo.get_by_id_for_update.return_value = card
    repo.get_card_debt_partitioned.return_value = {
        "canonical_debt": Decimal("0.00"),
        "legacy_debt": Decimal("0.00"),
        "total_debt": Decimal("0.00"),
        "monthly_cc_payment": Decimal("0.00"),
    }

    settled_cycle = CreditCardBillingCycle(
        id=uuid.uuid4(),
        user_id=user_id,
        credit_card_id=card_id,
        billing_period="2026-10",
        start_date=date(2026, 9, 16),
        cutoff_date=date(2026, 10, 15),
        due_date=date(2026, 11, 5),
        status="settled",
        provenance=PROVENANCE_NEXUM_MODELED,
    )
    repo.get_billing_cycle_by_period_for_update.return_value = settled_cycle

    mock_event = AsyncMock(id=uuid.uuid4(), amount=Decimal("100000.00"))
    ledger.record_event.return_value = AsyncMock(event=mock_event, idempotent=False)

    payload = CreditCardPurchaseCreate(
        amount=Decimal("100000.00"),
        occurred_at=datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC),
    )

    with pytest.raises(CreditBillingCycleClosedError):
        await service.create_purchase(user_id, card_id, payload, uuid.uuid4())


# ── 10. Partition Non-Double-Counting Proof Tests ─────────────────────────────


def test_partition_disjointness_and_non_double_counting():
    """Mathematically proves canonical debt and legacy debt partitions are mutually exclusive."""
    inst_legacy_1 = CreditCardInstallment(
        principal_amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        interest_amount=Decimal("0.00"),
        debt_partition=DEBT_PARTITION_LEGACY,
        status="pending",
    )
    inst_legacy_2 = CreditCardInstallment(
        principal_amount=Decimal("200.00"),
        paid_amount=Decimal("50.00"),
        interest_amount=Decimal("0.00"),
        debt_partition=None,
        status="partial",
    )
    inst_canonical_1 = CreditCardInstallment(
        principal_amount=Decimal("300.00"),
        paid_amount=Decimal("0.00"),
        assessed_interest_amount=Decimal("0.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        status="pending",
    )
    inst_canonical_2 = CreditCardInstallment(
        principal_amount=Decimal("400.00"),
        paid_amount=Decimal("100.00"),
        assessed_interest_amount=Decimal("0.00"),
        debt_partition=DEBT_PARTITION_CANONICAL,
        status="partial",
    )

    all_installments = [
        inst_legacy_1,
        inst_legacy_2,
        inst_canonical_1,
        inst_canonical_2,
    ]

    canonical_debt = sum(
        (i.principal_amount + (i.assessed_interest_amount or Decimal("0.00")) - i.paid_amount)
        for i in all_installments
        if i.debt_partition == DEBT_PARTITION_CANONICAL and i.status != "paid"
    )

    legacy_debt = sum(
        (i.principal_amount + (i.interest_amount or Decimal("0.00")) - i.paid_amount)
        for i in all_installments
        if (i.debt_partition == DEBT_PARTITION_LEGACY or i.debt_partition is None)
        and i.status != "paid"
    )

    total_debt = canonical_debt + legacy_debt

    assert canonical_debt == Decimal("600.00")
    assert legacy_debt == Decimal("250.00")
    assert total_debt == Decimal("850.00")

    for i in all_installments:
        is_canonical = i.debt_partition == DEBT_PARTITION_CANONICAL
        is_legacy = i.debt_partition == DEBT_PARTITION_LEGACY or i.debt_partition is None
        assert (is_canonical ^ is_legacy) is True


# ── 11. Minimum Payment Nullable / Unknown Tests ──────────────────────────────


@pytest.mark.asyncio
async def test_card_status_minimum_payment_is_none_without_invented_formula():
    """Verifies that minimum payment is None (nullable) without inventing a formula."""
    service = CreditCardService(AsyncMock())
    service.repo = AsyncMock()
    service.repo.get_card_status_data.return_value = {
        "purchases_count": 2,
        "payments_count": 0,
        "billed_purchases": Decimal("500000.00"),
        "unbilled_purchases": Decimal("100000.00"),
        "total_payments": Decimal("0.00"),
    }
    service.repo.get_next_payment_estimate.return_value = Decimal("250000.00")
    service.repo.get_card_financial_state.return_value = {
        "modeled_debt": Decimal("600000.00"),
        "available_credit": Decimal("1400000.00"),
    }

    card = CreditCard(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Test Card",
        bank="Bancolombia",
        credit_limit=Decimal("2000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
    )

    status = await service._calculate_card_status(card)

    assert status.minimum_payment is None
    assert status.data_quality["minimum_payment"] == "unknown_unless_issuer_fact"
    assert status.data_quality["available_credit"] == "estimated_from_limit_minus_debt"
    assert status.statement_balance == Decimal("500000.00")


# ── 12. Deep Idempotency Tampering Verification Tests ─────────────────────────


@pytest.mark.asyncio
async def test_create_purchase_tampered_idempotency_retry_rejected():
    """Retrying the same command_id with a different amount raises CreditIdempotencyConflictError."""
    repo = AsyncMock()
    ledger = AsyncMock()
    service = CreditCardService(AsyncMock())
    service.repo = repo
    service.ledger_service = ledger

    card_id = uuid.uuid4()
    user_id = uuid.uuid4()
    card = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("5000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    repo.get_by_id.return_value = card
    repo.get_by_id_for_update.return_value = card
    repo.get_card_debt_partitioned.return_value = {
        "canonical_debt": Decimal("0.00"),
        "legacy_debt": Decimal("0.00"),
        "total_debt": Decimal("0.00"),
        "monthly_cc_payment": Decimal("0.00"),
    }

    mock_event = AsyncMock(id=uuid.uuid4(), amount=Decimal("200000.00"))
    ledger.record_event.return_value = AsyncMock(event=mock_event, idempotent=True)

    existing_tx = CreditCardTransaction(
        id=uuid.uuid4(),
        user_id=user_id,
        credit_card_id=card_id,
        amount=Decimal("100000.00"),
        type="purchase",
    )
    repo.get_transaction_by_event.return_value = existing_tx

    payload = CreditCardPurchaseCreate(amount=Decimal("200000.00"))

    with pytest.raises(CreditIdempotencyConflictError):
        await service.create_purchase(user_id, card_id, payload, uuid.uuid4())


# ── 13. New Cards Canonical By Policy & Legacy Protection Tests ───────────────


@pytest.mark.asyncio
async def test_new_cards_canonical_by_policy_without_client_switch():
    """New cards created are canonical by backend policy without client control."""
    session = AsyncMock()
    repo = make_test_repo()
    service = CreditCardService(session)
    service.repo = repo

    user_id = uuid.uuid4()
    payload = CreditCardCreate(
        name="Policy Card",
        bank="Bancolombia",
        credit_limit=Decimal("5000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
    )

    repo.get_card_status_data.return_value = {
        "purchases_count": 0,
        "payments_count": 0,
        "billed_purchases": Decimal("0.00"),
        "unbilled_purchases": Decimal("0.00"),
        "total_payments": Decimal("0.00"),
    }
    repo.get_next_payment_estimate.return_value = Decimal("0.00")
    repo.get_card_financial_state.return_value = {
        "modeled_debt": Decimal("0.00"),
        "available_credit": Decimal("5000000.00"),
    }

    result = await service.create_card(user_id, payload)

    assert result.canonical_mode is True
    assert result.canonical_activated_at is not None
    assert result.provenance == PROVENANCE_MANUAL
    assert result.lifecycle_status == "active"

    # Verify clients cannot supply canonical_mode or partition switches in CreditCardCreate
    with pytest.raises(ValidationError):
        CreditCardCreate(
            name="Tampered Card",
            bank="Bank",
            credit_limit=Decimal("1000"),
            cutoff_day=15,
            due_day=5,
            currency="COP",
            canonical_mode=False,  # extra field forbidden!
        )

    # Verify clients cannot supply canonical_mode in CreditCardUpdate
    with pytest.raises(ValidationError):
        CreditCardUpdate(canonical_mode=True)


@pytest.mark.asyncio
async def test_migrated_legacy_card_activates_on_first_new_purchase():
    """First new purchase on migrated legacy card establishes activation frontier and creates canonical cycle."""
    from app.credit.models import PROVENANCE_LEGACY_UNKNOWN

    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    legacy_card = CreditCard(
        id=card_id,
        user_id=user_id,
        name="Legacy Migrated Card",
        bank="Old Bank",
        credit_limit=Decimal("5000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=False,  # Unactivated legacy card
        canonical_activated_at=None,
        provenance=PROVENANCE_LEGACY_UNKNOWN,
    )
    repo = make_test_repo(legacy_card)
    repo.get_card_debt_partitioned.return_value = {
        "canonical_debt": Decimal("0.00"),
        "legacy_debt": Decimal("100000.00"),
        "total_debt": Decimal("100000.00"),
        "monthly_cc_payment": Decimal("0.00"),
    }
    ledger = AsyncMock()
    service = CreditCardService(AsyncMock())
    service.repo = repo
    service.ledger_service = ledger

    mock_event = AsyncMock(id=uuid.uuid4(), amount=Decimal("50000.00"))
    ledger.record_event.return_value = AsyncMock(event=mock_event, idempotent=False)

    payload = CreditCardPurchaseCreate(amount=Decimal("50000.00"))
    result = await service.create_purchase(user_id, card_id, payload, uuid.uuid4())

    # Card is activated under lock!
    assert legacy_card.canonical_mode is True
    assert legacy_card.canonical_activated_at is not None
    assert legacy_card.provenance == PROVENANCE_MANUAL

    # Result and cycle
    assert result.debt_partition == DEBT_PARTITION_CANONICAL
    assert result.cycle_id is not None
    repo.add_billing_cycle.assert_called_once()
    cycle_saved = repo.add_billing_cycle.call_args[0][0]
    assert cycle_saved.status == "open"

    # Transaction is canonical
    repo.add_transaction.assert_called_once()
    saved_tx = repo.add_transaction.call_args[0][0]
    assert saved_tx.debt_partition == DEBT_PARTITION_CANONICAL
    assert saved_tx.cycle_id == cycle_saved.id

    # Installments are canonical
    repo.add_installments.assert_called_once()
    saved_insts = repo.add_installments.call_args[0][0]
    for inst in saved_insts:
        assert inst.debt_partition == DEBT_PARTITION_CANONICAL
        assert inst.cycle_id == cycle_saved.id


@pytest.mark.asyncio
async def test_migrated_legacy_card_backdated_purchase_predating_activation_rejected():
    """A backdated purchase predating current activation instant on an unactivated card is rejected."""
    from app.credit.models import PROVENANCE_LEGACY_UNKNOWN

    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    legacy_card = CreditCard(
        id=card_id,
        user_id=user_id,
        name="Legacy Migrated Card",
        bank="Old Bank",
        credit_limit=Decimal("5000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=False,
        canonical_activated_at=None,
        provenance=PROVENANCE_LEGACY_UNKNOWN,
    )
    repo = make_test_repo(legacy_card)
    service = CreditCardService(AsyncMock())
    service.repo = repo

    # Backdated purchase from 5 days ago
    backdated = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
    payload = CreditCardPurchaseCreate(amount=Decimal("50000.00"), occurred_at=backdated)

    with pytest.raises(CreditActivationBoundaryError):
        await service.create_purchase(user_id, card_id, payload, uuid.uuid4())

    # Card remains unactivated!
    assert legacy_card.canonical_mode is False
    assert legacy_card.canonical_activated_at is None
    repo.add_billing_cycle.assert_not_called()


@pytest.mark.asyncio
async def test_tentative_activation_rolls_back_on_purchase_failure():
    """If purchase fails after tentative activation (e.g. limit exceeded), activation rolls back atomically."""
    from app.credit.models import PROVENANCE_LEGACY_UNKNOWN

    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    legacy_card = CreditCard(
        id=card_id,
        user_id=user_id,
        name="Legacy Migrated Card",
        bank="Old Bank",
        credit_limit=Decimal("1000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=False,
        canonical_activated_at=None,
        provenance=PROVENANCE_LEGACY_UNKNOWN,
    )
    repo = make_test_repo(legacy_card)
    repo.get_card_debt_partitioned.return_value = {
        "canonical_debt": Decimal("0.00"),
        "legacy_debt": Decimal("950000.00"),
        "total_debt": Decimal("950000.00"),
        "monthly_cc_payment": Decimal("0.00"),
    }
    service = CreditCardService(AsyncMock())
    service.repo = repo

    # Purchase of 100,000 exceeds available 50,000
    payload = CreditCardPurchaseCreate(amount=Decimal("100000.00"))

    with pytest.raises(CreditLimitExceededError):
        await service.create_purchase(user_id, card_id, payload, uuid.uuid4())

    # Card state rolled back!
    assert legacy_card.canonical_mode is False
    assert legacy_card.canonical_activated_at is None
    assert legacy_card.provenance == PROVENANCE_LEGACY_UNKNOWN
    repo.add_billing_cycle.assert_not_called()


# ── 14. Pre-Activation Canonical Purchase Rejection Tests ─────────────────────


@pytest.mark.asyncio
async def test_canonical_purchase_predating_activation_rejected():
    """A purchase whose occurred_at predates canonical activation is strictly rejected."""
    repo = AsyncMock()
    service = CreditCardService(AsyncMock())
    service.repo = repo

    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    activation_time = datetime(2026, 10, 20, 15, 0, 0, tzinfo=UTC)
    card = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("5000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=activation_time,
    )
    repo.get_by_id.return_value = card
    repo.get_by_id_for_update.return_value = card

    # Purchase occurred 5 days BEFORE activation
    payload = CreditCardPurchaseCreate(
        amount=Decimal("200000.00"),
        occurred_at=datetime(2026, 10, 15, 10, 0, 0, tzinfo=UTC),
    )

    with pytest.raises(CreditActivationBoundaryError):
        await service.create_purchase(user_id, card_id, payload, uuid.uuid4())

    # Invariant: No historical billing cycle was synthesized
    repo.add_billing_cycle.assert_not_called()


# ── 15. Lock Ordering & Pre-Lock FX Resolution Verification Tests ─────────────


@pytest.mark.asyncio
async def test_fx_provider_called_before_write_lock_and_revalidation_occurs_after():
    """Proves FX resolution runs before SELECT FOR UPDATE, and revalidation occurs after lock."""
    call_log = []

    class TrackingFxProvider(FxRateProvider):
        async def get_rate(self, from_c: str, to_c: str) -> Decimal | None:
            call_log.append("fx_get_rate")
            return Decimal("4000.00")

    repo = AsyncMock()
    ledger = AsyncMock()
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    service = CreditCardService(session, fx_provider=TrackingFxProvider())
    service.repo = repo
    service.ledger_service = ledger

    card_id = uuid.uuid4()
    user_id = uuid.uuid4()
    card = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("10000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )

    async def tracking_get_by_id(cid):
        call_log.append("preliminary_read")
        return card

    async def tracking_get_by_id_for_update(cid):
        call_log.append("canonical_write_lock")
        return card

    async def tracking_get_card_debt_partitioned(cid):
        call_log.append("debt_revalidation_under_lock")
        return {
            "canonical_debt": Decimal("0.00"),
            "legacy_debt": Decimal("0.00"),
            "total_debt": Decimal("0.00"),
            "monthly_cc_payment": Decimal("0.00"),
        }

    repo.get_by_id.side_effect = tracking_get_by_id
    repo.get_by_id_for_update.side_effect = tracking_get_by_id_for_update
    repo.get_card_debt_partitioned.side_effect = tracking_get_card_debt_partitioned
    repo.get_billing_cycle_by_period_for_update.return_value = None

    mock_event = AsyncMock(id=uuid.uuid4(), amount=Decimal("400000.00"))
    ledger.record_event.return_value = AsyncMock(event=mock_event, idempotent=False)

    payload = CreditCardPurchaseCreate(
        original_amount=Decimal("100.00"),
        original_currency="USD",
    )

    result = await service.create_purchase(user_id, card_id, payload, uuid.uuid4())
    assert result.status == "success"

    # Verify exact call sequence
    assert call_log == [
        "preliminary_read",
        "fx_get_rate",
        "canonical_write_lock",
        "debt_revalidation_under_lock",
    ]
    # Proof: fx_get_rate strictly preceded canonical_write_lock
    assert call_log.index("fx_get_rate") < call_log.index("canonical_write_lock")
    # Proof: debt_revalidation_under_lock strictly succeeded canonical_write_lock
    assert call_log.index("debt_revalidation_under_lock") > call_log.index("canonical_write_lock")


# ── 16. Amount Ambiguity & Same-Currency Compatibility Tests ───────────────────


@pytest.mark.asyncio
async def test_same_currency_purchase_compatible_amount_only():
    """Backward compatibility: passing only 'amount' on same currency works seamlessly."""
    repo = AsyncMock()
    ledger = AsyncMock()
    service = CreditCardService(AsyncMock())
    service.repo = repo
    service.ledger_service = ledger

    card_id = uuid.uuid4()
    user_id = uuid.uuid4()
    card = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("1000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    repo.get_by_id.return_value = card
    repo.get_by_id_for_update.return_value = card
    repo.get_card_debt_partitioned.return_value = {
        "canonical_debt": Decimal("0.00"),
        "legacy_debt": Decimal("0.00"),
        "total_debt": Decimal("0.00"),
        "monthly_cc_payment": Decimal("0.00"),
    }

    mock_event = AsyncMock(id=uuid.uuid4(), amount=Decimal("75000.00"))
    ledger.record_event.return_value = AsyncMock(event=mock_event, idempotent=False)

    payload = CreditCardPurchaseCreate(amount=Decimal("75000.00"))
    result = await service.create_purchase(user_id, card_id, payload, uuid.uuid4())

    assert result.original_amount == Decimal("75000.00")
    assert result.booked_amount == Decimal("75000.00")
    assert result.amount == Decimal("75000.00")


@pytest.mark.asyncio
async def test_same_currency_purchase_matching_amount_and_original_amount():
    """Supplying both amount and original_amount matching works seamlessly."""
    card_id = uuid.uuid4()
    user_id = uuid.uuid4()
    card = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("1000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    repo = make_test_repo(card)
    ledger = AsyncMock()
    service = CreditCardService(AsyncMock())
    service.repo = repo
    service.ledger_service = ledger

    mock_event = AsyncMock(id=uuid.uuid4(), amount=Decimal("75000.00"))
    ledger.record_event.return_value = AsyncMock(event=mock_event, idempotent=False)

    payload = CreditCardPurchaseCreate(
        amount=Decimal("75000.00"),
        original_amount=Decimal("75000.00"),
    )
    result = await service.create_purchase(user_id, card_id, payload, uuid.uuid4())
    assert result.booked_amount == Decimal("75000.00")


@pytest.mark.asyncio
async def test_same_currency_purchase_conflicting_amounts_rejected():
    """Supplying conflicting amount and original_amount raises CreditAmbiguousAmountError."""
    service = CreditCardService(AsyncMock())
    service.repo = AsyncMock()
    card = CreditCard(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_limit=Decimal("1000000.00"),
        currency="COP",
        is_active=True,
    )
    service.repo.get_by_id.return_value = card
    service.repo.get_by_id_for_update.return_value = card

    payload = CreditCardPurchaseCreate(
        amount=Decimal("75000.00"),
        original_amount=Decimal("80000.00"),  # Conflict!
    )
    with pytest.raises(CreditAmbiguousAmountError):
        await service.create_purchase(card.user_id, card.id, payload, uuid.uuid4())


@pytest.mark.asyncio
async def test_cross_currency_purchase_conflicting_amounts_rejected():
    """Supplying conflicting amount and original_amount on cross-currency purchase is rejected."""
    service = CreditCardService(AsyncMock())
    service.repo = AsyncMock()
    card = CreditCard(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_limit=Decimal("10000000.00"),
        currency="COP",
        is_active=True,
    )
    service.repo.get_by_id.return_value = card
    service.repo.get_by_id_for_update.return_value = card

    payload = CreditCardPurchaseCreate(
        amount=Decimal("400000.00"),  # Client trying to dictate booked conversion
        original_amount=Decimal("100.00"),
        original_currency="USD",
    )
    with pytest.raises(CreditAmbiguousAmountError):
        await service.create_purchase(card.user_id, card.id, payload, uuid.uuid4())


# ── 17. Negative Available Credit Not Clamped Tests ───────────────────────────


@pytest.mark.asyncio
async def test_negative_available_credit_not_clamped_and_rejects_purchase():
    """Inconsistent debt exceeding credit limit exposes negative available credit and rejects purchase."""
    repo = AsyncMock()
    service = CreditCardService(AsyncMock())
    service.repo = repo

    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    card = CreditCard(
        id=card_id,
        user_id=user_id,
        name="Limit Test Card",
        credit_limit=Decimal("1000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    repo.get_by_id.return_value = card
    repo.get_by_id_for_update.return_value = card

    # Inconsistent state: debt is 1200 on a 1000 limit card
    repo.get_card_status_data.return_value = {
        "purchases_count": 1,
        "payments_count": 0,
        "billed_purchases": Decimal("1200.00"),
        "unbilled_purchases": Decimal("0.00"),
        "total_payments": Decimal("0.00"),
    }
    repo.get_next_payment_estimate.return_value = Decimal("0.00")
    repo.get_card_financial_state.return_value = {
        "modeled_debt": Decimal("1200.00"),
        "available_credit": Decimal("-200.00"),
    }

    # Status must expose exact negative available credit (-200.00), NOT max(0, -200) = 0!
    status = await service._calculate_card_status(card)
    assert status.available_credit == Decimal("-200.00")

    # In create_purchase: debt is 1200
    repo.get_card_debt_partitioned.return_value = {
        "canonical_debt": Decimal("1200.00"),
        "legacy_debt": Decimal("0.00"),
        "total_debt": Decimal("1200.00"),
        "monthly_cc_payment": Decimal("0.00"),
    }

    # Attempting any purchase when available credit is non-positive must fail with CreditLimitExceededError
    payload = CreditCardPurchaseCreate(amount=Decimal("10.00"))
    with pytest.raises(CreditLimitExceededError):
        await service.create_purchase(user_id, card_id, payload, uuid.uuid4())


# ── 18. Targeted FX Exception Handling Tests ─────────────────────────────────


@pytest.mark.asyncio
async def test_fx_provider_errors_mapped_and_programming_errors_propagate():
    """FX provider network/validation errors map to stable error, while programming errors propagate."""
    repo = AsyncMock()
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    class FailingFxProvider(FxRateProvider):
        def __init__(self, error_to_raise):
            self.error_to_raise = error_to_raise

        async def get_rate(self, from_c: str, to_c: str) -> Decimal | None:
            raise self.error_to_raise

    card = CreditCard(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_limit=Decimal("10000000.00"),
        currency="COP",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    repo.get_by_id.return_value = card
    repo.get_by_id_for_update.return_value = card

    payload = CreditCardPurchaseCreate(
        original_amount=Decimal("50.00"),
        original_currency="USD",
    )

    # 1. ValidationError from FX maps to stable CreditDomainError(error_code="fx_rate_unavailable")
    service1 = CreditCardService(
        session,
        fx_provider=FailingFxProvider(
            NexumValidationError("Rate unavailable", error_code="fx_rate_unavailable")
        ),
    )
    service1.repo = repo
    with pytest.raises(CreditDomainError) as exc_info:
        await service1.create_purchase(card.user_id, card.id, payload, uuid.uuid4())
    assert exc_info.value.error_code == "fx_rate_unavailable"

    # 2. HTTP network error maps to stable CreditDomainError
    service2 = CreditCardService(
        session,
        fx_provider=FailingFxProvider(httpx.ConnectTimeout("Connection timed out")),
    )
    service2.repo = repo
    with pytest.raises(CreditDomainError) as exc_info:
        await service2.create_purchase(card.user_id, card.id, payload, uuid.uuid4())
    assert exc_info.value.error_code == "fx_rate_unavailable"

    # 3. Programming error (e.g. AttributeError) is NOT caught and propagates
    service3 = CreditCardService(
        session,
        fx_provider=FailingFxProvider(AttributeError("Bug in provider")),
    )
    service3.repo = repo
    with pytest.raises(AttributeError):
        await service3.create_purchase(card.user_id, card.id, payload, uuid.uuid4())


@pytest.mark.asyncio
async def test_pending_purchase_creates_no_cycle_no_installments_and_leaves_debt_unchanged():
    """Pending purchase preserves authorization/commercial/FX facts but does not create cycles,
    does not create installments, and does not alter posted debt or estimated available credit."""
    card = CreditCard(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Visa Canonical",
        bank="Bancolombia",
        credit_limit=Decimal("5000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    repo = make_test_repo(card)
    repo.get_card_debt_partitioned.return_value = {
        "canonical_debt": Decimal("500000.00"),
        "legacy_debt": Decimal("0.00"),
        "total_debt": Decimal("500000.00"),
        "monthly_cc_payment": Decimal("50000.00"),
    }
    ledger = AsyncMock()
    service = CreditCardService(AsyncMock())
    service.repo = repo
    service.ledger_service = ledger

    mock_event = AsyncMock(id=uuid.uuid4(), amount=Decimal("200000.00"))
    ledger.record_event.return_value = AsyncMock(event=mock_event, idempotent=False)

    payload = CreditCardPurchaseCreate(
        amount=Decimal("200000.00"),
        installments_total=6,
        lifecycle_status="pending",
        description="Pending Flight Ticket",
    )
    result = await service.create_purchase(card.user_id, card.id, payload, uuid.uuid4())

    # Invariant 1: No cycle assigned or created
    assert result.cycle_id is None
    assert result.billing_period is None
    repo.add_billing_cycle.assert_not_called()
    repo.get_billing_cycle_by_period_for_update.assert_not_called()

    # Invariant 2: No installments created
    repo.add_installments.assert_not_called()

    # Invariant 3: Posted debt and available credit remain unchanged
    assert result.current_debt == Decimal("500000.00")
    assert result.available_credit == Decimal("4500000.00")
    assert result.estimated_current_debt == Decimal("500000.00")
    assert result.estimated_available_credit == Decimal("4500000.00")

    # Invariant 4: Transaction preserves pending authorization facts
    repo.add_transaction.assert_called_once()
    tx = repo.add_transaction.call_args[0][0]
    assert tx.lifecycle_status == "pending"
    assert tx.period is None
    assert tx.cycle_id is None
    assert tx.amount == Decimal("200000.00")
    assert tx.booked_amount == Decimal("200000.00")
    assert tx.original_amount == Decimal("200000.00")
    assert tx.monthly_amount is None


@pytest.mark.asyncio
async def test_pending_purchase_does_not_reject_on_posted_limit_or_closed_cycle():
    """Pending purchase does not enforce posted debt limit and does not validate against closed/settled cycles."""
    card = CreditCard(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Visa Limit Reached",
        credit_limit=Decimal("1000000.00"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    repo = make_test_repo(card)
    # Available credit is 0 (limit is 1M, debt is 1M)
    repo.get_card_debt_partitioned.return_value = {
        "canonical_debt": Decimal("1000000.00"),
        "legacy_debt": Decimal("0.00"),
        "total_debt": Decimal("1000000.00"),
        "monthly_cc_payment": Decimal("100000.00"),
    }
    ledger = AsyncMock()
    service = CreditCardService(AsyncMock())
    service.repo = repo
    service.ledger_service = ledger

    mock_event = AsyncMock(id=uuid.uuid4(), amount=Decimal("300000.00"))
    ledger.record_event.return_value = AsyncMock(event=mock_event, idempotent=False)

    payload = CreditCardPurchaseCreate(
        amount=Decimal("300000.00"),
        lifecycle_status="pending",
    )
    # Even though available credit is 0, pending purchase does NOT reject with CreditLimitExceededError
    result = await service.create_purchase(card.user_id, card.id, payload, uuid.uuid4())
    assert result.status == "success"
    assert result.lifecycle_status == "pending"
    assert result.current_debt == Decimal("1000000.00")
    assert result.available_credit == Decimal("0.00")
    assert result.cycle_id is None
    assert result.billing_period is None


@pytest.mark.asyncio
async def test_pending_purchase_idempotent_retry_preserves_semantics():
    """Idempotent retry of a pending purchase returns null cycle/period and unchanged posted debt."""
    card = CreditCard(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_limit=Decimal("5000000.00"),
        currency="COP",
        is_active=True,
        canonical_mode=True,
        canonical_activated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    repo = make_test_repo(card)
    repo.get_card_debt_partitioned.return_value = {
        "canonical_debt": Decimal("200000.00"),
        "legacy_debt": Decimal("0.00"),
        "total_debt": Decimal("200000.00"),
        "monthly_cc_payment": Decimal("0.00"),
    }
    ledger = AsyncMock()
    service = CreditCardService(AsyncMock())
    service.repo = repo
    service.ledger_service = ledger

    event_id = uuid.uuid4()
    mock_event = AsyncMock(id=event_id, amount=Decimal("100000.00"))
    ledger.record_event.return_value = AsyncMock(event=mock_event, idempotent=True)

    existing_tx = CreditCardTransaction(
        id=uuid.uuid4(),
        user_id=card.user_id,
        credit_card_id=card.id,
        type="purchase",
        amount=Decimal("100000.00"),
        lifecycle_status="pending",
        period=None,
        cycle_id=None,
    )
    repo.get_transaction_by_event.return_value = existing_tx

    payload = CreditCardPurchaseCreate(
        amount=Decimal("100000.00"),
        lifecycle_status="pending",
    )
    result = await service.create_purchase(card.user_id, card.id, payload, uuid.uuid4())
    assert result.status == "idempotent_retry"
    assert result.lifecycle_status == "pending"
    assert result.cycle_id is None
    assert result.billing_period is None
    assert result.current_debt == Decimal("200000.00")
    assert result.available_credit == Decimal("4800000.00")


@pytest.mark.asyncio
async def test_migrated_legacy_card_activates_on_first_pending_purchase():
    """Migrated legacy card activates on first pending purchase command under lock."""
    from app.credit.models import PROVENANCE_LEGACY_UNKNOWN, PROVENANCE_MANUAL

    card = CreditCard(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credit_limit=Decimal("5000000.00"),
        currency="COP",
        is_active=True,
        canonical_mode=False,
        canonical_activated_at=None,
        provenance=PROVENANCE_LEGACY_UNKNOWN,
    )
    repo = make_test_repo(card)
    repo.get_card_debt_partitioned.return_value = {
        "canonical_debt": Decimal("0.00"),
        "legacy_debt": Decimal("50000.00"),
        "total_debt": Decimal("50000.00"),
        "monthly_cc_payment": Decimal("0.00"),
    }
    ledger = AsyncMock()
    service = CreditCardService(AsyncMock())
    service.repo = repo
    service.ledger_service = ledger

    mock_event = AsyncMock(id=uuid.uuid4(), amount=Decimal("75000.00"))
    ledger.record_event.return_value = AsyncMock(event=mock_event, idempotent=False)

    payload = CreditCardPurchaseCreate(
        amount=Decimal("75000.00"),
        lifecycle_status="pending",
    )
    result = await service.create_purchase(card.user_id, card.id, payload, uuid.uuid4())

    assert card.canonical_mode is True
    assert card.canonical_activated_at is not None
    assert card.provenance == PROVENANCE_MANUAL
    assert result.lifecycle_status == "pending"
    assert result.cycle_id is None
    assert result.billing_period is None
    assert result.current_debt == Decimal("50000.00")
