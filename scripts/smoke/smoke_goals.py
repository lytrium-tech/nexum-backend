import asyncio
import logging
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.database import create_async_engine, get_engine, init_engine
from app.users.models import User

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

async def create_test_user(user_id_hex: str) -> uuid.UUID:
    await init_engine()
    async_session = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        user = User(
            email=f"smoke_{user_id_hex}@example.com",
            name="Smoke User",
            timezone="America/Bogota",
            currency="COP",
            status="active",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id



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
                text("SELECT id, source_amount FROM public.goal_transactions WHERE goal_id = :gid"),
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
    finally:
        await engine.dispose()


async def run_smoke():
    logger.info("Iniciando prueba Smoke E2E de Goals Domain...")
   
    # 0. Entorno y protecciones
    base_url = os.environ.get("SMOKE_BASE_URL", "http://localhost:8000")
    if "api.nexum.lytrium.tech" in base_url or "api.lytrium" in base_url:
        logger.error("Ejecución en producción bloqueada por seguridad.")
        sys.exit(1)
       
    user_id_hex = uuid.uuid4().hex[:10]
    user_id = await create_test_user(user_id_hex)
   
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        # 1. Health
        r = await client.get(f"{base_url}/health")
        r.raise_for_status()
        logger.info("FastAPI Health OK.")

        headers = {
            "Authorization": f"Bearer {user_id}",
            "X-Test-Bypass-Auth": "true",
            "X-Test-Email": f"smoke_{user_id_hex}@example.com",
        }

        # 2. Cuenta
        r = await client.post(
            f"{base_url}/api/v1/accounts/",
            json={"name": f"Smoke Goal Account {uuid.uuid4().hex[:6]}", "type": "bank", "currency": "COP"},
            headers=headers,
        )
        r.raise_for_status()
        account_id = r.json()["id"]
        logger.info(f"Cuenta creada: {account_id}")

        # 3. Income de 500
        inc_cmd = str(uuid.uuid4())
        r = await client.post(
            f"{base_url}/api/v1/cash/income",
            json={"account_id": account_id, "amount": "500", "description": "Fondeo para metas"},
            headers={**headers, "Idempotency-Key": inc_cmd},
        )
        r.raise_for_status()

        # 4. Check balance
        r = await client.get(f"{base_url}/api/v1/accounts/{account_id}/availability", headers=headers)
        r.raise_for_status()
        acc_data = r.json()
        logger.info(f"Balance de cuenta inicial: {acc_data['balance']} (Esperado: 500)")
        assert float(acc_data["balance"]) == 500.0

        # 5. Crear meta con fecha a 10 meses
        target_date_10_months = (datetime.now(UTC) + timedelta(days=300)).strftime("%Y-%m-%d")
        r = await client.post(
            f"{base_url}/api/v1/goals",
            json={
                "name": "Meta Smoke Goals",
                "target_amount": "300",
                "currency": "COP",
                "target_date": target_date_10_months,
            },
            headers=headers,
        )
        r.raise_for_status()
        goal_data = r.json()
        goal_id = goal_data["id"]
        logger.info(f"Meta creada: {goal_id}")

        assert float(goal_data["monthly_required"]) > 0

        # 6. Aporte 20
        cmd1 = str(uuid.uuid4())
        r = await client.post(
            f"{base_url}/api/v1/goals/{goal_id}/contributions",
            json={"account_id": account_id, "amount": "20"},
            headers={**headers, "Idempotency-Key": cmd1},
        )
        r.raise_for_status()
        data = r.json()

        logger.info(
            f"Aporte 1 OK: Idempotent {data['idempotent']}, AccountBalance: {data['account_balance']}, GoalCurrent: {data['goal_current_amount']}, Reserved: {data['goal_reserved_amount']}, Available: {data['available_balance']}"
        )
        assert float(data["account_balance"]) == 500.0
        assert float(data["available_balance"]) == 480.0
        assert float(data["goal_reserved_amount"]) == 20.0
        assert float(data["goal_current_amount"]) == 20.0
        assert data["idempotent"] is False

        # 7. Historial de transacciones
        r = await client.get(f"{base_url}/api/v1/goals/{goal_id}/transactions", headers=headers)
        r.raise_for_status()
        tx_data = r.json()
        assert len(tx_data["items"]) == 1
        assert float(tx_data["items"][0]["applied_amount"]) == 20.0

        # 8. Retry aporte 20
        r = await client.post(
            f"{base_url}/api/v1/goals/{goal_id}/contributions",
            json={"account_id": account_id, "amount": "20"},
            headers={**headers, "Idempotency-Key": cmd1},
        )
        r.raise_for_status()
        data = r.json()

        logger.info(
            f"Retry Aporte 1 OK: Idempotent {data['idempotent']}, Available: {data['available_balance']}, GoalCurrent: {data['goal_current_amount']}"
        )
        assert data["idempotent"] is True
        assert float(data["available_balance"]) == 480.0
        assert float(data["goal_current_amount"]) == 20.0

        # 9. Aporte 300 (Excede)
        r = await client.post(
            f"{base_url}/api/v1/goals/{goal_id}/contributions",
            json={"account_id": account_id, "amount": "300"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        logger.info(f"Intento Aporte Excedente: Status Code {r.status_code} (Esperado 409)")
        assert r.status_code == 409

        # 10. Aporte 280 (Completa meta)
        cmd2 = str(uuid.uuid4())
        r = await client.post(
            f"{base_url}/api/v1/goals/{goal_id}/contributions",
            json={"account_id": account_id, "amount": "280"},
            headers={**headers, "Idempotency-Key": cmd2},
        )
        r.raise_for_status()
        data = r.json()
        logger.info(
            f"Aporte Completado OK: Idempotent {data['idempotent']}, Available: {data['available_balance']}, GoalCurrent: {data['goal_current_amount']}"
        )
        assert float(data["available_balance"]) == 200.0
        assert float(data["goal_current_amount"]) == 300.0

        # Verificamos estado final de la meta
        r = await client.get(f"{base_url}/api/v1/goals/{goal_id}", headers=headers)
        r.raise_for_status()
        goal_data = r.json()
        logger.info(
            f"Meta Final Status: {goal_data['status']}, Progress: {goal_data['progress_percentage']}%"
        )
        assert goal_data["status"] == "completed"
        assert float(goal_data["progress_percentage"]) == 100.0

        # 11. Aporte extra (Meta completada)
        r = await client.post(
            f"{base_url}/api/v1/goals/{goal_id}/contributions",
            json={"account_id": account_id, "amount": "10"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        logger.info(f"Intento Aporte Post-Completado: Status Code {r.status_code} (Esperado 409)")
        assert r.status_code == 409

        # 12. Release parcial de 100
        release_cmd1 = str(uuid.uuid4())
        r = await client.post(
            f"{base_url}/api/v1/goals/{goal_id}/releases",
            json={"account_id": account_id, "amount": "100"},
            headers={**headers, "Idempotency-Key": release_cmd1},
        )
        r.raise_for_status()
        data = r.json()
        logger.info(
            f"Release 1 OK: Idempotent {data['idempotent']}, AccountBalance: {data['account_balance']}, GoalCurrent: {data['goal_current_amount']}, Available: {data['available_balance']}"
        )
       
        # Validaciones fuertes requeridas por PRD:
        assert data["transaction_id"] is not None
        assert data["event_id"] is not None
        assert Decimal(data["released_amount"]) == Decimal("100.00")
        assert Decimal(data["account_balance"]) == Decimal("500.00")
        assert Decimal(data["goal_current_amount"]) == Decimal("200.00")
        assert Decimal(data["goal_account_reserved_amount"]) == Decimal("200.00")
        assert Decimal(data["goal_total_reserved_amount"]) == Decimal("200.00")
        assert Decimal(data["account_total_reserved_amount"]) == Decimal("200.00")
        assert Decimal(data["available_balance"]) == Decimal("300.00")
        assert data["goal_status"] == "active"
        assert data["idempotent"] is False

        # 13. Retry Release parcial
        r = await client.post(
            f"{base_url}/api/v1/goals/{goal_id}/releases",
            json={"account_id": account_id, "amount": "100"},
            headers={**headers, "Idempotency-Key": release_cmd1},
        )
        r.raise_for_status()
        retry_data = r.json()
        logger.info(
            f"Retry Release 1 OK: Idempotent {retry_data['idempotent']}, Available: {retry_data['available_balance']}, GoalCurrent: {retry_data['goal_current_amount']}"
        )
        assert retry_data["transaction_id"] == data["transaction_id"]
        assert retry_data["event_id"] == data["event_id"]
        assert Decimal(retry_data["released_amount"]) == Decimal("100.00")
        assert Decimal(retry_data["account_balance"]) == Decimal("500.00")
        assert Decimal(retry_data["goal_current_amount"]) == Decimal("200.00")
        assert Decimal(retry_data["goal_account_reserved_amount"]) == Decimal("200.00")
        assert Decimal(retry_data["goal_total_reserved_amount"]) == Decimal("200.00")
        assert Decimal(retry_data["account_total_reserved_amount"]) == Decimal("200.00")
        assert Decimal(retry_data["available_balance"]) == Decimal("300.00")
        assert retry_data["goal_status"] == "active"
        assert retry_data["idempotent"] is True

        # 14. Status final de meta (reabierta)
        r = await client.get(f"{base_url}/api/v1/goals/{goal_id}", headers=headers)
        r.raise_for_status()
        goal_data = r.json()
        logger.info(
            f"Meta Status tras Release: {goal_data['status']}, Progress: {goal_data['progress_percentage']}%"
        )
        assert goal_data["status"] == "active"
        assert float(goal_data["progress_percentage"]) < 100.0

        # 15. Verificación final DB
        await check_db_state(goal_id, release_cmd1)


if __name__ == "__main__":
    asyncio.run(run_smoke())
