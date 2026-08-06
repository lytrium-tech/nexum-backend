import importlib.util
from pathlib import Path

import pytest


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
def goals_migration():
    migration_path = Path("alembic/versions/goals_v1_ph1_reserved.py")
    spec = importlib.util.spec_from_file_location("goals_v1_ph1_reserved", migration_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_constraint_converges_known_states_and_matches_model(
    goals_migration,
    monkeypatch,
):
    recorder = OperationRecorder()
    monkeypatch.setattr(goals_migration, "op", recorder)

    goals_migration._upgrade_financial_events_type_constraint()

    expected_phase1_event_types = (
        "income",
        "expense",
        "goal_contribution",
        "obligation_payment",
        "credit_card_purchase",
        "credit_card_payment",
        "manual_adjustment",
        "transfer_out",
        "transfer_in",
        "opening_balance",
        "balance_adjustment",
    )

    assert tuple(goals_migration.POST_GOALS_FINANCIAL_EVENT_TYPES) == expected_phase1_event_types
    assert [call[0] for call in recorder.calls] == [
        "execute",
        "drop_constraint",
        "create_check_constraint",
    ]
    precheck = recorder.calls[0][1]
    assert "regexp_matches" in precheck
    assert "convalidated" in precheck
    assert "unknown financial event types" in precheck
    assert " LIKE " not in precheck

    create_args, create_kwargs = recorder.calls[2][1]
    assert create_args == (
        "financial_events_type_check",
        "financial_events",
        goals_migration._event_type_check_sql(goals_migration.POST_GOALS_FINANCIAL_EVENT_TYPES),
    )
    assert create_kwargs == {}


def test_upgrade_preserves_every_pre_goals_type_and_adds_only_balance_adjustment(
    goals_migration,
):
    previous = set(goals_migration.PRE_GOALS_FINANCIAL_EVENT_TYPES)
    upgraded = set(goals_migration.POST_GOALS_FINANCIAL_EVENT_TYPES)

    assert "opening_balance" in previous
    assert upgraded - previous == {"balance_adjustment"}
    assert previous < upgraded


def test_constraint_precheck_rejects_unknown_semantics(goals_migration):
    precheck = goals_migration._constraint_precheck_sql(
        (goals_migration.PRE_GOALS_FINANCIAL_EVENT_TYPES,),
        "test",
    )

    assert "IF NOT (v_allowed_types =" in precheck
    assert "unknown financial event types" in precheck
    assert "pg_get_constraintdef" in precheck
    assert "regexp_matches" in precheck


def test_downgrade_restores_pre_goals_constraint_without_blocking_opening_balance(
    goals_migration,
    monkeypatch,
):
    recorder = OperationRecorder()
    monkeypatch.setattr(goals_migration, "op", recorder)

    goals_migration._downgrade_financial_events_type_constraint()

    assert [call[0] for call in recorder.calls] == [
        "execute",
        "execute",
        "drop_constraint",
        "create_check_constraint",
    ]
    remaining_rows_check = recorder.calls[1][1]
    assert "WHERE event_type = 'balance_adjustment'" in remaining_rows_check
    assert "opening_balance" not in remaining_rows_check

    create_args, create_kwargs = recorder.calls[3][1]
    assert create_args == (
        "financial_events_type_check",
        "financial_events",
        goals_migration._event_type_check_sql(goals_migration.PRE_GOALS_FINANCIAL_EVENT_TYPES),
    )
    assert create_kwargs == {}
    assert "opening_balance" in create_args[2]
    assert "balance_adjustment" not in create_args[2]


def test_upgrade_downgrade_upgrade_constraint_definition_is_reproducible(
    goals_migration,
):
    first_upgrade = goals_migration._event_type_check_sql(
        goals_migration.POST_GOALS_FINANCIAL_EVENT_TYPES
    )
    downgrade = goals_migration._event_type_check_sql(
        goals_migration.PRE_GOALS_FINANCIAL_EVENT_TYPES
    )
    second_upgrade = goals_migration._event_type_check_sql(
        goals_migration.POST_GOALS_FINANCIAL_EVENT_TYPES
    )

    assert first_upgrade == second_upgrade
    assert first_upgrade != downgrade
    assert "balance_adjustment" in first_upgrade
    assert "balance_adjustment" not in downgrade
