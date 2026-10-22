"""obligations_v1_7_auto_payments

Revision ID: obligations_v1_7_auto_payments
Revises: goals_v1_9_auto_contributions
Create Date: 2026-10-22 03:30:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "obligations_v1_7_auto_payments"
down_revision = "goals_v1_9_auto_contributions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create obligation auto payment configs table
    op.create_table(
        "obligation_auto_payment_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("obligation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.Text(), nullable=False, server_default="paused"),
        sa.Column("pause_reason", sa.Text(), nullable=True),
        sa.Column("next_evaluation_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["obligation_id"], ["obligations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("obligation_id", name="uq_obligation_auto_payment_obligation_id"),
        sa.CheckConstraint("status IN ('enabled', 'paused', 'cancelled')", name="chk_oapc_status"),
    )

    op.create_index(
        "ix_obligation_auto_payment_configs_status_next_eval",
        "obligation_auto_payment_configs",
        ["status", "next_evaluation_at"],
    )
    op.create_index(
        "ix_obligation_auto_payment_configs_user_id",
        "obligation_auto_payment_configs",
        ["user_id"],
    )
    op.create_index(
        "ix_obligation_auto_payment_configs_account_id",
        "obligation_auto_payment_configs",
        ["account_id"],
    )

    # 2. Create obligation auto payment runs table
    op.create_table(
        "obligation_auto_payment_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("obligation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("obligation_period_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_key", sa.Text(), nullable=False),
        sa.Column("canonical_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result_code", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("source_amount", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("source_currency", sa.Text(), nullable=True),
        sa.Column("fx_rate", sa.Numeric(precision=15, scale=6), nullable=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["config_id"], ["obligation_auto_payment_configs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["obligation_id"], ["obligations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["obligation_period_id"], ["obligation_periods.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["obligation_payments.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("attempt_key", name="uq_oapr_attempt_key"),
        sa.CheckConstraint("status IN ('succeeded', 'skipped', 'failed')", name="chk_oapr_status"),
    )

    op.create_index(
        "ix_obligation_auto_payment_runs_config_id",
        "obligation_auto_payment_runs",
        ["config_id"],
    )
    op.create_index(
        "ix_obligation_auto_payment_runs_obligation_id",
        "obligation_auto_payment_runs",
        ["obligation_id"],
    )
    op.create_index(
        "ix_obligation_auto_payment_runs_canonical_date",
        "obligation_auto_payment_runs",
        ["canonical_date"],
    )
    op.create_index(
        "ix_obligation_auto_payment_runs_created_at",
        "obligation_auto_payment_runs",
        ["created_at"],
    )
    op.create_index(
        "ix_obligation_auto_payment_runs_payment_id",
        "obligation_auto_payment_runs",
        ["payment_id"],
    )


def downgrade() -> None:
    op.drop_table("obligation_auto_payment_runs")
    op.drop_table("obligation_auto_payment_configs")
