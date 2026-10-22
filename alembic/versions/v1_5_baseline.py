"""Pre-Alembic V1.5 baseline schema

Revision ID: v1_5_baseline
Revises: None
Create Date: 2026-07-04 00:00:00.000000

Reconstructs repository-evidenced pre-Alembic / V1.5 database objects
required before formal Alembic tracking (v1.7 phase 2.1).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "v1_5_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. public.users
    op.create_table(
        "users",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("auth_user_id", sa.UUID(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column(
            "timezone",
            sa.Text(),
            server_default=sa.text("'America/Bogota'::text"),
            nullable=False,
        ),
        sa.Column(
            "currency",
            sa.Text(),
            server_default=sa.text("'COP'::text"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'active'::text"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("auth_user_id"),
        sa.UniqueConstraint("email"),
        sa.CheckConstraint(
            "currency = ANY (ARRAY['COP'::text, 'USD'::text])",
            name="users_currency_check",
        ),
        sa.CheckConstraint(
            "status = ANY (ARRAY['active'::text, 'inactive'::text, 'blocked'::text])",
            name="users_status_check",
        ),
    )

    # 2. public.accounts
    op.create_table(
        "accounts",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column(
            "balance",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "currency",
            sa.Text(),
            server_default=sa.text("'COP'::text"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("balance >= 0::numeric", name="accounts_balance_check"),
        sa.CheckConstraint(
            "type = ANY (ARRAY['bank'::text, 'wallet'::text, 'cash'::text, 'savings'::text])",
            name="accounts_type_check",
        ),
    )

    # 3. public.categories
    op.create_table(
        "categories",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "normalized_name",
            sa.Text(),
            server_default=sa.text("''::text"),
            nullable=False,
        ),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "type = ANY (ARRAY['income'::text, 'expense'::text, 'credit_card'::text, 'obligation'::text, 'goal'::text, 'transfer'::text, 'system'::text])",
            name="categories_type_check",
        ),
        sa.UniqueConstraint(
            "user_id",
            "type",
            "normalized_name",
            name="uq_category_user_type_normalized_name",
        ),
    )

    # 4. public.transfers
    op.create_table(
        "transfers",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("source_account_id", sa.UUID(), nullable=False),
        sa.Column("destination_account_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "currency",
            sa.Text(),
            server_default=sa.text("'COP'::text"),
            nullable=False,
        ),
        sa.Column("target_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("target_currency", sa.Text(), nullable=True),
        sa.Column("fx_rate", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("rate_source", sa.Text(), nullable=True),
        sa.Column("rate_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_estimated",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("command_id", sa.UUID(), nullable=True),
        sa.Column("source_message_id", sa.UUID(), nullable=True),
        sa.Column("raw_message", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'completed'::text"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["destination_account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("command_id"),
        sa.CheckConstraint("amount > 0::numeric", name="transfers_amount_check"),
        sa.CheckConstraint(
            "source_account_id != destination_account_id",
            name="check_different_accounts",
        ),
    )
    op.create_index("idx_transfers_user_id", "transfers", ["user_id"], unique=False)

    # 5. public.financial_events
    op.create_table(
        "financial_events",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column("category_id", sa.UUID(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "currency",
            sa.Text(),
            server_default=sa.text("'COP'::text"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("raw_message", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.Text(),
            server_default=sa.text("'backend'::text"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("command_id", sa.UUID(), nullable=True),
        sa.Column("source_message_id", sa.UUID(), nullable=True),
        sa.Column("transfer_id", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["transfer_id"], ["transfers.id"], ondelete="CASCADE"),
        sa.CheckConstraint("amount > 0::numeric", name="financial_events_amount_check"),
        sa.CheckConstraint(
            "direction = ANY (ARRAY['inflow'::text, 'outflow'::text, 'neutral'::text])",
            name="financial_events_direction_check",
        ),
        sa.CheckConstraint(
            "event_type = ANY (ARRAY['income'::text, 'expense'::text, 'credit_card_purchase'::text, 'credit_card_payment'::text, 'obligation_payment'::text, 'goal_contribution'::text, 'manual_adjustment'::text, 'transfer_out'::text, 'transfer_in'::text])",
            name="financial_events_type_check",
        ),
    )
    op.create_index(
        "idx_financial_events_command_id",
        "financial_events",
        ["command_id"],
        unique=True,
        postgresql_where=sa.text("command_id IS NOT NULL"),
    )
    op.create_index(
        "idx_financial_events_transfer_id",
        "financial_events",
        ["transfer_id"],
        unique=False,
    )
    op.create_index(
        "idx_financial_events_source_msg",
        "financial_events",
        ["source_message_id"],
        unique=False,
    )

    # 6. Trigger for financial_events.period
    op.execute("""
