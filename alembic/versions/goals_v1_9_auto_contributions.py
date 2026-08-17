"""goals_v1_9_auto_contributions

Revision ID: goals_v1_9_auto_contributions
Revises: goals_v1_ph2_releases
Create Date: 2026-08-15 15:00:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "goals_v1_9_auto_contributions"
down_revision = "goals_v1_ph2_releases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create schedules table
    op.create_table(
        "goal_auto_contribution_schedules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("frequency", sa.Text(), nullable=False),
        sa.Column("execution_day", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("pause_reason", sa.Text(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("amount > 0", name="chk_gacs_amount_pos"),
        sa.CheckConstraint("frequency IN ('weekly', 'monthly')", name="chk_gacs_frequency"),
        sa.CheckConstraint(
            "(frequency = 'weekly' AND execution_day IN ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')) OR "
            "(frequency = 'monthly' AND execution_day ~ '^[1-9]$|^[1-2][0-9]$|^3[0-1]$')",
            name="chk_gacs_execution_day",
        ),
        sa.CheckConstraint("status IN ('active', 'paused', 'cancelled')", name="chk_gacs_status"),
        sa.CheckConstraint(
            "pause_reason IS NULL OR pause_reason IN ('user_paused', 'goal_completed', 'account_inactive', 'goal_archived')",
            name="chk_gacs_pause_reason",
        ),
        sa.CheckConstraint(
            "status != 'active' OR next_run_at IS NOT NULL", name="chk_gacs_active_next_run"
        ),
    )

    # 2. Create runs table
    op.create_table(
        "goal_auto_contribution_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("schedule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result_code", sa.Text(), nullable=False),
        sa.Column("configured_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("executed_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("missed_occurrences_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("goal_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["schedule_id"], ["goal_auto_contribution_schedules.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["goal_transaction_id"], ["goal_transactions.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("schedule_id", "scheduled_for", name="uq_gacr_schedule_scheduled_for"),
        sa.CheckConstraint("status IN ('succeeded', 'skipped', 'failed')", name="chk_gacr_status"),
        sa.CheckConstraint(
            "result_code IN ('success', 'insufficient_available_balance', 'account_inactive', 'goal_completed', 'goal_archived', 'goal_cancelled', 'currency_mismatch', 'technical_error')",
            name="chk_gacr_result_code",
        ),
        sa.CheckConstraint("configured_amount > 0", name="chk_gacr_conf_amount_pos"),
        sa.CheckConstraint(
            "executed_amount IS NULL OR executed_amount > 0", name="chk_gacr_exec_amount_pos"
        ),
        sa.CheckConstraint("missed_occurrences_count >= 0", name="chk_gacr_missed_count"),
    )

    # 3. Create Indexes
    op.create_index(
        "ix_goal_auto_contrib_goal_id_active",
        "goal_auto_contribution_schedules",
        ["goal_id"],
        unique=True,
        postgresql_where=sa.text("status != 'cancelled'"),
    )
    op.create_index(
        "ix_goal_auto_contrib_status_next_run",
        "goal_auto_contribution_schedules",
        ["status", "next_run_at"],
    )
    op.create_index("ix_goal_auto_contrib_user_id", "goal_auto_contribution_schedules", ["user_id"])
    op.create_index(
        "ix_goal_auto_contrib_runs_schedule_id", "goal_auto_contribution_runs", ["schedule_id"]
    )


def downgrade() -> None:
    # 1. Drop Indexes
    op.drop_index("ix_goal_auto_contrib_runs_schedule_id", table_name="goal_auto_contribution_runs")
    op.drop_index("ix_goal_auto_contrib_user_id", table_name="goal_auto_contribution_schedules")
    op.drop_index(
        "ix_goal_auto_contrib_status_next_run", table_name="goal_auto_contribution_schedules"
    )
    op.drop_index(
        "ix_goal_auto_contrib_goal_id_active",
        table_name="goal_auto_contribution_schedules",
        postgresql_where=sa.text("status != 'cancelled'"),
    )

    # 2. Drop runs table
    op.drop_table("goal_auto_contribution_runs")

    # 3. Drop schedules table
    op.drop_table("goal_auto_contribution_schedules")
