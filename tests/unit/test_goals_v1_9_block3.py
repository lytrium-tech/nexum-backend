from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.accounts.exceptions import AccountForbiddenError
from app.accounts.models import Account
from app.core import database
from app.core.database import get_db_session, mark_session_read_only
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.goals.exceptions import GoalForbiddenError, GoalNotActiveError
from app.goals.models import Goal, GoalAutoContributionSchedule
from app.goals.repository import GoalRepository
from app.goals.router import get_goal_service
from app.goals.schemas import (
    GoalAutoContributionScheduleCreate,
    GoalAutoContributionScheduleRead,
    GoalAutoContributionScheduleUpdate,
)
from app.goals.service import GoalService
from app.main import app
from app.users.dependencies import get_current_user_profile_dep
from app.users.schemas import UserRead


def make_goal(
    *,
    user_id=None,
    status="active",
    active=True,
    current=Decimal("100.00"),
    target=Decimal("500.00"),
    currency="COP",
):
    now = datetime.now(UTC)
    return Goal(
        id=uuid4(),
        user_id=user_id or uuid4(),
        name="Meta",
        target_amount=target,
        current_amount=current,
        currency=currency,
        status=status,
        is_active=active,
        created_at=now,
        updated_at=now,
    )


def make_account(*, user_id, active=True, currency="COP", balance=Decimal("20.00")):
    return Account(
        id=uuid4(),
        user_id=user_id,
        name="Cuenta",
        type="bank",
        balance=balance,
        currency=currency,
        is_active=active,
    )


def make_schedule(
    *,
    user_id,
    goal_id,
    account_id,
    status="active",
    pause_reason=None,
    frequency="monthly",
    execution_day="15",
    timezone="America/Bogota",
    start_date=None,
):
    now = datetime.now(UTC)
    return GoalAutoContributionSchedule(
        id=uuid4(),
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        amount=Decimal("100000.00"),
        frequency=frequency,
        execution_day=execution_day,
        timezone=timezone,
        start_date=start_date,
        status=status,
        pause_reason=pause_reason,
        next_run_at=(datetime(2027, 1, 15, 13, 0, tzinfo=UTC) if status == "active" else None),
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def schedule_context():
    user_id = uuid4()
    goal = make_goal(user_id=user_id)
    account = make_account(user_id=user_id)
    repository = AsyncMock()
    repository.get_by_id.return_value = goal
    repository.session = MagicMock()
    repository.session.flush = AsyncMock()
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=False)
    repository.session.begin_nested.return_value = nested

    async def create_schedule(schedule):
        now = datetime.now(UTC)
        schedule.id = schedule.id or uuid4()
        schedule.created_at = schedule.created_at or now
        schedule.updated_at = schedule.updated_at or now
        return schedule

    repository.create_auto_contribution_schedule.side_effect = create_schedule
    account_repository = AsyncMock()
    account_repository.get_by_id.return_value = account
    ledger_repository = AsyncMock()
    service = GoalService(repository, account_repository, ledger_repository)
    return SimpleNamespace(
        user_id=user_id,
        goal=goal,
        account=account,
        repository=repository,
        account_repository=account_repository,
        ledger_repository=ledger_repository,
        service=service,
        nested=nested,
    )


def create_payload(**overrides):
    values = {
        "account_id": overrides.pop("account_id", uuid4()),
        "amount": Decimal("100000.00"),
        "frequency": "monthly",
        "execution_day": "15",
        "timezone": "America/Bogota",
        "start_date": None,
    }
    values.update(overrides)
    return GoalAutoContributionScheduleCreate(**values)


@pytest.fixture
async def schedule_http_client():
    now = datetime.now(UTC)
    user = UserRead(
        id=uuid4(),
        email="schedule@example.com",
        name="Schedule Owner",
        timezone="America/Bogota",
        currency="COP",
        status="active",
        created_at=now,
        updated_at=now,
    )
    service = AsyncMock(spec=GoalService)
    session = AsyncMock()
    session.info = {}
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user_profile_dep] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: session
    app.dependency_overrides[get_goal_service] = lambda: service

    with patch("app.goals.router.get_goal_service", return_value=service):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client, user, service, session

    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (
            datetime(2026, 8, 17, 6, 0, tzinfo=ZoneInfo("America/Bogota")),
            datetime(2026, 8, 17, 13, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 8, 17, 9, 0, tzinfo=ZoneInfo("America/Bogota")),
            datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
        ),
    ],
)
def test_weekly_same_day_before_and_after_execution_time(now, expected):
    result = GoalService._calculate_next_occurrence("weekly", "monday", "America/Bogota", None, now)
    assert result == expected
    assert result.tzinfo is UTC


