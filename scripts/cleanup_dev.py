import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("cleanup_dev")


async def cleanup():
    dev_user_id = settings.DEV_USER_ID
    if not dev_user_id:
        logger.error("Falta DEV_USER_ID en la configuración.")
        return

    if not settings.DATABASE_URL:
        logger.error("DATABASE_URL no configurada.")
        return

    engine = create_async_engine(settings.DATABASE_URL)

    logger.info("Iniciando limpieza de datos de prueba...")
    try:
        async with engine.begin() as conn:
            res = await conn.execute(
                text("DELETE FROM public.pending_actions WHERE user_id = :id"),
                {"id": dev_user_id},
            )
            logger.info(f"Pending actions eliminados: {res.rowcount}")

            res = await conn.execute(
                text("DELETE FROM public.ai_runs WHERE user_id = :id"),
                {"id": dev_user_id},
            )
            logger.info(f"AI Runs eliminados: {res.rowcount}")

            res = await conn.execute(
                text("DELETE FROM public.messages WHERE user_id = :id"),
                {"id": dev_user_id},
            )
            logger.info(f"Messages eliminados: {res.rowcount}")

            res = await conn.execute(
                text("DELETE FROM public.credit_card_transactions WHERE user_id = :id"),
                {"id": dev_user_id},
            )
            logger.info(f"Credit Card Transactions eliminados: {res.rowcount}")

            res = await conn.execute(
                text("DELETE FROM public.credit_cards WHERE user_id = :id"), {"id": dev_user_id}
            )
            logger.info(f"Credit Cards eliminadas: {res.rowcount}")

            res = await conn.execute(
                text("DELETE FROM public.obligation_payments WHERE user_id = :id"),
                {"id": dev_user_id},
            )
            logger.info(f"Obligation Payments eliminados: {res.rowcount}")

            res = await conn.execute(
                text("DELETE FROM public.obligations WHERE user_id = :id"), {"id": dev_user_id}
            )
            logger.info(f"Obligations eliminadas: {res.rowcount}")

            res = await conn.execute(
                text("DELETE FROM public.goal_contributions WHERE user_id = :id"),
                {"id": dev_user_id},
            )
            logger.info(f"Goal Contributions eliminados: {res.rowcount}")

            res = await conn.execute(
                text("DELETE FROM public.goals WHERE user_id = :id"), {"id": dev_user_id}
            )
            logger.info(f"Goals eliminadas: {res.rowcount}")

            res = await conn.execute(
                text("DELETE FROM public.financial_events WHERE user_id = :id"), {"id": dev_user_id}
            )
            logger.info(f"Eventos eliminados: {res.rowcount}")

            res = await conn.execute(
                text("DELETE FROM public.categories WHERE user_id = :id"), {"id": dev_user_id}
            )
            logger.info(f"Categorías eliminadas: {res.rowcount}")

            res = await conn.execute(
                text("DELETE FROM public.accounts WHERE user_id = :id"), {"id": dev_user_id}
            )
            logger.info(f"Cuentas eliminadas: {res.rowcount}")



    except Exception as e:
        logger.error(f"Fallo en limpieza: {e}")
    finally:
        await engine.dispose()
        logger.info("Limpieza completada.")


if __name__ == "__main__":
    asyncio.run(cleanup())
