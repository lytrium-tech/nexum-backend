import asyncio
import logging
import uuid

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.database import create_async_engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def check_db_state(obligation_id: str, command_id: str):
    logger.info("Consultando estado final en DB...")
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.connect() as conn:
            obl_res = await conn.execute(
                text("SELECT id, name, amount, is_active FROM public.obligations WHERE id = :oid"),
                {"oid": obligation_id},
            )
            obl_row = obl_res.fetchone()
            if obl_row:
                logger.info(
                    f" -> Obligación BD: {obl_row.name}, Amount: {obl_row.amount}, IsActive: {obl_row.is_active}"
                )

            payments = await conn.execute(
                text(
                    "SELECT id, amount, period FROM public.obligation_payments WHERE obligation_id = :oid"
                ),
                {"oid": obligation_id},
            )
            logger.info(f" -> Payments BD count: {len(payments.fetchall())}")

            cmd = await conn.execute(
                text("SELECT id FROM public.financial_events WHERE command_id = :cmd"),
                {"cmd": command_id},
            )
            logger.info(
                f" -> Filas con command_id {command_id}: {len(cmd.fetchall())} "
                "(debe ser 1 a pesar del retry)"
            )

            txs = await conn.execute(text("SELECT count(*) FROM public.transactions"))
            logger.info(f" -> Filas en public.transactions: {txs.scalar()} (debe ser 0)")
    finally:
        await engine.dispose()


async def run_smoke():
    logger.info("Iniciando prueba Smoke E2E de Obligations Domain...")
    base_url = "http://localhost:8000"

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Health
        r = await client.get(f"{base_url}/health")
        r.raise_for_status()
        logger.info("FastAPI Health OK.")

        headers = {"Authorization": "Bearer dev_bypass_token"}

        # 2. Categoría
        r = await client.post(
            f"{base_url}/api/v1/categories",
            json={"name": "Smoke Obligation Cat", "type": "expense"},
            headers=headers,
        )
        if r.status_code == 409:
            logger.warning("Categoría ya existe. Omitiendo...")
        else:
            r.raise_for_status()
            logger.info(f"Categoría creada: {r.json()['id']}")

        # 3. Cuenta
        r = await client.post(
            f"{base_url}/api/v1/accounts",
            json={"name": "Smoke Obligation Account", "type": "bank"},
            headers=headers,
        )
        r.raise_for_status()
        account_id = r.json()["id"]
        logger.info(f"Cuenta creada: {account_id}")

        # 4. Income de 500
        inc_cmd = str(uuid.uuid4())
        r = await client.post(
            f"{base_url}/api/v1/cash/income",
            json={"account_id": account_id, "amount": "500", "description": "Fondeo para pago"},
            headers={**headers, "Idempotency-Key": inc_cmd},
        )
        r.raise_for_status()

        # 5. Check balance
        r = await client.get(f"{base_url}/api/v1/accounts/{account_id}", headers=headers)
        r.raise_for_status()
        acc_data = r.json()
        logger.info(f"Balance de cuenta inicial: {acc_data['balance']} (Esperado: 500)")
        assert float(acc_data["balance"]) == 500.0

        # 6. Crear obligación monthly
        r = await client.post(
            f"{base_url}/api/v1/obligations",
            json={"name": "Obligacion Monthly", "amount": "100", "frequency": "monthly"},
            headers=headers,
        )
        r.raise_for_status()
        obl_id = r.json()["id"]
        logger.info(f"Obligación creada: {obl_id}")

        # 7. Pago parcial (Debe fallar con 409)
        r = await client.post(
            f"{base_url}/api/v1/obligations/{obl_id}/payments",
            json={"account_id": account_id, "amount": "50"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        logger.info(f"Intento Pago Parcial: Status Code {r.status_code} (Esperado 409)")
        assert r.status_code == 409

        # 7.5 Check pending obligations BEFORE payment
        engine = create_async_engine(settings.DATABASE_URL)
        async with engine.connect() as conn:
            res = await conn.execute(
                text(
                    "SELECT obligation_id, name, is_pending FROM public.v_pending_obligations_current_month WHERE obligation_id = :oid"
                ),
                {"oid": obl_id},
            )
            row = res.fetchone()
            logger.info(
                f"Vista Pending ANTES del pago -> is_pending: {row.is_pending if row else 'Not found'}"
            )
            assert row is not None and row.is_pending is True

        # 8. Pago exacto 100
        cmd1 = str(uuid.uuid4())
        r = await client.post(
            f"{base_url}/api/v1/obligations/{obl_id}/payments",
            json={"account_id": account_id, "amount": "100"},
            headers={**headers, "Idempotency-Key": cmd1},
        )
        r.raise_for_status()
        data = r.json()
        logger.info(f"Pago OK: Status {data['status']}, Balance: {data['balance_after']}")
        assert float(data["balance_after"]) == 400.0

        # 8.5 Check pending obligations AFTER payment
        async with engine.connect() as conn:
            res = await conn.execute(
                text(
                    "SELECT obligation_id, name, is_pending FROM public.v_pending_obligations_current_month WHERE obligation_id = :oid"
                ),
                {"oid": obl_id},
            )
            row = res.fetchone()
            logger.info(
                f"Vista Pending DESPUÉS del pago -> is_pending: {row.is_pending if row else 'Not found'} (Esperado False)"
            )
            assert row is not None and row.is_pending is False
        await engine.dispose()

        # 9. Retry pago 100
        r = await client.post(
            f"{base_url}/api/v1/obligations/{obl_id}/payments",
            json={"account_id": account_id, "amount": "100"},
            headers={**headers, "Idempotency-Key": cmd1},
        )
        r.raise_for_status()
        data = r.json()
        logger.info(f"Retry Pago OK: Status {data['status']}, Balance: {data['balance_after']}")
        assert data["status"] == "idempotent_retry"
        assert float(data["balance_after"]) == 400.0

        # 10. Check if monthly obligation is still active
        r = await client.get(f"{base_url}/api/v1/obligations/{obl_id}", headers=headers)
        r.raise_for_status()
        obl_data = r.json()
        logger.info(f"Monthly Obligation IsActive: {obl_data['is_active']} (Esperado True)")
        assert obl_data["is_active"] is True

        # 11. Crear obligación once
        r = await client.post(
            f"{base_url}/api/v1/obligations",
            json={"name": "Obligacion Once", "amount": "50", "frequency": "once"},
            headers=headers,
        )
        r.raise_for_status()
        obl_once_id = r.json()["id"]

        # 12. Pagar obligación once
        r = await client.post(
            f"{base_url}/api/v1/obligations/{obl_once_id}/payments",
            json={"account_id": account_id, "amount": "50"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        r.raise_for_status()

        # 13. Check if once obligation is inactive
        r = await client.get(f"{base_url}/api/v1/obligations/{obl_once_id}", headers=headers)
        r.raise_for_status()
        obl_once_data = r.json()
        logger.info(f"Once Obligation IsActive: {obl_once_data['is_active']} (Esperado False)")
        assert obl_once_data["is_active"] is False

        # 14. Intentar pagar obligación inactiva
        r = await client.post(
            f"{base_url}/api/v1/obligations/{obl_once_id}/payments",
            json={"account_id": account_id, "amount": "50"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        logger.info(f"Intento Pago Obligacion Inactiva: Status Code {r.status_code} (Esperado 400)")
        assert r.status_code == 400

        # 15. Verificación final DB
        await check_db_state(obl_id, cmd1)


if __name__ == "__main__":
    asyncio.run(run_smoke())
