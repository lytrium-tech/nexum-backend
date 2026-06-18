import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_engine, init_engine
from app.core.utils import normalize_name


async def migrate():
    await init_engine()
    async_session = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        # Add column if not exists
        await session.execute(
            text(
                "ALTER TABLE categories ADD COLUMN IF NOT EXISTS normalized_name TEXT NOT NULL DEFAULT ''"
            )
        )

        # Populate normalized_name
        result = await session.execute(
            text("SELECT id, name FROM categories WHERE normalized_name = ''")
        )
        rows = result.fetchall()
        for row in rows:
            norm = normalize_name(row.name)
            await session.execute(
                text("UPDATE categories SET normalized_name = :norm WHERE id = :id"),
                {"norm": norm, "id": row.id},
            )

        # Add constraint if not exists
        await session.execute(
            text(
                "ALTER TABLE categories DROP CONSTRAINT IF EXISTS uq_category_user_type_normalized_name"
            )
        )
        await session.execute(
            text(
                "ALTER TABLE categories ADD CONSTRAINT uq_category_user_type_normalized_name UNIQUE (user_id, type, normalized_name)"
            )
        )

        await session.commit()


asyncio.run(migrate())
