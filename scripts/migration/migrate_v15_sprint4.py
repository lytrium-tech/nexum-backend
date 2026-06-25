import asyncio
import logging

from sqlalchemy import text

from app.core.database import get_engine, init_engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def migrate_sprint4():
    await init_engine()
    engine = get_engine()
    async with engine.begin() as conn:
        logger.info("Migrating credit card statements and updates...")

        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS credit_card_statements (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL,
                    credit_card_id UUID NOT NULL REFERENCES credit_cards(id),
                    billing_period TEXT NOT NULL,
                    billing_period_start DATE,
                    cutoff_date DATE,
                    due_date DATE,
                    previous_balance NUMERIC NOT NULL DEFAULT 0.00,
                    new_purchases NUMERIC NOT NULL DEFAULT 0.00,
                    billed_installments NUMERIC NOT NULL DEFAULT 0.00,
                    fees_total NUMERIC NOT NULL DEFAULT 0.00,
                    interest_total NUMERIC NOT NULL DEFAULT 0.00,
                    payments_received NUMERIC NOT NULL DEFAULT 0.00,
                    statement_balance NUMERIC NOT NULL DEFAULT 0.00,
                    minimum_payment NUMERIC NOT NULL DEFAULT 0.00,
                    status TEXT NOT NULL DEFAULT 'open',
                    frozen_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now(),
                    CONSTRAINT uq_cc_statement_period UNIQUE (credit_card_id, billing_period)
                )
                """
            )
        )

        await conn.execute(text("ALTER TABLE credit_card_installments ADD COLUMN IF NOT EXISTS interest_amount NUMERIC NOT NULL DEFAULT 0.00"))
        await conn.execute(text("ALTER TABLE credit_card_installments ADD COLUMN IF NOT EXISTS total_amount NUMERIC NOT NULL DEFAULT 0.00"))
        await conn.execute(text("ALTER TABLE credit_card_installments ADD COLUMN IF NOT EXISTS scheduled_due_date DATE"))
        await conn.execute(text("ALTER TABLE credit_card_installments ADD COLUMN IF NOT EXISTS revision_id INTEGER NOT NULL DEFAULT 1"))

        await conn.execute(text("ALTER TABLE credit_card_transactions ADD COLUMN IF NOT EXISTS assigned_statement_id UUID REFERENCES credit_card_statements(id)"))
        await conn.execute(text("ALTER TABLE credit_card_transactions ADD COLUMN IF NOT EXISTS statement_assignment_reason TEXT"))

if __name__ == "__main__":
    asyncio.run(migrate_sprint4())
