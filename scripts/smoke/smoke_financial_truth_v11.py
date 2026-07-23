import asyncio
import logging
import os
import sys
import uuid
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_engine, init_engine
from app.users.models import User

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("smoke_financial_truth")

BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://localhost:8000").rstrip("/")
API_URL = f"{BASE_URL}/api/v1"

def headers_for(user_id: uuid.UUID) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {user_id}",
        "X-Test-Bypass-Auth": "true",
        "X-Test-Email": f"truth_{user_id}@example.com",
    }

def as_decimal(value) -> Decimal:
    return Decimal(str(value))

async def create_test_user() -> uuid.UUID:
    await init_engine()
    async_session = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        user = User(
            email=f"truth_{uuid.uuid4().hex[:10]}@example.com",
            name="Truth Smoke User",
            timezone="America/Bogota",
            currency="COP",
            status="active",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id

async def run_smoke():
    if "api.nexum.lytrium.tech" in BASE_URL or "api.lytrium" in BASE_URL:
        logger.error("Ejecución en producción bloqueada por seguridad.")
        sys.exit(1)
        
    user_id = await create_test_user()
    headers = headers_for(user_id)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 0. Health
        r = await client.get(f"{BASE_URL}/health")
        r.raise_for_status()

        # 1. Crear cuenta con saldo de prueba
        logger.info("1. Creando cuenta y registrando ingreso de 500,000")
        resp = await client.post(
            f"{API_URL}/accounts",
            json={
                "name": f"Cuenta Principal {uuid.uuid4().hex[:6]}",
                "type": "bank",
                "currency": "COP"
            },
            headers=headers,
        )
        resp.raise_for_status()
        account_id = resp.json()["id"]

        inc_cmd = str(uuid.uuid4())
        resp = await client.post(
            f"{API_URL}/cash/income",
            json={
                "account_id": account_id,
                "amount": "500000.00",
                "description": "Ingreso Inicial"
            },
            headers={**headers, "Idempotency-Key": inc_cmd},
        )
        resp.raise_for_status()
        
        # Consultar disponibilidad
        resp = await client.get(f"{API_URL}/accounts/{account_id}/availability", headers=headers)
        resp.raise_for_status()
        assert as_decimal(resp.json()["balance"]) == as_decimal("500000.00")

        # 2. Ajuste (Ingreso real +100,000)
        logger.info("2. Ajustando balance con ingreso real +100,000")
        inc2_cmd = str(uuid.uuid4())
        resp = await client.post(
            f"{API_URL}/cash/income",
            json={
                "account_id": account_id,
                "amount": "100000.00",
                "description": "Me encontré dinero"
            },
            headers={**headers, "Idempotency-Key": inc2_cmd},
        )
        resp.raise_for_status()
        
        # Check balance
        resp = await client.get(f"{API_URL}/accounts/{account_id}/availability", headers=headers)
        resp.raise_for_status()
        assert as_decimal(resp.json()["balance"]) == as_decimal("600000.00")

        # 3. Snapshot (verify cashflow and truth)
        resp = await client.get(f"{API_URL}/intelligence/snapshot", headers=headers)
        resp.raise_for_status()
        snap = resp.json()

        # income_current_period debe registrar el ingreso (500k + 100k = 600k)
        # O dependiendo si el inicial de 500k fue "opening_balance" o "income" - cash/income siempre es income.
        assert as_decimal(snap["cashflow"]["income_current_period"]) == as_decimal("600000.00")
        assert as_decimal(snap["truth"]["available_real"]) == as_decimal("600000.00")
        assert as_decimal(snap["truth"]["free_money"]) == as_decimal("600000.00")

        # 4. Crear obligación de 150,000
        logger.info("3. Creando obligación pendiente de 150,000")
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        
        resp = await client.post(
            f"{API_URL}/obligations",
            json={
                "name": "Arriendo",
                "base_amount": "150000.00",
                "currency": "COP",
                "type": "indefinite",
                "frequency": "monthly",
                "payment_mode": "fixed",
                "start_date": today,
                "first_due_date": today,
                "due_day": 5
            },
            headers=headers,
        )
        resp.raise_for_status()
        obligation_id = resp.json()["id"]

        logger.info("3.1 Sincronizando periodos de obligación")
        resp = await client.post(f"{API_URL}/obligations/{obligation_id}/sync-periods", headers=headers)
        resp.raise_for_status()

        # 5. Re-check snapshot truth
        resp = await client.get(f"{API_URL}/intelligence/snapshot", headers=headers)
        resp.raise_for_status()
        snap = resp.json()
        logger.info(f"Snapshot obligations: {snap.get('obligations')}")
        logger.info(f"Snapshot truth: {snap.get('truth')}")
        assert as_decimal(snap["obligations"]["pending_amount"]) == as_decimal("300000.00")
        assert as_decimal(snap["truth"]["committed_outflows"]) == as_decimal("300000.00")
        assert as_decimal(snap["truth"]["free_money"]) == as_decimal("300000.00")

        logger.info("Todos los asserts de Truth pasaron. OK!")


if __name__ == "__main__":
    asyncio.run(run_smoke())
