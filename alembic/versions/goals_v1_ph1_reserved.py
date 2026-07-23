"""Add the reserved-allocation persistence model for Goals V1 Phase 1.

Revision ID: goals_v1_ph1_reserved
Revises: transfers_v1_ph2_multicurrency
Create Date: 2026-07-22 13:40:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "goals_v1_ph1_reserved"
down_revision = "transfers_v1_ph2_multicurrency"
branch_labels = None
depends_on = None


LEGACY_FINANCIAL_EVENT_TYPES = (
    "income",
    "expense",
    "goal_contribution",
    "obligation_payment",
    "credit_card_purchase",
    "credit_card_payment",
    "manual_adjustment",
    "transfer_out",
    "transfer_in",
)
PRE_GOALS_FINANCIAL_EVENT_TYPES = (
    *LEGACY_FINANCIAL_EVENT_TYPES,
    "opening_balance",
)
POST_GOALS_FINANCIAL_EVENT_TYPES = (
    *PRE_GOALS_FINANCIAL_EVENT_TYPES,
    "balance_adjustment",
)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _event_type_check_sql(event_types: tuple[str, ...]) -> str:
    values = ", ".join(_sql_literal(value) for value in event_types)
    return f"event_type = ANY (ARRAY[{values}])"


def _sql_text_array(event_types: tuple[str, ...]) -> str:
    values = ", ".join(_sql_literal(value) for value in sorted(event_types))
    return f"ARRAY[{values}]::text[]"


def _constraint_precheck_sql(
    accepted_event_type_sets: tuple[tuple[str, ...], ...],
    operation: str,
) -> str:
    accepted_conditions = " OR ".join(
        f"v_allowed_types = {_sql_text_array(event_types)}"
        for event_types in accepted_event_type_sets
    )
    return f"""
        DO $$
        DECLARE
            v_allowed_types text[];
            v_constraint_validated boolean;
        BEGIN
            SELECT
                array_agg(DISTINCT extracted.value[1] ORDER BY extracted.value[1]),
                bool_and(con.convalidated)
            INTO v_allowed_types, v_constraint_validated
            FROM pg_constraint AS con
            CROSS JOIN LATERAL regexp_matches(
                pg_get_constraintdef(con.oid, true),
                '''([^'']+)''',
                'g'
            ) AS extracted(value)
            WHERE con.conrelid = 'public.financial_events'::regclass
              AND con.conname = 'financial_events_type_check'
              AND con.contype = 'c';

            IF v_allowed_types IS NULL THEN
                RAISE EXCEPTION
                    'financial_events_type_check does not exist or has no values';
            END IF;

            IF v_constraint_validated IS DISTINCT FROM true THEN
                RAISE EXCEPTION
                    'financial_events_type_check is not validated';
            END IF;

            IF NOT ({accepted_conditions}) THEN
                RAISE EXCEPTION
                    'Goals V1 {operation} blocked: unknown financial event types: %',
                    v_allowed_types;
            END IF;
        END $$;
    """


def _upgrade_financial_events_type_constraint() -> None:
    # V1.1 introduced opening_balance outside Alembic on some installations.
    # Accept only the exact known historical sets and converge all of them.
    op.execute(
        _constraint_precheck_sql(
            (
                LEGACY_FINANCIAL_EVENT_TYPES,
                PRE_GOALS_FINANCIAL_EVENT_TYPES,
                POST_GOALS_FINANCIAL_EVENT_TYPES,
            ),
            "upgrade",
        )
    )
    op.drop_constraint(
        "financial_events_type_check",
        "financial_events",
        type_="check",
    )
    op.create_check_constraint(
        "financial_events_type_check",
        "financial_events",
        _event_type_check_sql(POST_GOALS_FINANCIAL_EVENT_TYPES),
    )


def _downgrade_financial_events_type_constraint() -> None:
    op.execute(
        _constraint_precheck_sql(
            (POST_GOALS_FINANCIAL_EVENT_TYPES,),
            "downgrade",
        )
    )
    op.execute(
        """
        DO $$
        DECLARE
            v_remaining_balance_adjustments bigint;
        BEGIN
            SELECT COUNT(*)
            INTO v_remaining_balance_adjustments
            FROM financial_events
            WHERE event_type = 'balance_adjustment';

            IF v_remaining_balance_adjustments > 0 THEN
                RAISE EXCEPTION
                    'Downgrade blocked: % balance_adjustment events exist '
                    'outside this migration',
                    v_remaining_balance_adjustments;
            END IF;
        END $$;
        """
    )
    op.drop_constraint(
        "financial_events_type_check",
        "financial_events",
        type_="check",
    )
    op.create_check_constraint(
        "financial_events_type_check",
        "financial_events",
        _event_type_check_sql(PRE_GOALS_FINANCIAL_EVENT_TYPES),
    )


def upgrade() -> None:
    op.execute(
        "CREATE TYPE goal_transaction_type AS ENUM "
        "('allocation', 'release', 'adjustment', 'legacy_import')"
    )

    op.create_table(
        "goal_transactions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "transaction_type",
            postgresql.ENUM(
                "allocation",
                "release",
                "adjustment",
                "legacy_import",
                name="goal_transaction_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("source_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("source_currency", sa.String(3), nullable=False),
        sa.Column("applied_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("goal_currency", sa.String(3), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("command_fingerprint", sa.Text(), nullable=True),
        sa.Column(
            "legacy_contribution_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_amount > 0",
            name="chk_gtx_source_pos",
        ),
        sa.CheckConstraint(
            "applied_amount > 0",
            name="chk_gtx_applied_pos",
        ),
        sa.CheckConstraint(
            "(transaction_type = 'legacy_import') OR (event_id IS NOT NULL)",
            name="chk_gtx_event_id",
        ),
        sa.CheckConstraint(
            "(command_id IS NULL AND command_fingerprint IS NULL) OR "
            "(command_id IS NOT NULL AND command_fingerprint IS NOT NULL)",
            name="chk_gtx_fingerprint",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["financial_events.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["goal_id"],
            ["goals.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["legacy_contribution_id"],
            ["goal_contributions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("command_id", name="uq_gtx_command_id"),
        sa.UniqueConstraint(
            "legacy_contribution_id",
            name="uq_gtx_legacy_id",
        ),
    )
    op.create_index(
        "ix_goal_transactions_goal_id",
        "goal_transactions",
        ["goal_id"],
    )
    op.create_index(
        "ix_goal_transactions_account_id",
        "goal_transactions",
        ["account_id"],
    )
    op.create_index(
        "ix_goal_transactions_user_id",
        "goal_transactions",
        ["user_id"],
    )
    op.create_index(
        "ix_goal_transactions_created_at",
        "goal_transactions",
        ["created_at"],
    )
    op.create_index(
        "ix_goal_transactions_tx_type",
        "goal_transactions",
        ["transaction_type"],
    )

    _upgrade_financial_events_type_constraint()

    # Abort before writing if a legacy row cannot be reconciled losslessly.
    op.execute(
        """
        DO $$
        DECLARE
            v_bad_count bigint;
            v_duplicate_event_count bigint;
            v_existing_adjustment_count bigint;
        BEGIN
            SELECT COUNT(*)
            INTO v_bad_count
            FROM goal_contributions AS gc
            LEFT JOIN financial_events AS fe ON fe.id = gc.event_id
            LEFT JOIN goals AS g ON g.id = gc.goal_id
            LEFT JOIN accounts AS a ON a.id = gc.account_id
            WHERE gc.account_id IS NULL
               OR gc.goal_id IS NULL
               OR gc.event_id IS NULL
               OR fe.id IS NULL
               OR g.id IS NULL
               OR a.id IS NULL
               OR gc.amount IS NULL
               OR gc.applied_amount IS NULL
               OR gc.amount <= 0
               OR gc.applied_amount <= 0
               OR gc.amount > 999999999999.99
               OR gc.applied_amount > 999999999999.99
               OR gc.amount != round(gc.amount, 2)
               OR gc.applied_amount != round(gc.applied_amount, 2)
               OR a.balance IS NULL
               OR a.currency IS NULL
               OR length(a.currency) != 3
               OR g.currency IS NULL
               OR length(g.currency) != 3
               OR fe.amount IS DISTINCT FROM gc.amount
               OR fe.currency IS DISTINCT FROM a.currency
               OR fe.user_id IS DISTINCT FROM gc.user_id
               OR fe.account_id IS DISTINCT FROM gc.account_id
               OR fe.event_type IS DISTINCT FROM 'goal_contribution'
               OR fe.direction IS DISTINCT FROM 'outflow'
               OR a.user_id IS DISTINCT FROM gc.user_id
               OR g.user_id IS DISTINCT FROM gc.user_id;

            IF v_bad_count > 0 THEN
                RAISE EXCEPTION
                    'Goals V1 precheck failed: % invalid legacy contribution(s)',
                    v_bad_count;
            END IF;

            SELECT COUNT(*)
            INTO v_duplicate_event_count
            FROM (
                SELECT event_id
                FROM goal_contributions
                GROUP BY event_id
                HAVING COUNT(*) > 1
            ) AS duplicate_events;

            IF v_duplicate_event_count > 0 THEN
                RAISE EXCEPTION
                    'Goals V1 precheck failed: % duplicated legacy event reference(s)',
                    v_duplicate_event_count;
            END IF;

            SELECT COUNT(*)
            INTO v_existing_adjustment_count
            FROM financial_events
            WHERE metadata->>'migration_revision' = 'goals_v1_ph1_reserved';

            IF v_existing_adjustment_count > 0 THEN
                RAISE EXCEPTION
                    'Goals V1 precheck failed: reconciliation events already exist';
            END IF;
        END $$;
        """
    )

    # Each old destructive contribution becomes an active allocation. The
    # compensating ledger event restores gross account balance without
    # replacing the original event, whose id remains in immutable metadata.
    op.execute(
        """
        DO $$
        DECLARE
            rec record;
            v_command_id uuid;
            v_event_id uuid;
        BEGIN
            FOR rec IN
                SELECT
                    gc.*,
                    gc.amount AS stored_amount,
                    g.currency AS goal_currency,
                    a.currency AS account_currency
                FROM goal_contributions AS gc
                JOIN goals AS g ON g.id = gc.goal_id
                JOIN accounts AS a ON a.id = gc.account_id
                ORDER BY gc.id
            LOOP
                v_command_id := gen_random_uuid();
                v_event_id := gen_random_uuid();

                INSERT INTO financial_events (
                    id,
                    user_id,
                    account_id,
                    event_type,
                    direction,
                    amount,
                    currency,
                    description,
                    metadata,
                    command_id,
                    occurred_at
                )
                VALUES (
                    v_event_id,
                    rec.user_id,
                    rec.account_id,
                    'balance_adjustment',
                    'inflow',
                    rec.stored_amount,
                    rec.account_currency,
                    'Legacy Goal Conciliation',
                    jsonb_build_object(
                        'legacy_contribution_id', rec.id,
                        'migration_revision', 'goals_v1_ph1_reserved',
                        'original_event_id', rec.event_id
                    ),
                    v_command_id,
                    rec.created_at
                );

                UPDATE accounts
                SET balance = balance + rec.stored_amount
                WHERE id = rec.account_id;

                INSERT INTO goal_transactions (
                    user_id,
                    goal_id,
                    account_id,
                    event_id,
                    transaction_type,
                    source_amount,
                    source_currency,
                    applied_amount,
                    goal_currency,
                    command_id,
                    command_fingerprint,
                    created_at,
                    legacy_contribution_id,
                    metadata_json
                )
                VALUES (
                    rec.user_id,
                    rec.goal_id,
                    rec.account_id,
                    v_event_id,
                    'allocation',
                    rec.stored_amount,
                    rec.account_currency,
                    rec.applied_amount,
                    rec.goal_currency,
                    NULL,
                    NULL,
                    rec.created_at,
                    rec.id,
                    jsonb_build_object(
                        'origin', 'legacy_goal_contribution',
                        'legacy_contribution_id', rec.id,
                        'migration_revision', 'goals_v1_ph1_reserved',
                        'original_event_id', rec.event_id
                    )
                );
            END LOOP;
        END $$;
        """
    )

    # All checks run in the same Alembic transaction; any exception rolls back
    # the table, events, allocations, and balance restoration together.
    op.execute(
        """
        DO $$
        DECLARE
            v_legacy_count bigint;
            v_imported_count bigint;
            v_adjustment_count bigint;
            v_duplicate_count bigint;
            v_bad_event_count bigint;
            v_bad_transaction_count bigint;
            v_bad_goal_count bigint;
        BEGIN
            SELECT COUNT(*)
            INTO v_legacy_count
            FROM goal_contributions;

            SELECT COUNT(*)
            INTO v_imported_count
            FROM goal_transactions
            WHERE legacy_contribution_id IS NOT NULL
              AND metadata_json->>'migration_revision' =
                  'goals_v1_ph1_reserved';

            SELECT COUNT(*)
            INTO v_adjustment_count
            FROM financial_events
            WHERE metadata->>'migration_revision' =
                  'goals_v1_ph1_reserved';

            IF v_legacy_count != v_imported_count
               OR v_legacy_count != v_adjustment_count THEN
                RAISE EXCEPTION
                    'Goals V1 postcheck failed: legacy %, imported %, adjustments %',
                    v_legacy_count,
                    v_imported_count,
                    v_adjustment_count;
            END IF;

            SELECT COUNT(*)
            INTO v_duplicate_count
            FROM (
                SELECT legacy_contribution_id
                FROM goal_transactions
                WHERE legacy_contribution_id IS NOT NULL
                GROUP BY legacy_contribution_id
                HAVING COUNT(*) > 1
            ) AS duplicates;

            IF v_duplicate_count > 0 THEN
                RAISE EXCEPTION
                    'Goals V1 postcheck failed: duplicate legacy allocation';
            END IF;

            SELECT COUNT(*)
            INTO v_bad_event_count
            FROM goal_transactions AS gt
            JOIN goal_contributions AS gc
              ON gc.id = gt.legacy_contribution_id
            JOIN financial_events AS fe ON fe.id = gt.event_id
            WHERE fe.event_type IS DISTINCT FROM 'balance_adjustment'
               OR fe.direction IS DISTINCT FROM 'inflow'
               OR fe.amount IS DISTINCT FROM gt.source_amount
               OR fe.currency IS DISTINCT FROM gt.source_currency
               OR fe.user_id IS DISTINCT FROM gt.user_id
               OR fe.account_id IS DISTINCT FROM gt.account_id
               OR fe.metadata->>'migration_revision'
                    IS DISTINCT FROM 'goals_v1_ph1_reserved'
               OR fe.metadata->>'legacy_contribution_id'
                    IS DISTINCT FROM gt.legacy_contribution_id::text
               OR gt.metadata_json->>'original_event_id'
                    IS DISTINCT FROM gc.event_id::text;

            IF v_bad_event_count > 0 THEN
                RAISE EXCEPTION
                    'Goals V1 postcheck failed: invalid reconciliation event';
            END IF;

            SELECT COUNT(*)
            INTO v_bad_goal_count
            FROM goals AS g
            LEFT JOIN (
                SELECT
                    goal_id,
                    SUM(applied_amount) FILTER (
                        WHERE transaction_type = 'allocation'
                    )
                    - COALESCE(
                        SUM(applied_amount) FILTER (
                            WHERE transaction_type = 'release'
                        ),
                        0
                    ) AS progress
                FROM goal_transactions
                GROUP BY goal_id
            ) AS totals ON totals.goal_id = g.id
            WHERE g.current_amount IS DISTINCT FROM
                  COALESCE(totals.progress, 0);

            IF v_bad_goal_count > 0 THEN
                RAISE EXCEPTION
                    'Goals V1 postcheck failed: goal progress mismatch';
            END IF;

            SELECT COUNT(*)
            INTO v_bad_transaction_count
            FROM goal_transactions AS gt
            JOIN accounts AS a ON a.id = gt.account_id
            JOIN goals AS g ON g.id = gt.goal_id
            WHERE gt.transaction_type IS DISTINCT FROM 'allocation'
               OR gt.source_currency IS DISTINCT FROM a.currency
               OR gt.goal_currency IS DISTINCT FROM g.currency;

            IF v_bad_transaction_count > 0 THEN
                RAISE EXCEPTION
                    'Goals V1 postcheck failed: invalid imported allocation';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Downgrade is allowed only while the table still contains the exact,
    # complete reconciliation set created by this revision.
    op.execute(
        """
        DO $$
        DECLARE
            v_legacy_count bigint;
            v_imported_count bigint;
            v_new_transaction_count bigint;
            v_invalid_event_count bigint;
            v_bad_goal_count bigint;
        BEGIN
            SELECT COUNT(*)
            INTO v_new_transaction_count
            FROM goal_transactions
            WHERE legacy_contribution_id IS NULL
               OR metadata_json->>'migration_revision'
                    IS DISTINCT FROM 'goals_v1_ph1_reserved';

            IF v_new_transaction_count > 0 THEN
                RAISE EXCEPTION
                    'Downgrade blocked: non-migration goal transactions exist';
            END IF;

            SELECT COUNT(*)
            INTO v_legacy_count
            FROM goal_contributions;

            SELECT COUNT(*)
            INTO v_imported_count
            FROM goal_transactions;

            IF v_legacy_count != v_imported_count THEN
                RAISE EXCEPTION
                    'Downgrade blocked: legacy/imported counts differ';
            END IF;

            SELECT COUNT(*)
            INTO v_invalid_event_count
            FROM financial_events AS fe
            LEFT JOIN goal_transactions AS gt ON gt.event_id = fe.id
            WHERE fe.metadata->>'migration_revision' =
                  'goals_v1_ph1_reserved'
              AND (
                  gt.id IS NULL
                  OR fe.event_type IS DISTINCT FROM 'balance_adjustment'
                  OR fe.direction IS DISTINCT FROM 'inflow'
                  OR fe.metadata->>'legacy_contribution_id'
                       IS DISTINCT FROM gt.legacy_contribution_id::text
              );

            IF v_invalid_event_count > 0 THEN
                RAISE EXCEPTION
                    'Downgrade blocked: reconciliation event set is invalid';
            END IF;

            IF (
                SELECT COUNT(*)
                FROM financial_events
                WHERE metadata->>'migration_revision' =
                      'goals_v1_ph1_reserved'
            ) != v_imported_count THEN
                RAISE EXCEPTION
                    'Downgrade blocked: reconciliation event count differs';
            END IF;

            SELECT COUNT(*)
            INTO v_bad_goal_count
            FROM goals AS g
            LEFT JOIN (
                SELECT goal_id, SUM(applied_amount) AS progress
                FROM goal_contributions
                GROUP BY goal_id
            ) AS totals ON totals.goal_id = g.id
            WHERE g.current_amount IS DISTINCT FROM
                  COALESCE(totals.progress, 0);

            IF v_bad_goal_count > 0 THEN
                RAISE EXCEPTION
                    'Downgrade blocked: legacy goal progress is inconsistent';
            END IF;

            UPDATE accounts AS a
            SET balance = a.balance - totals.restored_amount
            FROM (
                SELECT account_id, SUM(source_amount) AS restored_amount
                FROM goal_transactions
                GROUP BY account_id
            ) AS totals
            WHERE a.id = totals.account_id;
        END $$;
        """
    )

    # Remove the referencing rows before their RESTRICT-protected events.
    op.execute("DELETE FROM goal_transactions")
    op.execute(
        """
        DELETE FROM financial_events
        WHERE metadata->>'migration_revision' = 'goals_v1_ph1_reserved'
          AND event_type = 'balance_adjustment'
          AND direction = 'inflow'
        """
    )
    _downgrade_financial_events_type_constraint()
    op.drop_table("goal_transactions")
    op.execute("DROP TYPE goal_transaction_type")
