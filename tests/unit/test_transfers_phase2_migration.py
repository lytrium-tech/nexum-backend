import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, call

from sqlalchemy import Numeric
from sqlalchemy.dialects.postgresql import UUID

MIGRATION_PATH = (
    Path(__file__).parents[2] / "alembic" / "versions" / "transfers_v1_phase2_multicurrency.py"
)


def load_migration():
    spec = importlib.util.spec_from_file_location(
        "transfers_v1_phase2_multicurrency", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_and_upgrade_contract() -> None:
    migration = load_migration()
    operation = MagicMock()
    migration.op = operation

    migration.upgrade()

    assert migration.revision == "transfers_v1_ph2_multicurrency"
    assert len(migration.revision) <= 32
    assert migration.down_revision == "categories_v1_ph2_reclass"
    alter = operation.alter_column.call_args
    assert alter.args == ("transfers", "fx_rate")
    assert isinstance(alter.kwargs["existing_type"], Numeric)
    assert (alter.kwargs["existing_type"].precision, alter.kwargs["existing_type"].scale) == (
        14,
        6,
    )
    assert (alter.kwargs["type_"].precision, alter.kwargs["type_"].scale) == (18, 8)

    column = operation.add_column.call_args.args[1]
    assert column.name == "rate_snapshot_id"
    assert isinstance(column.type, UUID)
    assert column.nullable is True
    operation.create_foreign_key.assert_called_once_with(
        "fk_transfers_exchange_rates_rate_snapshot_id",
        "transfers",
        "exchange_rates",
        ["rate_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    operation.create_index.assert_called_once_with(
        "ix_transfers_rate_snapshot_id", "transfers", ["rate_snapshot_id"]
    )


def test_migration_downgrade_reverses_objects_in_safe_order() -> None:
    migration = load_migration()
    operation = MagicMock()
    migration.op = operation

    migration.downgrade()

    assert operation.method_calls[1:4] == [
        call.drop_index("ix_transfers_rate_snapshot_id", table_name="transfers"),
        call.drop_constraint(
            "fk_transfers_exchange_rates_rate_snapshot_id",
            "transfers",
            type_="foreignkey",
        ),
        call.drop_column("transfers", "rate_snapshot_id"),
    ]
    downgrade_guard = operation.execute.call_args.args[0]
    assert "fx_rate <> round(fx_rate, 6)" in downgrade_guard
    assert "RAISE EXCEPTION" in downgrade_guard
    alter = operation.alter_column.call_args
    assert (alter.kwargs["existing_type"].precision, alter.kwargs["existing_type"].scale) == (
        18,
        8,
    )
    assert (alter.kwargs["type_"].precision, alter.kwargs["type_"].scale) == (14, 6)
