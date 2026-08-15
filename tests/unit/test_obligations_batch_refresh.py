import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun import freeze_time

from app.obligations.enums_v17 import PeriodStatus
from app.obligations.models import Obligation
from app.obligations.service_v17 import ObligationV17Service


@pytest.fixture
def mock_db():
    mock_session = AsyncMock()

    def fake_add(obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    mock_session.add = MagicMock(side_effect=fake_add)
    mock_session.add_all = MagicMock()
    mock_session.expunge = MagicMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin_nested = MagicMock(return_value=cm)

    return mock_session


@pytest.mark.asyncio
async def test_batch_refresh_fills_missing_sequence_gap(mock_db):
    service = ObligationV17Service(mock_db)
    user_id = uuid.uuid4()
    obs_id = uuid.uuid4()

    obligation = Obligation(
        id=obs_id,
        user_id=str(user_id),
        name="Gap Test",
        currency="USD",
        base_amount=Decimal("100.00"),
        status="active",
        type="indefinite",
        frequency="monthly",
        payment_mode="fixed",
        start_date=date(2026, 1, 1),
        first_due_date=date(2026, 1, 5),
    )

    class MockRow:
        def __init__(self, obligation_id, sequence_number):
            self.obligation_id = obligation_id
            self.sequence_number = sequence_number

    def side_effect(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        print(f"STMT_STR: {stmt_str}")
        if "update obligation_periods" in stmt_str:
            mock_result.scalars.return_value.all.return_value = []
        elif "select" in stmt_str and "obligations.user_id" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [obligation]
        elif "select" in stmt_str and "obligation_periods.obligation_id in" in stmt_str:
            # Return sequence 1 and 3, missing 2
            mock_result.all.return_value = [MockRow(obs_id, 1), MockRow(obs_id, 3)]
        return mock_result

    mock_db.execute.side_effect = side_effect

    await service.refresh_due_periods_for_user(user_id)

    # Verify add_all was called
    mock_db.add_all.assert_called_once()
    added_periods = mock_db.add_all.call_args[0][0]

    # Only sequence 2 and sequences from 4 up to today's should be generated
    # (Assuming today is July 2026, it would generate 2, 4, 5, 6, 7)
    added_seqs = [p.sequence_number for p in added_periods]
    assert 2 in added_seqs
    assert 1 not in added_seqs
    assert 3 not in added_seqs


@pytest.mark.asyncio
async def test_batch_refresh_does_not_duplicate_periods(mock_db):
    service = ObligationV17Service(mock_db)
    user_id = uuid.uuid4()
    obs_id = uuid.uuid4()

    obligation = Obligation(
        id=obs_id,
        user_id=str(user_id),
        name="Dupe Test",
        currency="USD",
        base_amount=Decimal("100.00"),
        status="active",
        type="indefinite",
        frequency="monthly",
        payment_mode="fixed",
        start_date=date(2026, 1, 1),
        first_due_date=date(2026, 1, 5),
    )

    def side_effect(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        print(f"STMT_STR: {stmt_str}")
        if "update obligation_periods" in stmt_str:
            pass
        elif "select" in stmt_str and "obligations.user_id" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [obligation]
        elif "obligation_periods.obligation_id in" in stmt_str:
            # Empty list means nothing generated
            mock_result.all.return_value = []
        return mock_result

    mock_db.execute.side_effect = side_effect

    from sqlalchemy.exc import IntegrityError

    mock_db.flush.side_effect = IntegrityError("mock error", params=None, orig=None)

    # Should not raise exception
    await service.refresh_due_periods_for_user(user_id)

    assert mock_db.begin_nested.call_count == 1
    assert mock_db.expunge.call_count > 0


@pytest.mark.asyncio
async def test_batch_refresh_variable_obligation_creates_pending_amount_definition(mock_db):
    service = ObligationV17Service(mock_db)
    user_id = uuid.uuid4()
    obs_id = uuid.uuid4()

    obligation = Obligation(
        id=obs_id,
        user_id=str(user_id),
        name="Variable Test",
        currency="USD",
        base_amount=None,
        status="active",
        type="indefinite",
        frequency="monthly",
        payment_mode="variable",
        amount_type="variable",
        start_date=date(2026, 7, 1),
        first_due_date=date(2026, 7, 5),
    )

    def side_effect(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        print(f"STMT_STR: {stmt_str}")
        if "update obligation_periods" in stmt_str:
            pass
        elif "select" in stmt_str and "obligations.user_id" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [obligation]
        elif "obligation_periods.obligation_id in" in stmt_str:
            mock_result.all.return_value = []
        return mock_result

    mock_db.execute.side_effect = side_effect

    await service.refresh_due_periods_for_user(user_id)

    added_periods = mock_db.add_all.call_args[0][0]
    assert len(added_periods) > 0
    assert added_periods[0].status == PeriodStatus.pending_amount_definition.value
    assert added_periods[0].amount is None


@pytest.mark.asyncio
async def test_batch_refresh_fixed_obligation_creates_pending_payment_or_overdue(mock_db):
    service = ObligationV17Service(mock_db)
    user_id = uuid.uuid4()
    obs_id = uuid.uuid4()

    # Past obligation that should be overdue
    obligation = Obligation(
        id=obs_id,
        user_id=str(user_id),
        name="Fixed Test",
        currency="USD",
        base_amount=Decimal("100"),
        status="active",
        type="indefinite",
        frequency="monthly",
        payment_mode="fixed",
        amount_type="fixed",
        start_date=date(2020, 1, 1),
        first_due_date=date(2020, 1, 5),
        end_date=date(2020, 3, 1),  # Only up to March 2020 to prevent huge loop
    )

    def side_effect(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        print(f"STMT_STR: {stmt_str}")
        if "update obligation_periods" in stmt_str:
            pass
        elif "select" in stmt_str and "obligations.user_id" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [obligation]
        elif "obligation_periods.obligation_id in" in stmt_str:
            mock_result.all.return_value = []
        return mock_result

    mock_db.execute.side_effect = side_effect

    await service.refresh_due_periods_for_user(user_id)

    added_periods = mock_db.add_all.call_args[0][0]
    assert len(added_periods) > 0
    assert added_periods[0].status == PeriodStatus.overdue.value
    assert added_periods[0].amount == Decimal("100")


@pytest.mark.asyncio
async def test_summary_does_not_refresh_closed_obligations(mock_db):
    service = ObligationV17Service(mock_db)
    user_id = uuid.uuid4()

    def side_effect(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        print(f"STMT_STR: {stmt_str}")
        print(f"STMT_VAR: {stmt_str}")
        if "select" in stmt_str and "obligations.user_id = " in stmt_str:
            # We return NO active obligations (meaning they are all closed)
            mock_result.scalars.return_value.all.return_value = []
        elif "obligation_periods.obligation_id in" in stmt_str:
            mock_result.all.return_value = []
        return mock_result

    mock_db.execute.side_effect = side_effect

    await service.refresh_due_periods_for_user(user_id)

    # Should not call add_all because active_obls is empty
    mock_db.add_all.assert_not_called()


@pytest.mark.asyncio
@freeze_time("2026-07-15")
async def test_auto_refresh_is_idempotent(mock_db):
    service = ObligationV17Service(mock_db)
    user_id = uuid.uuid4()
    obs_id = uuid.uuid4()

    obligation = Obligation(
        id=obs_id,
        user_id=str(user_id),
        name="Idempotent Test",
        currency="USD",
        base_amount=Decimal("100"),
        status="active",
        type="indefinite",
        frequency="monthly",
        payment_mode="fixed",
        amount_type="fixed",
        start_date=date(2026, 7, 1),
        first_due_date=date(2026, 7, 5),
    )

    class MockRow:
        def __init__(self, obligation_id, sequence_number):
            self.obligation_id = obligation_id
            self.sequence_number = sequence_number

    def side_effect(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        print(f"STMT_STR: {stmt_str}")
        if "update obligation_periods" in stmt_str:
            pass
        elif "select" in stmt_str and "obligations.user_id =" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [obligation]
        elif "obligation_periods.obligation_id in" in stmt_str:
            # Return sequence 1 and 2 which covers current and next
            mock_result.all.return_value = [MockRow(obs_id, 1), MockRow(obs_id, 2)]
        return mock_result

    mock_db.execute.side_effect = side_effect

    await service.refresh_due_periods_for_user(user_id)

    # Because sequence 1 is already returned as existing, it should generate nothing
    mock_db.add_all.assert_not_called()
