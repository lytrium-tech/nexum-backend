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
        logger.info("Migrating credit card early payments and statement charges...")

        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS credit_card_early_payments (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL,
                    credit_card_id UUID NOT NULL REFERENCES credit_cards(id),
                    purchase_transaction_id UUID NOT NULL REFERENCES credit_card_transactions(id),
                    amount NUMERIC NOT NULL,
                    allocation_mode TEXT NOT NULL,
                    event_id UUID,
                    applied_at TIMESTAMPTZ DEFAULT now(),
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now()
                )
                """
            )
        )

        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS credit_card_statement_charges (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    statement_id UUID NOT NULL REFERENCES credit_card_statements(id),
                    charge_type TEXT NOT NULL,
                    amount NUMERIC NOT NULL,
                    description TEXT,
                    reference_id UUID,
                    status TEXT NOT NULL DEFAULT 'pending',
                    paid_amount NUMERIC NOT NULL DEFAULT 0.00,
                    created_at TIMESTAMPTZ DEFAULT now()
                )
                """
            )
        )

        await conn.execute(text("ALTER TABLE credit_card_statement_charges ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'"))
        await conn.execute(text("ALTER TABLE credit_card_statement_charges ADD COLUMN IF NOT EXISTS paid_amount NUMERIC NOT NULL DEFAULT 0.00"))
        
        logger.info("Migration successful.")

if __name__ == "__main__":
    asyncio.run(migrate_sprint5())
