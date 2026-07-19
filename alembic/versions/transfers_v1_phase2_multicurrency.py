"""transfers_v1_phase2_multicurrency

Revision ID: transfers_v1_ph2_multicurrency
Revises: categories_v1_ph2_reclass
Create Date: 2026-07-19 13:40:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "transfers_v1_ph2_multicurrency"
down_revision = "categories_v1_ph2_reclass"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Match the precision of exchange_rates.rate so the immutable value is preserved.
    op.alter_column(
        "transfers",
        "fx_rate",
        existing_type=sa.Numeric(precision=14, scale=6),
        type_=sa.Numeric(precision=18, scale=8),
        existing_nullable=True,
    )
    op.add_column(
        "transfers",
        sa.Column("rate_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_transfers_exchange_rates_rate_snapshot_id",
        "transfers",
        "exchange_rates",
        ["rate_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_transfers_rate_snapshot_id", "transfers", ["rate_snapshot_id"])


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM transfers
                WHERE fx_rate IS NOT NULL
                  AND fx_rate <> round(fx_rate, 6)
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade transfers.fx_rate without losing FX precision';
            END IF;
        END
        $$
        """
    )
    op.drop_index("ix_transfers_rate_snapshot_id", table_name="transfers")
    op.drop_constraint(
        "fk_transfers_exchange_rates_rate_snapshot_id",
        "transfers",
        type_="foreignkey",
    )
    op.drop_column("transfers", "rate_snapshot_id")
    op.alter_column(
        "transfers",
        "fx_rate",
        existing_type=sa.Numeric(precision=18, scale=8),
        type_=sa.Numeric(precision=14, scale=6),
        existing_nullable=True,
    )