CREATE OR REPLACE FUNCTION set_financial_event_period()
RETURNS trigger AS $$
BEGIN
    IF NEW.period IS NULL THEN
        NEW.period := to_char(COALESCE(NEW.occurred_at, now()) AT TIME ZONE 'America/Bogota', 'YYYY-MM');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""")

    op.execute("""
CREATE TRIGGER trg_financial_events_period
BEFORE INSERT ON public.financial_events
FOR EACH ROW
EXECUTE FUNCTION set_financial_event_period();
""")

    # 7. public.credit_cards
    op.create_table(
        "credit_cards",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("bank", sa.Text(), nullable=False),
        sa.Column("credit_limit", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "current_debt",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=True,
        ),
        sa.Column("cutoff_day", sa.Integer(), nullable=False),
        sa.Column("due_day", sa.Integer(), nullable=False),
        sa.Column(
            "management_fee",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=True,
        ),
        sa.Column(
            "monthly_interest_rate",
            sa.Numeric(precision=6, scale=4),
            server_default=sa.text("0"),
            nullable=True,
        ),
        sa.Column(
            "annual_interest_rate",
            sa.Numeric(precision=6, scale=4),
            server_default=sa.text("0"),
            nullable=True,
        ),
        sa.Column(
            "currency",
            sa.Text(),
            server_default=sa.text("'COP'::text"),
            nullable=False,
        ),
        sa.Column("network", sa.Text(), nullable=True),
        sa.Column("franchise", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "cutoff_day >= 1 AND cutoff_day <= 31",
            name="credit_cards_cutoff_day_check",
        ),
        sa.CheckConstraint("due_day >= 1 AND due_day <= 31", name="credit_cards_due_day_check"),
    )

    # 8. public.credit_card_statements
    op.create_table(
        "credit_card_statements",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("credit_card_id", sa.UUID(), nullable=False),
        sa.Column("billing_period", sa.Text(), nullable=False),
        sa.Column("billing_period_start", sa.Date(), nullable=True),
        sa.Column("cutoff_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "previous_balance",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "new_purchases",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "billed_installments",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "fees_total",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "interest_total",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "payments_received",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "statement_balance",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "minimum_payment",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'open'::text"),
            nullable=False,
        ),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credit_card_id"], ["credit_cards.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("credit_card_id", "billing_period", name="uq_cc_statement_period"),
    )

    # 9. public.credit_card_transactions
    op.create_table(
        "credit_card_transactions",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("credit_card_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "installments_total",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=True,
        ),
        sa.Column(
            "installments_paid",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=True,
        ),
        sa.Column("monthly_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "interest_amount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=True,
        ),
        sa.Column("total_with_interest", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("event_id", sa.UUID(), nullable=True),
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column("category_id", sa.UUID(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("period", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.Text(),
            server_default=sa.text("'n8n_chat'::text"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("assigned_statement_id", sa.UUID(), nullable=True),
        sa.Column("statement_assignment_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credit_card_id"], ["credit_cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["financial_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["assigned_statement_id"],
            ["credit_card_statements.id"],
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("amount > 0::numeric", name="credit_card_transactions_amount_check"),
        sa.CheckConstraint(
            "type = ANY (ARRAY['purchase'::text, 'payment'::text, 'fee'::text, 'interest'::text, 'adjustment'::text])",
            name="credit_card_transactions_type_check",
        ),
    )

    # 10. public.credit_card_installments
    op.create_table(
        "credit_card_installments",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("credit_card_id", sa.UUID(), nullable=False),
        sa.Column("purchase_transaction_id", sa.UUID(), nullable=False),
        sa.Column("installment_number", sa.Integer(), nullable=False),
        sa.Column("installments_total", sa.Integer(), nullable=False),
        sa.Column("principal_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("scheduled_period", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'::text"),
            nullable=False,
        ),
        sa.Column(
            "paid_amount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "interest_amount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "total_amount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column("scheduled_due_date", sa.Date(), nullable=True),
        sa.Column("revision_id", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credit_card_id"], ["credit_cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["purchase_transaction_id"],
            ["credit_card_transactions.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "principal_amount > 0::numeric",
            name="credit_card_installments_principal_check",
        ),
        sa.CheckConstraint(
            "installment_number >= 1 AND installment_number <= installments_total",
            name="credit_card_installments_number_check",
        ),
        sa.CheckConstraint("installments_total >= 1", name="credit_card_installments_total_check"),
        sa.CheckConstraint(
            "paid_amount <= principal_amount",
            name="credit_card_installments_paid_check",
        ),
        sa.CheckConstraint(
            "status = ANY (ARRAY['pending'::text, 'partial'::text, 'paid'::text])",
            name="credit_card_installments_status_check",
        ),
        sa.UniqueConstraint(
            "purchase_transaction_id",
            "installment_number",
            name="credit_card_installments_unique_number",
        ),
    )
    op.create_index(
        "idx_credit_card_installments_card_period",
        "credit_card_installments",
        ["credit_card_id", "scheduled_period"],
        unique=False,
    )
    op.create_index(
        "idx_credit_card_installments_user_card",
        "credit_card_installments",
        ["user_id", "credit_card_id"],
        unique=False,
    )

    # 11. public.credit_card_early_payments
    op.create_table(
        "credit_card_early_payments",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("credit_card_id", sa.UUID(), nullable=False),
        sa.Column("purchase_transaction_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("allocation_mode", sa.Text(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=True),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credit_card_id"], ["credit_cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["purchase_transaction_id"],
            ["credit_card_transactions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["financial_events.id"], ondelete="SET NULL"),
    )

    # 12. public.credit_card_statement_charges
    op.create_table(
        "credit_card_statement_charges",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("statement_id", sa.UUID(), nullable=False),
        sa.Column("charge_type", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reference_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'::text"),
            nullable=False,
        ),
        sa.Column(
            "paid_amount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["statement_id"], ["credit_card_statements.id"], ondelete="CASCADE"
        ),
    )

    # 13. public.goals
    op.create_table(
        "goals",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("target_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "current_amount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("monthly_required", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("daily_required", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("progress_percentage", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column(
            "currency",
            sa.Text(),
            server_default=sa.text("'COP'::text"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'active'::text"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("category_id", sa.UUID(), nullable=True),
        sa.Column(
            "monthly_contribution",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.CheckConstraint("target_amount > 0::numeric", name="goals_target_amount_check"),
        sa.CheckConstraint("current_amount >= 0::numeric", name="goals_current_amount_check"),
        sa.CheckConstraint(
            "status = ANY (ARRAY['active'::text, 'completed'::text, 'paused'::text, 'cancelled'::text])",
            name="goals_status_check",
        ),
    )

    # 14. public.goal_contributions
    op.create_table(
        "goal_contributions",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("goal_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "currency",
            sa.Text(),
            server_default=sa.text("'COP'::text"),
            nullable=False,
        ),
        sa.Column("applied_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("goal_currency", sa.Text(), nullable=True),
        sa.Column("fx_rate", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("rate_source", sa.Text(), nullable=True),
        sa.Column("rate_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_estimated",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("period", sa.Text(), nullable=True),
        sa.Column("event_id", sa.UUID(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["event_id"], ["financial_events.id"], ondelete="SET NULL"),
        sa.CheckConstraint("amount > 0::numeric", name="goal_contributions_amount_check"),
    )

    # 15. public.obligations
    op.create_table(
        "obligations",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category_id", sa.UUID(), nullable=True),
        sa.Column(
            "currency",
            sa.Text(),
            server_default=sa.text("'COP'::text"),
            nullable=False,
        ),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column(
            "frequency",
            sa.Text(),
            server_default=sa.text("'monthly'::text"),
            nullable=False,
        ),
        sa.Column(
            "payment_mode",
            sa.Text(),
            server_default=sa.text("'fixed'::text"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("base_amount", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("first_due_date", sa.Date(), nullable=True),
        sa.Column("due_day", sa.Integer(), nullable=True),
        sa.Column("due_month", sa.Integer(), nullable=True),
        sa.Column(
            "interval_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("end_count", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'active'::text"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="SET NULL"),
    )

    # 16. public.obligation_periods
    op.create_table(
        "obligation_periods",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("obligation_id", sa.UUID(), nullable=False),
        sa.Column("period_key", sa.Text(), nullable=False),
        sa.Column(
            "sequence_number",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column(
            "currency",
            sa.Text(),
            server_default=sa.text("'COP'::text"),
            nullable=False,
        ),
        sa.Column(
            "paid_amount",
            sa.Numeric(precision=15, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending_payment'::text"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["obligation_id"], ["obligations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("obligation_id", "period_key", name="uq_obligation_period_key"),
        sa.CheckConstraint("paid_amount >= 0::numeric", name="chk_paid_amount_positive"),
        sa.CheckConstraint(
            "amount IS NULL OR paid_amount <= amount",
            name="chk_paid_amount_lte_amount",
        ),
        sa.CheckConstraint("amount IS NULL OR amount >= 0::numeric", name="chk_amount_positive"),
    )

    # 17. public.obligation_payments
    op.create_table(
        "obligation_payments",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("obligation_id", sa.UUID(), nullable=False),
        sa.Column("obligation_period_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column("financial_event_id", sa.UUID(), nullable=True),
        sa.Column("event_id", sa.UUID(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column(
            "currency",
            sa.Text(),
            server_default=sa.text("'COP'::text"),
            nullable=False,
        ),
        sa.Column("source_amount", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("source_currency", sa.Text(), nullable=True),
        sa.Column("fx_rate", sa.Numeric(precision=15, scale=6), nullable=True),
        sa.Column("rate_source", sa.Text(), nullable=True),
        sa.Column("rate_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_estimated", sa.Boolean(), nullable=True),
        sa.Column("period", sa.Text(), nullable=True),
        sa.Column(
            "paid_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["obligation_id"], ["obligations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["obligation_period_id"], ["obligation_periods.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["financial_event_id"], ["financial_events.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["event_id"], ["financial_events.id"], ondelete="SET NULL"),
    )

    # 18. public.messages
    op.create_table(
        "messages",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("external_message_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("financial_event_id", sa.UUID(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
        sa.Column("trace_id", sa.UUID(), nullable=True),
        sa.Column("command_id", sa.UUID(), nullable=True),
        sa.Column("sender_id", sa.Text(), nullable=True),
        sa.Column("recipient_id", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["financial_event_id"], ["financial_events.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "channel = ANY (ARRAY['whatsapp'::text, 'n8n_chat'::text, 'web'::text, 'email'::text, 'telegram'::text])",
            name="messages_channel_check",
        ),
        sa.CheckConstraint(
            "direction = ANY (ARRAY['inbound'::text, 'outbound'::text])",
            name="messages_direction_check",
        ),
    )

    # 19. public.pending_actions
    op.create_table(
        "pending_actions",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("missing_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'::text"),
            nullable=True,
        ),
        sa.Column("command_id", sa.UUID(), nullable=True),
        sa.Column("source_message_id", sa.UUID(), nullable=True),
        sa.Column("confirmation_message_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status = ANY (ARRAY['pending'::text, 'completed'::text, 'cancelled'::text, 'expired'::text, 'superseded'::text])",
            name="pending_actions_status_check",
        ),
    )

    # 20. public.ai_runs
    op.create_table(
        "ai_runs",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("message_id", sa.UUID(), nullable=True),
        sa.Column("financial_event_id", sa.UUID(), nullable=True),
        sa.Column(
            "provider",
            sa.Text(),
            server_default=sa.text("'gemini'::text"),
            nullable=False,
        ),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column(
            "input",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "output",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column(
            "success",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("trace_id", sa.UUID(), nullable=True),
        sa.Column("command_id", sa.UUID(), nullable=True),
        sa.Column("parse_ok", sa.Boolean(), nullable=True),
        sa.Column(
            "redacted_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
        sa.Column(
            "error_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["financial_event_id"], ["financial_events.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "provider = ANY (ARRAY['openai'::text, 'gemini'::text, 'anthropic'::text, 'local'::text, 'other'::text])",
            name="ai_runs_provider_check",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0", name="ai_runs_latency_ms_check"
        ),
    )

    # 21. public.logs
    op.create_table(
        "logs",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("level", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("event_id", sa.UUID(), nullable=True),
        sa.Column("financial_event_id", sa.UUID(), nullable=True),
        sa.Column("message_id", sa.UUID(), nullable=True),
        sa.Column("trace_id", sa.UUID(), nullable=True),
        sa.Column("command_id", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["financial_event_id"], ["financial_events.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "level = ANY (ARRAY['DEBUG'::text, 'INFO'::text, 'WARNING'::text, 'ERROR'::text, 'CRITICAL'::text])",
            name="logs_level_check",
        ),
    )

    # 22. public.user_channels
    op.create_table(
        "user_channels",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
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
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "channel = ANY (ARRAY['whatsapp'::text, 'n8n_chat'::text, 'web'::text, 'email'::text, 'telegram'::text])",
            name="user_channels_channel_check",
        ),
    )

    # 23. public.idempotency_keys
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column(
            "response_body",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status = ANY (ARRAY['processing'::text, 'completed'::text, 'failed'::text])",
            name="idempotency_keys_status_check",
        ),
    )

    # 24. public.transactions (legacy backup)
    op.create_table(
        "transactions",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "transaction_date",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("event_id", sa.UUID(), nullable=True),
        sa.Column("category_id", sa.UUID(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("period", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.Text(),
            server_default=sa.text("'n8n_chat'::text"),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["event_id"], ["financial_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.CheckConstraint("amount > 0::numeric", name="transactions_amount_check"),
        sa.CheckConstraint(
            "type = ANY (ARRAY['income'::text, 'expense'::text])",
            name="transactions_type_check",
        ),
    )

    # 25. Historical Views
    op.execute("""
