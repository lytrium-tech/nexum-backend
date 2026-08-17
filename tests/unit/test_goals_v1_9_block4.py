from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4, uuid5

import pytest
from sqlalchemy.exc import IntegrityError

from app.accounts.models import Account
from app.core.errors import ConflictError
from app.goals.enums import GoalTransactionType
from app.goals.models import (
    Goal,
    GoalAutoContributionRun,
    GoalAutoContributionSchedule,
    GoalTransaction,
)
from app.goals.schemas import GoalContributionResult
from app.goals.service import NAMESPACE_AUTOCONTRIB, GoalService
from app.ledger.enums import Direction, EventType
from app.ledger.models import FinancialEvent


@pytest.fixture
def mock_goal_repo():
    repository = AsyncMock()
    repository.session = MagicMock()
    nested = MagicMock()
    nested.__aenter__ = AsyncMock()
    nested.__aexit__ = AsyncMock(return_value=False)
    repository.session.begin_nested.return_value = nested
    repository.session.flush = AsyncMock()
    repository.session.commit = AsyncMock()
    repository.session.rollback = AsyncMock()
    return repository


@pytest.fixture
def mock_account_repo():
    return AsyncMock()


@pytest.fixture
def mock_ledger_repo():
    return AsyncMock()


@pytest.fixture
def goal_service(mock_goal_repo, mock_account_repo, mock_ledger_repo):
    return GoalService(mock_goal_repo, mock_account_repo, mock_ledger_repo)


@pytest.fixture
def auth_user_id():
    return uuid4()