@pytest.mark.parametrize(
    ("now", "execution_day", "expected"),
    [
        (datetime(2025, 1, 1, tzinfo=UTC), "1", datetime(2025, 1, 1, 8, tzinfo=UTC)),
        (datetime(2025, 1, 1, tzinfo=UTC), "15", datetime(2025, 1, 15, 8, tzinfo=UTC)),
        (datetime(2025, 2, 1, tzinfo=UTC), "28", datetime(2025, 2, 28, 8, tzinfo=UTC)),
        (datetime(2024, 2, 1, tzinfo=UTC), "29", datetime(2024, 2, 29, 8, tzinfo=UTC)),
        (datetime(2025, 4, 1, tzinfo=UTC), "30", datetime(2025, 4, 30, 8, tzinfo=UTC)),
        (datetime(2025, 3, 1, tzinfo=UTC), "31", datetime(2025, 3, 31, 8, tzinfo=UTC)),
        (datetime(2025, 2, 1, tzinfo=UTC), "31", datetime(2025, 2, 28, 8, tzinfo=UTC)),
        (datetime(2025, 4, 1, tzinfo=UTC), "31", datetime(2025, 4, 30, 8, tzinfo=UTC)),
    ],
)
def test_monthly_occurrences_preserve_logical_day_across_short_months(
    now,
    execution_day,
    expected,
):
    assert (
        GoalService._calculate_next_occurrence("monthly", execution_day, "UTC", None, now)
        == expected
    )


def test_start_date_future_today_and_past_do_not_create_catch_up():
    future = GoalService._calculate_next_occurrence(
        "monthly",
        "15",
        "UTC",
        date(2025, 3, 20),
        datetime(2025, 1, 1, tzinfo=UTC),
    )
    today_before = GoalService._calculate_next_occurrence(
        "monthly",
        "15",
        "UTC",
        date(2025, 1, 15),
        datetime(2025, 1, 15, 7, tzinfo=UTC),
    )
    past = GoalService._calculate_next_occurrence(
        "monthly",
        "15",
        "UTC",
        date(2024, 1, 1),
        datetime(2025, 1, 20, tzinfo=UTC),
    )
    today_after = GoalService._calculate_next_occurrence(
        "monthly",
        "15",
        "UTC",
        date(2025, 1, 15),
        datetime(2025, 1, 15, 9, tzinfo=UTC),
    )

    assert future == datetime(2025, 4, 15, 8, tzinfo=UTC)
    assert today_before == datetime(2025, 1, 15, 8, tzinfo=UTC)
    assert past == datetime(2025, 2, 15, 8, tzinfo=UTC)
    assert today_after == datetime(2025, 2, 15, 8, tzinfo=UTC)


def test_dst_preserves_eight_local_while_utc_offset_changes():
    before_dst = GoalService._calculate_next_occurrence(
        "weekly", "monday", "America/New_York", None, datetime(2025, 2, 24, 14, tzinfo=UTC)
    )
    after_dst = GoalService._calculate_next_occurrence(
        "weekly", "monday", "America/New_York", None, datetime(2025, 3, 3, 14, tzinfo=UTC)
    )
    timezone = ZoneInfo("America/New_York")

    assert before_dst == datetime(2025, 3, 3, 13, tzinfo=UTC)
    assert after_dst == datetime(2025, 3, 10, 12, tzinfo=UTC)
    assert before_dst.astimezone(timezone).hour == 8
    assert after_dst.astimezone(timezone).hour == 8


def test_temporal_helper_rejects_naive_now_and_invalid_domain_values():
    with pytest.raises(ValueError, match="zona horaria"):
        GoalService._calculate_next_occurrence(
            "weekly", "monday", "UTC", None, datetime(2025, 1, 1)
        )
    with pytest.raises(ValueError, match="weekly"):
        GoalService._calculate_next_occurrence(
            "weekly", "15", "UTC", None, datetime(2025, 1, 1, tzinfo=UTC)
        )
    with pytest.raises(ValueError, match="monthly"):
        GoalService._calculate_next_occurrence(
            "monthly", "01", "UTC", None, datetime(2025, 1, 1, tzinfo=UTC)
        )


