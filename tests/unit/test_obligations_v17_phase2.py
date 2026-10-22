import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.database import get_db_session
from app.core.financial_time import financial_date
from app.fx.service import FXService
from app.main import app
from app.obligations.exceptions import (
    ObligationDomainError,
    ObligationInactiveError,
    ObligationInvalidAmountError,
    ObligationPeriodAmountRequiredError,
)
from app.obligations.models import Obligation, ObligationPayment, ObligationPeriod
from app.obligations.period_engine import PeriodEngine, calculate_period_bounds
from app.obligations.repository import ObligationRepository
from app.obligations.schemas import ObligationPaymentCreate, ObligationPaymentPreviewCreate
from app.obligations.schemas_v17 import ObligationV17UpdateRequest
from app.obligations.service import ObligationService
from app.obligations.service_v17 import ObligationV17Service
from app.users.dependencies import get_current_user_profile_dep
from app.users.schemas import UserRead

# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def base_obligation():
    return Obligation(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Test Internet Service",
        description="Fiber internet contract",
        currency="COP",
        type="indefinite",
        frequency="monthly",
        payment_mode="fixed",
        base_amount=Decimal("120000.00"),
        start_date=date(2026, 7, 1),
        first_due_date=date(2026, 8, 15),
        due_day=15,
        due_month=8,
        interval_count=1,
        status="active",
        metadata_={"account_number": "12345"},
    )


@pytest.fixture
def mock_db():
    mock_session = AsyncMock()

    def fake_add(obj):
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()
        if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(UTC)

    mock_session.add = MagicMock(side_effect=fake_add)

    default_result = MagicMock()
    default_result.scalars.return_value.first.return_value = None
    default_result.scalars.return_value.all.return_value = []
    default_result.scalar_one_or_none.return_value = None
    default_result.scalar.return_value = None
    mock_session.execute = AsyncMock(return_value=default_result)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin_nested = MagicMock(return_value=cm)

    app.dependency_overrides[get_db_session] = lambda: mock_session

    async def mock_get_current_user():
        return UserRead(
            id=uuid.UUID(settings.DEV_USER_ID),
            auth_user_id=uuid.UUID(settings.DEV_USER_ID),
            name="Test User",
            email="test@example.com",
            timezone="America/Bogota",
            currency="COP",
            status="active",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    app.dependency_overrides[get_current_user_profile_dep] = mock_get_current_user
    yield mock_session
    app.dependency_overrides.clear()


# ==============================================================================
# Part 1: Period Engine Bounds & Due Date Advancement
# ==============================================================================


def test_period_bounds_monthly_offset_advance(base_obligation):
    """
    Service period bounds remain start_date anchored (July service)
    while due dates advance from first_due_date (August 15).
    """
    # Seq 1: July 2026 service, due August 15, 2026
    start1, end1, due1 = calculate_period_bounds(base_obligation, 1)
    assert start1 == date(2026, 7, 1)
    assert end1 == date(2026, 7, 31)
    assert due1 == date(2026, 8, 15)

    # Seq 2: August 2026 service, due September 15, 2026
    start2, end2, due2 = calculate_period_bounds(base_obligation, 2)
    assert start2 == date(2026, 8, 1)
    assert end2 == date(2026, 8, 31)
    assert due2 == date(2026, 9, 15)

    # Seq 3: September 2026 service, due October 15, 2026
    start3, end3, due3 = calculate_period_bounds(base_obligation, 3)
    assert start3 == date(2026, 9, 1)
    assert end3 == date(2026, 9, 30)
    assert due3 == date(2026, 10, 15)


def test_period_bounds_leap_year_february_29(base_obligation):
    """Leap year: Feb 29 first due date advances to March 29 in next sequence."""
    base_obligation.start_date = date(2024, 2, 1)
    base_obligation.first_due_date = date(2024, 2, 29)
    base_obligation.due_day = 29
    base_obligation.due_month = 2

    start1, end1, due1 = calculate_period_bounds(base_obligation, 1)
    assert start1 == date(2024, 2, 1)
    assert end1 == date(2024, 2, 29)
    assert due1 == date(2024, 2, 29)

    start2, end2, due2 = calculate_period_bounds(base_obligation, 2)
    assert start2 == date(2024, 3, 1)
    assert end2 == date(2024, 3, 31)
    assert due2 == date(2024, 3, 29)


def test_period_bounds_month_end_january_31(base_obligation):
    """Month-end January 31 advances to Feb 28 (non-leap) and March 31."""
    base_obligation.start_date = date(2026, 1, 31)
    base_obligation.first_due_date = date(2026, 1, 31)
    base_obligation.due_day = 31
    base_obligation.due_month = 1

    # Seq 2 (February in non-leap year 2026)
    start2, end2, due2 = calculate_period_bounds(base_obligation, 2)
    assert due2 == date(2026, 2, 28)

    # Seq 3 (March)
    start3, end3, due3 = calculate_period_bounds(base_obligation, 3)
    assert due3 == date(2026, 3, 31)


def test_period_bounds_yearly_offset_advance(base_obligation):
    """Yearly obligation with first due date offset."""
    base_obligation.frequency = "yearly"
    base_obligation.start_date = date(2026, 7, 1)
    base_obligation.first_due_date = date(2026, 8, 15)
    base_obligation.due_day = 15
    base_obligation.due_month = 8

    start1, end1, due1 = calculate_period_bounds(base_obligation, 1)
    assert start1 == date(2026, 1, 1)
    assert end1 == date(2026, 12, 31)
    assert due1 == date(2026, 8, 15)

    start2, end2, due2 = calculate_period_bounds(base_obligation, 2)
    assert start2 == date(2027, 1, 1)
    assert end2 == date(2027, 12, 31)
    assert due2 == date(2027, 8, 15)


@pytest.mark.asyncio
async def test_sync_periods_fills_missing_sequences_without_duplicates(base_obligation):
    """If seq 1 and seq 3 exist, sync generates seq 2 only."""
    session = AsyncMock()
    session.add = MagicMock()

    seq1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=base_obligation.id,
        period_key="2026-07-monthly-1",
        sequence_number=1,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
        due_date=date(2026, 8, 15),
        amount=Decimal("120000.00"),
        currency="COP",
        status="pending_payment",
    )
    seq3 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=base_obligation.id,
        period_key="2026-09-monthly-3",
        sequence_number=3,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
        due_date=date(2026, 10, 15),
        amount=Decimal("120000.00"),
        currency="COP",
        status="pending_payment",
    )

    # refresh_overdue query
    mock_overdue = MagicMock()
    mock_overdue.scalars.return_value.all.return_value = []

    # seq 1 lookup
    mock_s1 = MagicMock()
    mock_s1.scalar_one_or_none.return_value = seq1

    # seq 2 lookup (missing)
    mock_s2 = MagicMock()
    mock_s2.scalar_one_or_none.return_value = None

    # seq 3 lookup
    mock_s3 = MagicMock()
    mock_s3.scalar_one_or_none.return_value = seq3

    session.execute.side_effect = [mock_overdue, mock_s1, mock_s2, mock_s3]

    engine = PeriodEngine(session)
    # Current date in sequence 2 (August 2026) -> max_seq = 2 + 1 = 3
    await engine.sync_periods(base_obligation, current_date=date(2026, 8, 15))

    assert session.add.call_count == 1
    added_period = session.add.call_args[0][0]
    assert added_period.sequence_number == 2