CREATE OR REPLACE VIEW public.v_credit_card_debt AS
WITH card_movements AS (
  SELECT
    cct.user_id,
    cct.credit_card_id,
    sum(
      CASE
        WHEN cct.type = 'purchase'::text THEN COALESCE(cct.total_with_interest, cct.amount)
        ELSE 0::numeric
      END
    ) AS purchases_total,
    sum(
      CASE
        WHEN cct.type = 'payment'::text THEN cct.amount
        ELSE 0::numeric
      END
    ) AS payments_total,
    sum(
      CASE
        WHEN cct.type = 'purchase'::text
         AND (cct.occurred_at::date + (GREATEST(cct.installments_total, 1) || ' months')::interval) >= CURRENT_DATE
        THEN ROUND(COALESCE(cct.monthly_amount, COALESCE(cct.total_with_interest, cct.amount) / GREATEST(cct.installments_total, 1)::numeric), 2)
        ELSE 0::numeric
      END
    ) AS monthly_purchases_total
  FROM credit_card_transactions cct
  GROUP BY cct.user_id, cct.credit_card_id
)
SELECT
  cc.user_id,
  cc.id AS credit_card_id,
  cc.name AS credit_card_name,
  cc.bank,
  cc.cutoff_day,
  cc.due_day,
  GREATEST(COALESCE(cm.purchases_total, 0::numeric) - COALESCE(cm.payments_total, 0::numeric), 0::numeric) AS credit_card_debt,
  CASE
    WHEN GREATEST(COALESCE(cm.purchases_total, 0::numeric) - COALESCE(cm.payments_total, 0::numeric), 0::numeric) <= 0::numeric THEN 0::numeric
    ELSE LEAST(COALESCE(cm.monthly_purchases_total, 0::numeric), GREATEST(COALESCE(cm.purchases_total, 0::numeric) - COALESCE(cm.payments_total, 0::numeric), 0::numeric))
  END AS monthly_cc_payment,
  COALESCE(cm.purchases_total, 0::numeric) AS purchases_total,
  COALESCE(cm.payments_total, 0::numeric) AS payments_total