@pytest.mark.parametrize("timezone", ["America/Bogota", "America/New_York", "Europe/Madrid"])
def test_create_schema_accepts_valid_iana_timezones(timezone):
    payload = create_payload(timezone=timezone)
    assert payload.timezone == timezone


@pytest.mark.parametrize("timezone", ["Bogota", "GMT-5-custom", "foo/bar", ""])
def test_create_schema_rejects_invalid_iana_timezones(timezone):
    with pytest.raises(PydanticValidationError):
        create_payload(timezone=timezone)


def test_request_contract_forbids_server_fields_and_noncanonical_month_day():
    for forbidden_field in (
        "id",
        "user_id",
        "goal_id",
        "status",
        "pause_reason",
        "next_run_at",
        "channel",
        "created_at",
        "updated_at",
    ):
        with pytest.raises(PydanticValidationError):
            create_payload(**{forbidden_field: "invalid"})

    with pytest.raises(PydanticValidationError):
        create_payload(execution_day="01")


@pytest.mark.parametrize(
    "field_name",
    ["account_id", "amount", "frequency", "execution_day", "timezone"],
)
def test_patch_rejects_explicit_null_except_start_date(field_name):
    with pytest.raises(PydanticValidationError):
        GoalAutoContributionScheduleUpdate(**{field_name: None})
    assert GoalAutoContributionScheduleUpdate(start_date=None).model_fields_set == {"start_date"}


def test_response_contract_exposes_only_approved_fields(schedule_context):
    schedule = make_schedule(
        user_id=schedule_context.user_id,
        goal_id=schedule_context.goal.id,
        account_id=schedule_context.account.id,
    )
    result = GoalAutoContributionScheduleRead.model_validate(schedule).model_dump()

    assert set(result) == {
        "id",
        "goal_id",
        "account_id",
        "amount",
        "frequency",
        "execution_day",
        "timezone",
        "start_date",
        "status",
        "pause_reason",
        "next_run_at",
        "created_at",
        "updated_at",
    }


@pytest.mark.asyncio
async def test_public_crud_routes_delegate_and_keep_get_read_only(schedule_http_client):
    client, user, service, session = schedule_http_client
    goal_id = uuid4()
    account_id = uuid4()
    now = datetime.now(UTC)
    response_model = GoalAutoContributionScheduleRead(
        id=uuid4(),
        goal_id=goal_id,
        account_id=account_id,
        amount=Decimal("100.00"),
        frequency="monthly",
        execution_day="15",
        timezone="America/Bogota",
        start_date=None,
        status="active",
        pause_reason=None,
        next_run_at=now,
        created_at=now,
        updated_at=now,
    )
    service.get_auto_contribution_schedule.return_value = response_model
    service.put_auto_contribution_schedule.return_value = response_model
    service.patch_auto_contribution_schedule.return_value = response_model
    service.pause_auto_contribution_schedule.return_value = response_model
    service.resume_auto_contribution_schedule.return_value = response_model

    get_response = await client.get(f"/api/v1/goals/{goal_id}/auto-contribution")
    assert get_response.status_code == 200
    service.get_auto_contribution_schedule.assert_awaited_once_with(user.id, goal_id)
    assert session.info == {"nexum_read_only": True}
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()

    put_response = await client.put(
        f"/api/v1/goals/{goal_id}/auto-contribution",
        json={
            "account_id": str(account_id),
            "amount": "100.00",
            "frequency": "monthly",
            "execution_day": "15",
            "timezone": "America/Bogota",
        },
    )
    patch_response = await client.patch(
        f"/api/v1/goals/{goal_id}/auto-contribution",
        json={"amount": "200.00"},
    )
    pause_response = await client.post(f"/api/v1/goals/{goal_id}/auto-contribution/pause")
    resume_response = await client.post(f"/api/v1/goals/{goal_id}/auto-contribution/resume")
    delete_response = await client.delete(f"/api/v1/goals/{goal_id}/auto-contribution")

    assert [
        put_response.status_code,
        patch_response.status_code,
        pause_response.status_code,
        resume_response.status_code,
        delete_response.status_code,
    ] == [200, 200, 200, 200, 204]
    assert session.commit.await_count == 5
    session.rollback.assert_not_awaited()
    service.delete_auto_contribution_schedule.assert_awaited_once_with(user.id, goal_id)