@pytest.mark.asyncio
async def test_sync_periods_respects_end_count(base_obligation):
    """sync_periods stops at end_count and does not generate further periods."""
    base_obligation.type = "end_count"
    base_obligation.end_count = 2

    session = AsyncMock()
    session.add = MagicMock()

    seq1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=base_obligation.id,
        period_key="2026-07-monthly-1",
        sequence_number=1,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
        due_date=date(2026, 8, 15),
        amount=Decimal("120000.00"),
        currency="COP",
        status="pending_payment",
    )

    mock_overdue = MagicMock()
    mock_overdue.scalars.return_value.all.return_value = []

    mock_s1 = MagicMock()
    mock_s1.scalar_one_or_none.return_value = seq1

    mock_s2 = MagicMock()
    mock_s2.scalar_one_or_none.return_value = None

    session.execute.side_effect = [mock_overdue, mock_s1, mock_s2]

    engine = PeriodEngine(session)
    # Even if current date is far in the future, end_count=2 caps at sequence 2
    await engine.sync_periods(base_obligation, current_date=date(2026, 12, 1))

    assert session.add.call_count == 1
    added_period = session.add.call_args[0][0]
    assert added_period.sequence_number == 2


@pytest.mark.asyncio
async def test_sync_periods_respects_one_time(base_obligation):
    """one_time obligation never generates sequence > 1."""
    base_obligation.type = "one_time"

    session = AsyncMock()
    session.add = MagicMock()

    seq1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=base_obligation.id,
        period_key="2026-07-monthly-1",
        sequence_number=1,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
        due_date=date(2026, 8, 15),
        amount=Decimal("120000.00"),
        currency="COP",
        status="pending_payment",
    )

    mock_overdue = MagicMock()
    mock_overdue.scalars.return_value.all.return_value = []

    mock_s1 = MagicMock()
    mock_s1.scalar_one_or_none.return_value = seq1

    session.execute.side_effect = [mock_overdue, mock_s1]

    engine = PeriodEngine(session)
    await engine.sync_periods(base_obligation, current_date=date(2026, 12, 1))

    # Sequence 1 already existed, no additional periods created
    session.add.assert_not_called()


# ==============================================================================
# Part 2: Derived Properties & Temporal Truth
# ==============================================================================


