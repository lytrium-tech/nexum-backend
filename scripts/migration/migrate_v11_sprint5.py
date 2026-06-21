import asyncio
import logging

from sqlalchemy import text

from app.core.database import get_engine, init_engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def migrate_sprint5():
    await init_engine()
    engine = get_engine()
    async with engine.begin() as conn:
        logger.info("Migrating credit semantics to Sprint 5...")

        await conn.execute(text("ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS network TEXT"))
        await conn.execute(text("ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS franchise TEXT"))

        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS credit_card_installments (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id),
                credit_card_id UUID NOT NULL REFERENCES credit_cards(id),
                purchase_transaction_id UUID NOT NULL REFERENCES credit_card_transactions(id) ON DELETE CASCADE,
                installment_number INTEGER NOT NULL,
                installments_total INTEGER NOT NULL,
                principal_amount NUMERIC NOT NULL CHECK (principal_amount > 0),
                scheduled_period TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'partial', 'paid')),
                paid_amount NUMERIC NOT NULL DEFAULT 0 CHECK (paid_amount >= 0),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT credit_card_installments_number_check
                    CHECK (installment_number >= 1 AND installment_number <= installments_total),
                CONSTRAINT credit_card_installments_total_check
                    CHECK (installments_total >= 1),
                CONSTRAINT credit_card_installments_paid_check
                    CHECK (paid_amount <= principal_amount),
                CONSTRAINT credit_card_installments_unique_number
                    UNIQUE (purchase_transaction_id, installment_number)
            )
        """)
        )

        await conn.execute(
            text("""
            CREATE INDEX IF NOT EXISTS idx_credit_card_installments_card_period
            ON credit_card_installments (credit_card_id, scheduled_period)
        """)
        )
        await conn.execute(
            text("""
            CREATE INDEX IF NOT EXISTS idx_credit_card_installments_user_card
            ON credit_card_installments (user_id, credit_card_id)
        """)
        )

        logger.info("Backfilling installment schedule for existing purchases...")
        await conn.execute(
            text("""
            INSERT INTO credit_card_installments (
                user_id,
                credit_card_id,
                purchase_transaction_id,
                installment_number,
                installments_total,
                principal_amount,
                scheduled_period,
                status,
                paid_amount
            )
            SELECT
                cct.user_id,
                cct.credit_card_id,
                cct.id,
                gs.installment_number,
                GREATEST(COALESCE(cct.installments_total, 1), 1) AS installments_total,
                CASE
                    WHEN gs.installment_number < GREATEST(COALESCE(cct.installments_total, 1), 1)
                        THEN ROUND(cct.amount / GREATEST(COALESCE(cct.installments_total, 1), 1), 2)
                    ELSE cct.amount - (
                        ROUND(cct.amount / GREATEST(COALESCE(cct.installments_total, 1), 1), 2)
                        * (GREATEST(COALESCE(cct.installments_total, 1), 1) - 1)
                    )
                END AS principal_amount,
                TO_CHAR(
                    (cct.occurred_at AT TIME ZONE 'America/Bogota')
                    + ((gs.installment_number - 1)::text || ' months')::interval,
                    'YYYY-MM'
                ) AS scheduled_period,
                'pending' AS status,
                0 AS paid_amount
            FROM credit_card_transactions cct
            CROSS JOIN LATERAL generate_series(
                1,
                GREATEST(COALESCE(cct.installments_total, 1), 1)
            ) AS gs(installment_number)
            WHERE cct.type = 'purchase'
              AND NOT EXISTS (
                  SELECT 1
                  FROM credit_card_installments cci
                  WHERE cci.purchase_transaction_id = cct.id
              )
        """)
        )

        logger.info("Sprint 5 migration completed.")


if __name__ == "__main__":
    asyncio.run(migrate_sprint5())