@pytest.fixture
def base_time():
    return datetime(2026, 9, 14, 13, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_deterministic_uuid_same_inputs(base_time):
    schedule_id = uuid4()
    key1 = str(
        uuid5(NAMESPACE_AUTOCONTRIB, f"{schedule_id}|{base_time.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    )
    key2 = str(
        uuid5(NAMESPACE_AUTOCONTRIB, f"{schedule_id}|{base_time.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    )
    assert key1 == key2


@pytest.mark.asyncio
async def test_different_schedule_different_key(base_time):
    schedule_id1 = uuid4()
    schedule_id2 = uuid4()
    key1 = str(
        uuid5(NAMESPACE_AUTOCONTRIB, f"{schedule_id1}|{base_time.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    )
    key2 = str(
        uuid5(NAMESPACE_AUTOCONTRIB, f"{schedule_id2}|{base_time.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    )
    assert key1 != key2


@pytest.mark.asyncio
async def test_timezone_canonicalizes_consistently():
    schedule_id = uuid4()
    time_utc = datetime(2026, 9, 15, 13, 0, 0, tzinfo=UTC)
    time_est = datetime(2026, 9, 15, 9, 0, 0, tzinfo=timezone(timedelta(hours=-4)))

    key_utc = str(
        uuid5(
            NAMESPACE_AUTOCONTRIB,
            f"{schedule_id}|{time_utc.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        )
    )
    key_est = str(
        uuid5(
            NAMESPACE_AUTOCONTRIB,
            f"{schedule_id}|{time_est.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        )
    )
    assert key_utc == key_est


@pytest.mark.asyncio
async def test_successful_execution(
    mock_goal_repo, mock_account_repo, goal_service, auth_user_id, base_time
):
    schedule_id = uuid4()
    account_id = uuid4()
    goal_id = uuid4()

    mock_goal_repo.get_auto_contribution_run.return_value = None

    schedule = GoalAutoContributionSchedule(
        id=schedule_id,
        user_id=auth_user_id,
        goal_id=goal_id,
        account_id=account_id,
        amount=Decimal("100.00"),
        frequency="weekly",
        execution_day="monday",
        timezone="America/Bogota",
        start_date=base_time.date(),
        status="active",
        next_run_at=base_time,
    )
    mock_goal_repo.get_auto_contribution_schedule_by_id_for_update.return_value = schedule

    goal = Goal(
        id=goal_id,
        user_id=auth_user_id,
        target_amount=Decimal("500.00"),
        current_amount=Decimal("0.00"),
        currency="COP",
        status="active",
        is_active=True,
    )
    mock_goal_repo.get_by_id_for_update.return_value = goal

    account = Account(id=account_id, user_id=auth_user_id, currency="COP", is_active=True)
    mock_account_repo.get_by_id_for_update.return_value = account

    # Mock create_contribution
    contrib_result = GoalContributionResult(
        transaction_id=uuid4(),
        event_id=uuid4(),
        goal_id=goal_id,
        account_id=account_id,
        source_amount=Decimal("100.00"),
        source_currency="COP",
        applied_amount=Decimal("100.00"),
        goal_currency="COP",
        goal_current_amount=Decimal("100.00"),
        goal_remaining_amount=Decimal("400.00"),
        goal_status="active",
        account_balance=Decimal("900.00"),
        goal_reserved_amount=Decimal("100.00"),
        available_balance=Decimal("900.00"),
        progress_percentage=Decimal("20.00"),
        idempotent=False,
        created_at=base_time,
    )
    goal_service.create_contribution = AsyncMock(return_value=contrib_result)

    result = await goal_service.execute_auto_contribution_occurrence(
        schedule_id, base_time, base_time + timedelta(minutes=5)
    )

    assert result.status == "succeeded"
    assert result.result_code == "success"
    assert result.configured_amount == Decimal("100.00")
    assert result.executed_amount == Decimal("100.00")
    assert result.idempotent is False
    assert result.goal_transaction_id == contrib_result.transaction_id

    mock_goal_repo.create_auto_contribution_run.assert_called_once()
    run_arg = mock_goal_repo.create_auto_contribution_run.call_args[0][0]
    assert run_arg.status == "succeeded"
    assert run_arg.executed_amount == Decimal("100.00")
    assert run_arg.executed_at == base_time + timedelta(minutes=5)
    assert run_arg.executed_at != run_arg.scheduled_for
    assert schedule.next_run_at > base_time


@pytest.mark.asyncio
async def test_duplicate_run_replay(
    mock_goal_repo, mock_account_repo, goal_service, auth_user_id, base_time
):
    schedule_id = uuid4()
    run_id = uuid4()

    existing_run = GoalAutoContributionRun(
        id=run_id,
        schedule_id=schedule_id,
        scheduled_for=base_time,
        status="succeeded",
        result_code="success",
        configured_amount=Decimal("100.00"),
        executed_amount=Decimal("100.00"),
        goal_transaction_id=uuid4(),
    )
    mock_goal_repo.get_auto_contribution_run.return_value = existing_run

    result = await goal_service.execute_auto_contribution_occurrence(
        schedule_id, base_time, base_time
    )
    assert result.status == "succeeded"
    assert result.idempotent is True
    assert result.run_id == run_id
    mock_goal_repo.get_auto_contribution_schedule_by_id_for_update.assert_not_awaited()
    mock_goal_repo.create_auto_contribution_run.assert_not_awaited()
    goal_service.ledger_repo.insert_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_insufficient_funds_skipped(
    mock_goal_repo, mock_account_repo, goal_service, auth_user_id, base_time
):
    schedule_id = uuid4()
    account_id = uuid4()
    goal_id = uuid4()

    mock_goal_repo.get_auto_contribution_run.return_value = None

    schedule = GoalAutoContributionSchedule(
        id=schedule_id,
        user_id=auth_user_id,
        goal_id=goal_id,
        account_id=account_id,
        amount=Decimal("100.00"),
        frequency="weekly",
        execution_day="monday",
        timezone="America/Bogota",
        start_date=base_time.date(),
        status="active",
        next_run_at=base_time,
    )
    mock_goal_repo.get_auto_contribution_schedule_by_id_for_update.return_value = schedule

    goal = Goal(
        id=goal_id,
        user_id=auth_user_id,
        target_amount=Decimal("500.00"),
        current_amount=Decimal("0.00"),
        currency="COP",
        status="active",
        is_active=True,
    )
    mock_goal_repo.get_by_id_for_update.return_value = goal

    account = Account(
        id=account_id,
        user_id=auth_user_id,
        balance=Decimal("100.00"),
        currency="COP",
        is_active=True,
    )
    mock_account_repo.get_by_id_for_update.return_value = account
    mock_goal_repo.get_transaction_by_command_id.return_value = None
    mock_goal_repo.calculate_reserved_by_account.return_value = Decimal("50.00")

    result = await goal_service.execute_auto_contribution_occurrence(
        schedule_id, base_time, base_time
    )

    assert result.status == "skipped"
    assert result.result_code == "insufficient_available_balance"
    assert result.executed_amount is None
    assert result.idempotent is False

    mock_goal_repo.create_auto_contribution_run.assert_called_once()
    assert schedule.status == "active"
    assert schedule.next_run_at > base_time
    goal_service.ledger_repo.insert_event.assert_not_awaited()
    mock_goal_repo.create_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_lifecycle_skip_goal_completed(
    mock_goal_repo, mock_account_repo, goal_service, auth_user_id, base_time
):
    schedule_id = uuid4()
    account_id = uuid4()
    goal_id = uuid4()

    mock_goal_repo.get_auto_contribution_run.return_value = None

    schedule = GoalAutoContributionSchedule(
        id=schedule_id,
        user_id=auth_user_id,
        goal_id=goal_id,
        account_id=account_id,
        amount=Decimal("100.00"),
        frequency="weekly",
        execution_day="monday",
        timezone="America/Bogota",
        start_date=base_time.date(),
        status="active",
        next_run_at=base_time,
    )
    mock_goal_repo.get_auto_contribution_schedule_by_id_for_update.return_value = schedule

    goal = Goal(
        id=goal_id,
        user_id=auth_user_id,
        target_amount=Decimal("500.00"),
        current_amount=Decimal("500.00"),
        currency="COP",
        status="completed",
        is_active=True,
    )
    mock_goal_repo.get_by_id_for_update.return_value = goal

    account = Account(id=account_id, user_id=auth_user_id, currency="COP", is_active=True)
    mock_account_repo.get_by_id_for_update.return_value = account

    result = await goal_service.execute_auto_contribution_occurrence(
        schedule_id, base_time, base_time
    )

    assert result.status == "skipped"
    assert result.result_code == "goal_completed"
    assert schedule.status == "paused"
    assert schedule.pause_reason == "goal_completed"
    assert schedule.next_run_at is None


def configure_active_execution(
    *,
    repository,
    account_repository,
    user_id,
    scheduled_for,
    configured_amount=Decimal("100.00"),
    target_amount=Decimal("500.00"),
    current_amount=Decimal("0.00"),
):
    schedule = GoalAutoContributionSchedule(
        id=uuid4(),
        user_id=user_id,
        goal_id=uuid4(),
        account_id=uuid4(),
        amount=configured_amount,
        frequency="weekly",
        execution_day="monday",
        timezone="America/Bogota",
        start_date=scheduled_for.astimezone(timezone(timedelta(hours=-5))).date(),
        status="active",
        next_run_at=scheduled_for,
    )
    goal = Goal(
        id=schedule.goal_id,
        user_id=user_id,
        target_amount=target_amount,
        current_amount=current_amount,
        currency="COP",
        status="active",
        is_active=True,
    )
    account = Account(
        id=schedule.account_id,
        user_id=user_id,
        name="Cuenta",
        type="bank",
        balance=Decimal("1000.00"),
        currency="COP",
        is_active=True,
    )
    repository.get_auto_contribution_run.return_value = None
    repository.get_auto_contribution_schedule_by_id_for_update.return_value = schedule
    repository.get_by_id_for_update.return_value = goal
    account_repository.get_by_id_for_update.return_value = account
    return schedule, goal, account


def make_contribution_result(
    *,
    goal,
    account,
    amount,
    created_at,
    completed=False,
    idempotent=False,
):
    resulting_current = goal.current_amount + amount
    return GoalContributionResult(
        transaction_id=uuid4(),
        event_id=uuid4(),
        goal_id=goal.id,
        account_id=account.id,
        source_amount=amount,
        source_currency="COP",
        applied_amount=amount,
        goal_currency="COP",
        goal_current_amount=resulting_current,
        goal_remaining_amount=goal.target_amount - resulting_current,
        goal_status="completed" if completed else "active",
        account_balance=account.balance,
        goal_reserved_amount=amount,
        available_balance=account.balance - amount,
        progress_percentage=Decimal("100.00") if completed else Decimal("20.00"),
        idempotent=idempotent,
        created_at=created_at,
    )


def test_command_id_changes_by_occurrence_and_canonicalizes_timezone(base_time):
    schedule_id = uuid4()
    equivalent = base_time.astimezone(timezone(timedelta(hours=-5)))
    assert GoalService._auto_contribution_command_id(
        schedule_id, base_time
    ) == GoalService._auto_contribution_command_id(schedule_id, equivalent)
    assert GoalService._auto_contribution_command_id(
        schedule_id, base_time
    ) != GoalService._auto_contribution_command_id(schedule_id, base_time + timedelta(days=7))
    assert GoalService._auto_contribution_command_id(
        schedule_id, base_time
    ) != GoalService._auto_contribution_command_id(uuid4(), base_time)


def test_calendar_validation_preserves_eight_local_across_dst():
    schedule = GoalAutoContributionSchedule(
        id=uuid4(),
        user_id=uuid4(),
        goal_id=uuid4(),
        account_id=uuid4(),
        amount=Decimal("100.00"),
        frequency="weekly",
        execution_day="monday",
        timezone="America/New_York",
        status="active",
        next_run_at=datetime(2026, 3, 2, 13, 0, tzinfo=UTC),
    )
    GoalService._validate_auto_contribution_calendar(
        schedule, datetime(2026, 3, 2, 13, 0, tzinfo=UTC)
    )
    GoalService._validate_auto_contribution_calendar(
        schedule, datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
    )
    with pytest.raises(ValueError):
        GoalService._validate_auto_contribution_calendar(
            schedule, datetime(2026, 3, 9, 13, 0, tzinfo=UTC)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scheduled_for", "now", "missed"),
    [
        (datetime(2026, 9, 14, 8, 0), datetime(2026, 9, 14, 13, 0, tzinfo=UTC), 0),
        (datetime(2026, 9, 14, 13, 0, tzinfo=UTC), datetime(2026, 9, 14, 13, 1), 0),
        (
            datetime(2026, 9, 14, 13, 1, tzinfo=UTC),
            datetime(2026, 9, 14, 13, 0, tzinfo=UTC),
            0,
        ),
        (
            datetime(2026, 9, 14, 13, 0, tzinfo=UTC),
            datetime(2026, 9, 14, 13, 0, tzinfo=UTC),
            -1,
        ),
        (
            datetime(2026, 9, 14, 13, 0, tzinfo=UTC),
            datetime(2026, 9, 14, 13, 0, tzinfo=UTC),
            "1",
        ),
    ],
)
async def test_invalid_structural_inputs_are_rejected_before_db(
    goal_service, mock_goal_repo, scheduled_for, now, missed
):
    with pytest.raises(ValueError):
        await goal_service.execute_auto_contribution_occurrence(uuid4(), scheduled_for, now, missed)
    mock_goal_repo.get_auto_contribution_run.assert_not_awaited()
    mock_goal_repo.session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("frequency", "execution_day", "scheduled_for"),
    [
        ("weekly", "monday", datetime(2026, 9, 16, 13, 0, tzinfo=UTC)),
        ("weekly", "monday", datetime(2026, 9, 14, 19, 37, tzinfo=UTC)),
        ("monthly", "15", datetime(2026, 9, 9, 13, 0, tzinfo=UTC)),
    ],
)
async def test_impossible_calendar_occurrence_is_rejected_without_financial_write(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    auth_user_id,
    frequency,
    execution_day,
    scheduled_for,
):
    schedule, _, _ = configure_active_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        user_id=auth_user_id,
        scheduled_for=scheduled_for,
    )
    schedule.frequency = frequency
    schedule.execution_day = execution_day
    with pytest.raises(ValueError):
        await goal_service.execute_auto_contribution_occurrence(
            schedule.id, scheduled_for, scheduled_for
        )
    goal_service.ledger_repo.insert_event.assert_not_awaited()
    mock_goal_repo.create_transaction.assert_not_awaited()
    mock_goal_repo.create_auto_contribution_run.assert_not_awaited()
    mock_goal_repo.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scheduled_for",
    [
        datetime(2027, 2, 28, 13, 0, tzinfo=UTC),
        datetime(2028, 2, 29, 13, 0, tzinfo=UTC),
        datetime(2027, 4, 30, 13, 0, tzinfo=UTC),
    ],
)
async def test_monthly_day_31_accepts_short_month_occurrence(
    goal_service, mock_goal_repo, mock_account_repo, auth_user_id, scheduled_for
):
    schedule, goal, account = configure_active_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        user_id=auth_user_id,
        scheduled_for=scheduled_for,
    )
    schedule.frequency = "monthly"
    schedule.execution_day = "31"
    result = make_contribution_result(
        goal=goal,
        account=account,
        amount=Decimal("100.00"),
        created_at=scheduled_for,
    )
    goal_service.create_contribution = AsyncMock(return_value=result)
    await goal_service.execute_auto_contribution_occurrence(
        schedule.id, scheduled_for, scheduled_for
    )
    goal_service.create_contribution.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["paused", "cancelled"])
async def test_non_active_schedule_is_not_executable(
    goal_service, mock_goal_repo, mock_account_repo, auth_user_id, base_time, status
):
    schedule, _, _ = configure_active_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        user_id=auth_user_id,
        scheduled_for=base_time,
    )
    schedule.status = status
    schedule.next_run_at = None
    with pytest.raises(ConflictError):
        await goal_service.execute_auto_contribution_occurrence(schedule.id, base_time, base_time)
    mock_goal_repo.create_auto_contribution_run.assert_not_awaited()
    goal_service.ledger_repo.insert_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt_entity", ["goal", "account"])
async def test_ownership_corruption_is_technical_failure_without_writes(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    auth_user_id,
    base_time,
    corrupt_entity,
):
    schedule, goal, account = configure_active_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        user_id=auth_user_id,
        scheduled_for=base_time,
    )
    if corrupt_entity == "goal":
        goal.user_id = uuid4()
    else:
        account.user_id = uuid4()
    with pytest.raises(ConflictError):
        await goal_service.execute_auto_contribution_occurrence(schedule.id, base_time, base_time)
    mock_goal_repo.create_auto_contribution_run.assert_not_awaited()
    goal_service.ledger_repo.insert_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "result_code", "schedule_status", "pause_reason", "advances"),
    [
        ("completed", "goal_completed", "paused", "goal_completed", False),
        ("archived", "goal_archived", "paused", "goal_archived", False),
        ("cancelled", "goal_cancelled", "cancelled", None, False),
        ("account_inactive", "account_inactive", "paused", "account_inactive", False),
        ("currency", "currency_mismatch", "active", None, True),
    ],
)
async def test_business_lifecycle_skips_are_durable_and_financially_empty(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    auth_user_id,
    base_time,
    case,
    result_code,
    schedule_status,
    pause_reason,
    advances,
):
    schedule, goal, account = configure_active_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        user_id=auth_user_id,
        scheduled_for=base_time,
    )
    if case == "completed":
        goal.status = "completed"
        goal.current_amount = goal.target_amount
    elif case == "archived":
        goal.is_active = False
    elif case == "cancelled":
        goal.status = "cancelled"
        goal.is_active = True
    elif case == "account_inactive":
        account.is_active = False
    else:
        account.currency = "USD"

    result = await goal_service.execute_auto_contribution_occurrence(
        schedule.id, base_time, base_time
    )
    run = mock_goal_repo.create_auto_contribution_run.await_args.args[0]
    assert result.status == "skipped"
    assert result.result_code == result_code
    assert run.executed_at is None
    assert run.executed_amount is None
    assert schedule.status == schedule_status
    assert schedule.pause_reason == pause_reason
    assert (schedule.next_run_at is not None) is advances
    assert schedule.updated_at == base_time
    goal_service.ledger_repo.insert_event.assert_not_awaited()
    mock_goal_repo.create_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_skipped_replay_is_consumed_even_if_funds_later_exist(
    goal_service, mock_goal_repo, auth_user_id, base_time
):
    existing = GoalAutoContributionRun(
        id=uuid4(),
        schedule_id=uuid4(),
        scheduled_for=base_time,
        status="skipped",
        result_code="insufficient_available_balance",
        configured_amount=Decimal("100.00"),
        executed_amount=None,
        missed_occurrences_count=2,
    )
    mock_goal_repo.get_auto_contribution_run.return_value = existing
    result = await goal_service.execute_auto_contribution_occurrence(
        existing.schedule_id, base_time, base_time + timedelta(days=1)
    )
    assert result.run_id == existing.id
    assert result.status == "skipped"
    assert result.idempotent is True
    goal_service.create_contribution = AsyncMock()
    goal_service.create_contribution.assert_not_awaited()
    mock_goal_repo.get_auto_contribution_schedule_by_id_for_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_partial_uses_remaining_not_configured_amount_and_pauses(
    goal_service, mock_goal_repo, mock_account_repo, auth_user_id, base_time
):
    schedule, goal, account = configure_active_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        user_id=auth_user_id,
        scheduled_for=base_time,
        configured_amount=Decimal("100.00"),
        target_amount=Decimal("500.00"),
        current_amount=Decimal("460.00"),
    )

    async def complete_goal(*args, **kwargs):
        payload = kwargs["payload"]
        assert payload.amount == Decimal("40.00")
        goal.current_amount += payload.amount
        goal.status = "completed"
        return make_contribution_result(
            goal=Goal(
                id=goal.id,
                user_id=goal.user_id,
                target_amount=goal.target_amount,
                current_amount=Decimal("460.00"),
                currency=goal.currency,
                status="active",
                is_active=True,
            ),
            account=account,
            amount=payload.amount,
            created_at=base_time,
            completed=True,
        )

    goal_service.create_contribution = AsyncMock(side_effect=complete_goal)
    result = await goal_service.execute_auto_contribution_occurrence(
        schedule.id, base_time, base_time
    )
    run = mock_goal_repo.create_auto_contribution_run.await_args.args[0]
    assert result.executed_amount == Decimal("40.00")
    assert run.configured_amount == Decimal("100.00")
    assert run.executed_amount == Decimal("40.00")
    assert schedule.status == "paused"
    assert schedule.pause_reason == "goal_completed"
    assert schedule.next_run_at is None
    assert schedule.updated_at == base_time