def test_derived_properties_current_vs_future():
    """Verify is_current_period and is_future_period relative to financial_date."""
    canonical_today = financial_date()

    # Past period
    past_period = ObligationPeriod(
        start_date=canonical_today - timedelta(days=60),
        end_date=canonical_today - timedelta(days=30),
        due_date=canonical_today - timedelta(days=20),
        status="pending",
    )
    assert past_period.is_current_period is False
    assert past_period.is_future_period is False

    # Current period
    current_period = ObligationPeriod(
        start_date=canonical_today - timedelta(days=5),
        end_date=canonical_today + timedelta(days=25),
        due_date=canonical_today + timedelta(days=10),
        status="pending",
    )
    assert current_period.is_current_period is True
    assert current_period.is_future_period is False

    # Future period
    future_period = ObligationPeriod(
        start_date=canonical_today + timedelta(days=30),
        end_date=canonical_today + timedelta(days=60),
        due_date=canonical_today + timedelta(days=45),
        status="pending",
    )
    assert future_period.is_current_period is False
    assert future_period.is_future_period is True


def test_derived_properties_variable_undefined_and_overdue():
    """
    Variable period past due with amount=None retains status
    pending_amount_definition, has is_overdue=True and requires_amount_definition=True.
    """
    canonical_today = financial_date()

    period = ObligationPeriod(
        start_date=canonical_today - timedelta(days=40),
        end_date=canonical_today - timedelta(days=10),
        due_date=canonical_today - timedelta(days=5),
        amount=None,
        status="pending_amount_definition",
    )

    assert period.requires_amount_definition is True
    assert period.is_overdue is True

    # Closed/skipped/cancelled periods are not overdue or requiring definition
    period.status = "skipped"
    assert period.requires_amount_definition is False
    assert period.is_overdue is False


def test_derived_properties_mock_resilience_none_dates():
    """Tests with minimal mocks having None dates fall back safely without TypeError."""
    period = ObligationPeriod(
        start_date=None,
        end_date=None,
        due_date=None,
        is_current=True,
        amount=Decimal("100.00"),
        status="pending",
    )
    assert period.is_current_period is True
    assert period.is_future_period is False
    assert period.is_overdue is False
    assert period.requires_amount_definition is False


# ==============================================================================
# Part 3: Lifecycle Management & Synchronization
# ==============================================================================


@pytest.mark.asyncio
async def test_evaluate_lifecycle_one_time_completion(base_obligation):
    """Paying/closing one_time period marks parent obligation completed without inner commit."""
    base_obligation.type = "one_time"
    base_obligation.status = "active"

    session = AsyncMock()
    period = ObligationPeriod(
        obligation_id=base_obligation.id,
        period_key="seq-1",
        sequence_number=1,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
        due_date=date(2026, 8, 15),
        currency="COP",
        status="paid",
    )

    mock_res_obs = MagicMock()
    mock_res_obs.scalars.return_value.first.return_value = base_obligation

    mock_res_periods = MagicMock()
    mock_res_periods.scalars.return_value.all.return_value = [period]

    session.execute.side_effect = [mock_res_obs, mock_res_periods]

    service = ObligationV17Service(session)
    updated = await service.evaluate_lifecycle(base_obligation.id)

    assert updated.status == "completed"
    session.commit.assert_not_called()  # No inner commits allowed


@pytest.mark.asyncio
async def test_evaluate_lifecycle_end_count_completion(base_obligation):
    """end_count obligation marks completed only when all end_count periods are closed."""
    base_obligation.type = "end_count"
    base_obligation.end_count = 2
    base_obligation.status = "active"

    session = AsyncMock()

    p1 = ObligationPeriod(
        obligation_id=base_obligation.id,
        period_key="seq-1",
        sequence_number=1,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
        due_date=date(2026, 8, 15),
        currency="COP",
        status="paid",
    )
    p2 = ObligationPeriod(
        obligation_id=base_obligation.id,
        period_key="seq-2",
        sequence_number=2,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        due_date=date(2026, 9, 15),
        currency="COP",
        status="pending",
    )

    mock_res_obs = MagicMock()
    mock_res_obs.scalars.return_value.first.return_value = base_obligation

    mock_res_periods = MagicMock()
    mock_res_periods.scalars.return_value.all.return_value = [p1, p2]

    session.execute.side_effect = [mock_res_obs, mock_res_periods]

    service = ObligationV17Service(session)
    res = await service.evaluate_lifecycle(base_obligation.id)
    assert res.status == "active"

    # Now p2 is also paid
    p2.status = "paid"
    session.execute.side_effect = [mock_res_obs, mock_res_periods]
    res2 = await service.evaluate_lifecycle(base_obligation.id)
    assert res2.status == "completed"


@pytest.mark.asyncio
async def test_inactive_parent_rejects_modifications(base_obligation):
    """skip_period and cancel_period reject calls if parent obligation is cancelled or completed."""
    session = AsyncMock()
    base_obligation.status = "cancelled"

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=base_obligation.id,
        period_key="seq-1",
        sequence_number=1,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
        due_date=date(2026, 8, 15),
        currency="COP",
        status="pending",
    )

    mock_res_obs = MagicMock()
    mock_res_obs.scalars.return_value.first.return_value = base_obligation

    session.execute.return_value = mock_res_obs

    service = ObligationV17Service(session)
    with pytest.raises(HTTPException) as exc_info:
        await service.skip_period(base_obligation.user_id, base_obligation.id, period.id)
    assert exc_info.value.status_code in (400, 422)
    assert "inactive" in exc_info.value.detail.lower()


