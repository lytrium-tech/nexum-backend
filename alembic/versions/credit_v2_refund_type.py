"""credit_v2_refund_transaction_type

Revision ID: credit_v2_refund_type
Revises: credit_v2_canonical_closure
Create Date: 2026-10-29 13:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "credit_v2_refund_type"
down_revision: str | None = "credit_v2_canonical_closure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LEGACY_TYPES = "'purchase'::text, 'payment'::text, 'fee'::text, 'interest'::text, 'adjustment'::text"


def upgrade() -> None:
    op.drop_constraint(
        "credit_card_transactions_type_check", "credit_card_transactions", type_="check"
    )
    op.create_check_constraint(
        "credit_card_transactions_type_check",
        "credit_card_transactions",
        f"type = ANY (ARRAY[{_LEGACY_TYPES}, 'refund'::text])",
    )


def downgrade() -> None:
    has_refunds = op.get_bind().execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM credit_card_transactions WHERE type = 'refund')")
    ).scalar_one()
    if has_refunds:
        raise RuntimeError("Cannot downgrade while refund transaction facts exist")

    op.drop_constraint(
        "credit_card_transactions_type_check", "credit_card_transactions", type_="check"
    )
    op.create_check_constraint(
        "credit_card_transactions_type_check",
        "credit_card_transactions",
        f"type = ANY (ARRAY[{_LEGACY_TYPES}])",
    )