@pytest.mark.asyncio
async def test_insufficient_final_partial_does_not_fallback_to_available_amount(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
    auth_user_id,
    base_time,
):
    schedule, goal, account = configure_active_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        user_id=auth_user_id,
        scheduled_for=base_time,
        configured_amount=Decimal("100.00"),
        target_amount=Decimal("500.00"),
        current_amount=Decimal("460.00"),
    )
    original_balance = account.balance
    mock_goal_repo.get_transaction_by_command_id.return_value = None
    mock_goal_repo.calculate_reserved_by_account.return_value = Decimal("970.00")
    result = await goal_service.execute_auto_contribution_occurrence(
        schedule.id, base_time, base_time, missed_occurrences_count=3
    )
    run = mock_goal_repo.create_auto_contribution_run.await_args.args[0]
    assert result.result_code == "insufficient_available_balance"
    assert run.executed_amount is None
    assert run.missed_occurrences_count == 3
    assert goal.current_amount == Decimal("460.00")
    assert account.balance == original_balance
    mock_ledger_repo.insert_event.assert_not_awaited()
    mock_goal_repo.create_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_financial_primitive_writes_one_event_transaction_and_run(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
    auth_user_id,
    base_time,
):
    schedule, goal, account = configure_active_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        user_id=auth_user_id,
        scheduled_for=base_time,
    )
    original_balance = account.balance
    mock_goal_repo.get_transaction_by_command_id.return_value = None
    mock_goal_repo.calculate_reserved_by_account.return_value = Decimal("50.00")

    transactions = []

    async def create_transaction(transaction):
        transaction.id = uuid4()
        transaction.created_at = base_time
        transactions.append(transaction)
        return transaction

    async def insert_event(event_create):
        event = FinancialEvent(
            id=uuid4(),
            user_id=event_create.user_id,
            account_id=event_create.account_id,
            event_type=event_create.event_type.value,
            direction=event_create.direction.value,
            amount=event_create.amount,
            currency=event_create.currency,
            metadata_=event_create.metadata,
            command_id=event_create.command_id,
            created_at=base_time,
            occurred_at=base_time,
        )
        return SimpleNamespace(event=event, idempotent=False)

    async def create_run(run):
        run.id = uuid4()
        return run

    mock_goal_repo.create_transaction.side_effect = create_transaction
    mock_goal_repo.create_auto_contribution_run.side_effect = create_run
    mock_ledger_repo.insert_event.side_effect = insert_event

    result = await goal_service.execute_auto_contribution_occurrence(
        schedule.id, base_time, base_time
    )

    assert result.status == "succeeded"
    assert len(transactions) == 1
    transaction = transactions[0]
    event_create = mock_ledger_repo.insert_event.await_args.args[0]
    run = mock_goal_repo.create_auto_contribution_run.await_args.args[0]
    assert event_create.event_type == EventType.GOAL_CONTRIBUTION
    assert event_create.direction == Direction.NEUTRAL
    assert event_create.amount == Decimal("100.00")
    assert transaction.transaction_type == GoalTransactionType.allocation
    assert transaction.metadata_json["channel"] == "automatic"
    assert (
        GoalService._resolve_history_channel(
            GoalTransactionType.allocation, None, transaction.metadata_json
        )
        == "automatic"
    )
    assert run.goal_transaction_id == transaction.id
    assert run.executed_amount == Decimal("100.00")
    assert run.executed_at == base_time
    assert account.balance == original_balance
    assert goal.current_amount == Decimal("100.00")
    assert schedule.updated_at == base_time
    assert transaction.metadata_json["goal_reserved_amount"] == "150.00"
    assert transaction.metadata_json["available_balance"] == "850.00"
    mock_goal_repo.session.commit.assert_awaited_once()
    mock_goal_repo.session.rollback.assert_not_awaited()


