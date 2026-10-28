"""credit_v2_canonical_schema

Revision ID: credit_v2_canonical_schema
Revises: obligations_v1_7_auto_payments
Create Date: 2026-10-28 10:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "credit_v2_canonical_schema"
down_revision: str | None = "obligations_v1_7_auto_payments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. Create credit_card_billing_cycles ─────────────────────────────
    op.create_table(
        "credit_card_billing_cycles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credit_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("billing_period", sa.Text(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("cutoff_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'open'::text")),
        sa.Column(
            "provenance",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'nexum_modeled'::text"),
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
        sa.ForeignKeyConstraint(["credit_card_id"], ["credit_cards.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "credit_card_id", "billing_period", name="uq_cc_billing_cycles_card_period"
        ),
        sa.CheckConstraint(
            "start_date <= cutoff_date", name="chk_cc_billing_cycles_start_cutoff"
        ),
        sa.CheckConstraint(
            "cutoff_date <= due_date", name="chk_cc_billing_cycles_cutoff_due"
        ),
        sa.CheckConstraint(
            "status = ANY (ARRAY['open'::text, 'closed'::text, 'settled'::text])",
            name="chk_cc_billing_cycles_status",
        ),
        sa.CheckConstraint(
            "provenance = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'manual'::text, 'legacy_unknown'::text])",
            name="chk_cc_billing_cycles_provenance",
        ),
    )
    op.create_index(
        "ix_credit_card_billing_cycles_user_card",
        "credit_card_billing_cycles",
        ["user_id", "credit_card_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_billing_cycles_card_status",
        "credit_card_billing_cycles",
        ["credit_card_id", "status"],
        unique=False,
    )

    # ── 2. Create credit_card_statement_components ───────────────────────
    # Note: component amounts are positive. Domain logic applies compensating
    # direction by component_type (e.g. refund, reversal); no negative amounts.
    op.create_table(
        "credit_card_statement_components",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credit_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("statement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_type", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default=sa.text("'COP'::text")),
        sa.Column(
            "principal_amount",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "interest_amount",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "fee_amount",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("installment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("statement_charge_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "provenance",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'nexum_modeled'::text"),
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credit_card_id"], ["credit_cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["statement_id"], ["credit_card_statements.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["credit_card_transactions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["installment_id"], ["credit_card_installments.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["statement_charge_id"],
            ["credit_card_statement_charges.id"],
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "amount >= 0::numeric", name="chk_cc_stmt_comp_amount_nonneg"
        ),
        sa.CheckConstraint(
            "principal_amount >= 0::numeric AND interest_amount >= 0::numeric AND fee_amount >= 0::numeric",
            name="chk_cc_stmt_comp_parts_nonneg",
        ),
        sa.CheckConstraint(
            "amount = (principal_amount + interest_amount + fee_amount)",
            name="chk_cc_stmt_comp_conservation",
        ),
        sa.CheckConstraint(
            "component_type = ANY (ARRAY['purchase'::text, 'installment'::text, 'fee'::text, 'interest'::text, 'refund'::text, 'reversal'::text, 'adjustment'::text, 'payment'::text])",
            name="chk_cc_stmt_comp_type",
        ),
        sa.CheckConstraint(
            "provenance = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'manual'::text, 'legacy_unknown'::text])",
            name="chk_cc_stmt_comp_provenance",
        ),
    )
    op.create_index(
        "ix_credit_card_statement_components_stmt_id",
        "credit_card_statement_components",
        ["statement_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_statement_components_user_card",
        "credit_card_statement_components",
        ["user_id", "credit_card_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_statement_components_tx_id",
        "credit_card_statement_components",
        ["transaction_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_statement_components_inst_id",
        "credit_card_statement_components",
        ["installment_id"],
        unique=False,
    )

    # ── 3. Create credit_card_statement_facts ────────────────────────────
    # Statement-level facts (e.g. minimum_payment_due). Absence of fact represents
    # unknown minimum payment; never defaults to 0.00.
    op.create_table(
        "credit_card_statement_facts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credit_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("statement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_type", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column(
            "provenance",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'issuer_authoritative'::text"),
        ),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credit_card_id"], ["credit_cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["statement_id"], ["credit_card_statements.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "amount IS NULL OR amount >= 0::numeric",
            name="chk_cc_stmt_facts_amount_nonneg",
        ),
        sa.CheckConstraint(
            "fact_type = ANY (ARRAY['minimum_payment_due'::text, 'statement_balance'::text, 'interest_rate'::text, 'fee_assessed'::text])",
            name="chk_cc_stmt_facts_type",
        ),
        sa.CheckConstraint(
            "provenance = ANY (ARRAY['issuer_authoritative'::text, 'nexum_modeled'::text, 'manual'::text])",
            name="chk_cc_stmt_facts_provenance",
        ),
    )
    op.create_index(
        "ix_credit_card_statement_facts_stmt_type",
        "credit_card_statement_facts",
        ["statement_id", "fact_type"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_statement_facts_user_card",
        "credit_card_statement_facts",
        ["user_id", "credit_card_id"],
        unique=False,
    )

    # ── 4. Create credit_card_payments ───────────────────────────────────
    # Canonical payments store explicit booked_amount/booked_currency and
    # source_amount/source_currency. command_id matches financial_events.command_id as UUID.
    op.create_table(
        "credit_card_payments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credit_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payment_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("booked_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "booked_currency",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'COP'::text"),
        ),
        sa.Column("source_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("source_currency", sa.Text(), nullable=True),
        sa.Column("fx_rate", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("fx_quote_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fx_rate_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "principal_applied",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "interest_applied",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "fee_applied",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "unallocated_amount",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'posted'::text")),
        sa.Column(
            "provenance",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'nexum_modeled'::text"),
        ),
        sa.Column(
            "allocation_source",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'nexum_modeled'::text"),
        ),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("command_fingerprint", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["payment_transaction_id"],
            ["credit_card_transactions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["financial_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["fx_quote_id"], ["fx_quotes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["fx_rate_snapshot_id"], ["exchange_rates.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint("booked_amount > 0::numeric", name="chk_cc_payments_amount_pos"),
        sa.CheckConstraint(
            "source_amount IS NULL OR source_amount > 0::numeric",
            name="chk_cc_payments_source_amount_pos",
        ),
        sa.CheckConstraint(
            "principal_applied >= 0::numeric AND interest_applied >= 0::numeric AND fee_applied >= 0::numeric AND unallocated_amount >= 0::numeric",
            name="chk_cc_payments_applied_nonneg",
        ),
        sa.CheckConstraint(
            "booked_amount = (principal_applied + interest_applied + fee_applied + unallocated_amount)",
            name="chk_cc_payments_conservation",
        ),
        sa.CheckConstraint(
            "status = ANY (ARRAY['pending'::text, 'posted'::text, 'reversed'::text])",
            name="chk_cc_payments_status",
        ),
        sa.CheckConstraint(
            "provenance = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'manual'::text, 'legacy_unknown'::text])",
            name="chk_cc_payments_provenance",
        ),
        sa.CheckConstraint(
            "allocation_source = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'user_directed'::text])",
            name="chk_cc_payments_alloc_source",
        ),
    )
    op.create_index(
        "ix_credit_card_payments_user_card",
        "credit_card_payments",
        ["user_id", "credit_card_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_payments_event_id",
        "credit_card_payments",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_payments_account_id",
        "credit_card_payments",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_payments_tx_id",
        "credit_card_payments",
        ["payment_transaction_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_payments_user_command",
        "credit_card_payments",
        ["user_id", "command_id"],
        unique=True,
        postgresql_where=sa.text("command_id IS NOT NULL"),
    )

    # ── 5. Create credit_card_payment_allocations ────────────────────────
    # Targets exactly one of: target_installment_id, target_charge_id, or
    # target_statement_component_id (auditable exact debt component).
    op.create_table(
        "credit_card_payment_allocations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credit_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_installment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_charge_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "target_statement_component_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("allocated_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "allocated_principal",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "allocated_interest",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "allocated_fees",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "allocation_order", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "allocation_source",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'nexum_modeled'::text"),
        ),
        sa.Column(
            "provenance",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'nexum_modeled'::text"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credit_card_id"], ["credit_cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["payment_id"], ["credit_card_payments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_installment_id"],
            ["credit_card_installments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_charge_id"],
            ["credit_card_statement_charges.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_statement_component_id"],
            ["credit_card_statement_components.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "((CASE WHEN target_installment_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_charge_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_statement_component_id IS NOT NULL THEN 1 ELSE 0 END)) = 1",
            name="chk_cc_alloc_exactly_one_target",
        ),
        sa.CheckConstraint(
            "allocated_amount > 0::numeric", name="chk_cc_alloc_amount_pos"
        ),
        sa.CheckConstraint(
            "allocated_principal >= 0::numeric AND allocated_interest >= 0::numeric AND allocated_fees >= 0::numeric",
            name="chk_cc_alloc_parts_nonneg",
        ),
        sa.CheckConstraint(
            "allocated_amount = (allocated_principal + allocated_interest + allocated_fees)",
            name="chk_cc_alloc_conservation",
        ),
        sa.CheckConstraint("allocation_order >= 1", name="chk_cc_alloc_order_pos"),
        sa.CheckConstraint(
            "allocation_source = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'user_directed'::text])",
            name="chk_cc_alloc_source",
        ),
        sa.CheckConstraint(
            "provenance = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'manual'::text, 'legacy_unknown'::text])",
            name="chk_cc_alloc_provenance",
        ),
    )
    op.create_index(
        "ix_credit_card_payment_allocations_payment_id",
        "credit_card_payment_allocations",
        ["payment_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_payment_allocations_user_card",
        "credit_card_payment_allocations",
        ["user_id", "credit_card_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_payment_allocations_target_inst",
        "credit_card_payment_allocations",
        ["target_installment_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_payment_allocations_target_charge",
        "credit_card_payment_allocations",
        ["target_charge_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_payment_allocations_target_stmt_comp",
        "credit_card_payment_allocations",
        ["target_statement_component_id"],
        unique=False,
    )

    # ── 6. Create credit_card_adjustments ────────────────────────────────
    # Exposes original_amount/original_currency, booked_amount/booked_currency,
    # and explicit FX snapshot/quote/provider/timestamp evidence.
    op.create_table(
        "credit_card_adjustments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credit_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "original_transaction_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("adjustment_type", sa.Text(), nullable=False),
        sa.Column("booked_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "booked_currency",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'COP'::text"),
        ),
        sa.Column("original_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("original_currency", sa.Text(), nullable=True),
        sa.Column("fx_rate", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column(
            "fx_rate_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("fx_quote_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fx_provider", sa.Text(), nullable=True),
        sa.Column("fx_rate_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'posted'::text")),
        sa.Column(
            "provenance",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'nexum_modeled'::text"),
        ),
        sa.Column(
            "allocation_source",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'nexum_modeled'::text"),
        ),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("command_fingerprint", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
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
        sa.ForeignKeyConstraint(["credit_card_id"], ["credit_cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["original_transaction_id"],
            ["credit_card_transactions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["financial_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["fx_rate_snapshot_id"], ["exchange_rates.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["fx_quote_id"], ["fx_quotes.id"], ondelete="SET NULL"),
        sa.CheckConstraint("booked_amount > 0::numeric", name="chk_cc_adjustments_amount_pos"),
        sa.CheckConstraint(
            "original_amount IS NULL OR original_amount > 0::numeric",
            name="chk_cc_adjustments_orig_amount_pos",
        ),
        sa.CheckConstraint(
            "adjustment_type = ANY (ARRAY['refund'::text, 'reversal'::text, 'chargeback'::text, 'statement_credit'::text, 'manual_adjustment'::text])",
            name="chk_cc_adjustments_type",
        ),
        sa.CheckConstraint(
            "status = ANY (ARRAY['pending'::text, 'posted'::text, 'reversed'::text])",
            name="chk_cc_adjustments_status",
        ),
        sa.CheckConstraint(
            "provenance = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'manual'::text, 'legacy_unknown'::text])",
            name="chk_cc_adjustments_provenance",
        ),
        sa.CheckConstraint(
            "allocation_source = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'user_directed'::text])",
            name="chk_cc_adjustments_alloc_source",
        ),
    )
    op.create_index(
        "ix_credit_card_adjustments_user_card",
        "credit_card_adjustments",
        ["user_id", "credit_card_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_adjustments_orig_tx",
        "credit_card_adjustments",
        ["original_transaction_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_adjustments_event_id",
        "credit_card_adjustments",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_adjustments_user_command",
        "credit_card_adjustments",
        ["user_id", "command_id"],
        unique=True,
        postgresql_where=sa.text("command_id IS NOT NULL"),
    )

    # ── 7. Create credit_card_adjustment_allocations ─────────────────────
    op.create_table(
        "credit_card_adjustment_allocations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credit_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("adjustment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_installment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_charge_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("allocated_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "allocated_principal",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "allocated_interest",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "allocated_fees",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "allocation_order", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "allocation_source",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'nexum_modeled'::text"),
        ),
        sa.Column(
            "provenance",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'nexum_modeled'::text"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credit_card_id"], ["credit_cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["adjustment_id"], ["credit_card_adjustments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_installment_id"],
            ["credit_card_installments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_transaction_id"],
            ["credit_card_transactions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_charge_id"],
            ["credit_card_statement_charges.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "((CASE WHEN target_installment_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_transaction_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_charge_id IS NOT NULL THEN 1 ELSE 0 END)) = 1",
            name="chk_cc_adj_alloc_exactly_one_target",
        ),
        sa.CheckConstraint(
            "allocated_amount > 0::numeric", name="chk_cc_adj_alloc_amount_pos"
        ),
        sa.CheckConstraint(
            "allocated_principal >= 0::numeric AND allocated_interest >= 0::numeric AND allocated_fees >= 0::numeric",
            name="chk_cc_adj_alloc_parts_nonneg",
        ),
        sa.CheckConstraint(
            "allocated_amount = (allocated_principal + allocated_interest + allocated_fees)",
            name="chk_cc_adj_alloc_conservation",
        ),
        sa.CheckConstraint("allocation_order >= 1", name="chk_cc_adj_alloc_order_pos"),
        sa.CheckConstraint(
            "allocation_source = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'user_directed'::text])",
            name="chk_cc_adj_alloc_source",
        ),
        sa.CheckConstraint(
            "provenance = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'manual'::text, 'legacy_unknown'::text])",
            name="chk_cc_adj_alloc_provenance",
        ),
    )
    op.create_index(
        "ix_credit_card_adj_alloc_adj_id",
        "credit_card_adjustment_allocations",
        ["adjustment_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_adj_alloc_user_card",
        "credit_card_adjustment_allocations",
        ["user_id", "credit_card_id"],
        unique=False,
    )

    # ── 8. Safe nullable/defaulted columns on existing tables ────────────

    # 8.1 public.credit_cards
    op.add_column(
        "credit_cards",
        sa.Column(
            "canonical_mode",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "credit_cards",
        sa.Column("canonical_activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "credit_cards",
        sa.Column(
            "provenance",
            sa.Text(),
            server_default=sa.text("'legacy_unknown'::text"),
            nullable=False,
        ),
    )
    op.add_column(
        "credit_cards",
        sa.Column(
            "lifecycle_status",
            sa.Text(),
            server_default=sa.text("'active'::text"),
            nullable=False,
        ),
    )

    # 8.2 public.credit_card_transactions
    op.add_column(
        "credit_card_transactions",
        sa.Column("original_amount", sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.add_column(
        "credit_card_transactions",
        sa.Column("original_currency", sa.Text(), nullable=True),
    )
    op.add_column(
        "credit_card_transactions",
        sa.Column("booked_amount", sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.add_column(
        "credit_card_transactions",
        sa.Column("booked_currency", sa.Text(), nullable=True),
    )
    op.add_column(
        "credit_card_transactions",
        sa.Column("fx_rate", sa.Numeric(precision=18, scale=8), nullable=True),
    )
    op.add_column(
        "credit_card_transactions",
        sa.Column("fx_rate_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "credit_card_transactions",
        sa.Column("fx_quote_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "credit_card_transactions",
        sa.Column("cycle_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "credit_card_transactions",
        sa.Column(
            "lifecycle_status",
            sa.Text(),
            server_default=sa.text("'posted'::text"),
            nullable=False,
        ),
    )
    op.add_column(
        "credit_card_transactions",
        sa.Column(
            "provenance",
            sa.Text(),
            server_default=sa.text("'legacy_unknown'::text"),
            nullable=False,
        ),
    )
    op.add_column(
        "credit_card_transactions",
        sa.Column(
            "debt_partition",
            sa.Text(),
            server_default=sa.text("'legacy'::text"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_cct_fx_rate_snapshot",
        "credit_card_transactions",
        "exchange_rates",
        ["fx_rate_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_cct_fx_quote",
        "credit_card_transactions",
        "fx_quotes",
        ["fx_quote_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_cct_cycle",
        "credit_card_transactions",
        "credit_card_billing_cycles",
        ["cycle_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "chk_cct_debt_partition",
        "credit_card_transactions",
        "debt_partition = ANY (ARRAY['legacy'::text, 'canonical'::text])",
    )
    op.create_index(
        "ix_credit_card_transactions_event_id",
        "credit_card_transactions",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_transactions_cycle_id",
        "credit_card_transactions",
        ["cycle_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_transactions_card_partition",
        "credit_card_transactions",
        ["credit_card_id", "debt_partition"],
        unique=False,
    )

    # 8.3 public.credit_card_installments
    op.add_column(
        "credit_card_installments",
        sa.Column("original_amount", sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.add_column(
        "credit_card_installments",
        sa.Column("original_currency", sa.Text(), nullable=True),
    )
    op.add_column(
        "credit_card_installments",
        sa.Column("booked_amount", sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.add_column(
        "credit_card_installments",
        sa.Column("booked_currency", sa.Text(), nullable=True),
    )
    op.add_column(
        "credit_card_installments",
        sa.Column("cycle_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "credit_card_installments",
        sa.Column(
            "assessed_interest_amount", sa.Numeric(precision=14, scale=2), nullable=True
        ),
    )
    op.add_column(
        "credit_card_installments",
        sa.Column(
            "estimated_interest_amount", sa.Numeric(precision=14, scale=2), nullable=True
        ),
    )
    op.add_column(
        "credit_card_installments",
        sa.Column(
            "lifecycle_status",
            sa.Text(),
            server_default=sa.text("'active'::text"),
            nullable=False,
        ),
    )
    op.add_column(
        "credit_card_installments",
        sa.Column(
            "provenance",
            sa.Text(),
            server_default=sa.text("'legacy_unknown'::text"),
            nullable=False,
        ),
    )
    op.add_column(
        "credit_card_installments",
        sa.Column(
            "debt_partition",
            sa.Text(),
            server_default=sa.text("'legacy'::text"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_cci_cycle",
        "credit_card_installments",
        "credit_card_billing_cycles",
        ["cycle_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "chk_cci_debt_partition",
        "credit_card_installments",
        "debt_partition = ANY (ARRAY['legacy'::text, 'canonical'::text])",
    )
    op.create_index(
        "ix_credit_card_installments_cycle_id",
        "credit_card_installments",
        ["cycle_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_installments_card_partition",
        "credit_card_installments",
        ["credit_card_id", "debt_partition"],
        unique=False,
    )

    # 8.4 public.credit_card_statements
    op.add_column(
        "credit_card_statements",
        sa.Column("cycle_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "credit_card_statements",
        sa.Column("currency", sa.Text(), nullable=True),
    )
    op.add_column(
        "credit_card_statements",
        sa.Column("assessed_interest", sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.add_column(
        "credit_card_statements",
        sa.Column("estimated_interest", sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.add_column(
        "credit_card_statements",
        sa.Column(
            "provenance",
            sa.Text(),
            server_default=sa.text("'legacy_unknown'::text"),
            nullable=False,
        ),
    )
    op.add_column(
        "credit_card_statements",
        sa.Column(
            "debt_partition",
            sa.Text(),
            server_default=sa.text("'legacy'::text"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_ccs_cycle",
        "credit_card_statements",
        "credit_card_billing_cycles",
        ["cycle_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "chk_ccs_debt_partition",
        "credit_card_statements",
        "debt_partition = ANY (ARRAY['legacy'::text, 'canonical'::text])",
    )

    # 8.5 public.credit_card_early_payments
    op.add_column(
        "credit_card_early_payments",
        sa.Column("payment_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "credit_card_early_payments",
        sa.Column("original_amount", sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.add_column(
        "credit_card_early_payments",
        sa.Column("original_currency", sa.Text(), nullable=True),
    )
    op.add_column(
        "credit_card_early_payments",
        sa.Column("booked_amount", sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.add_column(
        "credit_card_early_payments",
        sa.Column("booked_currency", sa.Text(), nullable=True),
    )
    op.add_column(
        "credit_card_early_payments",
        sa.Column(
            "provenance",
            sa.Text(),
            server_default=sa.text("'legacy_unknown'::text"),
            nullable=False,
        ),
    )
    op.add_column(
        "credit_card_early_payments",
        sa.Column(
            "debt_partition",
            sa.Text(),
            server_default=sa.text("'legacy'::text"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_ccep_payment_tx",
        "credit_card_early_payments",
        "credit_card_transactions",
        ["payment_transaction_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "chk_ccep_debt_partition",
        "credit_card_early_payments",
        "debt_partition = ANY (ARRAY['legacy'::text, 'canonical'::text])",
    )
    op.create_index(
        "ix_credit_card_early_payments_event_id",
        "credit_card_early_payments",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        "ix_credit_card_early_payments_payment_tx_id",
        "credit_card_early_payments",
        ["payment_transaction_id"],
        unique=False,
    )


def downgrade() -> None:
    # 8.5 Drop early payment additions
    op.drop_index(
        "ix_credit_card_early_payments_payment_tx_id",
        table_name="credit_card_early_payments",
    )
    op.drop_index(
        "ix_credit_card_early_payments_event_id",
        table_name="credit_card_early_payments",
    )
    op.drop_constraint(
        "chk_ccep_debt_partition",
        "credit_card_early_payments",
        type_="check",
    )
    op.drop_constraint(
        "fk_ccep_payment_tx",
        "credit_card_early_payments",
        type_="foreignkey",
    )
    op.drop_column("credit_card_early_payments", "debt_partition")
    op.drop_column("credit_card_early_payments", "provenance")
    op.drop_column("credit_card_early_payments", "booked_currency")
    op.drop_column("credit_card_early_payments", "booked_amount")
    op.drop_column("credit_card_early_payments", "original_currency")
    op.drop_column("credit_card_early_payments", "original_amount")
    op.drop_column("credit_card_early_payments", "payment_transaction_id")

    # 8.4 Drop statement additions
    op.drop_constraint(
        "chk_ccs_debt_partition",
        "credit_card_statements",
        type_="check",
    )
    op.drop_constraint("fk_ccs_cycle", "credit_card_statements", type_="foreignkey")
    op.drop_column("credit_card_statements", "debt_partition")
    op.drop_column("credit_card_statements", "provenance")
    op.drop_column("credit_card_statements", "estimated_interest")
    op.drop_column("credit_card_statements", "assessed_interest")
    op.drop_column("credit_card_statements", "currency")
    op.drop_column("credit_card_statements", "cycle_id")

    # 8.3 Drop installment additions
    op.drop_index(
        "ix_credit_card_installments_card_partition",
        table_name="credit_card_installments",
    )
    op.drop_index(
        "ix_credit_card_installments_cycle_id",
        table_name="credit_card_installments",
    )
    op.drop_constraint(
        "chk_cci_debt_partition",
        "credit_card_installments",
        type_="check",
    )
    op.drop_constraint("fk_cci_cycle", "credit_card_installments", type_="foreignkey")
    op.drop_column("credit_card_installments", "debt_partition")
    op.drop_column("credit_card_installments", "provenance")
    op.drop_column("credit_card_installments", "lifecycle_status")
    op.drop_column("credit_card_installments", "estimated_interest_amount")
    op.drop_column("credit_card_installments", "assessed_interest_amount")
    op.drop_column("credit_card_installments", "cycle_id")
    op.drop_column("credit_card_installments", "booked_currency")
    op.drop_column("credit_card_installments", "booked_amount")
    op.drop_column("credit_card_installments", "original_currency")
    op.drop_column("credit_card_installments", "original_amount")

    # 8.2 Drop transaction additions
    op.drop_index(
        "ix_credit_card_transactions_card_partition",
        table_name="credit_card_transactions",
    )
    op.drop_index(
        "ix_credit_card_transactions_cycle_id",
        table_name="credit_card_transactions",
    )
    op.drop_index(
        "ix_credit_card_transactions_event_id",
        table_name="credit_card_transactions",
    )
    op.drop_constraint(
        "chk_cct_debt_partition",
        "credit_card_transactions",
        type_="check",
    )
    op.drop_constraint("fk_cct_cycle", "credit_card_transactions", type_="foreignkey")
    op.drop_constraint("fk_cct_fx_quote", "credit_card_transactions", type_="foreignkey")
    op.drop_constraint(
        "fk_cct_fx_rate_snapshot", "credit_card_transactions", type_="foreignkey"
    )
    op.drop_column("credit_card_transactions", "debt_partition")
    op.drop_column("credit_card_transactions", "provenance")
    op.drop_column("credit_card_transactions", "lifecycle_status")
    op.drop_column("credit_card_transactions", "cycle_id")
    op.drop_column("credit_card_transactions", "fx_quote_id")
    op.drop_column("credit_card_transactions", "fx_rate_snapshot_id")
    op.drop_column("credit_card_transactions", "fx_rate")
    op.drop_column("credit_card_transactions", "booked_currency")
    op.drop_column("credit_card_transactions", "booked_amount")
    op.drop_column("credit_card_transactions", "original_currency")
    op.drop_column("credit_card_transactions", "original_amount")

    # 8.1 Drop credit card additions
    op.drop_column("credit_cards", "lifecycle_status")
    op.drop_column("credit_cards", "provenance")
    op.drop_column("credit_cards", "canonical_activated_at")
    op.drop_column("credit_cards", "canonical_mode")

    # Drop tables in reverse creation order
    op.drop_table("credit_card_adjustment_allocations")
    op.drop_table("credit_card_adjustments")
    op.drop_table("credit_card_payment_allocations")
    op.drop_table("credit_card_payments")
    op.drop_table("credit_card_statement_facts")
    op.drop_table("credit_card_statement_components")
    op.drop_table("credit_card_billing_cycles")
