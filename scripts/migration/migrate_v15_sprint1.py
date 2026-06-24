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
        logger.info("Migrating transfers and goal_contributions for FX...")

        # Transfers
        await conn.execute(
            text("ALTER TABLE transfers ADD COLUMN IF NOT EXISTS target_amount NUMERIC(14, 2)")
        )
        await conn.execute(
            text("ALTER TABLE transfers ADD COLUMN IF NOT EXISTS target_currency TEXT")
        )
        await conn.execute(
            text("ALTER TABLE transfers ADD COLUMN IF NOT EXISTS fx_rate NUMERIC(14, 6)")
        )
        await conn.execute(text("ALTER TABLE transfers ADD COLUMN IF NOT EXISTS rate_source TEXT"))
        await conn.execute(
            text("ALTER TABLE transfers ADD COLUMN IF NOT EXISTS rate_timestamp TIMESTAMPTZ")
        )
        await conn.execute(
            text(
                "ALTER TABLE transfers ADD COLUMN IF NOT EXISTS is_estimated BOOLEAN NOT NULL DEFAULT false"
            )
        )

        # Goal Contributions
        await conn.execute(
            text(
                "ALTER TABLE goal_contributions ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'COP'"
            )
        )
        await conn.execute(
            text("ALTER TABLE goal_contributions ADD COLUMN IF NOT EXISTS applied_amount NUMERIC")
        )
        await conn.execute(
            text("ALTER TABLE goal_contributions ADD COLUMN IF NOT EXISTS goal_currency TEXT")
        )
        await conn.execute(
            text("ALTER TABLE goal_contributions ADD COLUMN IF NOT EXISTS fx_rate NUMERIC(14, 6)")
        )
        await conn.execute(
            text("ALTER TABLE goal_contributions ADD COLUMN IF NOT EXISTS rate_source TEXT")
        )
        await conn.execute(
            text(
                "ALTER TABLE goal_contributions ADD COLUMN IF NOT EXISTS rate_timestamp TIMESTAMPTZ"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE goal_contributions ADD COLUMN IF NOT EXISTS is_estimated BOOLEAN NOT NULL DEFAULT false"
            )
        )


if __name__ == "__main__":
    asyncio.run(migrate_sprint1())
