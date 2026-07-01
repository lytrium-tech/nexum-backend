import asyncio
import logging

from sqlalchemy import text

from app.core.database import get_engine, init_engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def migrate_sprint1():
    await init_engine()
    engine = get_engine()
    async with engine.begin() as conn:
        logger.info("Resetting obligations core for V1.6...")

        logger.info("Dropping old obligation tables...")
        await conn.execute(text("DROP TABLE IF EXISTS obligation_payments CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS obligation_periods CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS obligations CASCADE"))

        logger.info("Creating new obligations table...")
        await conn.execute(
            text(
                """
                CREATE TABLE obligations (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id),
                    name TEXT NOT NULL,
                    description TEXT,
                    category_id UUID REFERENCES categories(id),
                    currency TEXT NOT NULL,
                    type TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    payment_mode TEXT NOT NULL,
                    base_amount NUMERIC(15,2),
                    start_date DATE NOT NULL,
                    first_due_date DATE NOT NULL,
                    due_day INT,
                    due_month INT,
                    interval_count INT NOT NULL DEFAULT 1,
                    end_date DATE,
                    end_count INT,
                    status TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now()
                )
                """
            )
        )

        logger.info("Creating new obligation_periods table...")
        await conn.execute(
            text(
                """
                CREATE TABLE obligation_periods (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    obligation_id UUID NOT NULL REFERENCES obligations(id) ON DELETE CASCADE,
                    period_key TEXT NOT NULL,
                    sequence_number INT NOT NULL DEFAULT 1,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    due_date DATE NOT NULL,
                    amount NUMERIC(15,2),
                    currency TEXT NOT NULL,
                    paid_amount NUMERIC(15,2) NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now(),
                    CONSTRAINT uq_obligation_period_key UNIQUE (obligation_id, period_key),
                    CONSTRAINT chk_paid_amount_positive CHECK (paid_amount >= 0),
                    CONSTRAINT chk_paid_amount_lte_amount CHECK (amount IS NULL OR paid_amount <= amount),
                    CONSTRAINT chk_amount_positive CHECK (amount IS NULL OR amount >= 0)
                )
                """
            )
        )

        logger.info("Creating new obligation_payments table...")
        await conn.execute(
            text(
                """
                CREATE TABLE obligation_payments (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    obligation_id UUID NOT NULL REFERENCES obligations(id) ON DELETE CASCADE,
                    obligation_period_id UUID NOT NULL REFERENCES obligation_periods(id) ON DELETE CASCADE,
                    account_id UUID REFERENCES accounts(id),
                    financial_event_id UUID REFERENCES financial_events(id),
                    amount NUMERIC(15,2) NOT NULL,
                    currency TEXT NOT NULL,
                    source_amount NUMERIC(15,2) NOT NULL,
                    source_currency TEXT NOT NULL,
                    fx_rate NUMERIC(15,6),
                    rate_source TEXT,
                    rate_timestamp TIMESTAMPTZ,
                    is_estimated BOOLEAN,
                    paid_at TIMESTAMPTZ DEFAULT now(),
                    created_at TIMESTAMPTZ DEFAULT now()
                )
                """
            )
        )

        logger.info("Obligations reset migration successful.")

if __name__ == "__main__":
    asyncio.run(migrate_sprint1())