@pytest.mark.asyncio
async def test_read_only_session_marker_rolls_back_instead_of_committing(monkeypatch):
    session = AsyncMock()
    session.info = {}

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(database, "_session_factory", lambda: SessionContext())
    dependency = get_db_session()
    yielded_session = await anext(dependency)
    mark_session_read_only(yielded_session)

    with pytest.raises(StopAsyncIteration):
        await anext(dependency)

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    ("exception", "expected_status"),
    [
        (GoalForbiddenError(), 403),
        (NotFoundError(message="Schedule no encontrado."), 404),
        (ConflictError(message="Schedule duplicado."), 409),
        (ValidationError(message="Configuración inválida."), 422),
    ],
)
@pytest.mark.asyncio
async def test_put_http_maps_domain_errors_and_rolls_back(
    schedule_http_client,
    exception,
    expected_status,
):
    client, _, service, session = schedule_http_client
    service.put_auto_contribution_schedule.side_effect = exception

    response = await client.put(
        f"/api/v1/goals/{uuid4()}/auto-contribution",
        json={
            "account_id": str(uuid4()),
            "amount": "100.00",
            "frequency": "monthly",
            "execution_day": "15",
            "timezone": "America/Bogota",
        },
    )

    assert response.status_code == expected_status
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_put_rejects_internal_channel(schedule_http_client):
    client, _, service, session = schedule_http_client

    response = await client.put(
        f"/api/v1/goals/{uuid4()}/auto-contribution",
        json={
            "account_id": str(uuid4()),
            "amount": "100.00",
            "frequency": "monthly",
            "execution_day": "15",
            "timezone": "America/Bogota",
            "channel": "automatic",
        },
    )

    assert response.status_code == 422
    service.put_auto_contribution_schedule.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_is_strictly_read_only_for_active_and_paused(schedule_context):
    for status in ("active", "paused"):
        schedule_context.repository.get_current_auto_contribution_schedule.return_value = (
            make_schedule(
                user_id=schedule_context.user_id,
                goal_id=schedule_context.goal.id,
                account_id=schedule_context.account.id,
                status=status,
            )
        )
        result = await schedule_context.service.get_auto_contribution_schedule(
            schedule_context.user_id, schedule_context.goal.id
        )
        assert result.status == status

    schedule_context.repository.session.flush.assert_not_awaited()
    schedule_context.repository.get_auto_contribution_schedule_for_update.assert_not_awaited()
    schedule_context.repository.create_auto_contribution_schedule.assert_not_awaited()
    assert schedule_context.ledger_repository.mock_calls == []


@pytest.mark.asyncio
async def test_get_returns_not_found_when_only_cancelled_history_exists(schedule_context):
    schedule_context.repository.get_current_auto_contribution_schedule.return_value = None
    with pytest.raises(NotFoundError):
        await schedule_context.service.get_auto_contribution_schedule(
            schedule_context.user_id, schedule_context.goal.id
        )


@pytest.mark.asyncio
async def test_put_creates_schedule_without_availability_or_goal_remaining_rejection(
    schedule_context,
):
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = None
    original_balance = schedule_context.account.balance
    original_goal_current = schedule_context.goal.current_amount
    payload = create_payload(
        account_id=schedule_context.account.id,
        amount=Decimal("100000.00"),
    )

    result = await schedule_context.service.put_auto_contribution_schedule(
        schedule_context.user_id,
        schedule_context.goal.id,
        payload,
    )

    assert result.amount == Decimal("100000.00")
    assert result.status == "active"
    assert result.next_run_at is not None and result.next_run_at.tzinfo is not None
    schedule_context.repository.create_auto_contribution_schedule.assert_awaited_once()
    assert schedule_context.account.balance == original_balance
    assert schedule_context.goal.current_amount == original_goal_current
    assert schedule_context.goal.status == "active"
    assert schedule_context.ledger_repository.mock_calls == []
    schedule_context.repository.create_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_put_existing_active_is_resource_idempotent(schedule_context):
    schedule = make_schedule(
        user_id=schedule_context.user_id,
        goal_id=schedule_context.goal.id,
        account_id=schedule_context.account.id,
    )
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = schedule
    payload = create_payload(account_id=schedule_context.account.id)

    first = await schedule_context.service.put_auto_contribution_schedule(
        schedule_context.user_id, schedule_context.goal.id, payload
    )
    first_next_run = first.next_run_at
    second = await schedule_context.service.put_auto_contribution_schedule(
        schedule_context.user_id, schedule_context.goal.id, payload
    )

    assert first.id == second.id == schedule.id
    assert first_next_run == second.next_run_at
    schedule_context.repository.create_auto_contribution_schedule.assert_not_awaited()


