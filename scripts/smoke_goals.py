import asyncio
import logging
import uuid
from datetime import UTC

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.database import create_async_engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def check_db_state(goal_id: str, command_id: str):
    logger.info("Consultando estado final en DB...")
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.connect() as conn:
            goal_res = await conn.execute(
                text(
                    "SELECT id, name, target_amount, current_amount, status FROM public.goals WHERE id = :gid"
                ),
                {"gid": goal_id},
            )
            goal_row = goal_res.fetchone()
            if goal_row:
                logger.info(
                    f" -> Meta BD: {goal_row.name}, Target: {goal_row.target_amount}, Current: {goal_row.current_amount}, Status: {goal_row.status}"
                )

            contributions = await conn.execute(
                text("SELECT id, amount FROM public.goal_contributions WHERE goal_id = :gid"),
                {"gid": goal_id},
            )
            logger.info(f" -> Contributions BD count: {len(contributions.fetchall())}")

            cmd = await conn.execute(
                text("SELECT id FROM public.financial_events WHERE command_id = :cmd"),
                {"cmd": command_id},
            )
            logger.info(
                f" -> Filas con command_id {command_id}: {len(cmd.fetchall())} "
                "(debe ser 1 a pesar del retry)"
            )

            txs = await conn.execute(
                text(
                    "SELECT count(*) FROM public.financial_events WHERE event_type = 'goal_contribution'"
                )
            )
            logger.info(
                f" -> Filas en public.financial_events (goal_contributions): {txs.scalar()}"
            )
    finally:
        await engine.dispose()


