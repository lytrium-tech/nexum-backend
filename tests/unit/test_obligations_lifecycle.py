import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.obligations.models import Obligation, ObligationPeriod
from app.obligations.schemas import ObligationPeriodAmountUpdate
from app.obligations.service import ObligationService


@pytest.fixture
def mock_repo():
    return AsyncMock()

@pytest.fixture
def obligation_service(mock_repo):
    with patch("app.obligations.service.AccountRepository"), \
         patch("app.obligations.service.LedgerRepository"), \
         patch("app.obligations.service.LedgerService"):
        service = ObligationService(mock_repo)
        return service

@pytest.fixture
def test_user_id():
    return uuid.uuid4()

@pytest.fixture
def variable_obligation(test_user_id):
    return Obligation(
        id=uuid.uuid4(),
        user_id=test_user_id,
        name="Test Var",
        currency="COP",
        type="indefinite",
        frequency="monthly",
        payment_mode="variable",
        start_date=date.today(),
        first_due_date=date.today() + timedelta(days=5),
        status="active"
    )

@pytest.mark.asyncio
async def test_define_amount_pending_payment(obligation_service, mock_repo, test_user_id, variable_obligation):
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=variable_obligation.id,
        period_key="2026-07",
        sequence_number=1,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        due_date=date.today() + timedelta(days=5),
        amount=None,
        currency="COP",
        paid_amount=Decimal("0.00"),
        status="pending_amount_definition",
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    mock_repo.session.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = period
    mock_result.scalars.return_value.all.return_value = [period]
    mock_repo.session.execute.return_value = mock_result

    mock_repo.get_by_id.return_value = variable_obligation

    updated = await obligation_service.define_amount(test_user_id, period.id, ObligationPeriodAmountUpdate(amount=Decimal("150000")))

    assert updated.amount == Decimal("150000")
    assert updated.status == "pending_payment"
    assert variable_obligation.status == "active"

@pytest.mark.asyncio
async def test_define_amount_overdue(obligation_service, mock_repo, test_user_id, variable_obligation):
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=variable_obligation.id,
        period_key="2026-06",
        sequence_number=1,
        start_date=date.today() - timedelta(days=30),
        end_date=date.today(),
        due_date=date.today() - timedelta(days=15),
        amount=None,
        currency="COP",
        paid_amount=Decimal("0.00"),
        status="pending_amount_definition",
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    mock_repo.session.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = period
    mock_result.scalars.return_value.all.return_value = [period]
    mock_repo.session.execute.return_value = mock_result

    mock_repo.get_by_id.return_value = variable_obligation

    updated = await obligation_service.define_amount(test_user_id, period.id, ObligationPeriodAmountUpdate(amount=Decimal("150000")))

    assert updated.amount == Decimal("150000")
    assert updated.status == "overdue"

@pytest.mark.asyncio
async def test_define_amount_on_fixed_rejected(obligation_service, mock_repo, test_user_id):
    obl = Obligation(
        id=uuid.uuid4(),
        user_id=test_user_id,
        name="Test Fixed",
        currency="COP",
        type="indefinite",
        frequency="monthly",
        payment_mode="fixed",
        status="active"
    )
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obl.id,
        status="pending_payment"
    )

    mock_repo.session.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = period
    mock_repo.session.execute.return_value = mock_result
    
    mock_repo.get_by_id.return_value = obl

    with pytest.raises(ValueError, match="Only variable obligations can define amount"):
        await obligation_service.define_amount(test_user_id, period.id, ObligationPeriodAmountUpdate(amount=Decimal("150000")))

@pytest.mark.asyncio
async def test_define_amount_lower_than_paid_rejected(obligation_service, mock_repo, test_user_id, variable_obligation):
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=variable_obligation.id,
        status="partially_paid",
        paid_amount=Decimal("500.00")
    )
    mock_repo.session.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = period
    mock_repo.session.execute.return_value = mock_result
    mock_repo.get_by_id.return_value = variable_obligation

    with pytest.raises(ValueError, match="Amount cannot be less than already paid amount"):
        await obligation_service.define_amount(test_user_id, period.id, ObligationPeriodAmountUpdate(amount=Decimal("400")))