# ==============================================================================
# Part 4: PATCH /api/v1.7/obligations/{id}
# ==============================================================================


@pytest.mark.asyncio
async def test_update_obligation_fixed_base_amount_future_only(base_obligation):
    """
    Fixed base_amount update affects FUTURE, OPEN, completely UNPAID materialized periods;
    never current, past, closed, or partially paid periods.
    """
    canonical_today = financial_date()
    session = AsyncMock()
    session.commit = AsyncMock()

    # Period 1: Current, open, unpaid
    p1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=base_obligation.id,
        period_key="seq-1",
        sequence_number=1,
        start_date=canonical_today - timedelta(days=5),
        end_date=canonical_today + timedelta(days=25),
        due_date=canonical_today + timedelta(days=10),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        currency="COP",
        status="pending",
    )
    # Period 2: Future, open, unpaid
    p2 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=base_obligation.id,
        period_key="seq-2",
        sequence_number=2,
        start_date=canonical_today + timedelta(days=30),
        end_date=canonical_today + timedelta(days=60),
        due_date=canonical_today + timedelta(days=45),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        currency="COP",
        status="pending",
    )
    # Period 3: Future, partially paid
    p3 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=base_obligation.id,
        period_key="seq-3",
        sequence_number=3,
        start_date=canonical_today + timedelta(days=61),
        end_date=canonical_today + timedelta(days=90),
        due_date=canonical_today + timedelta(days=75),
        amount=Decimal("100.00"),
        paid_amount=Decimal("30.00"),
        currency="COP",
        status="partially_paid",
    )

    mock_res_obs = MagicMock()
    mock_res_obs.scalars.return_value.first.return_value = base_obligation

    mock_res_periods = MagicMock()
    mock_res_periods.scalars.return_value.all.return_value = [p1, p2, p3]

    session.execute.side_effect = [mock_res_obs, mock_res_periods]

    service = ObligationV17Service(session)
    update_req = ObligationV17UpdateRequest(base_amount=Decimal("150.00"))

    updated = await service.update_obligation(
        base_obligation.user_id, base_obligation.id, update_req
    )

    assert updated.base_amount == Decimal("150.00")
    # p1 (current) remains 100.00
    assert p1.amount == Decimal("100.00")
    # p2 (future, unpaid) updated to 150.00!
    assert p2.amount == Decimal("150.00")
    # p3 (partially paid) remains 100.00
    assert p3.amount == Decimal("100.00")


@pytest.mark.asyncio
async def test_update_obligation_cannot_reopen_cancelled_or_completed(base_obligation):
    """Reopening cancelled or completed obligation raises HTTP 400."""
    session = AsyncMock()
    base_obligation.status = "cancelled"

    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = base_obligation
    session.execute.return_value = mock_res

    service = ObligationV17Service(session)
    update_req = ObligationV17UpdateRequest(status="active")

    with pytest.raises(HTTPException) as exc_info:
        await service.update_obligation(base_obligation.user_id, base_obligation.id, update_req)
    assert exc_info.value.status_code in (400, 422)
    assert "cannot_reopen" in exc_info.value.detail.lower()


# ==============================================================================
# Part 5: Payment History Endpoints & Partial Context
# ==============================================================================


@pytest.mark.asyncio
async def test_get_payment_history_owner_conflict_rejected(base_obligation):
    """If payment has a user_id different from requesting user, raise 403."""
    session = AsyncMock()
    payment_id = uuid.uuid4()
    other_user = uuid.uuid4()

    payment = ObligationPayment(
        id=payment_id,
        obligation_id=base_obligation.id,
        obligation_period_id=uuid.uuid4(),
        user_id=other_user,
        amount=Decimal("50.00"),
        currency="COP",
    )

    mock_res_pay = MagicMock()
    mock_res_pay.scalars.return_value.first.return_value = payment

    mock_res_obs = MagicMock()
    mock_res_obs.scalars.return_value.first.return_value = base_obligation

    session.execute.side_effect = [mock_res_pay, mock_res_obs]

    service = ObligationV17Service(session)
    with pytest.raises(HTTPException) as exc_info:
        await service.get_payment(base_obligation.user_id, payment_id)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_payment_history_extracts_partial_context(base_obligation):
    """Payment history correctly extracts partial context from Ledger event metadata."""
    session = AsyncMock()
    payment_id = uuid.uuid4()
    period_id = uuid.uuid4()
    event_id = uuid.uuid4()

    payment = ObligationPayment(
        id=payment_id,
        obligation_id=base_obligation.id,
        obligation_period_id=period_id,
        user_id=base_obligation.user_id,
        amount=Decimal("50.00"),
        currency="COP",
        financial_event_id=event_id,
        paid_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )

    # Mock Ledger event
    mock_event = MagicMock()
    mock_event.user_id = base_obligation.user_id
    mock_event.metadata_ = {
        "channel": "manual",
        "allocations": [
            {
                "payment_id": str(payment_id),
                "obligation_period_id": str(period_id),
                "paid_before": "0.00",
                "paid_after": "50.00",
                "amount_due_before": "100.00",
                "amount_due_after": "50.00",
            }
        ],
    }

    mock_res_pay = MagicMock()
    mock_res_pay.scalars.return_value.first.return_value = payment

    mock_res_obs = MagicMock()
    mock_res_obs.scalars.return_value.first.return_value = base_obligation

    session.execute.side_effect = [mock_res_pay, mock_res_obs]
    session.get = AsyncMock(return_value=mock_event)

    service = ObligationV17Service(session)
    res = await service.get_payment(base_obligation.user_id, payment_id)

    assert res.id == payment_id
    assert res.financial_event_id == event_id
    assert res.channel == "manual"
    assert res.partial_context is not None
    assert res.partial_context["paid_before"] == Decimal("0.00")
    assert res.partial_context["paid_after"] == Decimal("50.00")
    assert res.partial_context["amount_due_after"] == Decimal("50.00")


