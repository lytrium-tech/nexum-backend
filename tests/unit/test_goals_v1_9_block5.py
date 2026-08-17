from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.dialects import postgresql

from app.goals.models import GoalAutoContributionRun, GoalAutoContributionSchedule
from app.goals.repository import GoalRepository
from app.goals.service import (
    AUTO_CONTRIBUTION_EXECUTION_TIME,
    AutoContributionExecutionResult,
    GoalService,
)

NOW = datetime(2026, 8, 17, 13, 0, tzinfo=UTC)
NY = ZoneInfo("America/New_York")


def local_occurrence(year: int, month: int, day: int, tz: ZoneInfo = NY) -> datetime:
    return datetime.combine(
        date(year, month, day),
        AUTO_CONTRIBUTION_EXECUTION_TIME,
        tzinfo=tz,
    ).astimezone(UTC)


@pytest.fixture
def make_schedule():
    def factory(
        next_run_at: datetime,
        *,
        frequency: str = "weekly",
        execution_day: str = "monday",
        timezone: str = "America/New_York",
        start_date: date | None = None,
    ) -> GoalAutoContributionSchedule:
        return GoalAutoContributionSchedule(
            id=uuid4(),
            user_id=uuid4(),
            goal_id=uuid4(),
            account_id=uuid4(),
            amount=Decimal("100.00"),
            frequency=frequency,
            execution_day=execution_day,
            timezone=timezone,
            start_date=start_date,
            status="active",
            pause_reason=None,
            next_run_at=next_run_at,
            created_at=NOW,
            updated_at=NOW,
        )

    return factory


@pytest.fixture
def processor_service():
    repository = MagicMock()
    repository.session = MagicMock()
    repository.session.commit = AsyncMock()
    repository.session.rollback = AsyncMock()
    repository.get_next_due_auto_contribution_schedule_for_update = AsyncMock()
    repository.get_auto_contribution_run = AsyncMock(return_value=None)
    repository.get_by_id = AsyncMock()
    service = GoalService(repository, MagicMock(), MagicMock())
    return service, repository


def execution_result(
    status: str = "succeeded",
    *,
    result_code: str = "success",
    idempotent: bool = False,
) -> AutoContributionExecutionResult:
    return AutoContributionExecutionResult(
        run_id=uuid4(),
        status=status,
        result_code=result_code,
        configured_amount=Decimal("100.00"),
        executed_amount=Decimal("100.00") if status == "succeeded" else None,
        goal_transaction_id=uuid4() if status == "succeeded" else None,
        idempotent=idempotent,
    )


async def captured_due_statement(excluded_schedule_ids=None):
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    repository = GoalRepository(session)
    await repository.get_next_due_auto_contribution_schedule_for_update(
        NOW, excluded_schedule_ids=excluded_schedule_ids
    )
    return session.execute.await_args.args[0]


def compiled_sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.mark.asyncio
async def test_due_query_has_exact_filters_order_limit_and_lock():
    sql = compiled_sql(await captured_due_statement())

    assert "goal_auto_contribution_schedules.status = 'active'" in sql
    assert "goal_auto_contribution_schedules.next_run_at IS NOT NULL" in sql
    assert "goal_auto_contribution_schedules.next_run_at <=" in sql
    assert (
        "ORDER BY goal_auto_contribution_schedules.next_run_at ASC, "
        "goal_auto_contribution_schedules.id ASC" in sql
    )
    assert "LIMIT 1" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


@pytest.mark.asyncio
async def test_due_query_empty_exclusions_emit_no_not_in():
    assert "NOT IN" not in compiled_sql(await captured_due_statement([]))


@pytest.mark.asyncio
async def test_due_query_excludes_attempted_ids():
    excluded = [uuid4(), uuid4()]
    sql = compiled_sql(await captured_due_statement(excluded))

    assert "goal_auto_contribution_schedules.id NOT IN" in sql
    assert all(str(value) in sql for value in excluded)


@pytest.mark.asyncio
async def test_due_query_has_no_joins_or_availability_filter():
    sql = compiled_sql(await captured_due_statement()).upper()

    assert " JOIN " not in sql
    assert "BALANCE" not in sql
    assert "GOALS." not in sql
    assert "ACCOUNTS." not in sql


