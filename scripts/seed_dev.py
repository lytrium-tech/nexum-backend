import asyncio
import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("seed_dev")


async def seed():
    dev_user_id = settings.DEV_USER_ID
    dev_email = settings.DEV_USER_EMAIL

    if not dev_user_id:
        logger.error("Falta DEV_USER_ID en la configuración.")
        return

    try:
        UUID(dev_user_id)
    except ValueError:
        logger.error("DEV_USER_ID no es un UUID válido.")
        return

    if not settings.DATABASE_URL:
        logger.error("DATABASE_URL no configurada.")
        return

    engine = create_async_engine(settings.DATABASE_URL)

    query = text("""
        INSERT INTO public.users (id, email, name, status)
        VALUES (:id, :email, 'Dev User', 'active')
        ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, status = 'active'
    """)

    try:
        async with engine.begin() as conn:
            await conn.execute(query, {"id": dev_user_id, "email": dev_email})
            logger.info("Usuario de desarrollo asegurado en public.users.")
            
            query_cat = text("""
                INSERT INTO public.categories (id, user_id, name, type, is_active)
                VALUES ('00000000-0000-0000-0000-000000000000'::UUID, NULL, 'sin_clasificar', 'expense', true)
                ON CONFLICT DO NOTHING
            """)
            await conn.execute(query_cat)
            logger.info("Categoria global sin_clasificar asegurada.")
    except Exception as e:
        logger.error(f"Fallo al sembrar datos: {e}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