def configure_integrated_processor_execution(
    *,
    repository,
    account_repository,
    ledger_repository,
    user_id,
    scheduled_for,
    configured_amount=Decimal("100.00"),
    target_amount=Decimal("500.00"),
    current_amount=Decimal("0.00"),
    reserved_amount=Decimal("50.00"),
):
    """Compose el selector Block 5 con el núcleo financiero real de Block 4."""
    schedule, goal, account = configure_active_execution(
        repository=repository,
        account_repository=account_repository,
        user_id=user_id,
        scheduled_for=scheduled_for,
        configured_amount=configured_amount,
        target_amount=target_amount,
        current_amount=current_amount,
    )
    repository.get_next_due_auto_contribution_schedule_for_update.side_effect = [
        schedule,
        None,
    ]
    repository.get_transaction_by_command_id.return_value = None
    repository.calculate_reserved_by_account.return_value = reserved_amount

    transactions = []
    runs = []

    async def create_transaction(transaction):
        transaction.id = uuid4()
        transaction.created_at = scheduled_for
        transactions.append(transaction)
        return transaction

    async def insert_event(event_create):
        event = FinancialEvent(
            id=uuid4(),
            user_id=event_create.user_id,
            account_id=event_create.account_id,
            event_type=event_create.event_type.value,
            direction=event_create.direction.value,
            amount=event_create.amount,
            currency=event_create.currency,
            metadata_=event_create.metadata,
            command_id=event_create.command_id,
            created_at=scheduled_for,
            occurred_at=scheduled_for,
        )
        return SimpleNamespace(event=event, idempotent=False)

    async def create_run(run):
        run.id = uuid4()
        runs.append(run)
        return run

    repository.create_transaction.side_effect = create_transaction
    repository.create_auto_contribution_run.side_effect = create_run
    ledger_repository.insert_event.side_effect = insert_event
    return schedule, goal, account, transactions, runs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "current_amount",
        "reserved_amount",
        "expected_status",
        "expected_executed",
        "expected_goal_status",
    ),
    [
        (Decimal("0.00"), Decimal("50.00"), "succeeded", Decimal("100.00"), "active"),
        (Decimal("460.00"), Decimal("50.00"), "succeeded", Decimal("40.00"), "completed"),
        (Decimal("0.00"), Decimal("970.00"), "skipped", None, "active"),
    ],
)
async def test_integrated_processor_executes_success_partial_and_insufficient_scenarios(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
    auth_user_id,
    base_time,
    current_amount,
    reserved_amount,
    expected_status,
    expected_executed,
    expected_goal_status,
):
    schedule, goal, account, transactions, runs = configure_integrated_processor_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        ledger_repository=mock_ledger_repo,
        user_id=auth_user_id,
        scheduled_for=base_time,
        current_amount=current_amount,
        reserved_amount=reserved_amount,
    )
    original_balance = account.balance

    summary = await goal_service.process_due_auto_contributions(base_time, batch_size=2)

    assert summary.selected == 1
    assert summary.technical_failures == 0
    assert len(runs) == 1
    assert runs[0].status == expected_status
    assert runs[0].executed_amount == expected_executed
    assert account.balance == original_balance

    if expected_status == "succeeded":
        assert summary.succeeded == 1
        assert summary.skipped == 0
        assert len(transactions) == 1
        assert transactions[0].metadata_json["channel"] == "automatic"
        assert transactions[0].source_amount == expected_executed
        assert mock_ledger_repo.insert_event.await_args.args[0].direction == Direction.NEUTRAL
        assert goal.status == expected_goal_status
        assert schedule.status == ("paused" if expected_goal_status == "completed" else "active")
    else:
        assert summary.succeeded == 0
        assert summary.skipped == 1
        assert transactions == []
        mock_ledger_repo.insert_event.assert_not_awaited()
        assert schedule.status == "active"
        assert schedule.next_run_at > base_time


