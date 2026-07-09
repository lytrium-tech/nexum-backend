"""Add rate_snapshot_id

Revision ID: v1_7_phase_fx_rate_snapshot
Revises: v1_7_phase2_1
Create Date: 2026-07-09 13:46:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'v1_7_phase_fx_rate_snapshot'
down_revision: Union[str, None] = 'v1_7_phase2_1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('obligation_payments', sa.Column('rate_snapshot_id', sa.UUID(), nullable=True))

def downgrade() -> None:
    op.drop_column('obligation_payments', 'rate_snapshot_id')
