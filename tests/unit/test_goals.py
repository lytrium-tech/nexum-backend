import uuid
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.accounts.models import Account
from app.goals.exceptions import (
    GoalAmountExceededError,
    GoalDuplicateError,
    GoalTargetAmountError,
)
from app.goals.models import Goal
from app.goals.schemas import GoalContributionCreate, GoalCreate, GoalUpdate
from app.goals.service import GoalService


@pytest.fixture
def mock_goal_repo():
    return AsyncMock()


@pytest.fixture
def mock_user_service():
    return AsyncMock()


@pytest.fixture
def mock_account_repo():
    return AsyncMock()


@pytest.fixture
def mock_ledger_repo():
    return AsyncMock()


@pytest.fixture
def goal_service(mock_goal_repo, mock_user_service, mock_account_repo, mock_ledger_repo):
    return GoalService(mock_goal_repo, mock_user_service, mock_account_repo, mock_ledger_repo)


@pytest.mark.asyncio
async def test_create_goal_success(goal_service, mock_goal_repo):
    user_id = uuid.uuid4()
    payload = GoalCreate(
        name="  Viaje a japón  ", target_amount=Decimal("5000000"), target_date=date(2027, 1, 1)
    )

    mock_goal_repo.check_name_exists.return_value = False

    mock_goal = Goal(
        id=uuid.uuid4(),
        user_id=user_id,
        name="Viaje A Japón",
        target_amount=payload.target_amount,
        current_amount=Decimal("0"),
        progress_percentage=Decimal("0"),
        target_date=payload.target_date,
        is_active=True,
        status="active",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        monthly_required=Decimal("0"),
        daily_required=Decimal("0"),
    )
    mock_goal_repo.create.return_value = mock_goal

    result = await goal_service.create_goal(user_id, payload)

    assert result.name == "Viaje A Japón"
    assert result.target_amount == Decimal("5000000")
    assert result.current_amount == Decimal("0")
    mock_goal_repo.check_name_exists.assert_called_once_with(user_id, "viaje a japon")


@pytest.mark.asyncio
async def test_create_goal_duplicate_name(goal_service, mock_goal_repo):
    user_id = uuid.uuid4()
    payload = GoalCreate(name="Coche", target_amount=Decimal("1000"))

    mock_goal_repo.check_name_exists.return_value = True

    with pytest.raises(GoalDuplicateError):
        await goal_service.create_goal(user_id, payload)


@pytest.mark.asyncio
async def test_update_goal_target_less_than_current(goal_service, mock_goal_repo):
    user_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    payload = GoalUpdate(target_amount=Decimal("50"))

    mock_goal = Goal(
        user_id=user_id, current_amount=Decimal("100"), name="Test", target_amount=Decimal("200")
    )
    mock_goal_repo.get_by_id.return_value = mock_goal

    with pytest.raises(GoalTargetAmountError):
        await goal_service.update_goal(user_id, goal_id, payload)


@pytest.mark.asyncio
async def test_delete_goal_success(goal_service, mock_goal_repo):
    user_id = uuid.uuid4()
    goal_id = uuid.uuid4()

    mock_goal = Goal(user_id=user_id, is_active=True, status="active", name="Test")
    mock_goal_repo.get_by_id.return_value = mock_goal

    await goal_service.delete_goal(user_id, goal_id)

    assert mock_goal.is_active is False
    assert mock_goal.status == "cancelled"


@pytest.mark.asyncio
async def test_contribution_success(
    goal_service, mock_goal_repo, mock_account_repo, mock_ledger_repo
):
    user_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    account_id = uuid.uuid4()
    payload = GoalContributionCreate(account_id=account_id, amount=Decimal("100"))

    mock_goal = Goal(
        id=goal_id,
        user_id=user_id,
        is_active=True,
        status="active",
        target_amount=Decimal("500"),
        current_amount=Decimal("100"),
        progress_percentage=Decimal("20"),
    )
    mock_goal_repo.get_by_id_for_update.return_value = mock_goal

    mock_account = Account(id=account_id, user_id=user_id, balance=Decimal("1000"))
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_event = AsyncMock()
    mock_event.id = uuid.uuid4()
    mock_event.period = "2026-06"
    mock_result = AsyncMock()
    mock_result.event = mock_event
    mock_result.idempotent = False
    mock_ledger_repo.insert_event.return_value = mock_result

    result = await goal_service.create_contribution(user_id, goal_id, payload, "123")

    assert result.status == "success"
    assert result.amount == Decimal("100")
    assert mock_goal.current_amount == Decimal("200")
    mock_account_repo.update_balance.assert_called_once_with(mock_account, Decimal("-100"))


@pytest.mark.asyncio
async def test_contribution_amount_exceeded(goal_service, mock_goal_repo, mock_account_repo):
    user_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    account_id = uuid.uuid4()
    payload = GoalContributionCreate(account_id=account_id, amount=Decimal("450"))

    mock_goal = Goal(
        id=goal_id,
        user_id=user_id,
        is_active=True,
        status="active",
        target_amount=Decimal("500"),
        current_amount=Decimal("100"),
    )
    mock_goal_repo.get_by_id_for_update.return_value = mock_goal

    mock_account = Account(id=account_id, user_id=user_id, balance=Decimal("1000"))
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    with pytest.raises(GoalAmountExceededError):
        await goal_service.create_contribution(user_id, goal_id, payload, None)


@pytest.mark.asyncio
async def test_contribution_idempotent_retry(
    goal_service, mock_goal_repo, mock_account_repo, mock_ledger_repo
):
    user_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    account_id = uuid.uuid4()
    payload = GoalContributionCreate(account_id=account_id, amount=Decimal("100"))

    mock_goal = Goal(
        id=goal_id,
        user_id=user_id,
        is_active=True,
        status="active",
        target_amount=Decimal("500"),
        current_amount=Decimal("100"),
        progress_percentage=Decimal("20"),
    )
    mock_goal_repo.get_by_id_for_update.return_value = mock_goal

    mock_account = Account(id=account_id, user_id=user_id, balance=Decimal("1000"))
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_result = AsyncMock()
    mock_result.idempotent = True
    mock_result.event = None
    mock_ledger_repo.insert_event.return_value = mock_result
    result = await goal_service.create_contribution(user_id, goal_id, payload, "test-key")

    assert result.status == "idempotent_retry"
    assert result.goal_current_amount == Decimal("100")
    mock_account_repo.update_balance.assert_not_called()