@pytest.mark.asyncio
async def test_integrated_processor_executes_only_latest_occurrence_after_downtime(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
    auth_user_id,
    base_time,
):
    first_due = base_time - timedelta(days=21)
    schedule, _, _, transactions, runs = configure_integrated_processor_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        ledger_repository=mock_ledger_repo,
        user_id=auth_user_id,
        scheduled_for=first_due,
    )

    summary = await goal_service.process_due_auto_contributions(base_time, batch_size=2)

    assert summary.selected == 1
    assert summary.succeeded == 1
    assert summary.missed_occurrences == 3
    assert len(transactions) == 1
    assert len(runs) == 1
    assert runs[0].scheduled_for == base_time
    assert runs[0].missed_occurrences_count == 3
    assert schedule.next_run_at > base_time
    mock_ledger_repo.insert_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_integrated_processor_reconciles_stale_cursor_without_financial_replay(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
    auth_user_id,
    base_time,
):
    first_due = base_time - timedelta(days=21)
    schedule, goal, _, _, _ = configure_integrated_processor_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        ledger_repository=mock_ledger_repo,
        user_id=auth_user_id,
        scheduled_for=first_due,
    )
    existing_run = GoalAutoContributionRun(
        id=uuid4(),
        schedule_id=schedule.id,
        scheduled_for=base_time,
        executed_at=base_time,
        status="succeeded",
        result_code="success",
        configured_amount=schedule.amount,
        executed_amount=schedule.amount,
        missed_occurrences_count=3,
        goal_transaction_id=uuid4(),
    )
    mock_goal_repo.get_auto_contribution_run.return_value = existing_run
    mock_goal_repo.get_by_id.return_value = goal

    summary = await goal_service.process_due_auto_contributions(base_time, batch_size=2)

    assert summary.selected == 1
    assert summary.reconciled == 1
    assert summary.succeeded == 0
    assert summary.skipped == 0
    assert summary.technical_failures == 0
    assert schedule.next_run_at > base_time
    mock_goal_repo.create_transaction.assert_not_awaited()
    mock_goal_repo.create_auto_contribution_run.assert_not_awaited()
    mock_ledger_repo.insert_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_technical_failure_rolls_back_and_does_not_advance_schedule(
    goal_service, mock_goal_repo, mock_account_repo, auth_user_id, base_time
):
    schedule, _, _ = configure_active_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        user_id=auth_user_id,
        scheduled_for=base_time,
    )
    original_next_run = schedule.next_run_at
    goal_service.create_contribution = AsyncMock(side_effect=RuntimeError("ledger failure"))
    with pytest.raises(RuntimeError, match="ledger failure"):
        await goal_service.execute_auto_contribution_occurrence(schedule.id, base_time, base_time)
    assert schedule.next_run_at == original_next_run
    mock_goal_repo.create_auto_contribution_run.assert_not_awaited()
    mock_goal_repo.session.commit.assert_not_awaited()
    mock_goal_repo.session.rollback.assert_awaited_once()


