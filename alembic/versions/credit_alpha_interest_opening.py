"""Add explicit Credit Alpha interest and opening balance facts."""

import sqlalchemy as sa

from alembic import op

revision = "credit_alpha_interest_opening"
down_revision = "credit_v2_refund_type"
branch_labels = None
depends_on = None

_TYPES = "'purchase'::text, 'payment'::text, 'fee'::text, 'interest'::text, 'adjustment'::text, 'refund'::text"


def upgrade() -> None:
    op.add_column(
        "credit_card_transactions",
        sa.Column("zero_interest", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "credit_card_installments",
        sa.Column("interest_estimator_version", sa.String(), nullable=True),
    )
    op.add_column(
        "credit_card_installments",
        sa.Column("opening_balance_amount", sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.drop_constraint(
        "credit_card_installments_principal_check", "credit_card_installments", type_="check"
    )
    op.create_check_constraint(
        "credit_card_installments_principal_check",
        "credit_card_installments",
        "principal_amount >= 0 AND principal_amount + COALESCE(opening_balance_amount, 0) + COALESCE(assessed_interest_amount, 0) > 0",
    )
    op.create_index(
        "ix_cc_installments_card_due_active",
        "credit_card_installments",
        ["credit_card_id", "scheduled_due_date"],
        postgresql_where=sa.text("status <> 'paid' AND lifecycle_status = 'active'"),
    )
    op.drop_constraint(
        "credit_card_transactions_type_check", "credit_card_transactions", type_="check"
    )
    op.add_column(
        "credit_card_adjustments",
        sa.Column(
            "reversal_of_adjustment_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("credit_card_adjustments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_cc_adjustments_reversal_of_id",
        "credit_card_adjustments",
        ["reversal_of_adjustment_id"],
        postgresql_where=sa.text("reversal_of_adjustment_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "credit_card_transactions_type_check",
        "credit_card_transactions",
        f"type = ANY (ARRAY[{_TYPES}, 'opening_balance'::text])",
    )


def downgrade() -> None:
    has_opening = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM credit_card_transactions WHERE type = 'opening_balance')"
            )
        )
        .scalar_one()
    )
    has_zero_interest = (
        op.get_bind()
        .execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM credit_card_transactions WHERE zero_interest)")
        )
        .scalar_one()
    )
    has_estimates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM credit_card_installments WHERE interest_estimator_version IS NOT NULL)"
            )
        )
        .scalar_one()
    )
    has_zero_principal = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM credit_card_installments WHERE principal_amount <= 0)"
            )
        )
        .scalar_one()
    )
    has_reversal_links = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM credit_card_adjustments WHERE reversal_of_adjustment_id IS NOT NULL)"
            )
        )
        .scalar_one()
    )
    if (
        has_opening
        or has_zero_interest
        or has_estimates
        or has_zero_principal
        or has_reversal_links
    ):
        raise RuntimeError(
            "Cannot downgrade while opening balance, zero-interest, estimator, or reversal-link facts exist"
        )
    op.drop_constraint(
        "credit_card_transactions_type_check", "credit_card_transactions", type_="check"
    )
    op.drop_index("ix_cc_installments_card_due_active", table_name="credit_card_installments")
    op.create_check_constraint(
        "credit_card_transactions_type_check",
        "credit_card_transactions",
        f"type = ANY (ARRAY[{_TYPES}])",
    )
    op.drop_column("credit_card_transactions", "zero_interest")
    op.drop_column("credit_card_installments", "interest_estimator_version")
    op.drop_constraint(
        "credit_card_installments_principal_check", "credit_card_installments", type_="check"
    )
    op.create_check_constraint(
        "credit_card_installments_principal_check",
        "credit_card_installments",
        "principal_amount > 0",
    )
    op.drop_column("credit_card_installments", "opening_balance_amount")
    op.drop_index("ix_cc_adjustments_reversal_of_id", table_name="credit_card_adjustments")
    op.drop_column("credit_card_adjustments", "reversal_of_adjustment_id")