@pytest.mark.asyncio
async def test_one_time_lifecycle(obligation_service, mock_repo, test_user_id):
    obl = Obligation(
        id=uuid.uuid4(),
        user_id=test_user_id,
        type="one_time",
        frequency="one_time",
        status="active"
    )
    period = ObligationPeriod(id=uuid.uuid4(), status="paid", sequence_number=1)
    
    mock_repo.get_by_id.return_value = obl
    mock_repo.session.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [period]
    mock_repo.session.execute.return_value = mock_result
    
    await obligation_service.evaluate_lifecycle(obl.id)
    assert obl.status == "completed"

@pytest.mark.asyncio
async def test_end_count_lifecycle_completed(obligation_service, mock_repo, test_user_id):
    obl = Obligation(
        id=uuid.uuid4(),
        user_id=test_user_id,
        type="end_count",
        frequency="monthly",
        end_count=2,
        status="active"
    )
    p1 = ObligationPeriod(id=uuid.uuid4(), status="paid", sequence_number=1)
    p2 = ObligationPeriod(id=uuid.uuid4(), status="skipped", sequence_number=2)
    
    mock_repo.get_by_id.return_value = obl
    mock_repo.session.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [p1, p2]
    mock_repo.session.execute.return_value = mock_result
    
    await obligation_service.evaluate_lifecycle(obl.id)
    assert obl.status == "completed"

@pytest.mark.asyncio
async def test_end_count_lifecycle_active(obligation_service, mock_repo, test_user_id):
    obl = Obligation(
        id=uuid.uuid4(),
        user_id=test_user_id,
        type="end_count",
        frequency="monthly",
        end_count=2,
        status="active"
    )
    p1 = ObligationPeriod(id=uuid.uuid4(), status="paid", sequence_number=1)
    p2 = ObligationPeriod(id=uuid.uuid4(), status="pending_payment", sequence_number=2)
    
    mock_repo.get_by_id.return_value = obl
    mock_repo.session.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [p1, p2]
    mock_repo.session.execute.return_value = mock_result
    
    await obligation_service.evaluate_lifecycle(obl.id)
    assert obl.status == "active"

@pytest.mark.asyncio
async def test_end_date_lifecycle(obligation_service, mock_repo, test_user_id):
    obl = Obligation(
        id=uuid.uuid4(),
        user_id=test_user_id,
        type="end_date",
        frequency="monthly",
        end_date=date.today(),
        interval_count=1,
        start_date=date.today() - timedelta(days=30),
        first_due_date=date.today() - timedelta(days=30),
        due_day=15,
        status="active"
    )
    p1 = ObligationPeriod(id=uuid.uuid4(), status="paid", sequence_number=1)
    p2 = ObligationPeriod(id=uuid.uuid4(), status="paid", sequence_number=2)
    
    mock_repo.get_by_id.return_value = obl
    mock_repo.session.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [p1, p2]
    mock_repo.session.execute.return_value = mock_result
    
    await obligation_service.evaluate_lifecycle(obl.id)
    # The next period would start +2 months, which is > end_date. Since both are paid, it should complete.
    assert obl.status == "completed"

@pytest.mark.asyncio
async def test_indefinite_never_completes(obligation_service, mock_repo, test_user_id):
    obl = Obligation(
        id=uuid.uuid4(),
        user_id=test_user_id,
        type="indefinite",
        frequency="monthly",
        status="active"
    )
    p1 = ObligationPeriod(id=uuid.uuid4(), status="paid", sequence_number=1)
    
    mock_repo.get_by_id.return_value = obl
    mock_repo.session.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [p1]
    mock_repo.session.execute.return_value = mock_result
    
    await obligation_service.evaluate_lifecycle(obl.id)
    assert obl.status == "active"
