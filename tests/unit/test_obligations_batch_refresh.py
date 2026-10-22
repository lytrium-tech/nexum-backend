import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun import freeze_time

from app.obligations.enums_v17 import PeriodStatus
from app.obligations.models import Obligation, ObligationPeriod
from app.obligations.service_v17 import ObligationV17Service


@pytest.fixture
def mock_db():
    mock_session = AsyncMock()
    mock_session.added_periods = []

    def fake_add(obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        mock_session.added_periods.append(obj)

    def fake_add_all(objs):
        for obj in objs:
            fake_add(obj)

    mock_session.add = MagicMock(side_effect=fake_add)
    mock_session.add_all = MagicMock(side_effect=fake_add_all)
    mock_session.expunge = MagicMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin_nested = MagicMock(return_value=cm)

    return mock_session


@pytest.mark.asyncio
async def test_batch_refresh_fills_missing_sequence_gap(mock_db):
    """Batch refresh detects and fills sequence gaps using PeriodEngine."""
    service = ObligationV17Service(mock_db)
    user_id = uuid.uuid4()
    obs_id = uuid.uuid4()

    obligation = Obligation(
        id=obs_id,
        user_id=user_id,
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

    # Periods 1 and 3 exist; period 2 is missing
    p1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obs_id,
        sequence_number=1,
        status="pending_payment",
        amount=Decimal("100.00"),
        due_date=date(2026, 1, 5),
    )
    p3 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obs_id,
        sequence_number=3,
        status="pending_payment",
        amount=Decimal("100.00"),
        due_date=date(2026, 3, 5),
    )
    existing_by_seq = {1: p1, 3: p3}

    def side_effect(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        if "from obligations" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [obligation]
            mock_result.scalars.return_value.first.return_value = obligation
            return mock_result
        if "from obligation_periods" in stmt_str:
            if "sequence_number =" in stmt_str or "sequence_number = :" in stmt_str:
                for seq, p in existing_by_seq.items():
                    if (
                        f":sequence_number_1 = {seq}" in stmt_str
                        or f"sequence_number = {seq}" in stmt_str
                    ):
                        mock_result.scalar_one_or_none.return_value = p
                        return mock_result
                # By default check params if accessible, or return None
                mock_result.scalar_one_or_none.return_value = None
                return mock_result
            mock_result.scalars.return_value.all.return_value = list(existing_by_seq.values())
            return mock_result
        mock_result.scalars.return_value.all.return_value = []
        mock_result.scalar_one_or_none.return_value = None
        return mock_result

    # Better mock: inspect execution params for sequence_number
    def execute_mock(stmt, *args, **kwargs):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        if "from obligations" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [obligation]
            mock_result.scalars.return_value.first.return_value = obligation
            return mock_result
        if "from obligation_periods" in stmt_str:
            if "sequence_number" in stmt_str:
                # If sequence 1 or 3 is queried, return existing
                # Check kwargs or return according to seq
                seq = kwargs.get("sequence_number") or getattr(stmt, "_params", {}).get(
                    "sequence_number_1"
                )
                if seq in (1, 3):
                    mock_result.scalar_one_or_none.return_value = existing_by_seq[seq]
                else:
                    mock_result.scalar_one_or_none.return_value = None
                return mock_result
            mock_result.scalars.return_value.all.return_value = list(existing_by_seq.values())
            return mock_result
        mock_result.scalars.return_value.all.return_value = []
        mock_result.scalar_one_or_none.return_value = None
        return mock_result

    mock_db.execute.side_effect = execute_mock

    await service.refresh_due_periods_for_user(user_id)

    # Sequence 2 (the gap) must have been created
    added_seqs = [p.sequence_number for p in mock_db.added_periods]
    assert 2 in added_seqs


@pytest.mark.asyncio
async def test_batch_refresh_propagates_database_errors(mock_db):
    """Batch refresh does not swallow DB errors (e.g. IntegrityError)."""
    service = ObligationV17Service(mock_db)
    user_id = uuid.uuid4()
    obs_id = uuid.uuid4()

    obligation = Obligation(
        id=obs_id,
        user_id=user_id,
        name="Error Propagation Test",
        currency="USD",
        base_amount=Decimal("100.00"),
        status="active",
        type="indefinite",
        frequency="monthly",
        payment_mode="fixed",
        start_date=date(2026, 1, 1),
        first_due_date=date(2026, 1, 5),
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [obligation]
    mock_result.scalars.return_value.first.return_value = obligation
    mock_db.execute.return_value = mock_result

    from sqlalchemy.exc import IntegrityError

    mock_db.commit.side_effect = IntegrityError("duplicate key", params=None, orig=None)

    with pytest.raises(IntegrityError):
        await service.refresh_due_periods_for_user(user_id, commit=True)


@pytest.mark.asyncio
async def test_batch_refresh_variable_obligation_creates_pending_amount_definition(mock_db):
    """Variable obligation generates periods in pending_amount_definition status with amount=None."""
    service = ObligationV17Service(mock_db)
    user_id = uuid.uuid4()
    obs_id = uuid.uuid4()

    obligation = Obligation(
        id=obs_id,
        user_id=user_id,
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

    def side_effect(stmt, *args, **kwargs):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        if "from obligations" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [obligation]
            mock_result.scalars.return_value.first.return_value = obligation
            return mock_result
        mock_result.scalars.return_value.all.return_value = []
        mock_result.scalar_one_or_none.return_value = None
        return mock_result

    mock_db.execute.side_effect = side_effect

    await service.refresh_due_periods_for_user(user_id)

    assert len(mock_db.added_periods) > 0
    assert mock_db.added_periods[0].status == PeriodStatus.pending_amount_definition.value
    assert mock_db.added_periods[0].amount is None


@pytest.mark.asyncio
async def test_batch_refresh_fixed_obligation_creates_pending_payment_or_overdue(mock_db):
    """Past fixed obligation generates overdue periods with fixed amount."""
    service = ObligationV17Service(mock_db)
    user_id = uuid.uuid4()
    obs_id = uuid.uuid4()

    obligation = Obligation(
        id=obs_id,
        user_id=user_id,
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
        end_date=date(2020, 3, 1),
    )

    def side_effect(stmt, *args, **kwargs):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        if "from obligations" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [obligation]
            mock_result.scalars.return_value.first.return_value = obligation
            return mock_result
        mock_result.scalars.return_value.all.return_value = []
        mock_result.scalar_one_or_none.return_value = None
        return mock_result

    mock_db.execute.side_effect = side_effect

    await service.refresh_due_periods_for_user(user_id)

    assert len(mock_db.added_periods) > 0
    assert mock_db.added_periods[0].status == PeriodStatus.overdue.value
    assert mock_db.added_periods[0].amount == Decimal("100")


@pytest.mark.asyncio
async def test_summary_does_not_refresh_closed_obligations(mock_db):
    """Inactive obligations are untouched by batch refresh."""
    service = ObligationV17Service(mock_db)
    user_id = uuid.uuid4()

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    await service.refresh_due_periods_for_user(user_id)
    assert len(mock_db.added_periods) == 0


@pytest.mark.asyncio
@freeze_time("2026-07-15")
async def test_auto_refresh_is_idempotent(mock_db):
    """Auto-refresh generates nothing when all periods exist."""
    service = ObligationV17Service(mock_db)
    user_id = uuid.uuid4()
    obs_id = uuid.uuid4()

    obligation = Obligation(
        id=obs_id,
        user_id=user_id,
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

    p1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obs_id,
        sequence_number=1,
        status="pending_payment",
        amount=Decimal("100"),
    )
    p2 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obs_id,
        sequence_number=2,
        status="pending_payment",
        amount=Decimal("100"),
    )

    def side_effect(stmt, *args, **kwargs):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        if "from obligations" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [obligation]
            mock_result.scalars.return_value.first.return_value = obligation
            return mock_result
        if "from obligation_periods" in stmt_str:
            # All sequences return existing period
            mock_result.scalar_one_or_none.return_value = p1
            mock_result.scalars.return_value.all.return_value = [p1, p2]
            return mock_result
        mock_result.scalars.return_value.all.return_value = []
        mock_result.scalar_one_or_none.return_value = None
        return mock_result

    mock_db.execute.side_effect = side_effect

    await service.refresh_due_periods_for_user(user_id)
    assert len(mock_db.added_periods) == 0