@pytest.mark.parametrize(
    ("first_due", "now", "expected", "missed"),
    [
        (local_occurrence(2026, 8, 10), NOW, local_occurrence(2026, 8, 17), 1),
        (
            local_occurrence(2026, 7, 6),
            local_occurrence(2026, 7, 27),
            local_occurrence(2026, 7, 27),
            3,
        ),
        (
            local_occurrence(2016, 8, 15),
            local_occurrence(2026, 8, 17),
            local_occurrence(2026, 8, 17),
            522,
        ),
    ],
)
def test_weekly_latest_only_cases(make_schedule, first_due, now, expected, missed):
    schedule = make_schedule(first_due)

    assert GoalService.calculate_latest_due_occurrence(schedule, now) == (
        expected,
        missed,
    )


@pytest.mark.parametrize(
    ("first_date", "now_date", "expected_date"),
    [
        (date(2026, 3, 2), date(2026, 3, 9), date(2026, 3, 9)),
        (date(2026, 10, 26), date(2026, 11, 2), date(2026, 11, 2)),
    ],
)
def test_weekly_latest_only_preserves_local_time_across_dst(
    make_schedule, first_date, now_date, expected_date
):
    first_due = datetime.combine(
        first_date, AUTO_CONTRIBUTION_EXECUTION_TIME, tzinfo=NY
    ).astimezone(UTC)
    now = datetime.combine(now_date, AUTO_CONTRIBUTION_EXECUTION_TIME, tzinfo=NY).astimezone(UTC)
    expected = datetime.combine(
        expected_date, AUTO_CONTRIBUTION_EXECUTION_TIME, tzinfo=NY
    ).astimezone(UTC)

    latest, missed = GoalService.calculate_latest_due_occurrence(make_schedule(first_due), now)

    assert (latest, missed) == (expected, 1)
    assert latest.astimezone(NY).time() == AUTO_CONTRIBUTION_EXECUTION_TIME


def test_weekly_exact_boundary_is_due(make_schedule):
    first_due = local_occurrence(2026, 8, 17)
    assert GoalService.calculate_latest_due_occurrence(make_schedule(first_due), first_due) == (
        first_due,
        0,
    )


def test_weekly_one_due_before_next_week_has_zero_missed(make_schedule):
    first_due = local_occurrence(2026, 8, 10)
    now = datetime(2026, 8, 12, 12, tzinfo=NY).astimezone(UTC)

    assert GoalService.calculate_latest_due_occurrence(make_schedule(first_due), now) == (
        first_due,
        0,
    )


def test_weekly_never_moves_before_cursor(make_schedule):
    cursor = local_occurrence(2026, 8, 17)
    schedule = make_schedule(cursor)

    latest, missed = GoalService.calculate_latest_due_occurrence(schedule, cursor)

    assert latest == cursor
    assert missed == 0


def test_weekly_never_moves_before_start_date(make_schedule):
    cursor = local_occurrence(2026, 8, 17)
    schedule = make_schedule(cursor, start_date=date(2026, 8, 17))

    latest, _ = GoalService.calculate_latest_due_occurrence(schedule, cursor)

    assert latest.astimezone(NY).date() >= schedule.start_date


@pytest.mark.parametrize(
    ("first_date", "now_local", "expected_date", "missed"),
    [
        (date(2026, 1, 15), datetime(2026, 1, 20, 8, tzinfo=NY), date(2026, 1, 15), 0),
        (date(2026, 1, 15), datetime(2026, 5, 20, 8, tzinfo=NY), date(2026, 5, 15), 4),
        (date(2026, 1, 31), datetime(2026, 3, 10, 8, tzinfo=NY), date(2026, 2, 28), 1),
        (date(2028, 1, 31), datetime(2028, 3, 1, 8, tzinfo=NY), date(2028, 2, 29), 1),
        (date(2026, 3, 31), datetime(2026, 5, 1, 8, tzinfo=NY), date(2026, 4, 30), 1),
        (date(2025, 12, 31), datetime(2026, 1, 31, 9, tzinfo=NY), date(2026, 1, 31), 1),
        (date(2025, 11, 30), datetime(2026, 2, 28, 9, tzinfo=NY), date(2026, 2, 28), 3),
        (date(2016, 1, 31), datetime(2026, 1, 31, 9, tzinfo=NY), date(2026, 1, 31), 120),
    ],
)
def test_monthly_latest_only_cases(make_schedule, first_date, now_local, expected_date, missed):
    first_due = datetime.combine(
        first_date, AUTO_CONTRIBUTION_EXECUTION_TIME, tzinfo=NY
    ).astimezone(UTC)
    expected = datetime.combine(
        expected_date, AUTO_CONTRIBUTION_EXECUTION_TIME, tzinfo=NY
    ).astimezone(UTC)
    schedule = make_schedule(
        first_due,
        frequency="monthly",
        execution_day=str(first_date.day),
        start_date=first_date,
    )

    assert GoalService.calculate_latest_due_occurrence(schedule, now_local.astimezone(UTC)) == (
        expected,
        missed,
    )