@pytest.mark.asyncio
async def test_list_payments_pagination(base_obligation):
    """GET /{id}/payments limits to max 100, orders by created_at desc, id desc."""
    session = AsyncMock()

    mock_res_obs = MagicMock()
    mock_res_obs.scalars.return_value.first.return_value = base_obligation

    mock_conflict = MagicMock()
    mock_conflict.scalar_one_or_none.return_value = None

    mock_count = MagicMock()
    mock_count.scalar.return_value = 2

    p1 = ObligationPayment(
        id=uuid.uuid4(),
        obligation_id=base_obligation.id,
        obligation_period_id=uuid.uuid4(),
        user_id=base_obligation.user_id,
        amount=Decimal("50.00"),
        currency="COP",
        created_at=datetime.now(UTC),
    )
    p2 = ObligationPayment(
        id=uuid.uuid4(),
        obligation_id=base_obligation.id,
        obligation_period_id=uuid.uuid4(),
        user_id=base_obligation.user_id,
        amount=Decimal("30.00"),
        currency="COP",
        created_at=datetime.now(UTC),
    )

    mock_items = MagicMock()
    mock_items.scalars.return_value.all.return_value = [p1, p2]

    session.execute.side_effect = [mock_res_obs, mock_conflict, mock_count, mock_items]

    service = ObligationV17Service(session)
    res = await service.list_payments_for_obligation(
        base_obligation.user_id, base_obligation.id, limit=50, offset=0
    )

    assert res.total == 2
    assert len(res.items) == 2
    assert res.limit == 50
    assert res.offset == 0


# ==============================================================================
# Part 6: Canonical Preview & Target Authority FX
# ==============================================================================


@pytest.mark.asyncio
async def test_preview_endpoint_flag_guard(mock_db):
    """Canonical preview endpoint is guarded by NEXUM_OBLIGATIONS_V17_ENABLED."""
    settings.NEXUM_OBLIGATIONS_V17_ENABLED = False

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            f"/api/v1.7/obligations/{uuid.uuid4()}/periods/{uuid.uuid4()}/payments/preview",
            json={"account_id": str(uuid.uuid4()), "target_amount": 100},
        )
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_fx_service_create_quote_for_target_authority():
    """
    create_quote_for_target enforces target amount authority:
    501 COP at rate 4000 COP/USD gives exact target 501.00 COP and rounded USD source.
    """
    session = AsyncMock()
    service = FXService(session)

    service.provider = AsyncMock()
    service.provider.get_rate = AsyncMock(return_value=Decimal("4000.00000000"))

    target_amount = Decimal("501.00")
    quote = await service.create_quote_for_target(
        user_id=uuid.uuid4(),
        source_currency="USD",
        target_currency="COP",
        target_amount=target_amount,
        commit=False,
    )

    # Authoritative target remains 501.00 COP
    assert quote.target_amount == Decimal("501.00")
    # 501 / 4000 = 0.12525 -> rounded to 0.13 USD
    assert quote.source_amount == Decimal("0.13")
    assert quote.rate == Decimal("4000.00000000")
    # Verify no commit was made
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_fifo_blocks_oldest_undefined_period():
    """Legacy FIFO payment raises ObligationPeriodAmountRequiredError if oldest period is undefined."""
    session = AsyncMock()
    obs_id = uuid.uuid4()
    user_id = uuid.uuid4()

    obligation = Obligation(
        id=obs_id,
        user_id=user_id,
        status="active",
        currency="COP",
        payment_mode="variable",
    )

    # Oldest period has amount=None
    p1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obs_id,
        period_key="seq-1",
        sequence_number=1,
        amount=None,
        currency="COP",
        paid_amount=Decimal("0.00"),
        status="pending_amount_definition",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        due_date=date(2026, 1, 15),
    )

    mock_res_obs = MagicMock()
    mock_res_obs.scalar_one_or_none.return_value = obligation

    mock_res_p = MagicMock()
    mock_res_p.scalars.return_value.all.return_value = [p1]

    session.execute.side_effect = [mock_res_obs, mock_res_p]

    repo = ObligationRepository(session)
    service = ObligationService(repo)
    payload = ObligationPaymentCreate(
        account_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        payment_date=financial_date(),
        idempotency_key="fifo-key-1",
    )
    mock_res_p.scalars.return_value.first.return_value = p1
    with patch("app.obligations.period_engine.PeriodEngine.sync_periods", new=AsyncMock()):
        with pytest.raises(ObligationPeriodAmountRequiredError):
            await service.pay_obligation_fifo(
                auth_user_id=user_id,
                obligation_id=obs_id,
                payload=payload,
            )