FROM credit_cards cc
LEFT JOIN card_movements cm ON cm.credit_card_id = cc.id
WHERE cc.is_active = true;
""")

    op.execute("""
CREATE OR REPLACE VIEW public.v_pending_obligations_current_month AS
SELECT
    o.id AS obligation_id,
    o.user_id,
    o.name,
    o.amount,
    o.currency,
    o.category_id,
    NOT EXISTS (
        SELECT 1
        FROM public.obligation_payments op
        WHERE op.obligation_id = o.id
        AND op.period = to_char(now() AT TIME ZONE 'America/Bogota', 'YYYY-MM')
    ) AS is_pending
FROM public.obligations o
WHERE o.is_active = true;
""")

    op.execute("""
CREATE OR REPLACE VIEW public.v_goals_current_month AS
 WITH current_period AS (
         SELECT to_char((now() AT TIME ZONE 'America/Bogota'::text), 'YYYY-MM'::text) AS period
        ), goal_contributions_month AS (
         SELECT gc.user_id,
            gc.goal_id,
            gc.period,
            sum(gc.amount) AS contributed_this_period
           FROM goal_contributions gc
          GROUP BY gc.user_id, gc.goal_id, gc.period
        )
 SELECT g.user_id,
    g.id AS goal_id,
    g.name,
    g.target_amount,
    g.current_amount,
    g.target_date,
    g.monthly_required,
    g.daily_required,
    g.progress_percentage,
    g.status,
    cp.period,
    COALESCE(gcm.contributed_this_period, (0)::numeric) AS contributed_this_period,
    GREATEST((COALESCE(g.monthly_required, (0)::numeric) - COALESCE(gcm.contributed_this_period, (0)::numeric)), (0)::numeric) AS remaining_required_this_period
   FROM ((goals g
     CROSS JOIN current_period cp)
     LEFT JOIN goal_contributions_month gcm ON (((gcm.goal_id = g.id) AND (gcm.user_id = g.user_id) AND (gcm.period = cp.period))))
  WHERE ((g.is_active = true) AND (g.status = ANY (ARRAY['active'::text, 'paused'::text])));