def test_monthly_current_candidate_before_execution_uses_previous_month(make_schedule):
    first_due = local_occurrence(2026, 1, 15)
    now = datetime(2026, 5, 15, 7, tzinfo=NY).astimezone(UTC)
    schedule = make_schedule(first_due, frequency="monthly", execution_day="15")

    assert GoalService.calculate_latest_due_occurrence(schedule, now) == (
        local_occurrence(2026, 4, 15),
        3,
    )


def test_monthly_never_moves_before_cursor(make_schedule):
    cursor = local_occurrence(2026, 8, 31)
    schedule = make_schedule(
        cursor,
        frequency="monthly",
        execution_day="31",
        start_date=date(2026, 8, 31),
    )

    latest, missed = GoalService.calculate_latest_due_occurrence(schedule, cursor)

    assert latest == cursor
    assert missed == 0


@pytest.mark.parametrize("field", ["now", "next_run_at"])
def test_latest_only_rejects_naive_datetimes(make_schedule, field):
    next_run_at = local_occurrence(2026, 8, 17)
    now = NOW
    if field == "now":
        now = NOW.replace(tzinfo=None)
    else:
        next_run_at = next_run_at.replace(tzinfo=None)

    with pytest.raises(ValueError, match="zona horaria"):
        GoalService.calculate_latest_due_occurrence(make_schedule(next_run_at), now)


@pytest.mark.asyncio
@pytest.mark.parametrize("batch_size", [0, -1, 51, True, False, 1.0, "1"])
async def test_processor_rejects_invalid_batch_sizes(processor_service, batch_size):
    service, repository = processor_service

    with pytest.raises(ValueError, match="batch_size"):
        await service.process_due_auto_contributions(NOW, batch_size=batch_size)

    repository.get_next_due_auto_contribution_schedule_for_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_processor_rejects_naive_now(processor_service):
    service, _ = processor_service
    with pytest.raises(ValueError, match="zona horaria"):
        await service.process_due_auto_contributions(NOW.replace(tzinfo=None))


@pytest.mark.asyncio
async def test_processor_empty_stops_after_one_selection(processor_service):
    service, repository = processor_service
    repository.get_next_due_auto_contribution_schedule_for_update.return_value = None

    summary = await service.process_due_auto_contributions(NOW)

    assert summary.selected == 0
    repository.get_next_due_auto_contribution_schedule_for_update.assert_awaited_once()
    repository.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_processor_success_uses_locked_schedule_and_same_uow(
    make_schedule, processor_service
):
    service, repository = processor_service
    schedule = make_schedule(local_occurrence(2026, 8, 17))
    repository.get_next_due_auto_contribution_schedule_for_update.side_effect = [
        schedule,
        None,
    ]
    core = AsyncMock(return_value=execution_result())

    with patch.object(service, "_execute_auto_contribution_occurrence_in_uow", core):
        summary = await service.process_due_auto_contributions(NOW, batch_size=2)

    assert summary.selected == 1
    assert summary.succeeded == 1
    assert summary.technical_failures == 0
    core.assert_awaited_once_with(
        schedule=schedule,
        scheduled_for_utc=local_occurrence(2026, 8, 17),
        now_utc=NOW,
        missed_occurrences_count=0,
    )
    assert repository.session.commit.await_count == 2
    repository.session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_processor_does_not_call_block4_uow_wrapper(make_schedule, processor_service):
    service, repository = processor_service
    schedule = make_schedule(local_occurrence(2026, 8, 17))
    repository.get_next_due_auto_contribution_schedule_for_update.return_value = schedule
    core = AsyncMock(return_value=execution_result())
    wrapper = AsyncMock()

    with (
        patch.object(service, "_execute_auto_contribution_occurrence_in_uow", core),
        patch.object(service, "execute_auto_contribution_occurrence", wrapper),
    ):
        await service.process_due_auto_contributions(NOW, batch_size=1)

    core.assert_awaited_once()
    wrapper.assert_not_awaited()


