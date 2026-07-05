"""v1.7 phase 2.1 schema additive migration

Revision ID: v1_7_phase2_1
Revises: 
Create Date: 2026-07-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'v1_7_phase2_1'
# Representa el inicio del tracking Alembic formal, no el inicio histórico del schema (V1.5 base)
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. public.obligations
    op.add_column('obligations', sa.Column('amount_type', sa.Text(), nullable=True))

    # 2. public.obligation_periods
    op.add_column('obligation_periods', sa.Column('is_current', sa.Boolean(), server_default=sa.text('false'), nullable=True))
    op.create_index('ix_obligation_periods_obligation_status', 'obligation_periods', ['obligation_id', 'status'], unique=False)
    op.create_index('ix_obligation_periods_obligation_due_date', 'obligation_periods', ['obligation_id', 'due_date'], unique=False)
    # Nota: (user_id, status) se omite porque obligation_periods no tiene columna user_id en V1.5.

    # 3. public.obligation_payments
    op.add_column('obligation_payments', sa.Column('quote_id', sa.UUID(), nullable=True))
    op.add_column('obligation_payments', sa.Column('idempotency_key', sa.Text(), nullable=True))
    
    op.create_index(
        'ix_obligation_payments_user_idempotency', 
        'obligation_payments', 
        ['user_id', 'idempotency_key'], 
        unique=True, 
        postgresql_where=sa.text('idempotency_key IS NOT NULL')
    )
    op.create_index('ix_obligation_payments_obligation_id_idx', 'obligation_payments', ['obligation_id'], unique=False)
    op.create_index('ix_obligation_payments_period_id', 'obligation_payments', ['obligation_period_id'], unique=False)
    op.create_index('ix_obligation_payments_user_created', 'obligation_payments', ['user_id', 'created_at'], unique=False)

    # 4. public.exchange_rates
    op.create_table('exchange_rates',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('base_currency', sa.Text(), nullable=False),
        sa.Column('quote_currency', sa.Text(), nullable=False),
        sa.Column('rate', sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_stale', sa.Boolean(), server_default=sa.text('false'), nullable=True),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_exchange_rates_lookup', 'exchange_rates', ['base_currency', 'quote_currency', 'provider'], unique=False)
    op.create_index('ix_exchange_rates_expires', 'exchange_rates', ['expires_at'], unique=False)

    # 5. public.fx_quotes
    op.create_table('fx_quotes',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('from_currency', sa.Text(), nullable=False),
        sa.Column('to_currency', sa.Text(), nullable=False),
        sa.Column('source_amount', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('target_amount', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('rate', sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('rate_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('idempotency_key', sa.Text(), nullable=True),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_fx_quotes_user_status', 'fx_quotes', ['user_id', 'status'], unique=False)
    op.create_index('ix_fx_quotes_expires', 'fx_quotes', ['expires_at'], unique=False)
    op.create_index(
        'ix_fx_quotes_user_idempotency', 
        'fx_quotes', 
        ['user_id', 'idempotency_key'], 
        unique=True, 
        postgresql_where=sa.text('idempotency_key IS NOT NULL')
    )


def downgrade() -> None:
    # 5. public.fx_quotes
    op.drop_table('fx_quotes')
    
    # 4. public.exchange_rates
    op.drop_table('exchange_rates')
    
    # 3. public.obligation_payments
    op.drop_index('ix_obligation_payments_user_idempotency', table_name='obligation_payments')
    op.drop_index('ix_obligation_payments_user_created', table_name='obligation_payments')
    op.drop_index('ix_obligation_payments_period_id', table_name='obligation_payments')
    op.drop_index('ix_obligation_payments_obligation_id_idx', table_name='obligation_payments')
    op.drop_column('obligation_payments', 'idempotency_key')
    op.drop_column('obligation_payments', 'quote_id')
    
    # 2. public.obligation_periods
    op.drop_index('ix_obligation_periods_obligation_due_date', table_name='obligation_periods')
    op.drop_index('ix_obligation_periods_obligation_status', table_name='obligation_periods')
    op.drop_column('obligation_periods', 'is_current')
    
    # 1. public.obligations
    op.drop_column('obligations', 'amount_type')