class NamedConstraintError(Exception):
    def __init__(self, constraint_name):
        self.constraint_name = constraint_name
        super().__init__(constraint_name)


@pytest.mark.asyncio
async def test_run_unique_race_rolls_back_whole_attempt_and_returns_winner(
    goal_service, mock_goal_repo, mock_account_repo, auth_user_id, base_time
):
    schedule, goal, account = configure_active_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        user_id=auth_user_id,
        scheduled_for=base_time,
    )
    winner = GoalAutoContributionRun(
        id=uuid4(),
        schedule_id=schedule.id,
        scheduled_for=base_time,
        status="succeeded",
        result_code="success",
        configured_amount=schedule.amount,
        executed_amount=schedule.amount,
        goal_transaction_id=uuid4(),
    )
    mock_goal_repo.get_auto_contribution_run.side_effect = [None, None, winner]
    goal_service.create_contribution = AsyncMock(
        return_value=make_contribution_result(
            goal=goal,
            account=account,
            amount=schedule.amount,
            created_at=base_time,
        )
    )
    mock_goal_repo.create_auto_contribution_run.side_effect = IntegrityError(
        "insert", {}, NamedConstraintError("uq_gacr_schedule_scheduled_for")
    )
    result = await goal_service.execute_auto_contribution_occurrence(
        schedule.id, base_time, base_time
    )
    assert result.run_id == winner.id
    assert result.idempotent is True
    mock_goal_repo.session.rollback.assert_awaited_once()
    assert mock_goal_repo.session.commit.await_count == 1


