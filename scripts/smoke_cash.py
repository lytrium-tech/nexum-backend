import asyncio
import logging
import uuid

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("smoke_cash")

API_URL = "http://localhost:8000/api/v1"
HEALTH_URL = "http://localhost:8000/health"


async def check_db_state(account_id: str, command_id: str):
    logger.info("Consultando estado final en DB...")
    if not settings.DATABASE_URL:
        logger.error("DATABASE_URL no configurada.")
        return

    engine = create_async_engine(settings.DATABASE_URL)

    try:
        async with engine.connect() as conn:
            events = await conn.execute(
                text(
                    "SELECT id, event_type, amount, period "
                    "FROM public.financial_events WHERE account_id = :acc"
                ),
                {"acc": account_id},
            )
            event_rows = events.fetchall()
            logger.info(f"Eventos en BD: {len(event_rows)}")
            for row in event_rows:
                logger.info(
                    f" -> Evento: {row.event_type}, Amount: {row.amount}, Period: {row.period}"
                )

            cmd = await conn.execute(
                text("SELECT id FROM public.financial_events WHERE command_id = :cmd"),
                {"cmd": command_id},
            )
            logger.info(
                f"Filas con command_id {command_id}: {len(cmd.fetchall())} "
                "(debe ser 1 a pesar del retry)"
            )

            txs = await conn.execute(
                text(
                    "SELECT count(*) FROM public.financial_events WHERE event_type IN ('income', 'expense')"
                )
            )
            logger.info(f"Filas en public.financial_events (cash): {txs.scalar()}")
    except Exception as e:
        logger.error(f"Error consultando BD: {type(e).__name__}")
    finally:
        await engine.dispose()


async def smoke_test():
    dev_user_id = settings.DEV_USER_ID
    if not dev_user_id:
        logger.error("Falta DEV_USER_ID en la configuración.")
        return

    logger.info("Iniciando prueba Smoke E2E de Cash Domain...")

    async with httpx.AsyncClient() as client:
        r = await client.get(HEALTH_URL)
        if r.status_code != 200:
            logger.error("FastAPI no responde o no está sano.")
            return
        logger.info("FastAPI Health OK.")

        cat_idempotency = str(uuid.uuid4())
        r = await client.post(
            f"{API_URL}/categories",
            json={"name": f"Smoke Category {uuid.uuid4().hex[:6]}", "type": "expense"},
            headers={"Idempotency-Key": cat_idempotency},
        )
        if r.status_code not in (200, 201):
            logger.error(f"Error creando categoría: {r.status_code}")
            return
        category_id = r.json().get("id")
        logger.info(f"Categoría creada: {category_id}")

        acc_idempotency = str(uuid.uuid4())
        r = await client.post(
            f"{API_URL}/accounts",
            json={"name": f"Smoke Account {uuid.uuid4().hex[:6]}", "type": "cash"},
            headers={"Idempotency-Key": acc_idempotency},
        )
        if r.status_code not in (200, 201):
            logger.error(f"Error creando cuenta: {r.status_code}")
            return
        account_id = r.json().get("id")
        logger.info(f"Cuenta creada: {account_id}")

        inc_cmd = str(uuid.uuid4())
        logger.info("Enviando POST /cash/income por 100 COP...")
        r = await client.post(
            f"{API_URL}/cash/income",
            json={"account_id": account_id, "amount": 100, "category_id": None},
            headers={"Idempotency-Key": inc_cmd},
        )
        data = r.json()
        logger.info(f"Income 1 Status: {r.status_code}, Balance: {data.get('balance_after')}")

        logger.info("Re-enviando mismo POST /cash/income (Retry)...")
        r = await client.post(
            f"{API_URL}/cash/income",
            json={"account_id": account_id, "amount": 100, "category_id": None},
            headers={"Idempotency-Key": inc_cmd},
        )
        data = r.json()
        logger.info(
            f"Income Retry Status: {r.status_code}, "
            f"Balance: {data.get('balance_after')}, Result: {data.get('status')}"
        )

        exp_fail_cmd = str(uuid.uuid4())
        logger.info("Enviando POST /cash/expense por 150 COP (sin fondos suficientes)...")
        r = await client.post(
            f"{API_URL}/cash/expense",
            json={"account_id": account_id, "amount": 150, "category_id": category_id},
            headers={"Idempotency-Key": exp_fail_cmd},
        )
        logger.info(f"Expense Fail Status: {r.status_code} (Esperado 409)")

        exp_ok_cmd = str(uuid.uuid4())
        logger.info("Enviando POST /cash/expense por 40 COP...")
        r = await client.post(
            f"{API_URL}/cash/expense",
            json={"account_id": account_id, "amount": 40, "category_id": category_id},
            headers={"Idempotency-Key": exp_ok_cmd},
        )
        data = r.json()
        logger.info(
            f"Expense Ok Status: {r.status_code}, Balance final: {data.get('balance_after')}"
        )

    await check_db_state(account_id, inc_cmd)


if __name__ == "__main__":
    asyncio.run(smoke_test())