""")

    op.execute("""
CREATE OR REPLACE VIEW public.v_financial_snapshot_current_month AS
 WITH account_totals AS (
         SELECT accounts.user_id,
            sum(accounts.balance) AS available_real
           FROM accounts
          WHERE (accounts.is_active = true)
          GROUP BY accounts.user_id
        ), card_totals AS (
         SELECT v_credit_card_debt.user_id,
            sum(v_credit_card_debt.credit_card_debt) AS credit_card_debt,
            sum(v_credit_card_debt.monthly_cc_payment) AS monthly_cc_payment
           FROM v_credit_card_debt
          GROUP BY v_credit_card_debt.user_id
        ), obligation_totals AS (
         SELECT v_pending_obligations_current_month.user_id,
            sum(
                CASE
                    WHEN (v_pending_obligations_current_month.is_pending = true) THEN v_pending_obligations_current_month.amount
                    ELSE (0)::numeric
                END) AS pending_obligations_total
           FROM v_pending_obligations_current_month
          GROUP BY v_pending_obligations_current_month.user_id
        ), goal_totals AS (
         SELECT v_goals_current_month.user_id,
            sum(v_goals_current_month.remaining_required_this_period) AS monthly_goals_required_remaining
           FROM v_goals_current_month
          GROUP BY v_goals_current_month.user_id
        )
 SELECT u.id AS user_id,
    u.name AS user_name,
    COALESCE(at.available_real, (0)::numeric) AS available_real,
    COALESCE(ct.credit_card_debt, (0)::numeric) AS credit_card_debt,
    COALESCE(ct.monthly_cc_payment, (0)::numeric) AS monthly_cc_payment,
    COALESCE(ot.pending_obligations_total, (0)::numeric) AS pending_obligations_total,
    COALESCE(gt.monthly_goals_required_remaining, (0)::numeric) AS monthly_goals_required_remaining,
    ((COALESCE(at.available_real, (0)::numeric) - COALESCE(ct.monthly_cc_payment, (0)::numeric)) - COALESCE(ot.pending_obligations_total, (0)::numeric)) AS safe_money,
    (((COALESCE(at.available_real, (0)::numeric) - COALESCE(ct.monthly_cc_payment, (0)::numeric)) - COALESCE(ot.pending_obligations_total, (0)::numeric)) - COALESCE(gt.monthly_goals_required_remaining, (0)::numeric)) AS free_money,
    to_char((now() AT TIME ZONE 'America/Bogota'::text), 'YYYY-MM'::text) AS period,
    now() AS calculated_at
   FROM ((((users u
     LEFT JOIN account_totals at ON ((at.user_id = u.id)))
     LEFT JOIN card_totals ct ON ((ct.user_id = u.id)))
     LEFT JOIN obligation_totals ot ON ((ot.user_id = u.id)))
     LEFT JOIN goal_totals gt ON ((gt.user_id = u.id)))
  WHERE (u.status = 'active'::text);
