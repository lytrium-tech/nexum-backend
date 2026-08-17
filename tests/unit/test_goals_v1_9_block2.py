import importlib
import importlib.util
import re
from pathlib import Path
from unittest.mock import MagicMock

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.goals.models import (
    GoalAutoContributionRun,
    GoalAutoContributionSchedule,
    GoalTransaction,
)

MIGRATION_PATH = (
    Path(__file__).parents[2] / "alembic" / "versions" / "goals_v1_9_auto_contributions.py"
)
POSTGRESQL = postgresql.dialect()

SCHEDULE_COLUMNS = {
    "id",
    "user_id",
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
RUN_COLUMNS = {
    "id",
    "schedule_id",
    "scheduled_for",
    "executed_at",
    "status",
    "result_code",
    "configured_amount",
    "executed_amount",
    "missed_occurrences_count",
    "goal_transaction_id",
    "created_at",
}


def load_migration():
    spec = importlib.util.spec_from_file_location("goals_v1_9_auto_contributions", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_upgrade():
    migration = load_migration()
    operation = MagicMock()
    migration.op = operation
    migration.upgrade()
    return migration, operation


def table_call(operation: MagicMock, table_name: str):
    return next(
        call for call in operation.create_table.call_args_list if call.args[0] == table_name
    )


def migration_columns(operation: MagicMock, table_name: str) -> dict[str, sa.Column]:
    return {
        item.name: item
        for item in table_call(operation, table_name).args[1:]
        if isinstance(item, sa.Column)
    }


def named_constraints(items) -> dict[str, sa.Constraint]:
    return {
        item.name: item
        for item in items
        if isinstance(item, sa.Constraint) and item.name is not None
    }


def normalized_sql(expression) -> str:
    if isinstance(expression, str):
        return " ".join(expression.split())
    return " ".join(
        str(expression.compile(dialect=POSTGRESQL, compile_kwargs={"literal_binds": True})).split()
    )


def server_default(column: sa.Column) -> str | None:
    if column.server_default is None:
        return None
    return normalized_sql(column.server_default.arg)


def column_signature(column: sa.Column) -> tuple[str, bool, str | None]:
    return (
        str(column.type.compile(dialect=POSTGRESQL)),
        column.nullable,
        server_default(column),
    )


def foreign_key_signatures(table) -> set[tuple[str, str, str | None]]:
    return {
        (element.parent.name, element.target_fullname, constraint.ondelete)
        for constraint in table.foreign_key_constraints
        for element in constraint.elements
    }


def test_migration_revision_and_single_head() -> None:
    migration = load_migration()
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert migration.revision == "goals_v1_9_auto_contributions"
    assert len(migration.revision) <= 32
    assert migration.down_revision == "goals_v1_ph2_releases"
    assert script.get_heads() == ["goals_v1_9_auto_contributions"]


def test_migration_upgrade_creates_only_block2_objects() -> None:
    _, operation = run_upgrade()

    assert [call.args[0] for call in operation.create_table.call_args_list] == [
        "goal_auto_contribution_schedules",
        "goal_auto_contribution_runs",
    ]
    assert [call.args[0] for call in operation.create_index.call_args_list] == [
        "ix_goal_auto_contrib_goal_id_active",
        "ix_goal_auto_contrib_status_next_run",
        "ix_goal_auto_contrib_user_id",
        "ix_goal_auto_contrib_runs_schedule_id",
    ]
    operation.alter_column.assert_not_called()
    operation.drop_column.assert_not_called()
    operation.drop_table.assert_not_called()


def test_migration_downgrade_reverses_objects_in_safe_order() -> None:
    migration = load_migration()
    operation = MagicMock()
    migration.op = operation

    migration.downgrade()

    assert [call.args[0] for call in operation.drop_index.call_args_list] == [
        "ix_goal_auto_contrib_runs_schedule_id",
        "ix_goal_auto_contrib_user_id",
        "ix_goal_auto_contrib_status_next_run",
        "ix_goal_auto_contrib_goal_id_active",
    ]
    assert [call.args[0] for call in operation.drop_table.call_args_list] == [
        "goal_auto_contribution_runs",
        "goal_auto_contribution_schedules",
    ]


def test_schedule_columns_types_nullability_and_defaults_match_migration() -> None:
    importlib.import_module("app.accounts.models")
    importlib.import_module("app.users.models")
    _, operation = run_upgrade()
    model_columns = GoalAutoContributionSchedule.__table__.columns
    migrated_columns = migration_columns(operation, "goal_auto_contribution_schedules")

    assert set(model_columns.keys()) == set(migrated_columns) == SCHEDULE_COLUMNS
    assert {name: column_signature(model_columns[name]) for name in SCHEDULE_COLUMNS} == {
        name: column_signature(migrated_columns[name]) for name in SCHEDULE_COLUMNS
    }
    assert model_columns.status.default.arg == "active"
    assert str(model_columns.amount.type) == "NUMERIC(14, 2)"


def test_run_columns_types_nullability_and_defaults_match_migration() -> None:
    _, operation = run_upgrade()
    model_columns = GoalAutoContributionRun.__table__.columns
    migrated_columns = migration_columns(operation, "goal_auto_contribution_runs")

    assert set(model_columns.keys()) == set(migrated_columns) == RUN_COLUMNS
    assert {name: column_signature(model_columns[name]) for name in RUN_COLUMNS} == {
        name: column_signature(migrated_columns[name]) for name in RUN_COLUMNS
    }
    assert model_columns.missed_occurrences_count.default.arg == 0
    assert str(model_columns.configured_amount.type) == "NUMERIC(14, 2)"
    assert str(model_columns.executed_amount.type) == "NUMERIC(14, 2)"


def test_schedule_checks_match_migration_exactly() -> None:
    _, operation = run_upgrade()
    model = named_constraints(GoalAutoContributionSchedule.__table__.constraints)
    migrated = named_constraints(table_call(operation, "goal_auto_contribution_schedules").args[1:])

    expected_names = {
        "chk_gacs_amount_pos",
        "chk_gacs_frequency",
        "chk_gacs_execution_day",
        "chk_gacs_status",
        "chk_gacs_pause_reason",
        "chk_gacs_active_next_run",
    }
    assert set(model) == set(migrated) == expected_names
    assert {name: normalized_sql(model[name].sqltext) for name in expected_names} == {
        name: normalized_sql(migrated[name].sqltext) for name in expected_names
    }
    assert normalized_sql(model["chk_gacs_amount_pos"].sqltext) == "amount > 0"
    assert normalized_sql(model["chk_gacs_frequency"].sqltext) == (
        "frequency IN ('weekly', 'monthly')"
    )


def test_schedule_explicit_column_contract() -> None:
    columns = GoalAutoContributionSchedule.__table__.columns

    assert isinstance(columns.id.type, postgresql.UUID)
    assert columns.id.nullable is False
    assert server_default(columns.id) == "gen_random_uuid()"
    assert columns.amount.type.precision == 14
    assert columns.amount.type.scale == 2
    assert columns.amount.nullable is False
    assert isinstance(columns.frequency.type, sa.Text)
    assert columns.frequency.nullable is False
    assert isinstance(columns.execution_day.type, sa.Text)
    assert columns.execution_day.nullable is False
    assert isinstance(columns.timezone.type, sa.Text)
    assert columns.timezone.nullable is False
    assert isinstance(columns.start_date.type, sa.Date)
    assert columns.start_date.nullable is True
    assert columns.pause_reason.nullable is True
    assert columns.next_run_at.type.timezone is True
    assert columns.next_run_at.nullable is True
    assert columns.created_at.type.timezone is True
    assert columns.created_at.nullable is False
    assert columns.updated_at.type.timezone is True
    assert columns.updated_at.nullable is False


def test_execution_day_constraint_covers_weekly_and_monthly_contract() -> None:
    constraints = named_constraints(GoalAutoContributionSchedule.__table__.constraints)
    sql = normalized_sql(constraints["chk_gacs_execution_day"].sqltext)

    for weekday in (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ):
        assert f"'{weekday}'" in sql
    assert "frequency = 'weekly'" in sql
    assert "frequency = 'monthly'" in sql

    monthly_pattern = r"^[1-9]$|^[1-2][0-9]$|^3[0-1]$"
    assert monthly_pattern in sql
    assert all(re.fullmatch(monthly_pattern, value) for value in ("1", "9", "10", "31"))
    assert all(
        re.fullmatch(monthly_pattern, value) is None
        for value in ("0", "01", "32", "-1", "abc", "monday", "")
    )


def test_schedule_status_pause_reason_and_next_run_contract() -> None:
    constraints = named_constraints(GoalAutoContributionSchedule.__table__.constraints)

    assert normalized_sql(constraints["chk_gacs_status"].sqltext) == (
        "status IN ('active', 'paused', 'cancelled')"
    )
    assert normalized_sql(constraints["chk_gacs_pause_reason"].sqltext) == (
        "pause_reason IS NULL OR pause_reason IN ('user_paused', 'goal_completed', "
        "'account_inactive', 'goal_archived')"
    )
    assert normalized_sql(constraints["chk_gacs_active_next_run"].sqltext) == (
        "status != 'active' OR next_run_at IS NOT NULL"
    )


def test_partial_unique_and_supporting_indexes_match_migration() -> None:
    _, operation = run_upgrade()
    model_indexes = {index.name: index for index in GoalAutoContributionSchedule.__table__.indexes}
    migration_indexes = {call.args[0]: call for call in operation.create_index.call_args_list}

    assert set(model_indexes) == {
        "ix_goal_auto_contrib_goal_id_active",
        "ix_goal_auto_contrib_status_next_run",
        "ix_goal_auto_contrib_user_id",
    }
    partial = model_indexes["ix_goal_auto_contrib_goal_id_active"]
    assert partial.unique is True
    assert [column.name for column in partial.columns] == ["goal_id"]
    assert normalized_sql(partial.dialect_options["postgresql"]["where"]) == (
        "status != 'cancelled'"
    )
    assert str(CreateIndex(partial).compile(dialect=POSTGRESQL)) == (
        "CREATE UNIQUE INDEX ix_goal_auto_contrib_goal_id_active "
        "ON goal_auto_contribution_schedules (goal_id) WHERE status != 'cancelled'"
    )

    migrated_partial = migration_indexes["ix_goal_auto_contrib_goal_id_active"]
    assert migrated_partial.kwargs["unique"] is True
    assert migrated_partial.args[2] == ["goal_id"]
    assert normalized_sql(migrated_partial.kwargs["postgresql_where"]) == ("status != 'cancelled'")
    assert migration_indexes["ix_goal_auto_contrib_status_next_run"].args[2] == [
        "status",
        "next_run_at",
    ]
    assert migration_indexes["ix_goal_auto_contrib_user_id"].args[2] == ["user_id"]


def test_run_checks_and_unique_occurrence_match_migration() -> None:
    _, operation = run_upgrade()
    model = named_constraints(GoalAutoContributionRun.__table__.constraints)
    migrated = named_constraints(table_call(operation, "goal_auto_contribution_runs").args[1:])

    expected_names = {
        "uq_gacr_schedule_scheduled_for",
        "chk_gacr_status",
        "chk_gacr_result_code",
        "chk_gacr_conf_amount_pos",
        "chk_gacr_exec_amount_pos",
        "chk_gacr_missed_count",
    }
    assert set(model) == set(migrated) == expected_names
    for name in expected_names - {"uq_gacr_schedule_scheduled_for"}:
        assert normalized_sql(model[name].sqltext) == normalized_sql(migrated[name].sqltext)
    assert list(model["uq_gacr_schedule_scheduled_for"].columns.keys()) == [
        "schedule_id",
        "scheduled_for",
    ]
    assert list(migrated["uq_gacr_schedule_scheduled_for"]._pending_colargs) == [
        "schedule_id",
        "scheduled_for",
    ]


def test_run_explicit_column_and_index_contract() -> None:
    columns = GoalAutoContributionRun.__table__.columns
    indexes = {index.name: index for index in GoalAutoContributionRun.__table__.indexes}
    _, operation = run_upgrade()
    migration_indexes = {call.args[0]: call for call in operation.create_index.call_args_list}

    assert isinstance(columns.id.type, postgresql.UUID)
    assert columns.id.nullable is False
    assert server_default(columns.id) == "gen_random_uuid()"
    assert columns.scheduled_for.type.timezone is True
    assert columns.scheduled_for.nullable is False
    assert columns.executed_at.type.timezone is True
    assert columns.executed_at.nullable is True
    assert columns.status.nullable is False
    assert columns.result_code.nullable is False
    assert columns.configured_amount.type.precision == 14
    assert columns.configured_amount.type.scale == 2
    assert columns.configured_amount.nullable is False
    assert columns.executed_amount.type.precision == 14
    assert columns.executed_amount.type.scale == 2
    assert columns.executed_amount.nullable is True
    assert columns.missed_occurrences_count.nullable is False
    assert server_default(columns.missed_occurrences_count) == "0"
    assert columns.goal_transaction_id.nullable is True
    assert columns.created_at.type.timezone is True
    assert columns.created_at.nullable is False
    assert set(indexes) == {"ix_goal_auto_contrib_runs_schedule_id"}
    assert [column.name for column in indexes["ix_goal_auto_contrib_runs_schedule_id"].columns] == [
        "schedule_id"
    ]
    assert migration_indexes["ix_goal_auto_contrib_runs_schedule_id"].args[2] == ["schedule_id"]


def test_run_status_result_and_amount_contract() -> None:
    constraints = named_constraints(GoalAutoContributionRun.__table__.constraints)

    assert normalized_sql(constraints["chk_gacr_status"].sqltext) == (
        "status IN ('succeeded', 'skipped', 'failed')"
    )
    assert normalized_sql(constraints["chk_gacr_result_code"].sqltext) == (
        "result_code IN ('success', 'insufficient_available_balance', 'account_inactive', "
        "'goal_completed', 'goal_archived', 'goal_cancelled', 'currency_mismatch', "
        "'technical_error')"
    )
    assert normalized_sql(constraints["chk_gacr_conf_amount_pos"].sqltext) == (
        "configured_amount > 0"
    )
    assert normalized_sql(constraints["chk_gacr_exec_amount_pos"].sqltext) == (
        "executed_amount IS NULL OR executed_amount > 0"
    )
    assert normalized_sql(constraints["chk_gacr_missed_count"].sqltext) == (
        "missed_occurrences_count >= 0"
    )


def test_foreign_keys_target_authoritative_tables_with_expected_ondelete() -> None:
    importlib.import_module("app.accounts.models")
    importlib.import_module("app.users.models")

    assert GoalTransaction.__tablename__ == "goal_transactions"
    assert foreign_key_signatures(GoalAutoContributionSchedule.__table__) == {
        ("user_id", "users.id", "RESTRICT"),
        ("goal_id", "goals.id", "RESTRICT"),
        ("account_id", "accounts.id", "RESTRICT"),
    }
    assert foreign_key_signatures(GoalAutoContributionRun.__table__) == {
        ("schedule_id", "goal_auto_contribution_schedules.id", "RESTRICT"),
        ("goal_transaction_id", "goal_transactions.id", "SET NULL"),
    }


def test_migration_foreign_keys_match_model() -> None:
    _, operation = run_upgrade()

    for table_name, model in (
        ("goal_auto_contribution_schedules", GoalAutoContributionSchedule),
        ("goal_auto_contribution_runs", GoalAutoContributionRun),
    ):
        migrated_items = table_call(operation, table_name).args[1:]
        migrated_foreign_keys = {
            (
                constraint.column_keys[0],
                constraint.elements[0]._colspec,
                constraint.ondelete,
            )
            for constraint in migrated_items
            if isinstance(constraint, sa.ForeignKeyConstraint)
        }
        assert migrated_foreign_keys == foreign_key_signatures(model.__table__)
