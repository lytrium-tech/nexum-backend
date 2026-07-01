import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.obligations.models import Obligation, ObligationPeriod
from app.obligations.period_engine import PeriodEngine, calculate_period_bounds


@pytest.fixture
def base_obligation():
    return Obligation(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Test Obligation",
        currency="COP",
        type="indefinite",
        frequency="monthly",
        payment_mode="fixed",
        base_amount=Decimal("100.00"),
        start_date=date(2026, 1, 1),
        first_due_date=date(2026, 1, 15),
        due_day=15,
        due_month=1,
        interval_count=1,
        status="active"
    )

def test_monthly_bounds(base_obligation):
    # Sequence 1
    p_start, p_end, p_due = calculate_period_bounds(base_obligation, 1)
    assert p_start == date(2026, 1, 1)
    assert p_end == date(2026, 1, 31)
    assert p_due == date(2026, 1, 15)

    # Sequence 2
    p_start, p_end, p_due = calculate_period_bounds(base_obligation, 2)
    assert p_start == date(2026, 2, 1)
    assert p_end == date(2026, 2, 28)
    assert p_due == date(2026, 2, 15)

def test_monthly_due_day_31_february(base_obligation):
    base_obligation.first_due_date = date(2026, 1, 31)
    base_obligation.due_day = 31

    # Feb
    p_start, p_end, p_due = calculate_period_bounds(base_obligation, 2)
    assert p_due == date(2026, 2, 28)

def test_yearly_bounds(base_obligation):
    base_obligation.frequency = "yearly"
    base_obligation.due_month = 2
    base_obligation.due_day = 28
    base_obligation.first_due_date = date(2026, 2, 28)

    p_start, p_end, p_due = calculate_period_bounds(base_obligation, 1)
    assert p_start == date(2026, 1, 1)
    assert p_end == date(2026, 12, 31)
    assert p_due == date(2026, 2, 28)

    p_start, p_end, p_due = calculate_period_bounds(base_obligation, 2)
    assert p_due == date(2027, 2, 28)

def test_weekly_bounds(base_obligation):
    base_obligation.frequency = "weekly"
    base_obligation.first_due_date = date(2026, 1, 5) # due_offset = 4 days

    p_start, p_end, p_due = calculate_period_bounds(base_obligation, 1)
    assert p_start == date(2026, 1, 1)
    assert p_end == date(2026, 1, 7)
    assert p_due == date(2026, 1, 5)
    
    p_start, p_end, p_due = calculate_period_bounds(base_obligation, 2)
    assert p_start == date(2026, 1, 8)
    assert p_end == date(2026, 1, 14)
    assert p_due == date(2026, 1, 12)

def test_biweekly_bounds(base_obligation):
    base_obligation.frequency = "biweekly"
    base_obligation.first_due_date = date(2026, 1, 10) # due_offset = 9 days

    p_start, p_end, p_due = calculate_period_bounds(base_obligation, 1)
    assert p_start == date(2026, 1, 1)
    assert p_end == date(2026, 1, 14)
    assert p_due == date(2026, 1, 10)
    
    p_start, p_end, p_due = calculate_period_bounds(base_obligation, 2)
    assert p_start == date(2026, 1, 15)
    assert p_due == date(2026, 1, 24)

def test_one_time_bounds(base_obligation):
    base_obligation.frequency = "one_time"
    base_obligation.end_date = date(2026, 1, 15)

    p_start, p_end, p_due = calculate_period_bounds(base_obligation, 1)
    assert p_start == date(2026, 1, 1)
    assert p_end == date(2026, 1, 15)
    assert p_due == date(2026, 1, 15)

def test_find_sequence_for_date(base_obligation):
    engine = PeriodEngine(AsyncMock())
    # Before start
    assert engine._find_sequence_for_date(base_obligation, date(2025, 12, 31)) == 1
    # Monthly
    assert engine._find_sequence_for_date(base_obligation, date(2026, 1, 15)) == 1
    assert engine._find_sequence_for_date(base_obligation, date(2026, 2, 10)) == 2
    assert engine._find_sequence_for_date(base_obligation, date(2027, 1, 1)) == 13

    # Yearly
    base_obligation.frequency = "yearly"
    assert engine._find_sequence_for_date(base_obligation, date(2026, 12, 31)) == 1
    assert engine._find_sequence_for_date(base_obligation, date(2027, 1, 1)) == 2

@pytest.mark.asyncio
async def test_ensure_period_exists_new_fixed(base_obligation):
    session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_res
    engine = PeriodEngine(session)

    new_p = await engine._ensure_period_exists(base_obligation, 1, date(2026, 1, 1))
    assert new_p.amount == Decimal("100.00")
    assert new_p.status == "pending_payment"
    assert new_p.period_key == "2026-01"
    session.add.assert_called_once()
    session.flush.assert_called_once()

@pytest.mark.asyncio
async def test_ensure_period_exists_new_variable(base_obligation):
    base_obligation.payment_mode = "variable"
    base_obligation.base_amount = None
    
    session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_res
    engine = PeriodEngine(session)

    new_p = await engine._ensure_period_exists(base_obligation, 1, date(2026, 1, 1))
    assert new_p.amount is None
    assert new_p.status == "pending_amount_definition"

@pytest.mark.asyncio
async def test_ensure_period_exists_already_overdue(base_obligation):
    session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_res
    engine = PeriodEngine(session)

    # seq 1 due date is Jan 15. Current date is Feb 1. So it should be overdue upon creation.
    new_p = await engine._ensure_period_exists(base_obligation, 1, date(2026, 2, 1))
    assert new_p.status == "overdue"

@pytest.mark.asyncio
async def test_skip_period_success():
    session = AsyncMock()
    p = ObligationPeriod(id=uuid.uuid4(), status="pending_payment")
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = p
    session.execute.return_value = mock_res
    
    engine = PeriodEngine(session)
    await engine.skip_period(p.id)
    assert p.status == "skipped"

@pytest.mark.asyncio
async def test_skip_period_already_paid():
    session = AsyncMock()
    p = ObligationPeriod(id=uuid.uuid4(), status="paid")
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = p
    session.execute.return_value = mock_res
    
    engine = PeriodEngine(session)
    with pytest.raises(ValueError, match="Cannot skip period"):
        await engine.skip_period(p.id)

@pytest.mark.asyncio
async def test_sync_periods_idempotency(base_obligation):
    session = AsyncMock()
    
    # Mock refresh_overdue to do nothing
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_res
    
    # Ensure it only generates seq 1 and 2 if current date is 2026-01-01
    engine = PeriodEngine(session)
    
    # Let's mock _ensure_period_exists directly
    engine._ensure_period_exists = AsyncMock()
    
    await engine.sync_periods(base_obligation, date(2026, 1, 1))
    
    # Should be called for seq 1 and 2
    assert engine._ensure_period_exists.call_count == 2
    engine._ensure_period_exists.assert_any_call(base_obligation, 1, date(2026, 1, 1))
    engine._ensure_period_exists.assert_any_call(base_obligation, 2, date(2026, 1, 1))