""")

    op.execute("""
CREATE OR REPLACE VIEW public.v_cashflow_current_month AS
 WITH current_period AS (
         SELECT to_char((now() AT TIME ZONE 'America/Bogota'::text), 'YYYY-MM'::text) AS period
        ), monthly_aggregates AS (
         SELECT fe.user_id,
            cp.period,
            sum(
                CASE
                    WHEN (fe.event_type = 'income'::text) THEN fe.amount
                    ELSE (0)::numeric
                END) AS monthly_income,
            sum(
                CASE
                    WHEN (fe.event_type = 'expense'::text) THEN fe.amount
                    ELSE (0)::numeric
                END) AS consumption_outflow,
            sum(
                CASE
                    WHEN (fe.event_type = 'goal_contribution'::text) THEN fe.amount
                    ELSE (0)::numeric
                END) AS wealth_allocation,
            sum(
                CASE
                    WHEN (fe.event_type = 'credit_card_payment'::text) THEN fe.amount
                    ELSE (0)::numeric
                END) AS debt_service,
            sum(
                CASE
                    WHEN (fe.event_type = 'obligation_payment'::text) THEN fe.amount
                    ELSE (0)::numeric
                END) AS committed_outflow
           FROM (financial_events fe
             CROSS JOIN current_period cp)
          WHERE (fe.period = cp.period)
          GROUP BY fe.user_id, cp.period
        )
 SELECT u.id AS user_id,
    COALESCE(ma.period, to_char((now() AT TIME ZONE 'America/Bogota'::text), 'YYYY-MM'::text)) AS period,
    COALESCE(ma.monthly_income, (0)::numeric) AS monthly_income,
    COALESCE(ma.consumption_outflow, (0)::numeric) AS consumption_outflow,
    COALESCE(ma.wealth_allocation, (0)::numeric) AS wealth_allocation,
    COALESCE(ma.debt_service, (0)::numeric) AS debt_service,
    COALESCE(ma.committed_outflow, (0)::numeric) AS committed_outflow,
    (((COALESCE(ma.consumption_outflow, (0)::numeric) + COALESCE(ma.wealth_allocation, (0)::numeric)) + COALESCE(ma.debt_service, (0)::numeric)) + COALESCE(ma.committed_outflow, (0)::numeric)) AS total_outflow,
    ((COALESCE(ma.monthly_income, (0)::numeric) - COALESCE(ma.consumption_outflow, (0)::numeric)) - COALESCE(ma.committed_outflow, (0)::numeric)) AS surplus_after_consumption,
    (COALESCE(ma.monthly_income, (0)::numeric) - (((COALESCE(ma.consumption_outflow, (0)::numeric) + COALESCE(ma.wealth_allocation, (0)::numeric)) + COALESCE(ma.debt_service, (0)::numeric)) + COALESCE(ma.committed_outflow, (0)::numeric))) AS net_liquidity_change,
    now() AS calculated_at
   FROM (users u
     LEFT JOIN monthly_aggregates ma ON ((u.id = ma.user_id)))
  WHERE (u.status = 'active'::text);