@pytest.mark.asyncio
async def test_put_existing_paused_preserves_pause_state(schedule_context):
    schedule = make_schedule(
        user_id=schedule_context.user_id,
        goal_id=schedule_context.goal.id,
        account_id=schedule_context.account.id,
        status="paused",
        pause_reason="goal_completed",
    )
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = schedule

    result = await schedule_context.service.put_auto_contribution_schedule(
        schedule_context.user_id,
        schedule_context.goal.id,
        create_payload(account_id=schedule_context.account.id, amount=Decimal("200.00")),
    )

    assert result.status == "paused"
    assert result.pause_reason == "goal_completed"
    assert result.next_run_at is None


@pytest.mark.asyncio
async def test_put_after_cancelled_creates_new_schedule(schedule_context):
    cancelled = make_schedule(
        user_id=schedule_context.user_id,
        goal_id=schedule_context.goal.id,
        account_id=schedule_context.account.id,
        status="cancelled",
    )
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = None

    result = await schedule_context.service.put_auto_contribution_schedule(
        schedule_context.user_id,
        schedule_context.goal.id,
        create_payload(account_id=schedule_context.account.id),
    )

    assert result.id != cancelled.id
    assert cancelled.status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("existing_frequency", "existing_day", "patch"),
    [
        ("monthly", "15", GoalAutoContributionScheduleUpdate(frequency="weekly")),
        ("weekly", "monday", GoalAutoContributionScheduleUpdate(frequency="monthly")),
    ],
)
async def test_patch_validates_merged_calendar_state(
    schedule_context,
    existing_frequency,
    existing_day,
    patch,
):
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = (
        make_schedule(
            user_id=schedule_context.user_id,
            goal_id=schedule_context.goal.id,
            account_id=schedule_context.account.id,
            frequency=existing_frequency,
            execution_day=existing_day,
        )
    )

    with pytest.raises(ValidationError):
        await schedule_context.service.patch_auto_contribution_schedule(
            schedule_context.user_id,
            schedule_context.goal.id,
            patch,
        )


@pytest.mark.asyncio
async def test_patch_amount_and_account_preserve_next_run(schedule_context):
    schedule = make_schedule(
        user_id=schedule_context.user_id,
        goal_id=schedule_context.goal.id,
        account_id=schedule_context.account.id,
    )
    original_next_run = schedule.next_run_at
    new_account = make_account(user_id=schedule_context.user_id)
    schedule_context.account_repository.get_by_id.return_value = new_account
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = schedule

    result = await schedule_context.service.patch_auto_contribution_schedule(
        schedule_context.user_id,
        schedule_context.goal.id,
        GoalAutoContributionScheduleUpdate(
            amount=Decimal("250.00"),
            account_id=new_account.id,
        ),
    )

    assert result.amount == Decimal("250.00")
    assert result.account_id == new_account.id
    assert result.next_run_at == original_next_run
    schedule_context.account_repository.get_by_id.assert_awaited_with(
        new_account.id,
        include_inactive=True,
    )


@pytest.mark.asyncio
async def test_patch_calendar_recalculates_active_and_clears_explicit_start_date(
    schedule_context,
):
    schedule = make_schedule(
        user_id=schedule_context.user_id,
        goal_id=schedule_context.goal.id,
        account_id=schedule_context.account.id,
        start_date=date(2027, 1, 1),
    )
    old_next_run = schedule.next_run_at
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = schedule

    result = await schedule_context.service.patch_auto_contribution_schedule(
        schedule_context.user_id,
        schedule_context.goal.id,
        GoalAutoContributionScheduleUpdate(
            frequency="weekly",
            execution_day="monday",
            timezone="Europe/Madrid",
            start_date=None,
        ),
    )

    assert result.start_date is None
    assert result.frequency == "weekly"
    assert result.next_run_at != old_next_run
    assert result.next_run_at is not None and result.next_run_at.tzinfo is UTC


