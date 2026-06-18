import asyncio
import datetime
import logging
import uuid
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_engine, init_engine
from app.users.models import User

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

API_URL = "http://localhost:8000/api/v1"


async def setup_test_user() -> uuid.UUID:
    await init_engine()
    async_session = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        user = User(
            email=f"goal_smoke_{uuid.uuid4().hex[:10]}@example.com",
            name="Goals Smoke User",
            timezone="America/Bogota",
            currency="COP",
            status="active",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


def headers_for(user_id: uuid.UUID) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {user_id}",
        "X-Test-Bypass-Auth": "true",
        "X-Test-Email": f"goal_smoke_{user_id}@example.com",
    }


async def run_smoke():
    async with httpx.AsyncClient() as client:
        user_id = await setup_test_user()
        headers = headers_for(user_id)

        logger.info("1. Crear cuenta con saldo inicial")
        resp = await client.post(
            f"{API_URL}/accounts",
            json={"name": "Nequi Goals", "type": "wallet", "initial_balance": "500000.00"},
            headers=headers,
        )
        resp.raise_for_status()
        account_id = resp.json()["id"]

        logger.info("2. Crear meta flexible sin fecha")
        resp = await client.post(
            f"{API_URL}/goals",
            json={"name": "Fondo Emergencia", "target_amount": "1000000.00"},
            headers=headers,
        )
        resp.raise_for_status()
        flex_goal = resp.json()
        assert flex_goal["is_flexible"] is True
        assert Decimal(str(flex_goal["required_this_period"])) == Decimal("0.00")
        assert flex_goal["period_status"] == "flexible"

        logger.info("3. Crear meta con fecha y requerido mensual")
        # Target in 5 months
        tz = ZoneInfo("America/Bogota")
        now = datetime.datetime.now(tz).date()
        target_date = now + datetime.timedelta(days=150)

        resp = await client.post(
            f"{API_URL}/goals",
            json={
                "name": "Viaje",
                "target_amount": "300000.00",
                "target_date": target_date.isoformat(),
            },
            headers=headers,
        )
        resp.raise_for_status()
        dated_goal = resp.json()
        goal_id = dated_goal["id"]

        # 300,000 / 5 = 60,000
        req_month = Decimal(str(dated_goal["monthly_required"]))
        assert req_month > 0
        assert Decimal(str(dated_goal["required_this_period"])) == req_month
        assert dated_goal["period_status"] == "pending"

        logger.info("4. Hacer aporte menor al requerido")
        aport = req_month / Decimal("2")
        resp = await client.post(
            f"{API_URL}/goals/{goal_id}/contributions",
            json={"account_id": account_id, "amount": str(aport)},
            headers=headers,
        )
        resp.raise_for_status()

        resp = await client.get(f"{API_URL}/goals/{goal_id}", headers=headers)
        g = resp.json()
        assert Decimal(str(g["contributed_this_period"])) == aport
        assert Decimal(str(g["remaining_required_this_period"])) == req_month - aport
        assert g["period_status"] == "partial"

        logger.info("5. Hacer aporte adicional para cubrir el periodo")
        resp = await client.post(
            f"{API_URL}/goals/{goal_id}/contributions",
            json={"account_id": account_id, "amount": str(aport)},
            headers=headers,
        )
        resp.raise_for_status()

        resp = await client.get(f"{API_URL}/goals/{goal_id}", headers=headers)
        g = resp.json()
        assert Decimal(str(g["remaining_required_this_period"])) == Decimal("0.00")
        assert g["period_status"] == "covered"

        logger.info("6. Hacer aporte extra")
        resp = await client.post(
            f"{API_URL}/goals/{goal_id}/contributions",
            json={"account_id": account_id, "amount": "10000.00"},
            headers=headers,
        )
        resp.raise_for_status()

        resp = await client.get(f"{API_URL}/goals/{goal_id}", headers=headers)
        g = resp.json()
        assert Decimal(str(g["remaining_required_this_period"])) == Decimal("0.00")
        assert g["period_status"] == "covered"

        logger.info(
            "7. Consultar snapshot y verificar goals_required_this_period y committed_outflows"
        )
        resp = await client.get(f"{API_URL}/intelligence/snapshot", headers=headers)
        resp.raise_for_status()
        snap = resp.json()
        truth = snap["truth"]

        # Since the dated goal is fully covered and the other is flexible, goals_required_this_period = 0
        assert Decimal(str(truth["goals_required_this_period"])) == Decimal("0.00")

        # Let's create another goal that is pending to verify sum
        resp = await client.post(
            f"{API_URL}/goals",
            json={
                "name": "Computador",
                "target_amount": "500000.00",
                "target_date": target_date.isoformat(),
            },
            headers=headers,
        )
        new_g = resp.json()
        new_req = Decimal(str(new_g["required_this_period"]))

        resp = await client.get(f"{API_URL}/intelligence/snapshot", headers=headers)
        snap = resp.json()
        truth = snap["truth"]
        assert Decimal(str(truth["goals_required_this_period"])) == new_req
        # committed outflows should include it
        assert Decimal(str(truth["committed_outflows"])) >= new_req

        logger.info("8. Validar que goal_contribution no aparece como expense de consumo")
        resp = await client.get(f"{API_URL}/ledger/summary", headers=headers)
        resp.raise_for_status()
        ledger_sum = resp.json()
        # Since we only transferred to goals, expenses should be 0
        assert Decimal(str(ledger_sum["total_expense"])) == Decimal("0.00")

        logger.info("9. Validar ownership bsico")
        user_id2 = await setup_test_user()
        headers2 = headers_for(user_id2)
        resp = await client.get(f"{API_URL}/goals/{goal_id}", headers=headers2)
        assert resp.status_code == 403

        logger.info("Todos los asserts de Goals Consistency pasaron. OK!")


if __name__ == "__main__":
    asyncio.run(run_smoke())