""")

    op.execute("""
CREATE OR REPLACE VIEW public.v_consumption_summary_current_month AS
 WITH current_period AS (
         SELECT to_char((now() AT TIME ZONE 'America/Bogota'::text), 'YYYY-MM'::text) AS period
        ), consumption_aggregates AS (
         SELECT fe.user_id,
            cp.period,
            sum(
                CASE
                    WHEN (fe.event_type = ANY (ARRAY['expense'::text, 'obligation_payment'::text])) THEN fe.amount
                    ELSE (0)::numeric
                END) AS cash_consumption_outflow,
            sum(
                CASE
                    WHEN (fe.event_type = 'credit_card_purchase'::text) THEN fe.amount
                    ELSE (0)::numeric
                END) AS credit_card_consumption_committed
           FROM (financial_events fe
             CROSS JOIN current_period cp)
          WHERE (fe.period = cp.period)
          GROUP BY fe.user_id, cp.period
        )
 SELECT u.id AS user_id,
    COALESCE(ca.period, to_char((now() AT TIME ZONE 'America/Bogota'::text), 'YYYY-MM'::text)) AS period,
    COALESCE(ca.cash_consumption_outflow, (0)::numeric) AS cash_consumption_outflow,
    COALESCE(ca.credit_card_consumption_committed, (0)::numeric) AS credit_card_consumption_committed,
    (COALESCE(ca.cash_consumption_outflow, (0)::numeric) + COALESCE(ca.credit_card_consumption_committed, (0)::numeric)) AS total_consumption_committed,
    now() AS calculated_at
   FROM (users u
     LEFT JOIN consumption_aggregates ca ON ((u.id = ca.user_id)))
  WHERE (u.status = 'active'::text);