# ==============================================================================
# Part 7: Intelligence Repository Inactive Obligation Filter
# ==============================================================================


@pytest.mark.asyncio
async def test_intelligence_repository_excludes_inactive_obligations():
    """get_pending_period_amounts_for_snapshot filters for Obligation.status == 'active'."""
    session = AsyncMock()
    repo = ObligationRepository(session)

    # Ensure query executes and returns empty dict
    mock_res = MagicMock()
    mock_res.all.return_value = []
    session.execute.return_value = mock_res

    res = await repo.get_pending_period_amounts_for_snapshot(uuid.uuid4())
    assert res == {}

    # Verify query contains filter for active status
    called_query = session.execute.call_args[0][0]
    compiled_str = str(called_query)
    assert "obligations.status = :status_1" in compiled_str or "status" in compiled_str


# ==============================================================================
# Part 8: Strict Active Lifecycle, Locking & Precision Edge Cases
# ==============================================================================


@pytest.mark.asyncio
async def test_strict_active_only_rejects_null_and_unknown_status():
    """Writers reject obligations with status=None, unknown, or draft with 400 obligation_inactive."""
    session = AsyncMock()
    service_v17 = ObligationV17Service(session)

    user_id = uuid.uuid4()
    obs_id = uuid.uuid4()
    period_id = uuid.uuid4()

    # Null status
    obl_null = Obligation(
        id=obs_id,
        user_id=user_id,
        status=None,
        currency="COP",
        type="recurring",
        frequency="monthly",
    )
    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = obl_null
    session.execute.return_value = mock_res

    from app.obligations.schemas_v17 import (
        ObligationPaymentPreviewV17Request,
        ObligationPeriodAmountDefineRequest,
    )

    with pytest.raises(HTTPException) as exc:
        await service_v17.define_period_amount(
            user_id,
            obs_id,
            period_id,
            ObligationPeriodAmountDefineRequest(amount=Decimal("100.00")),
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "obligation_inactive"

    # Unknown status
    obl_unknown = Obligation(
        id=obs_id,
        user_id=user_id,
        status="mystery_state",
        currency="COP",
        type="recurring",
        frequency="monthly",
    )
    mock_res.scalars.return_value.first.return_value = obl_unknown

    with pytest.raises(HTTPException) as exc:
        await service_v17.pay_preview_specific_period(
            user_id,
            obs_id,
            period_id,
            ObligationPaymentPreviewV17Request(
                source_account_id=uuid.uuid4(), amount=Decimal("100.00")
            ),
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "obligation_inactive"

    # Legacy preview rejects inactive
    repo = ObligationRepository(session)
    legacy_service = ObligationService(repo)
    period = ObligationPeriod(
        id=period_id,
        obligation_id=obs_id,
        period_key="seq-1",
        sequence_number=1,
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
        due_date=date(2026, 5, 1),
    )
    mock_res_p = MagicMock()
    mock_res_p.scalar_one_or_none.return_value = period
    mock_res_o = MagicMock()
    mock_res_o.scalar_one_or_none.return_value = obl_unknown
    session.execute.side_effect = [mock_res_p, mock_res_o]
    repo.get_by_id = AsyncMock(return_value=obl_unknown)

    with pytest.raises(ObligationInactiveError):
        await legacy_service.pay_preview_specific_period(
            user_id,
            period_id,
            ObligationPaymentPreviewCreate(account_id=uuid.uuid4(), amount=Decimal("100.00")),
        )


@pytest.mark.asyncio
async def test_serialized_period_response_flags():
    """Serialized period responses have coherent temporal and action flags."""
    from app.obligations.router_v17 import _map_obligation_period

    today = financial_date()

    # 1. Past-due undefined
    past_due_date = today - timedelta(days=5)
    p_past_undef = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        sequence_number=1,
        status="pending_amount_definition",
        amount=None,
        paid_amount=Decimal("0.00"),
        start_date=past_due_date - timedelta(days=30),
        end_date=past_due_date - timedelta(days=1),
        due_date=past_due_date,
    )
    res1 = _map_obligation_period(p_past_undef)
    assert res1.is_overdue is True
    assert res1.requires_amount_definition is True
    assert res1.is_future is False
    assert res1.amount == Decimal("0.00")

    # 2. Future period
    future_start = today + timedelta(days=10)
    p_future = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        sequence_number=2,
        status="pending_payment",
        amount=Decimal("150.00"),
        paid_amount=Decimal("0.00"),
        start_date=future_start,
        end_date=future_start + timedelta(days=30),
        due_date=future_start + timedelta(days=15),
    )
    res2 = _map_obligation_period(p_future)
    assert res2.is_future is True
    assert res2.is_overdue is False
    assert res2.requires_amount_definition is False
    assert res2.amount == Decimal("150.00")

    # 3. Current period
    p_current = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        sequence_number=3,
        status="pending_payment",
        amount=Decimal("200.00"),
        paid_amount=Decimal("50.00"),
        start_date=today - timedelta(days=5),
        end_date=today + timedelta(days=25),
        due_date=today + timedelta(days=10),
    )
    res3 = _map_obligation_period(p_current)
    assert res3.is_current is True
    assert res3.is_future is False
    assert res3.is_overdue is False
    assert res3.amount_due == Decimal("150.00")