@pytest.mark.asyncio
async def test_patch_calendar_while_paused_keeps_next_run_null(schedule_context):
    schedule = make_schedule(
        user_id=schedule_context.user_id,
        goal_id=schedule_context.goal.id,
        account_id=schedule_context.account.id,
        status="paused",
        pause_reason="user_paused",
    )
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = schedule

    result = await schedule_context.service.patch_auto_contribution_schedule(
        schedule_context.user_id,
        schedule_context.goal.id,
        GoalAutoContributionScheduleUpdate(frequency="weekly", execution_day="tuesday"),
    )

    assert result.status == "paused"
    assert result.pause_reason == "user_paused"
    assert result.next_run_at is None


@pytest.mark.asyncio
async def test_pause_is_idempotent_and_preserves_system_reason(schedule_context):
    active = make_schedule(
        user_id=schedule_context.user_id,
        goal_id=schedule_context.goal.id,
        account_id=schedule_context.account.id,
    )
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = active
    first = await schedule_context.service.pause_auto_contribution_schedule(
        schedule_context.user_id, schedule_context.goal.id
    )
    assert first.status == "paused"
    assert first.pause_reason == "user_paused"
    assert first.next_run_at is None

    active.pause_reason = "goal_completed"
    schedule_context.repository.session.flush.reset_mock()
    second = await schedule_context.service.pause_auto_contribution_schedule(
        schedule_context.user_id, schedule_context.goal.id
    )
    assert second.pause_reason == "goal_completed"
    schedule_context.repository.session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_revalidates_dependencies_and_recalculates_without_catch_up(schedule_context):
    schedule = make_schedule(
        user_id=schedule_context.user_id,
        goal_id=schedule_context.goal.id,
        account_id=schedule_context.account.id,
        status="paused",
        pause_reason="account_inactive",
        start_date=date(2020, 1, 1),
    )
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = schedule

    result = await schedule_context.service.resume_auto_contribution_schedule(
        schedule_context.user_id, schedule_context.goal.id
    )

    assert result.status == "active"
    assert result.pause_reason is None
    assert result.next_run_at is not None
    assert result.next_run_at > datetime.now(UTC)
    schedule_context.account_repository.get_by_id.assert_awaited_with(
        schedule_context.account.id,
        include_inactive=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("goal_status", "goal_active"),
    [("completed", True), ("cancelled", False), ("active", False)],
)
async def test_put_and_resume_reject_non_operational_goal(
    schedule_context,
    goal_status,
    goal_active,
):
    schedule_context.goal.status = goal_status
    schedule_context.goal.is_active = goal_active
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = (
        make_schedule(
            user_id=schedule_context.user_id,
            goal_id=schedule_context.goal.id,
            account_id=schedule_context.account.id,
            status="paused",
            pause_reason="goal_completed",
        )
    )

    with pytest.raises(GoalNotActiveError):
        await schedule_context.service.put_auto_contribution_schedule(
            schedule_context.user_id,
            schedule_context.goal.id,
            create_payload(account_id=schedule_context.account.id),
        )
    with pytest.raises(GoalNotActiveError):
        await schedule_context.service.resume_auto_contribution_schedule(
            schedule_context.user_id,
            schedule_context.goal.id,
        )


@pytest.mark.asyncio
async def test_account_validation_covers_owner_active_and_same_currency(schedule_context):
    foreign = make_account(user_id=uuid4())
    schedule_context.account_repository.get_by_id.return_value = foreign
    with pytest.raises(AccountForbiddenError):
        await schedule_context.service.put_auto_contribution_schedule(
            schedule_context.user_id,
            schedule_context.goal.id,
            create_payload(account_id=foreign.id),
        )

    inactive = make_account(user_id=schedule_context.user_id, active=False)
    schedule_context.account_repository.get_by_id.return_value = inactive
    with pytest.raises(ForbiddenError):
        await schedule_context.service.put_auto_contribution_schedule(
            schedule_context.user_id,
            schedule_context.goal.id,
            create_payload(account_id=inactive.id),
        )

    mismatched = make_account(user_id=schedule_context.user_id, currency="USD")
    schedule_context.account_repository.get_by_id.return_value = mismatched
    with pytest.raises(ValidationError):
        await schedule_context.service.put_auto_contribution_schedule(
            schedule_context.user_id,
            schedule_context.goal.id,
            create_payload(account_id=mismatched.id),
        )


