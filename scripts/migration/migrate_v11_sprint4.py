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
        logger.info("Migrating obligations to Sprint 4...")

        # Check if payment_mode exists
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='obligations' AND column_name='payment_mode'"
            )
        )
        if not result.scalar():
            logger.info("Adding payment_mode column...")
            await conn.execute(
                text(
                    "ALTER TABLE obligations ADD COLUMN payment_mode TEXT NOT NULL DEFAULT 'fixed_full_payment'"
                )
            )

        # Check if amount is nullable
        result = await conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name='obligations' AND column_name='amount'"
            )
        )
        is_nullable = result.scalar()
        if is_nullable == "NO":
            logger.info("Making amount column nullable...")
            await conn.execute(text("ALTER TABLE obligations ALTER COLUMN amount DROP NOT NULL"))

        logger.info("Dropping unique period constraint if exists...")
        await conn.execute(text("DROP INDEX IF EXISTS obligation_payments_unique_period_idx"))
        await conn.execute(
            text(
                "ALTER TABLE obligation_payments DROP CONSTRAINT IF EXISTS obligation_payments_unique_period_idx"
            )
        )

        logger.info("Sprint 4 migration completed.")


if __name__ == "__main__":
    asyncio.run(migrate_sprint4())
