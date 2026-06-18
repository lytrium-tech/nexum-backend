import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("check_db")


async def main() -> None:
    logger.info("Verificando conexión a la base de datos...")

    if not settings.DATABASE_URL:
        logger.error("DATABASE_URL no está configurada en el entorno.")
        return

    # Use settings.DATABASE_URL directly since it is a string
    engine = create_async_engine(settings.DATABASE_URL)

    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            row = result.fetchone()
            if row and row[0] == 1:
                logger.info("¡Conexión exitosa a PostgreSQL asíncrono!")
            else:
                logger.error("La conexión tuvo éxito pero SELECT 1 falló inesperadamente.")
    except Exception as e:
        logger.error(f"Fallo de conexión: {type(e).__name__} (Detalles ofuscados por seguridad).")
        logger.error("Verifica que tu DATABASE_URL en .env sea correcta.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
