"""categories_v1_phase2_reclassification

Revision ID: categories_v1_phase2_reclassification
Revises: categories_v1_phase1
Create Date: 2026-07-16 00:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'categories_v1_ph2_reclass'
down_revision = "categories_v1_phase1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financial_event_reclassifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("financial_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("new_category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reclassified_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.Text(), server_default="manual", nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source = ANY (ARRAY['manual', 'rule', 'ai', 'system'])",
            name="reclassifications_source_check",
        ),
        sa.CheckConstraint(
            "previous_category_id IS DISTINCT FROM new_category_id",
            name="reclassifications_different_categories_check",
        ),
        sa.ForeignKeyConstraint(
            ["financial_event_id"], ["financial_events.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["new_category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["previous_category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reclassified_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_id", name="uq_financial_event_reclassifications_command_id"),
    )
    op.create_index(
        "idx_financial_event_reclassifications_event_created",
        "financial_event_reclassifications",
        ["financial_event_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_financial_event_reclassifications_event_created",
        table_name="financial_event_reclassifications",
    )
    op.drop_table("financial_event_reclassifications")