@pytest.mark.asyncio
async def test_processor_business_skip_is_not_technical_failure(make_schedule, processor_service):
    service, repository = processor_service
    schedule = make_schedule(local_occurrence(2026, 8, 17))
    repository.get_next_due_auto_contribution_schedule_for_update.return_value = schedule
    result = execution_result("skipped", result_code="insufficient_available_balance")

    with patch.object(
        service,
        "_execute_auto_contribution_occurrence_in_uow",
        AsyncMock(return_value=result),
    ):
        summary = await service.process_due_auto_contributions(NOW, batch_size=1)

    assert summary.skipped == 1
    assert summary.technical_failures == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected_succeeded", "expected_skipped"),
    [
        (execution_result(), 1, 0),
        (
            execution_result("skipped", result_code="insufficient_available_balance"),
            0,
            1,
        ),
    ],
)
async def test_latest_backlog_calls_financial_core_once_and_persists_missed_count(
    make_schedule,
    processor_service,
    result,
    expected_succeeded,
    expected_skipped,
):
    service, repository = processor_service
    schedule = make_schedule(local_occurrence(2026, 7, 27))
    repository.get_next_due_auto_contribution_schedule_for_update.return_value = schedule
    core = AsyncMock(return_value=result)

    with patch.object(service, "_execute_auto_contribution_occurrence_in_uow", core):
        summary = await service.process_due_auto_contributions(NOW, batch_size=1)

    assert summary.succeeded == expected_succeeded
    assert summary.skipped == expected_skipped
    assert summary.missed_occurrences == 3
    core.assert_awaited_once_with(
        schedule=schedule,
        scheduled_for_utc=local_occurrence(2026, 8, 17),
        now_utc=NOW,
        missed_occurrences_count=3,
    )


@pytest.mark.asyncio
async def test_processor_technical_failure_rolls_back_then_continues(
    make_schedule, processor_service
):
    service, repository = processor_service
    first = make_schedule(local_occurrence(2026, 8, 17))
    second = make_schedule(local_occurrence(2026, 8, 17))
    repository.get_next_due_auto_contribution_schedule_for_update.side_effect = [
        first,
        second,
        None,
    ]
    core = AsyncMock(side_effect=[RuntimeError("boom"), execution_result()])

    with patch.object(service, "_execute_auto_contribution_occurrence_in_uow", core):
        summary = await service.process_due_auto_contributions(NOW, batch_size=3)

    assert summary.selected == 2
    assert summary.technical_failures == 1
    assert summary.succeeded == 1
    assert first.next_run_at == local_occurrence(2026, 8, 17)
    repository.session.rollback.assert_awaited_once()
    assert repository.session.commit.await_count == 2
    second_query = repository.get_next_due_auto_contribution_schedule_for_update.await_args_list[
        1
    ].kwargs
    assert second_query["excluded_schedule_ids"] == [first.id]


@pytest.mark.asyncio
async def test_processor_attempted_set_is_reset_next_invocation(make_schedule, processor_service):
    service, repository = processor_service
    schedule = make_schedule(local_occurrence(2026, 8, 17))
    repository.get_next_due_auto_contribution_schedule_for_update.side_effect = [
        schedule,
        schedule,
    ]
    core = AsyncMock(side_effect=RuntimeError("retryable"))

    with patch.object(service, "_execute_auto_contribution_occurrence_in_uow", core):
        first = await service.process_due_auto_contributions(NOW, batch_size=1)
        second = await service.process_due_auto_contributions(NOW, batch_size=1)

    assert first.technical_failures == second.technical_failures == 1
    assert (
        repository.get_next_due_auto_contribution_schedule_for_update.await_args_list[0].kwargs[
            "excluded_schedule_ids"
        ]
        is None
    )
    assert (
        repository.get_next_due_auto_contribution_schedule_for_update.await_args_list[1].kwargs[
            "excluded_schedule_ids"
        ]
        is None
    )