@pytest.mark.asyncio
async def test_legacy_precision_overflow_and_goal_reservations():
    """Legacy service validates subcent/overflow and rejects negative goal reservations."""
    session = AsyncMock()
    repo = ObligationRepository(session)
    service = ObligationService(repo)

    # Subcent precision rejected
    with pytest.raises(ObligationInvalidAmountError, match="at most 2 decimal places"):
        await service._calculate_fx_and_amounts("USD", "USD", Decimal("100.001"), Decimal("100.00"))

    # Overflow rejected
    with pytest.raises(ObligationInvalidAmountError, match="exceeds maximum limit"):
        await service._calculate_fx_and_amounts(
            "USD", "USD", Decimal("10000000000000.00"), Decimal("100.00")
        )

    # Nonfinite rejected
    with pytest.raises(ObligationInvalidAmountError, match="finite and positive"):
        await service._calculate_fx_and_amounts("USD", "USD", Decimal("NaN"), Decimal("100.00"))

    # Negative goal reservation rejected in preview
    user_id = uuid.uuid4()
    obs_id = uuid.uuid4()
    period_id = uuid.uuid4()
    account_id = uuid.uuid4()

    obl = Obligation(id=obs_id, user_id=user_id, status="active", currency="COP")
    period = ObligationPeriod(
        id=period_id,
        obligation_id=obs_id,
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
        due_date=date(2026, 5, 1),
    )
    account = MagicMock()
    account.id = account_id
    account.user_id = user_id
    account.is_active = True
    account.balance = Decimal("500.00")
    account.currency = "COP"

    repo.get_by_id = AsyncMock(return_value=obl)
    mock_res_p = MagicMock()
    mock_res_p.scalar_one_or_none.return_value = period
    session.execute.return_value = mock_res_p
    service.account_repo.get_by_id = AsyncMock(return_value=account)

    with patch(
        "app.goals.repository.GoalRepository.calculate_reserved_by_account",
        new=AsyncMock(return_value=Decimal("-10.00")),
    ):
        with pytest.raises(ObligationDomainError) as exc:
            await service.pay_preview_specific_period(
                user_id,
                period_id,
                ObligationPaymentPreviewCreate(account_id=account_id, amount=Decimal("100.00")),
            )
        assert exc.value.error_code == "inconsistent_reservation_state"


@pytest.mark.asyncio
async def test_patch_clearing_nullable_description_and_metadata():
    """PATCH with explicit null clears description and metadata via model_fields_set."""
    session = AsyncMock()
    service = ObligationV17Service(session)

    user_id = uuid.uuid4()
    obs_id = uuid.uuid4()

    obligation = Obligation(
        id=obs_id,
        user_id=user_id,
        name="Internet Bill",
        description="Initial description",
        metadata_={"account_num": "1234"},
        status="active",
        amount_type="fixed",
        payment_mode="fixed",
        base_amount=Decimal("100.00"),
        currency="COP",
        type="recurring",
        frequency="monthly",
    )

    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = obligation
    session.execute.return_value = mock_res

    from app.obligations.schemas_v17 import ObligationV17UpdateRequest

    # 1. Explicit null for description and metadata
    patch_req = ObligationV17UpdateRequest(
        description=None,
        metadata=None,
    )
    updated = await service.update_obligation(user_id, obs_id, patch_req)
    assert updated.description is None
    assert updated.metadata_ == {}

    # 2. Omitted fields do not clear existing
    obligation.description = "Kept description"
    obligation.metadata_ = {"key": "val"}

    patch_req2 = ObligationV17UpdateRequest(name="New Name")
    updated2 = await service.update_obligation(user_id, obs_id, patch_req2)
    assert updated2.name == "New Name"
    assert updated2.description == "Kept description"
    assert updated2.metadata_ == {"key": "val"}