@pytest.mark.asyncio
async def test_resume_rejects_account_that_remains_inactive(schedule_context):
    inactive = make_account(user_id=schedule_context.user_id, active=False)
    schedule_context.account_repository.get_by_id.return_value = inactive
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = (
        make_schedule(
            user_id=schedule_context.user_id,
            goal_id=schedule_context.goal.id,
            account_id=inactive.id,
            status="paused",
            pause_reason="account_inactive",
        )
    )

    with pytest.raises(ForbiddenError):
        await schedule_context.service.resume_auto_contribution_schedule(
            schedule_context.user_id,
            schedule_context.goal.id,
        )


@pytest.mark.asyncio
async def test_cancel_is_logical_for_active_and_paused(schedule_context):
    for initial_status in ("active", "paused"):
        schedule = make_schedule(
            user_id=schedule_context.user_id,
            goal_id=schedule_context.goal.id,
            account_id=schedule_context.account.id,
            status=initial_status,
            pause_reason="user_paused" if initial_status == "paused" else None,
        )
        schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = (
            schedule
        )
        await schedule_context.service.delete_auto_contribution_schedule(
            schedule_context.user_id, schedule_context.goal.id
        )
        assert schedule.status == "cancelled"
        assert schedule.pause_reason is None
        assert schedule.next_run_at is None


@pytest.mark.asyncio
async def test_cancelled_schedule_is_immutable_via_current_resource_operations(schedule_context):
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = None

    for operation in (
        lambda: schedule_context.service.patch_auto_contribution_schedule(
            schedule_context.user_id,
            schedule_context.goal.id,
            GoalAutoContributionScheduleUpdate(amount=Decimal("1.00")),
        ),
        lambda: schedule_context.service.pause_auto_contribution_schedule(
            schedule_context.user_id, schedule_context.goal.id
        ),
        lambda: schedule_context.service.resume_auto_contribution_schedule(
            schedule_context.user_id, schedule_context.goal.id
        ),
        lambda: schedule_context.service.delete_auto_contribution_schedule(
            schedule_context.user_id, schedule_context.goal.id
        ),
    ):
        with pytest.raises(NotFoundError):
            await operation()


@pytest.mark.asyncio
async def test_every_operation_checks_goal_ownership_before_schedule_access(schedule_context):
    schedule_context.goal.user_id = uuid4()
    payload = create_payload(account_id=schedule_context.account.id)
    operations = (
        lambda: schedule_context.service.get_auto_contribution_schedule(
            schedule_context.user_id, schedule_context.goal.id
        ),
        lambda: schedule_context.service.put_auto_contribution_schedule(
            schedule_context.user_id, schedule_context.goal.id, payload
        ),
        lambda: schedule_context.service.patch_auto_contribution_schedule(
            schedule_context.user_id,
            schedule_context.goal.id,
            GoalAutoContributionScheduleUpdate(amount=Decimal("1.00")),
        ),
        lambda: schedule_context.service.pause_auto_contribution_schedule(
            schedule_context.user_id, schedule_context.goal.id
        ),
        lambda: schedule_context.service.resume_auto_contribution_schedule(
            schedule_context.user_id, schedule_context.goal.id
        ),
        lambda: schedule_context.service.delete_auto_contribution_schedule(
            schedule_context.user_id, schedule_context.goal.id
        ),
    )

    for operation in operations:
        with pytest.raises(GoalForbiddenError):
            await operation()

    schedule_context.repository.get_current_auto_contribution_schedule.assert_not_awaited()
    schedule_context.repository.get_auto_contribution_schedule_for_update.assert_not_awaited()


class ConstraintError(Exception):
    def __init__(self, constraint_name):
        self.constraint_name = constraint_name
        self.diag = SimpleNamespace(constraint_name=constraint_name)
        super().__init__(constraint_name)