@pytest.mark.asyncio
async def test_processor_hard_batch_limit_is_fifty(make_schedule, processor_service):
    service, repository = processor_service
    schedules = [make_schedule(local_occurrence(2026, 8, 17)) for _ in range(50)]
    repository.get_next_due_auto_contribution_schedule_for_update.side_effect = schedules
    core = AsyncMock(return_value=execution_result())

    with patch.object(service, "_execute_auto_contribution_occurrence_in_uow", core):
        summary = await service.process_due_auto_contributions(NOW)

    assert summary.selected == 50
    assert summary.succeeded == 50
    assert core.await_count == 50
    assert repository.session.commit.await_count == 50


@pytest.mark.asyncio
async def test_global_due_selection_failure_aborts_without_blind_loop(processor_service):
    service, repository = processor_service
    repository.get_next_due_auto_contribution_schedule_for_update.side_effect = RuntimeError(
        "database unavailable"
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.process_due_auto_contributions(NOW)

    repository.get_next_due_auto_contribution_schedule_for_update.assert_awaited_once()
    repository.session.rollback.assert_awaited_once()


def existing_run(schedule, *, status: str, result_code: str):
    return GoalAutoContributionRun(
        id=uuid4(),
        schedule_id=schedule.id,
        scheduled_for=schedule.next_run_at,
        status=status,
        result_code=result_code,
        configured_amount=schedule.amount,
        executed_amount=(schedule.amount if status == "succeeded" else None),
        missed_occurrences_count=0,
        goal_transaction_id=(uuid4() if status == "succeeded" else None),
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_stale_succeeded_run_advances_without_summary_inflation(
    make_schedule, processor_service
):
    service, repository = processor_service
    schedule = make_schedule(local_occurrence(2026, 8, 10))
    run = existing_run(schedule, status="succeeded", result_code="success")
    goal = MagicMock(status="active", is_active=True)

    async def select_due(*, now, excluded_schedule_ids):
        if (
            schedule.status == "active"
            and schedule.next_run_at is not None
            and schedule.next_run_at <= now
            and (excluded_schedule_ids is None or schedule.id not in excluded_schedule_ids)
        ):
            return schedule
        return None

    repository.get_next_due_auto_contribution_schedule_for_update.side_effect = select_due
    repository.get_auto_contribution_run.return_value = run
    repository.get_by_id.return_value = goal
    core = AsyncMock()

    with patch.object(service, "_execute_auto_contribution_occurrence_in_uow", core):
        first = await service.process_due_auto_contributions(NOW, batch_size=2)
        second = await service.process_due_auto_contributions(NOW, batch_size=1)

    assert first.selected == first.reconciled == 1
    assert first.succeeded == first.skipped == first.missed_occurrences == 0
    assert second.selected == second.reconciled == second.succeeded == 0
    assert schedule.next_run_at > NOW
    core.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result_code", "expected_status", "expected_reason"),
    [
        ("account_inactive", "paused", "account_inactive"),
        ("goal_archived", "paused", "goal_archived"),
        ("goal_completed", "paused", "goal_completed"),
        ("goal_cancelled", "cancelled", None),
    ],
)
async def test_stale_skipped_run_restores_terminal_lifecycle(
    make_schedule,
    processor_service,
    result_code,
    expected_status,
    expected_reason,
):
    service, repository = processor_service
    schedule = make_schedule(local_occurrence(2026, 8, 17))
    repository.get_next_due_auto_contribution_schedule_for_update.return_value = schedule
    repository.get_auto_contribution_run.return_value = existing_run(
        schedule, status="skipped", result_code=result_code
    )
    core = AsyncMock()

    with patch.object(service, "_execute_auto_contribution_occurrence_in_uow", core):
        summary = await service.process_due_auto_contributions(NOW, batch_size=1)

    assert summary.reconciled == 1
    assert summary.succeeded == summary.skipped == 0
    assert schedule.status == expected_status
    assert schedule.pause_reason == expected_reason
    assert schedule.next_run_at is None
    core.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("result_code", ["insufficient_available_balance", "currency_mismatch"])
async def test_stale_consumed_skip_advances_cursor(make_schedule, processor_service, result_code):
    service, repository = processor_service
    schedule = make_schedule(local_occurrence(2026, 8, 17))
    repository.get_next_due_auto_contribution_schedule_for_update.return_value = schedule
    repository.get_auto_contribution_run.return_value = existing_run(
        schedule, status="skipped", result_code=result_code
    )

    summary = await service.process_due_auto_contributions(NOW, batch_size=1)

    assert summary.reconciled == 1
    assert summary.skipped == 0
    assert schedule.status == "active"
    assert schedule.next_run_at > NOW


@pytest.mark.asyncio
async def test_stale_succeeded_run_completed_goal_pauses_without_completed_inflation(
    make_schedule, processor_service
):
    service, repository = processor_service
    schedule = make_schedule(local_occurrence(2026, 8, 17))
    repository.get_next_due_auto_contribution_schedule_for_update.return_value = schedule
    repository.get_auto_contribution_run.return_value = existing_run(
        schedule, status="succeeded", result_code="success"
    )
    repository.get_by_id.return_value = MagicMock(status="completed", is_active=True)

    summary = await service.process_due_auto_contributions(NOW, batch_size=1)

    assert summary.reconciled == 1
    assert summary.completed_goals == 0
    assert summary.paused_schedules == 1
    assert schedule.status == "paused"
    assert schedule.pause_reason == "goal_completed"


@pytest.mark.asyncio
async def test_new_success_that_completes_goal_counts_transition_once(
    make_schedule, processor_service
):
    service, repository = processor_service
    schedule = make_schedule(local_occurrence(2026, 8, 17))
    repository.get_next_due_auto_contribution_schedule_for_update.return_value = schedule

    async def complete_goal(**_kwargs):
        schedule.status = "paused"
        schedule.pause_reason = "goal_completed"
        schedule.next_run_at = None
        return execution_result()

    with patch.object(
        service,
        "_execute_auto_contribution_occurrence_in_uow",
        AsyncMock(side_effect=complete_goal),
    ):
        summary = await service.process_due_auto_contributions(NOW, batch_size=1)

    assert summary.succeeded == 1
    assert summary.completed_goals == 1
    assert summary.paused_schedules == 1


@pytest.mark.asyncio
async def test_idempotent_core_recovery_is_reconciled_not_new_success(
    make_schedule, processor_service
):
    service, repository = processor_service
    schedule = make_schedule(local_occurrence(2026, 8, 17))
    repository.get_next_due_auto_contribution_schedule_for_update.return_value = schedule

    with patch.object(
        service,
        "_execute_auto_contribution_occurrence_in_uow",
        AsyncMock(return_value=execution_result(idempotent=True)),
    ):
        summary = await service.process_due_auto_contributions(NOW, batch_size=1)

    assert summary.reconciled == 1
    assert summary.succeeded == 0


@pytest.mark.asyncio
async def test_processor_logs_start_outcome_finish_and_traceback(make_schedule, processor_service):
    service, repository = processor_service
    schedule = make_schedule(local_occurrence(2026, 8, 17))
    repository.get_next_due_auto_contribution_schedule_for_update.return_value = schedule
    core = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch.object(service, "_execute_auto_contribution_occurrence_in_uow", core),
        patch("app.goals.service.logger") as mocked_logger,
    ):
        await service.process_due_auto_contributions(NOW, batch_size=1)

    info_events = [call.args[0] for call in mocked_logger.info.call_args_list]
    assert "auto_contribution_processor_started" in info_events
    assert "auto_contribution_schedule_selected" in info_events
    assert "auto_contribution_processor_finished" in info_events
    mocked_logger.exception.assert_called_once()
    assert mocked_logger.exception.call_args.args[0] == "auto_contribution_occurrence_failed"
    assert "user_id" not in mocked_logger.exception.call_args.kwargs


def test_processor_primitive_is_internal_only():
    from app.main import app

    paths = app.openapi()["paths"]
    assert all("process_due_auto_contributions" not in path for path in paths)
    assert all("processor" not in path for path in paths)
