"""credit_v2_canonical_closure

Revision ID: credit_v2_canonical_closure
Revises: credit_v2_canonical_check
Create Date: 2026-10-28 12:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "credit_v2_canonical_closure"
down_revision: str | None = "credit_v2_canonical_check"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Allow nullable minimum_payment on credit_card_statements for unknown minimum payment
    op.alter_column(
        "credit_card_statements",
        "minimum_payment",
        existing_type=sa.Numeric(precision=14, scale=2),
        nullable=True,
        server_default=None,
    )

    # 2. Scope canonical cycle statement uniqueness via partial unique index
    # Never enforce unconditional uniqueness over unverified legacy rows
    op.create_index(
        "uq_cc_statements_canonical_cycle_id",
        "credit_card_statements",
        ["cycle_id"],
        unique=True,
        postgresql_where=sa.text("cycle_id IS NOT NULL AND debt_partition = 'canonical'"),
    )

    # 3. Add explicit additive direction to credit_card_adjustments
    # Nullable for safe legacy compatibility; required by canonical service for new manual adjustments
    op.add_column(
        "credit_card_adjustments",
        sa.Column("direction", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "chk_cc_adjustments_direction",
        "credit_card_adjustments",
        "direction IS NULL OR direction = ANY (ARRAY['debit'::text, 'credit'::text])",
    )

    # 4. Extend statement component type constraint to include 'prior_balance' and 'prior_credit'
    op.drop_constraint(
        "chk_cc_stmt_comp_type",
        "credit_card_statement_components",
        type_="check",
    )
    op.create_check_constraint(
        "chk_cc_stmt_comp_type",
        "credit_card_statement_components",
        "component_type = ANY (ARRAY['purchase'::text, 'installment'::text, 'fee'::text, "
        "'interest'::text, 'refund'::text, 'reversal'::text, 'adjustment'::text, "
        "'payment'::text, 'prior_balance'::text, 'prior_credit'::text])",
    )

    # 5. Partial unique indexes to prevent duplicate components under retry/concurrency
    op.create_index(
        "uq_cc_stmt_comp_statement_installment",
        "credit_card_statement_components",
        ["statement_id", "installment_id"],
        unique=True,
        postgresql_where=sa.text("installment_id IS NOT NULL"),
    )
    op.create_index(
        "uq_cc_stmt_comp_statement_tx",
        "credit_card_statement_components",
        ["statement_id", "transaction_id"],
        unique=True,
        postgresql_where=sa.text("transaction_id IS NOT NULL"),
    )
    op.create_index(
        "uq_cc_stmt_comp_statement_charge",
        "credit_card_statement_components",
        ["statement_id", "statement_charge_id"],
        unique=True,
        postgresql_where=sa.text("statement_charge_id IS NOT NULL"),
    )

    # 6. Unique index on statement facts to guarantee single fact per type
    op.create_index(
        "uq_cc_stmt_facts_stmt_fact_type",
        "credit_card_statement_facts",
        ["statement_id", "fact_type"],
        unique=True,
    )

    # 7. Physical immutability triggers for closed/settled statements and components
    op.execute(
        """
        CREATE OR REPLACE FUNCTION trg_fn_prevent_statement_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status IN ('closed', 'settled') THEN
                    RAISE EXCEPTION 'Cannot delete credit card statement in status % (id=%)', OLD.status, OLD.id USING ERRCODE = '20000';
                END IF;
                RETURN OLD;
            ELSIF TG_OP = 'UPDATE' THEN
                IF OLD.status IN ('closed', 'settled') THEN
                    RAISE EXCEPTION 'Cannot update credit card statement in status % (id=%)', OLD.status, OLD.id USING ERRCODE = '20000';
                END IF;
                RETURN NEW;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_cc_statements_immutable
        BEFORE UPDATE OR DELETE ON credit_card_statements
        FOR EACH ROW
        EXECUTE FUNCTION trg_fn_prevent_statement_mutation();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION trg_fn_prevent_statement_component_mutation()
        RETURNS TRIGGER AS $$
        DECLARE
            v_status text;
        BEGIN
            SELECT status INTO v_status FROM credit_card_statements WHERE id = OLD.statement_id;
            IF v_status IS NULL OR v_status IN ('closed', 'settled') THEN
                RAISE EXCEPTION 'Cannot % immutable statement component % (parent statement status %)', TG_OP, OLD.id, COALESCE(v_status, 'deleted') USING ERRCODE = '20000';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            ELSE
                RETURN NEW;
            END IF;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_cc_stmt_comp_immutable
        BEFORE UPDATE OR DELETE ON credit_card_statement_components
        FOR EACH ROW
        EXECUTE FUNCTION trg_fn_prevent_statement_component_mutation();
        """
    )


def downgrade() -> None:
    # Revert immutability triggers and functions
    op.execute(
        "DROP TRIGGER IF EXISTS trg_cc_stmt_comp_immutable ON credit_card_statement_components;"
    )
    op.execute("DROP FUNCTION IF EXISTS trg_fn_prevent_statement_component_mutation();")
    op.execute("DROP TRIGGER IF EXISTS trg_cc_statements_immutable ON credit_card_statements;")
    op.execute("DROP FUNCTION IF EXISTS trg_fn_prevent_statement_mutation();")

    op.drop_index("uq_cc_stmt_facts_stmt_fact_type", table_name="credit_card_statement_facts")
    op.drop_index("uq_cc_stmt_comp_statement_charge", table_name="credit_card_statement_components")
    op.drop_index("uq_cc_stmt_comp_statement_tx", table_name="credit_card_statement_components")
    op.drop_index(
        "uq_cc_stmt_comp_statement_installment", table_name="credit_card_statement_components"
    )

    # Revert component type check constraint
    op.drop_constraint("chk_cc_stmt_comp_type", "credit_card_statement_components", type_="check")
    op.create_check_constraint(
        "chk_cc_stmt_comp_type",
        "credit_card_statement_components",
        "component_type = ANY (ARRAY['purchase'::text, 'installment'::text, 'fee'::text, "
        "'interest'::text, 'refund'::text, 'reversal'::text, 'adjustment'::text, 'payment'::text])",
    )

    op.drop_constraint("chk_cc_adjustments_direction", "credit_card_adjustments", type_="check")
    op.drop_column("credit_card_adjustments", "direction")

    op.drop_index("uq_cc_statements_canonical_cycle_id", table_name="credit_card_statements")
    # Note on minimum_payment: do not force NOT NULL on downgrade to prevent aborting on legitimate NULL facts
