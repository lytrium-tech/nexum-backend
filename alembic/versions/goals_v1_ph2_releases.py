"""Add goal_release event type to financial events check constraint.

Revision ID: goals_v1_ph2_releases
Revises: goals_v1_ph1_reserved
Create Date: 2026-07-23 19:20:00.000000
"""

from alembic import op

revision: str = "goals_v1_ph2_releases"
down_revision = "goals_v1_ph1_reserved"
branch_labels = None
depends_on = None

CANONICAL_EVENT_TYPE_CONSTRAINT = "financial_events_type_check"
LEGACY_EVENT_TYPE_CONSTRAINT = "financial_events_event_type_check"

BASE_FINANCIAL_EVENT_TYPES = (
    "income",
    "expense",
    "credit_card_purchase",
    "credit_card_payment",
    "obligation_payment",
    "goal_contribution",
    "manual_adjustment",
)
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
GOALS_PH2_FINANCIAL_EVENT_TYPES = (
    *POST_GOALS_FINANCIAL_EVENT_TYPES,
    "goal_release",
)
KNOWN_LEGACY_CONSTRAINT_EVENT_TYPE_SETS = (
    BASE_FINANCIAL_EVENT_TYPES,
    LEGACY_FINANCIAL_EVENT_TYPES,
    PRE_GOALS_FINANCIAL_EVENT_TYPES,
    POST_GOALS_FINANCIAL_EVENT_TYPES,
)
DROP_LEGACY_CONSTRAINT_SQL = (
    f"ALTER TABLE public.financial_events DROP CONSTRAINT IF EXISTS {LEGACY_EVENT_TYPE_CONSTRAINT}"
)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _event_type_check_sql(event_types: tuple[str, ...]) -> str:
    values = ", ".join(_sql_literal(value) for value in event_types)
    return f"event_type = ANY (ARRAY[{values}])"


def _sql_text_array(event_types: tuple[str, ...]) -> str:
    values = ", ".join(_sql_literal(value) for value in sorted(event_types))
    return f"ARRAY[{values}]::text[]"


def _constraints_precheck_sql(
    canonical_event_types: tuple[str, ...],
    operation: str,
) -> str:
    canonical_condition = f"v_canonical_types = {_sql_text_array(canonical_event_types)}"
    legacy_conditions = " OR ".join(
        f"v_allowed_types = {_sql_text_array(event_types)}"
        for event_types in KNOWN_LEGACY_CONSTRAINT_EVENT_TYPE_SETS
    )
    return f"""
        DO $$
        DECLARE
            v_canonical_types text[];
            v_canonical_validated boolean;
            v_legacy_exists boolean;
            v_allowed_types text[];
            v_legacy_validated boolean;
        BEGIN
            SELECT
                array_agg(DISTINCT extracted.value[1] ORDER BY extracted.value[1]),
                bool_and(con.convalidated)
            INTO v_canonical_types, v_canonical_validated
            FROM pg_constraint AS con
            CROSS JOIN LATERAL regexp_matches(
                pg_get_constraintdef(con.oid, true),
                '''([^'']+)''',
                'g'
            ) AS extracted(value)
            WHERE con.conrelid = 'public.financial_events'::regclass
              AND con.conname = '{CANONICAL_EVENT_TYPE_CONSTRAINT}'
              AND con.contype = 'c';

            IF v_canonical_types IS NULL THEN
                RAISE EXCEPTION
                    '{CANONICAL_EVENT_TYPE_CONSTRAINT} does not exist '
                    'or has no values';
            END IF;

            IF v_canonical_validated IS DISTINCT FROM true THEN
                RAISE EXCEPTION
                    '{CANONICAL_EVENT_TYPE_CONSTRAINT} is not validated';
            END IF;

            IF NOT ({canonical_condition}) THEN
                RAISE EXCEPTION
                    'Goals V1 Phase 2 {operation} blocked: '
                    'unknown canonical financial event types: %',
                    v_canonical_types;
            END IF;

            SELECT EXISTS (
                SELECT 1
                FROM pg_constraint AS con
                WHERE con.conrelid = 'public.financial_events'::regclass
                  AND con.conname = '{LEGACY_EVENT_TYPE_CONSTRAINT}'
            )
            INTO v_legacy_exists;

            IF v_legacy_exists THEN
                SELECT
                    array_agg(
                        DISTINCT extracted.value[1]
                        ORDER BY extracted.value[1]
                    ),
                    bool_and(con.convalidated)
                INTO v_allowed_types, v_legacy_validated
                FROM pg_constraint AS con
                CROSS JOIN LATERAL regexp_matches(
                    pg_get_constraintdef(con.oid, true),
                    '''([^'']+)''',
                    'g'
                ) AS extracted(value)
                WHERE con.conrelid = 'public.financial_events'::regclass
                  AND con.conname = '{LEGACY_EVENT_TYPE_CONSTRAINT}'
                  AND con.contype = 'c';

                IF v_allowed_types IS NULL THEN
                    RAISE EXCEPTION
                        'Legacy constraint {LEGACY_EVENT_TYPE_CONSTRAINT} '
                        'is not a recognized event type check';
                END IF;

                IF v_legacy_validated IS DISTINCT FROM true THEN
                    RAISE EXCEPTION
                        'Legacy constraint {LEGACY_EVENT_TYPE_CONSTRAINT} '
                        'is not validated';
                END IF;

                IF NOT ({legacy_conditions}) THEN
                    RAISE EXCEPTION
                        'Goals V1 Phase 2 {operation} blocked: '
                        'unknown legacy financial event types: %',
                        v_allowed_types;
                END IF;
            END IF;
        END $$;
    """


def upgrade() -> None:
    # Validate existing constraint has known state before replacing it
    op.execute(
        _constraints_precheck_sql(
            POST_GOALS_FINANCIAL_EVENT_TYPES,
            "upgrade",
        )
    )
    op.execute(DROP_LEGACY_CONSTRAINT_SQL)
    op.drop_constraint(
        CANONICAL_EVENT_TYPE_CONSTRAINT,
        "financial_events",
        type_="check",
    )
    op.create_check_constraint(
        CANONICAL_EVENT_TYPE_CONSTRAINT,
        "financial_events",
        _event_type_check_sql(GOALS_PH2_FINANCIAL_EVENT_TYPES),
    )


def downgrade() -> None:
    # Ensure current state is what we expect
    op.execute(
        _constraints_precheck_sql(
            GOALS_PH2_FINANCIAL_EVENT_TYPES,
            "downgrade",
        )
    )
    # Block downgrade if there are any goal_release events
    op.execute(
        """
        DO $$
        DECLARE
            v_remaining_releases bigint;
        BEGIN
            SELECT COUNT(*)
            INTO v_remaining_releases
            FROM financial_events
            WHERE event_type = 'goal_release';

            IF v_remaining_releases > 0 THEN
                RAISE EXCEPTION
                    'Downgrade blocked: % goal_release events exist',
                    v_remaining_releases;
            END IF;
        END $$;
        """
    )
    op.execute(DROP_LEGACY_CONSTRAINT_SQL)
    op.drop_constraint(
        CANONICAL_EVENT_TYPE_CONSTRAINT,
        "financial_events",
        type_="check",
    )
    op.create_check_constraint(
        CANONICAL_EVENT_TYPE_CONSTRAINT,
        "financial_events",
        _event_type_check_sql(POST_GOALS_FINANCIAL_EVENT_TYPES),
    )