@pytest.mark.asyncio
async def test_unrelated_run_integrity_error_is_not_mapped_to_replay(
    goal_service, mock_goal_repo, mock_account_repo, auth_user_id, base_time
):
    schedule, goal, account = configure_active_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        user_id=auth_user_id,
        scheduled_for=base_time,
    )
    goal_service.create_contribution = AsyncMock(
        return_value=make_contribution_result(
            goal=goal,
            account=account,
            amount=schedule.amount,
            created_at=base_time,
        )
    )
    error = IntegrityError("insert", {}, NamedConstraintError("some_other_constraint"))
    mock_goal_repo.create_auto_contribution_run.side_effect = error
    with pytest.raises(IntegrityError) as raised:
        await goal_service.execute_auto_contribution_occurrence(schedule.id, base_time, base_time)
    assert raised.value is error
    mock_goal_repo.session.rollback.assert_awaited_once()


def configure_committed_financial_operation_without_run(
    *, service, repository, ledger_repository, schedule, goal, account, created_at, channel
):
    command_id = service._auto_contribution_command_id(schedule.id, created_at)
    amount = schedule.amount
    event = FinancialEvent(
        id=uuid4(),
        user_id=schedule.user_id,
        account_id=account.id,
        event_type=EventType.GOAL_CONTRIBUTION.value,
        direction=Direction.NEUTRAL.value,
        amount=amount,
        currency=account.currency,
        metadata_={"goal_id": str(goal.id)},
        command_id=command_id,
        created_at=created_at,
        occurred_at=created_at,
    )
    transaction = GoalTransaction(
        id=uuid4(),
        user_id=schedule.user_id,
        goal_id=goal.id,
        account_id=account.id,
        event_id=event.id,
        transaction_type=GoalTransactionType.allocation,
        source_amount=amount,
        source_currency=account.currency,
        applied_amount=amount,
        goal_currency=goal.currency,
        command_id=command_id,
        command_fingerprint=service._generate_fingerprint(
            schedule.user_id,
            goal.id,
            account.id,
            GoalTransactionType.allocation.value,
            amount,
            account.currency,
            amount,
            goal.currency,
        ),
        metadata_json={
            "goal_current_amount": str(goal.current_amount),
            "goal_remaining_amount": str(goal.target_amount - goal.current_amount),
            "goal_status": goal.status,
            "account_balance": str(account.balance),
            "goal_reserved_amount": str(amount),
            "available_balance": str(account.balance - amount),
            "progress_percentage": "20.00",
            "channel": channel,
        },
        created_at=created_at,
    )
    repository.get_transaction_by_command_id.return_value = transaction
    ledger_repository.get_by_command_id.return_value = event
    return transaction