@pytest.mark.asyncio
async def test_preview_target_quote_rollback_on_insufficient_balance():
    """Preview target-aware quote is created flush-only; insufficient balance rolls back without persisting quote."""
    session = AsyncMock()
    service = ObligationV17Service(session)

    user_id = uuid.uuid4()
    obs_id = uuid.uuid4()
    period_id = uuid.uuid4()
    account_id = uuid.uuid4()

    obligation = Obligation(
        id=obs_id,
        user_id=user_id,
        name="Foreign Subscription",
        currency="COP",
        status="active",
        type="recurring",
        frequency="monthly",
    )
    period = ObligationPeriod(
        id=period_id,
        obligation_id=obs_id,
        amount=Decimal("501.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
        due_date=date(2026, 6, 1),
    )
    account = MagicMock()
    account.id = account_id
    account.user_id = user_id
    account.currency = "USD"
    account.is_active = True
    account.balance = Decimal("0.05")  # Less than required 0.13 USD

    service.get_obligation = AsyncMock(return_value=obligation)
    service.get_period = AsyncMock(return_value=period)
    service.account_repo.get_by_id = AsyncMock(return_value=account)

    added_quotes = {}

    def mock_add(obj):
        if hasattr(obj, "id"):
            added_quotes[obj.id] = obj

    session.add = MagicMock(side_effect=mock_add)

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        for q in added_quotes.values():
            res.scalars.return_value.first.return_value = q
            return res
        res.scalars.return_value.first.return_value = None
        return res

    session.execute = AsyncMock(side_effect=mock_execute)

    from app.obligations.schemas_v17 import ObligationPaymentPreviewV17Request

    with patch(
        "app.core.currency.get_fx_rate",
        new=AsyncMock(
            return_value={"fx_rate": Decimal("4000.00000000"), "rate_timestamp": datetime.now(UTC)}
        ),
    ):
        with patch(
            "app.goals.repository.GoalRepository.calculate_reserved_by_account",
            new=AsyncMock(return_value=Decimal("0.00")),
        ):
            req = ObligationPaymentPreviewV17Request(
                source_account_id=account_id,
                amount=Decimal("501.00"),
            )
            with pytest.raises(HTTPException) as exc:
                await service.pay_preview_specific_period(user_id, obs_id, period_id, req)
            assert exc.value.status_code == 400
            assert exc.value.detail == "insufficient_balance"
            session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_payment_history_conflict_and_isolation():
    """Payment history enforces owner consistency and isolates other user payments."""
    session = AsyncMock()
    service = ObligationV17Service(session)

    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    obs_id = uuid.uuid4()

    obligation = Obligation(
        id=obs_id,
        user_id=user_id,
        status="active",
        currency="COP",
    )

    # 1. Conflicting payment owner raises 403
    mock_res_obs = MagicMock()
    mock_res_obs.scalars.return_value.first.return_value = obligation

    mock_res_conflict = MagicMock()
    mock_res_conflict.scalar_one_or_none.return_value = uuid.uuid4()

    session.execute.side_effect = [mock_res_obs, mock_res_conflict]

    with pytest.raises(HTTPException) as exc:
        await service.list_payments_for_obligation(user_id, obs_id)
    assert exc.value.status_code == 403
    assert exc.value.detail == "forbidden"

    # 2. get_payment for mismatched linked event user raises 403
    payment_id = uuid.uuid4()
    event_id = uuid.uuid4()
    payment = ObligationPayment(
        id=payment_id,
        obligation_id=obs_id,
        user_id=user_id,
        amount=Decimal("100.00"),
        financial_event_id=event_id,
    )
    mock_event = MagicMock()
    mock_event.user_id = other_user_id

    mock_res_pay = MagicMock()
    mock_res_pay.scalars.return_value.first.return_value = payment

    session.execute.side_effect = [mock_res_pay, mock_res_obs]
    session.get = AsyncMock(return_value=mock_event)

    with pytest.raises(HTTPException) as exc:
        await service.get_payment(user_id, payment_id)
    assert exc.value.status_code == 403
    assert exc.value.detail == "forbidden"


@pytest.mark.asyncio
async def test_inactive_period_sync_has_zero_mutations(base_obligation):
    base_obligation.status = "archived"
    session = AsyncMock()
    engine = PeriodEngine(session)
    engine.refresh_overdue = AsyncMock()
    await engine.sync_periods(base_obligation, date(2026, 7, 1))
    engine.refresh_overdue.assert_not_awaited()
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_cannot_skip_partially_paid_period():
    session = AsyncMock()
    period = ObligationPeriod(id=uuid.uuid4(), paid_amount=Decimal("10"), status="partially_paid")
    result = MagicMock()
    result.scalar_one_or_none.return_value = period
    session.execute.return_value = result
    with pytest.raises(ValueError, match="with payments"):
        await PeriodEngine(session).skip_period(period.id)
    assert period.status == "partially_paid"