async def run_smoke():
    logger.info("Iniciando prueba Smoke E2E de Goals Domain...")
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
            json={"name": f"Smoke Goal Cat {uuid.uuid4().hex[:6]}", "type": "goal"},
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
            json={"name": f"Smoke Goal Account {uuid.uuid4().hex[:6]}", "type": "bank"},
            headers=headers,
        )
        r.raise_for_status()
        account_id = r.json()["id"]
        logger.info(f"Cuenta creada: {account_id}")

        # 4. Income de 500
        inc_cmd = str(uuid.uuid4())
        r = await client.post(
            f"{base_url}/api/v1/cash/income",
            json={"account_id": account_id, "amount": "500", "description": "Fondeo para metas"},
            headers={**headers, "Idempotency-Key": inc_cmd},
        )
        r.raise_for_status()

        # 5. Check balance
        r = await client.get(f"{base_url}/api/v1/accounts/{account_id}", headers=headers)
        r.raise_for_status()
        acc_data = r.json()
        logger.info(f"Balance de cuenta inicial: {acc_data['balance']} (Esperado: 500)")
        assert float(acc_data["balance"]) == 500.0

        # 6. Crear meta con fecha a 10 meses
        from datetime import datetime, timedelta

        target_date_10_months = (datetime.now(UTC) + timedelta(days=300)).strftime("%Y-%m-%d")
        r = await client.post(
            f"{base_url}/api/v1/goals",
            json={
                "name": "Meta Smoke Goals",
                "target_amount": "300",
                "target_date": target_date_10_months,
            },
            headers=headers,
        )
        r.raise_for_status()
        goal_data = r.json()
        goal_id = goal_data["id"]
        logger.info(f"Meta creada: {goal_id}")

        # Validar proyección inicial
        assert goal_data["monthly_required"] == "30.00"

        # Check initial snapshot
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                snap_res = await conn.execute(
                    text("SELECT free_money FROM v_financial_snapshot_current_month")
                )
                snap_row = snap_res.fetchone()
                initial_free_money = snap_row[0] if snap_row else 0
                logger.info(f"Initial Free Money: {initial_free_money}")
        finally:
            await engine.dispose()

        # 7. Aporte 20
        cmd1 = str(uuid.uuid4())
        r = await client.post(
            f"{base_url}/api/v1/goals/{goal_id}/contributions",
            json={"account_id": account_id, "amount": "20"},
            headers={**headers, "Idempotency-Key": cmd1},
        )
        r.raise_for_status()
        data = r.json()

        # 8. Verificar aporte 20
        logger.info(
            f"Aporte 1 OK: Status {data['status']}, Balance: {data['balance_after']}, GoalCurrent: {data['goal_current_amount']}, Progress: {data['progress_percentage']}%"
        )
        assert float(data["balance_after"]) == 480.0
        assert float(data["goal_current_amount"]) == 20.0

        # Verify projections via GET
        r = await client.get(f"{base_url}/api/v1/goals/{goal_id}", headers=headers)
        r.raise_for_status()
        goal_data = r.json()
        assert float(goal_data["progress_percentage"]) == 6.67
        assert float(goal_data["monthly_required"]) == 28.00

        # Check snapshot
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                snap_res = await conn.execute(
                    text("SELECT free_money FROM v_financial_snapshot_current_month")
                )
                snap_row = snap_res.fetchone()
                post_free_money = snap_row[0] if snap_row else 0
                logger.info(f"Post-Contribution Free Money: {post_free_money}")
                # free money should remain identical since the contribution was <= monthly required
                assert float(post_free_money) == float(initial_free_money)
        finally:
            await engine.dispose()

        # 9. Retry aporte 20
        r = await client.post(
            f"{base_url}/api/v1/goals/{goal_id}/contributions",
            json={"account_id": account_id, "amount": "20"},
            headers={**headers, "Idempotency-Key": cmd1},
        )
        r.raise_for_status()
        data = r.json()

        # 10. Verificar Retry
        logger.info(
            f"Retry Aporte 1 OK: Status {data['status']}, Balance: {data['balance_after']}, GoalCurrent: {data['goal_current_amount']}"
        )
        assert data["status"] == "idempotent_retry"
        assert float(data["balance_after"]) == 480.0
        assert float(data["goal_current_amount"]) == 20.0

        # 11 y 12. Aporte 300 (Excede)
        r = await client.post(
            f"{base_url}/api/v1/goals/{goal_id}/contributions",
            json={"account_id": account_id, "amount": "300"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        logger.info(f"Intento Aporte Excedente: Status Code {r.status_code} (Esperado 409)")
        assert r.status_code == 409  # GoalAmountExceededError is 409

        # 13 y 14. Aporte 280 (Completa meta)
        cmd2 = str(uuid.uuid4())
        r = await client.post(
            f"{base_url}/api/v1/goals/{goal_id}/contributions",
            json={"account_id": account_id, "amount": "280"},
            headers={**headers, "Idempotency-Key": cmd2},
        )
        r.raise_for_status()
        data = r.json()
        logger.info(
            f"Aporte Completado OK: Status {data['status']}, Balance: {data['balance_after']}, GoalCurrent: {data['goal_current_amount']}, Progress: {data['progress_percentage']}%"
        )
        assert float(data["balance_after"]) == 200.0
        assert float(data["goal_current_amount"]) == 300.0
        # Wait, the backend doesn't know progress_percentage is 100 because it's recalculated in DB. We refresh the goal so it should be returned! Let's assert.

        # We need to GET the goal to verify status = 'completed'
        r = await client.get(f"{base_url}/api/v1/goals/{goal_id}", headers=headers)
        r.raise_for_status()
        goal_data = r.json()
        logger.info(
            f"Meta Final Status: {goal_data['status']}, Progress: {goal_data['progress_percentage']}%"
        )
        assert goal_data["status"] == "completed"
        assert float(goal_data["progress_percentage"]) == 100.0

        # 15 y 16. Aporte extra (Meta completada)
        r = await client.post(
            f"{base_url}/api/v1/goals/{goal_id}/contributions",
            json={"account_id": account_id, "amount": "10"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        logger.info(f"Intento Aporte Post-Completado: Status Code {r.status_code} (Esperado 409)")
        assert r.status_code == 409

        # 17. Verificación final DB
        await check_db_state(goal_id, cmd1)


if __name__ == "__main__":
    asyncio.run(run_smoke())
