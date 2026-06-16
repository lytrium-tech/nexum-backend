import asyncio
import logging
import os
import uuid
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_engine, init_engine
from app.users.models import User

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("smoke_financial_truth")

BASE_URL = os.environ.get("NEXUM_BASE_URL", "http://localhost:8000").rstrip("/")
API_URL = f"{BASE_URL}/api/v1"


def headers_for(user_id: uuid.UUID) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {user_id}",
        "X-Test-Bypass-Auth": "true",
        "X-Test-Email": f"truth_{user_id}@example.com",
    }


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


def as_decimal(value) -> Decimal:
    return Decimal(str(value))


async def run_smoke():
    user_id = await create_test_user()
    headers = headers_for(user_id)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Crear cuenta con saldo inicial
        logger.info("1. Creando cuenta con saldo inicial de 500,000")
        resp = await client.post(
            f"{API_URL}/accounts",
            json={
                "name": "Cuenta Principal",
                "type": "bank",
                "currency": "COP",
                "initial_balance": "500000.00",
            },
            headers=headers,
        )
        if resp.status_code != 201:
            print(resp.json())
        resp.raise_for_status()
        acc1 = resp.json()
        assert as_decimal(acc1["balance"]) == as_decimal("500000.00")
        account_id = acc1["id"]

        # 2. Ajuste manual de balance (+100,000)
        logger.info("2. Ajustando balance con +100,000")
        resp = await client.post(
            f"{API_URL}/accounts/{account_id}/balance-adjustments",
            json={
                "amount": "100000.00",
                "direction": "increase",
                "type": "balance_adjustment",
                "description": "Me encontré dinero",
            },
            headers=headers,
        )
        resp.raise_for_status()
        acc1_updated = resp.json()
        assert as_decimal(acc1_updated["balance"]) == as_decimal("600000.00")

        # 3. Snapshot (verify cashflow and truth)
        resp = await client.get(f"{API_URL}/intelligence/snapshot", headers=headers)
        resp.raise_for_status()
        snap = resp.json()

        # Income y expense no deben haber cambiado por los ajustes
        assert as_decimal(snap["cashflow"]["income"]) == as_decimal("0.00")
        assert as_decimal(snap["cashflow"]["expenses"]) == as_decimal("0.00")
        assert as_decimal(snap["truth"]["available_real"]) == as_decimal("600000.00")
        assert as_decimal(snap["truth"]["free_money"]) == as_decimal("600000.00")

        # 4. Crear obligación de 150,000
        logger.info("3. Creando obligación pendiente de 150,000")
        resp = await client.post(
            f"{API_URL}/obligations",
            json={"name": "Arriendo", "amount": "150000.00", "frequency": "monthly", "due_day": 5},
            headers=headers,
        )
        resp.raise_for_status()

        # 5. Re-check snapshot truth
        resp = await client.get(f"{API_URL}/intelligence/snapshot", headers=headers)
        resp.raise_for_status()
        snap = resp.json()
        assert as_decimal(snap["obligations"]["pending_amount"]) == as_decimal("150000.00")
        assert as_decimal(snap["truth"]["committed_outflows"]) == as_decimal("150000.00")
        assert as_decimal(snap["truth"]["free_money"]) == as_decimal("450000.00")

        # 6. Ask free money via Conversational
        logger.info("4. Preguntando 'cuánto dinero libre tengo' por chat")
        resp = await client.post(
            f"{API_URL}/conversations/message",
            json={"message": "¿Cuánto dinero libre tengo?", "channel": "pwa"},
            headers=headers,
        )
        if resp.status_code != 200 and resp.status_code != 201:
            print(resp.json())
        resp.raise_for_status()
        chat_resp = resp.json()

        logger.info(f"Chat respondió: {chat_resp['response_text']}")
        assert "450000.0" in chat_resp["response_text"], "El chat no dio la cifra correcta"
        assert chat_resp["intent"] == "ask_free_money"

        logger.info("Todos los asserts de Truth pasaron. OK!")


if __name__ == "__main__":
    asyncio.run(run_smoke())