@pytest.mark.asyncio
async def test_financial_success_without_run_is_reconciled_without_duplicate_money(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
    auth_user_id,
    base_time,
):
    schedule, goal, account = configure_active_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        user_id=auth_user_id,
        scheduled_for=base_time,
    )
    goal.current_amount = Decimal("100.00")
    transaction = configure_committed_financial_operation_without_run(
        service=goal_service,
        repository=mock_goal_repo,
        ledger_repository=mock_ledger_repo,
        schedule=schedule,
        goal=goal,
        account=account,
        created_at=base_time,
        channel="automatic",
    )
    result = await goal_service.execute_auto_contribution_occurrence(
        schedule.id, base_time, base_time
    )
    run = mock_goal_repo.create_auto_contribution_run.await_args.args[0]
    assert result.idempotent is True
    assert result.goal_transaction_id == transaction.id
    assert run.goal_transaction_id == transaction.id
    assert run.executed_at == transaction.created_at
    mock_ledger_repo.insert_event.assert_not_awaited()
    mock_goal_repo.create_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_automatic_replay_rejects_manual_operation_with_same_command_id(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
    auth_user_id,
    base_time,
):
    schedule, goal, account = configure_active_execution(
        repository=mock_goal_repo,
        account_repository=mock_account_repo,
        user_id=auth_user_id,
        scheduled_for=base_time,
    )
    goal.current_amount = Decimal("100.00")
    configure_committed_financial_operation_without_run(
        service=goal_service,
        repository=mock_goal_repo,
        ledger_repository=mock_ledger_repo,
        schedule=schedule,
        goal=goal,
        account=account,
        created_at=base_time,
        channel="manual",
    )
    with pytest.raises(ConflictError, match="canal difiere"):
        await goal_service.execute_auto_contribution_occurrence(schedule.id, base_time, base_time)
    mock_goal_repo.create_auto_contribution_run.assert_not_awaited()
    mock_goal_repo.session.rollback.assert_awaited_once()


def test_block4_has_no_public_route_or_openapi_contract():
    from app.main import app

    schema = app.openapi()
    assert not any(
        "run" in path or "execute" in path or "process" in path
        for path in schema["paths"]
        if "auto-contribution" in path
    )