@pytest.mark.asyncio
async def test_concurrent_create_maps_only_expected_unique_violation(schedule_context):
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = None
    expected = IntegrityError(
        "insert",
        {},
        ConstraintError("ix_goal_auto_contrib_goal_id_active"),
    )
    schedule_context.repository.create_auto_contribution_schedule.side_effect = expected

    with pytest.raises(ConflictError):
        await schedule_context.service.put_auto_contribution_schedule(
            schedule_context.user_id,
            schedule_context.goal.id,
            create_payload(account_id=schedule_context.account.id),
        )
    schedule_context.nested.__aexit__.assert_awaited()


@pytest.mark.asyncio
async def test_unrelated_integrity_error_is_not_misreported_as_schedule_conflict(schedule_context):
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = None
    unexpected = IntegrityError("insert", {}, ConstraintError("chk_gacs_amount_pos"))
    schedule_context.repository.create_auto_contribution_schedule.side_effect = unexpected

    with pytest.raises(IntegrityError) as raised:
        await schedule_context.service.put_auto_contribution_schedule(
            schedule_context.user_id,
            schedule_context.goal.id,
            create_payload(account_id=schedule_context.account.id),
        )
    assert raised.value is unexpected


@pytest.mark.asyncio
async def test_repository_uses_lock_only_for_mutation_lookup():
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    repository = GoalRepository(session)
    user_id = uuid4()
    goal_id = uuid4()

    await repository.get_current_auto_contribution_schedule(user_id, goal_id)
    read_statement = session.execute.await_args.args[0]
    await repository.get_auto_contribution_schedule_for_update(user_id, goal_id)
    lock_statement = session.execute.await_args.args[0]

    assert "FOR UPDATE" not in str(
        read_statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert "FOR UPDATE" in str(
        lock_statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


@pytest.mark.asyncio
async def test_crud_lifecycle_is_financially_neutral(schedule_context):
    schedule = make_schedule(
        user_id=schedule_context.user_id,
        goal_id=schedule_context.goal.id,
        account_id=schedule_context.account.id,
    )
    schedule_context.repository.get_auto_contribution_schedule_for_update.return_value = schedule
    balance_before = schedule_context.account.balance
    current_before = schedule_context.goal.current_amount
    goal_status_before = schedule_context.goal.status

    await schedule_context.service.put_auto_contribution_schedule(
        schedule_context.user_id,
        schedule_context.goal.id,
        create_payload(account_id=schedule_context.account.id),
    )
    await schedule_context.service.patch_auto_contribution_schedule(
        schedule_context.user_id,
        schedule_context.goal.id,
        GoalAutoContributionScheduleUpdate(amount=Decimal("200.00")),
    )
    await schedule_context.service.pause_auto_contribution_schedule(
        schedule_context.user_id, schedule_context.goal.id
    )
    await schedule_context.service.resume_auto_contribution_schedule(
        schedule_context.user_id, schedule_context.goal.id
    )
    await schedule_context.service.delete_auto_contribution_schedule(
        schedule_context.user_id, schedule_context.goal.id
    )

    assert schedule_context.account.balance == balance_before
    assert schedule_context.goal.current_amount == current_before
    assert schedule_context.goal.status == goal_status_before
    assert schedule_context.ledger_repository.mock_calls == []
    schedule_context.repository.create_transaction.assert_not_awaited()
    schedule_context.repository.calculate_reserved_by_account.assert_not_awaited()
    assert not any("run" in str(call).lower() for call in schedule_context.repository.mock_calls)


def test_openapi_has_exact_block3_routes_and_preserves_history_contract():
    schema = app.openapi()
    base = "/api/v1/goals/{goal_id}/auto-contribution"
    assert set(schema["paths"][base]) == {"get", "put", "patch", "delete"}
    assert set(schema["paths"][f"{base}/pause"]) == {"post"}
    assert set(schema["paths"][f"{base}/resume"]) == {"post"}
    assert not any("run" in path or "execute" in path for path in schema["paths"] if base in path)

    history = schema["components"]["schemas"]["GoalTransactionRead"]
    assert history["properties"]["origin"]["enum"] == ["legacy", "native"]
    assert history["properties"]["channel"]["enum"] == ["manual", "automatic", "legacy"]