""")


def downgrade() -> None:
    # 1. Drop Views
    op.execute("DROP VIEW IF EXISTS public.v_consumption_summary_current_month CASCADE")
    op.execute("DROP VIEW IF EXISTS public.v_cashflow_current_month CASCADE")
    op.execute("DROP VIEW IF EXISTS public.v_financial_snapshot_current_month CASCADE")
    op.execute("DROP VIEW IF EXISTS public.v_goals_current_month CASCADE")
    op.execute("DROP VIEW IF EXISTS public.v_pending_obligations_current_month CASCADE")
    op.execute("DROP VIEW IF EXISTS public.v_credit_card_debt CASCADE")

    # 2. Drop Trigger and Function
    op.execute(
        "DROP TRIGGER IF EXISTS trg_financial_events_period ON public.financial_events CASCADE"
    )
    op.execute("DROP FUNCTION IF EXISTS set_financial_event_period() CASCADE")

    # 3. Drop Tables in reverse dependency order
    op.drop_table("transactions")
    op.drop_table("idempotency_keys")
    op.drop_table("user_channels")
    op.drop_table("logs")
    op.drop_table("ai_runs")
    op.drop_table("pending_actions")
    op.drop_table("messages")
    op.drop_table("obligation_payments")
    op.drop_table("obligation_periods")
    op.drop_table("obligations")
    op.drop_table("goal_contributions")
    op.drop_table("goals")
    op.drop_table("credit_card_statement_charges")
    op.drop_table("credit_card_early_payments")
    op.drop_table("credit_card_installments")
    op.drop_table("credit_card_transactions")
    op.drop_table("credit_card_statements")
    op.drop_table("credit_cards")
    op.drop_table("financial_events")
    op.drop_table("transfers")
    op.drop_table("categories")
    op.drop_table("accounts")
    op.drop_table("users")
