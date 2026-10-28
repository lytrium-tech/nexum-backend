"""credit_v2_canonical_check

Revision ID: credit_v2_canonical_check
Revises: credit_v2_canonical_schema
Create Date: 2026-10-28 10:35:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "credit_v2_canonical_check"
down_revision: str | None = "credit_v2_canonical_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Supersede credit_card_installments_paid_check forward-only
    # Legacy constraint enforced paid_amount <= principal_amount which broke on interest installments.
    # We stage the new check paid_amount <= total_amount with NOT VALID to avoid assuming legacy consistency.
    op.execute(
        "ALTER TABLE credit_card_installments DROP CONSTRAINT IF EXISTS credit_card_installments_paid_check"
    )
    op.execute(
        "ALTER TABLE credit_card_installments "
        "ADD CONSTRAINT credit_card_installments_paid_check_v2 "
        "CHECK (paid_amount <= total_amount) NOT VALID"
    )


def downgrade() -> None:
    # Revert to legacy constraint definition staged with NOT VALID to preserve forward payments
    op.execute(
        "ALTER TABLE credit_card_installments DROP CONSTRAINT IF EXISTS credit_card_installments_paid_check_v2"
    )
    op.execute(
        "ALTER TABLE credit_card_installments "
        "ADD CONSTRAINT credit_card_installments_paid_check "
        "CHECK (paid_amount <= principal_amount) NOT VALID"
    )
