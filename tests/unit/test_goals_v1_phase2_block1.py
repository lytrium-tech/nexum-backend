import importlib.util
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.goals.repository import GoalRepository
from app.goals.schemas import GoalReleaseCreate, GoalReleaseResult
from app.ledger.enums import EventType
from app.ledger.models import (
    FINANCIAL_EVENT_TYPE_CHECK_SQL,
    FINANCIAL_EVENT_TYPES,
    FinancialEvent,
)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _RecordingSession:
    def __init__(self, value):
        self.value = value
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _ScalarResult(self.value)


class OperationRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def execute(self, statement: str) -> None:
        self.calls.append(("execute", statement))

    def drop_constraint(self, *args, **kwargs) -> None:
        self.calls.append(("drop_constraint", (args, kwargs)))

    def create_check_constraint(self, *args, **kwargs) -> None:
        self.calls.append(("create_check_constraint", (args, kwargs)))


@pytest.fixture
def releases_migration():
    migration_path = Path("alembic/versions/goals_v1_ph2_releases.py")
    spec = importlib.util.spec_from_file_location("goals_v1_ph2_releases", migration_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_goal_release_create_schema_valid_and_command_optional():
    payload = GoalReleaseCreate(
        account_id=uuid4(),
        amount=Decimal("150.00"),
        description="Test release",
    )

    assert payload.amount == Decimal("150.00")
    assert payload.command_id is None
    assert payload.description == "Test release"


@pytest.mark.parametrize(
    "amount",
    (
        Decimal("0"),
        Decimal("-0.01"),
        Decimal("1.001"),
        Decimal("1000000000000.00"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ),
)
def test_goal_release_create_rejects_invalid_amounts(amount):
    with pytest.raises(ValidationError):
        GoalReleaseCreate(account_id=uuid4(), amount=amount)


def test_goal_release_create_accepts_numeric_14_2_maximum():
    payload = GoalReleaseCreate(
        account_id=uuid4(),
        amount=Decimal("999999999999.99"),
    )

    assert payload.amount == Decimal("999999999999.99")


def test_goal_release_create_requires_account_and_limits_description():
    with pytest.raises(ValidationError):
        GoalReleaseCreate(amount=Decimal("10.00"))

    with pytest.raises(ValidationError):
        GoalReleaseCreate(
            account_id=uuid4(),
            amount=Decimal("10.00"),
            description="x" * 256,
        )


def test_goal_release_create_forbids_authoritative_currency_and_other_extras():
    with pytest.raises(ValidationError):
        GoalReleaseCreate(
            account_id=uuid4(),
            amount=Decimal("10.00"),
            currency="USD",
        )

    with pytest.raises(ValidationError):
        GoalReleaseCreate(
            account_id=uuid4(),
            amount=Decimal("10.00"),
            applied_amount=Decimal("10.00"),
        )


def test_goal_release_result_currency_uppercase():
    result = GoalReleaseResult(
        transaction_id=uuid4(),
        event_id=uuid4(),
        goal_id=uuid4(),
        account_id=uuid4(),
        released_amount=Decimal("100"),
        source_currency="usd",
        applied_amount=Decimal("100"),
        goal_currency="usd",
        goal_current_amount=Decimal("0"),
        goal_remaining_amount=Decimal("100"),
        goal_status="active",
        account_balance=Decimal("100"),
        goal_account_reserved_amount=Decimal("0"),
        goal_total_reserved_amount=Decimal("0"),
        account_total_reserved_amount=Decimal("0"),
        available_balance=Decimal("100"),
        idempotent=False,
        created_at="2023-01-01T00:00:00Z",
    )

    assert result.source_currency == "USD"
    assert result.goal_currency == "USD"


def test_ledger_enum_model_and_phase2_migration_are_exactly_aligned(
    releases_migration,
):
    model_constraint = next(
        constraint
        for constraint in FinancialEvent.__table_args__
        if getattr(constraint, "name", None) == "financial_events_type_check"
    )

    assert EventType.GOAL_RELEASE.value == "goal_release"
    assert tuple(releases_migration.GOALS_PH2_FINANCIAL_EVENT_TYPES) == (FINANCIAL_EVENT_TYPES)
    assert tuple(releases_migration.POST_GOALS_FINANCIAL_EVENT_TYPES) == FINANCIAL_EVENT_TYPES[:-1]
    assert set(releases_migration.GOALS_PH2_FINANCIAL_EVENT_TYPES) - set(
        releases_migration.POST_GOALS_FINANCIAL_EVENT_TYPES
    ) == {"goal_release"}
    assert model_constraint.name == "financial_events_type_check"
    assert model_constraint.sqltext.text == FINANCIAL_EVENT_TYPE_CHECK_SQL
    assert model_constraint.sqltext.text == releases_migration._event_type_check_sql(
        releases_migration.GOALS_PH2_FINANCIAL_EVENT_TYPES
    )


def test_constraint_sql_has_stable_order_and_safe_escaping(releases_migration):
    first = releases_migration._event_type_check_sql(
        releases_migration.GOALS_PH2_FINANCIAL_EVENT_TYPES
    )
    second = releases_migration._event_type_check_sql(
        releases_migration.GOALS_PH2_FINANCIAL_EVENT_TYPES
    )

    assert first == second
    assert releases_migration._event_type_check_sql(("quoted'value",)) == (
        "event_type = ANY (ARRAY['quoted''value'])"
    )


def _compiled_sql(statement) -> str:
    compiled = statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    return str(compiled)


@pytest.mark.asyncio
async def test_repository_calculates_reserved_by_goal_and_account_from_source_amount():
    session = _RecordingSession(Decimal("400.00"))
    repository = GoalRepository(session)  # type: ignore[arg-type]
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    goal_id = UUID("22222222-2222-2222-2222-222222222222")
    account_id = UUID("33333333-3333-3333-3333-333333333333")

    result = await repository.calculate_reserved_by_goal_and_account(
        user_id,
        goal_id,
        account_id,
    )
    sql = _compiled_sql(session.statement)

    assert result == Decimal("400.00")
    assert "sum(goal_transactions.source_amount)" in sql
    assert "applied_amount" not in sql
    assert "IN ('allocation', 'legacy_import')" in sql
    assert " = 'release'" in sql
    assert ") - coalesce(" in sql
    assert "adjustment" not in sql
    assert f"goal_transactions.user_id = '{user_id}'" in sql
    assert f"goal_transactions.goal_id = '{goal_id}'" in sql
    assert f"goal_transactions.account_id = '{account_id}'" in sql


@pytest.mark.asyncio
async def test_repository_calculates_total_reserved_by_goal_from_applied_amount():
    session = _RecordingSession(Decimal("700.00"))
    repository = GoalRepository(session)  # type: ignore[arg-type]
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    goal_id = UUID("22222222-2222-2222-2222-222222222222")

    result = await repository.calculate_total_reserved_by_goal(user_id, goal_id)
    sql = _compiled_sql(session.statement)

    assert result == Decimal("700.00")
    assert "sum(goal_transactions.applied_amount)" in sql
    assert "IN ('allocation', 'legacy_import')" in sql
    assert " = 'release'" in sql
    assert ") - coalesce(" in sql
    assert "adjustment" not in sql
    assert f"goal_transactions.user_id = '{user_id}'" in sql
    assert f"goal_transactions.goal_id = '{goal_id}'" in sql
    assert "goal_transactions.account_id =" not in sql


@pytest.mark.asyncio
async def test_repository_calculates_progress_from_applied_amount_with_legacy_import():
    session = _RecordingSession(Decimal("700.00"))
    repository = GoalRepository(session)  # type: ignore[arg-type]
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    goal_id = UUID("22222222-2222-2222-2222-222222222222")

    result = await repository.calculate_progress_by_goal(goal_id, user_id)
    sql = _compiled_sql(session.statement)

    assert result == Decimal("700.00")
    assert "sum(goal_transactions.applied_amount)" in sql
    assert "source_amount" not in sql
    assert "IN ('allocation', 'legacy_import')" in sql
    assert " = 'release'" in sql
    assert "adjustment" not in sql


def test_migration_revision_and_exclusive_type_addition(releases_migration):
    assert releases_migration.revision == "goals_v1_ph2_releases"
    assert releases_migration.down_revision == "goals_v1_ph1_reserved"
    assert len(releases_migration.revision) <= 32
    assert tuple(releases_migration.GOALS_PH2_FINANCIAL_EVENT_TYPES) == (
        *releases_migration.POST_GOALS_FINANCIAL_EVENT_TYPES,
        "goal_release",
    )


def test_precheck_supports_absent_or_known_legacy_constraint(releases_migration):
    precheck = releases_migration._constraints_precheck_sql(
        releases_migration.POST_GOALS_FINANCIAL_EVENT_TYPES,
        "test",
    )

    assert "SELECT EXISTS" in precheck
    assert "IF v_legacy_exists THEN" in precheck
    assert releases_migration.LEGACY_EVENT_TYPE_CONSTRAINT in precheck
    for event_types in releases_migration.KNOWN_LEGACY_CONSTRAINT_EVENT_TYPE_SETS:
        assert releases_migration._sql_text_array(event_types) in precheck
    assert "unknown legacy financial event types" in precheck


def test_removing_known_legacy_constraint_preserves_canonical_protection(
    releases_migration,
):
    canonical_types = set(releases_migration.POST_GOALS_FINANCIAL_EVENT_TYPES)

    assert all(
        set(legacy_types) <= canonical_types
        for legacy_types in releases_migration.KNOWN_LEGACY_CONSTRAINT_EVENT_TYPE_SETS
    )
    assert "unknown_type" not in canonical_types


def test_upgrade_validates_exact_previous_state_and_creates_canonical_constraint(
    releases_migration,
    monkeypatch,
):
    recorder = OperationRecorder()
    monkeypatch.setattr(releases_migration, "op", recorder)

    releases_migration.upgrade()

    assert [call[0] for call in recorder.calls] == [
        "execute",
        "execute",
        "drop_constraint",
        "create_check_constraint",
    ]
    precheck = recorder.calls[0][1]
    assert "regexp_matches" in precheck
    assert "convalidated" in precheck
    assert "IF NOT (v_canonical_types =" in precheck
    assert "unknown canonical financial event types" in precheck
    assert "IF v_legacy_exists THEN" in precheck
    assert " LIKE " not in precheck
    assert recorder.calls[1] == (
        "execute",
        releases_migration.DROP_LEGACY_CONSTRAINT_SQL,
    )

    drop_args, drop_kwargs = recorder.calls[2][1]
    assert drop_args == (
        releases_migration.CANONICAL_EVENT_TYPE_CONSTRAINT,
        "financial_events",
    )
    assert drop_kwargs == {"type_": "check"}

    create_args, create_kwargs = recorder.calls[3][1]
    assert create_args == (
        releases_migration.CANONICAL_EVENT_TYPE_CONSTRAINT,
        "financial_events",
        releases_migration._event_type_check_sql(
            releases_migration.GOALS_PH2_FINANCIAL_EVENT_TYPES
        ),
    )
    assert create_kwargs == {}


def test_upgrade_finishes_with_only_the_canonical_constraint(
    releases_migration,
    monkeypatch,
):
    recorder = OperationRecorder()
    monkeypatch.setattr(releases_migration, "op", recorder)

    releases_migration.upgrade()

    created_names = [
        call[1][0][0] for call in recorder.calls if call[0] == "create_check_constraint"
    ]
    assert created_names == [releases_migration.CANONICAL_EVENT_TYPE_CONSTRAINT]
    assert releases_migration.LEGACY_EVENT_TYPE_CONSTRAINT in (
        releases_migration.DROP_LEGACY_CONSTRAINT_SQL
    )
    assert all(
        releases_migration.LEGACY_EVENT_TYPE_CONSTRAINT not in created_name
        for created_name in created_names
    )


def test_downgrade_blocks_existing_releases_without_deleting_or_backfilling(
    releases_migration,
    monkeypatch,
):
    recorder = OperationRecorder()
    monkeypatch.setattr(releases_migration, "op", recorder)

    releases_migration.downgrade()

    assert [call[0] for call in recorder.calls] == [
        "execute",
        "execute",
        "execute",
        "drop_constraint",
        "create_check_constraint",
    ]
    precheck = recorder.calls[0][1]
    blocking_check = recorder.calls[1][1]
    executed_sql = f"{precheck}\n{blocking_check}".upper()

    assert "regexp_matches" in precheck
    assert "IF NOT (v_canonical_types =" in precheck
    assert "WHERE event_type = 'goal_release'" in blocking_check
    assert "RAISE EXCEPTION" in blocking_check
    assert "DELETE " not in executed_sql
    assert "INSERT " not in executed_sql
    assert "UPDATE " not in executed_sql
    assert recorder.calls[2] == (
        "execute",
        releases_migration.DROP_LEGACY_CONSTRAINT_SQL,
    )

    create_args, create_kwargs = recorder.calls[4][1]
    assert create_args == (
        releases_migration.CANONICAL_EVENT_TYPE_CONSTRAINT,
        "financial_events",
        releases_migration._event_type_check_sql(
            releases_migration.POST_GOALS_FINANCIAL_EVENT_TYPES
        ),
    )
    assert create_kwargs == {}
    assert "goal_release" not in create_args[2]


def test_downgrade_keeps_legacy_removed_and_restores_phase1_canonical_state(
    releases_migration,
    monkeypatch,
):
    recorder = OperationRecorder()
    monkeypatch.setattr(releases_migration, "op", recorder)

    releases_migration.downgrade()

    created_names = [
        call[1][0][0] for call in recorder.calls if call[0] == "create_check_constraint"
    ]
    assert created_names == [releases_migration.CANONICAL_EVENT_TYPE_CONSTRAINT]
    assert releases_migration.LEGACY_EVENT_TYPE_CONSTRAINT in (
        releases_migration.DROP_LEGACY_CONSTRAINT_SQL
    )


def test_upgrade_downgrade_reupgrade_is_structurally_reproducible(
    releases_migration,
    monkeypatch,
):
    first_upgrade = OperationRecorder()
    monkeypatch.setattr(releases_migration, "op", first_upgrade)
    releases_migration.upgrade()

    downgrade = OperationRecorder()
    monkeypatch.setattr(releases_migration, "op", downgrade)
    releases_migration.downgrade()

    second_upgrade = OperationRecorder()
    monkeypatch.setattr(releases_migration, "op", second_upgrade)
    releases_migration.upgrade()

    assert first_upgrade.calls == second_upgrade.calls
    first_upgrade_sql = first_upgrade.calls[-1][1][0][2]
    downgrade_sql = downgrade.calls[-1][1][0][2]
    assert first_upgrade_sql == releases_migration._event_type_check_sql(
        releases_migration.GOALS_PH2_FINANCIAL_EVENT_TYPES
    )
    assert downgrade_sql == releases_migration._event_type_check_sql(
        releases_migration.POST_GOALS_FINANCIAL_EVENT_TYPES
    )
