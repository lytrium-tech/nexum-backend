import asyncio
import logging
import os
import uuid
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.database import get_engine, init_engine
from app.users.models import User

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("smoke_financial_snapshot")

BASE_URL = os.environ.get("NEXUM_BASE_URL", "http://localhost:8000").rstrip("/")
API_URL = f"{BASE_URL}/api/v1"


def headers_for(user_id: uuid.UUID) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {user_id}",
        "X-Test-Bypass-Auth": "true",
        "X-Test-Email": f"snapshot_{user_id}@example.com",
    }


async def create_test_user() -> uuid.UUID:
    await init_engine()
    async_session = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        user = User(
            email=f"snapshot_{uuid.uuid4().hex[:10]}@example.com",
            name="Snapshot Smoke User",
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


async def post_json(
    client: httpx.AsyncClient,
    path: str,
    payload: dict,
    headers: dict,
    extra_headers: dict[str, str] | None = None,
) -> dict:
    request_headers = {**headers, **(extra_headers or {})}
    response = await client.post(f"{API_URL}{path}", json=payload, headers=request_headers)
    response.raise_for_status()
    return response.json()


async def main() -> None:
    logger.info("=== SMOKE TEST: C.5 FINANCIAL SNAPSHOT MVP ===")

    if not settings.AUTH_BYPASS_ENABLED:
        raise RuntimeError("Este smoke requiere AUTH_BYPASS_ENABLED=true en desarrollo.")

    user_a = await create_test_user()
    user_b = await create_test_user()
    headers_a = headers_for(user_a)
    headers_b = headers_for(user_b)

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        acc_1 = await post_json(
            client,
            "/accounts",
            {"name": f"Snapshot Main {uuid.uuid4().hex[:6]}", "type": "wallet", "currency": "COP"},
            headers_a,
        )
        acc_2 = await post_json(
            client,
            "/accounts",
            {"name": f"Snapshot Secondary {uuid.uuid4().hex[:6]}", "type": "bank", "currency": "COP"},
            headers_a,
        )

        await post_json(
            client,
            "/cash/income",
            {
                "account_id": acc_1["id"],
                "amount": "3000000.00",
                "currency": "COP",
                "description": "snapshot income",
            },
            headers_a,
            {"Idempotency-Key": str(uuid.uuid4())},
        )
        await post_json(
            client,
            "/cash/expense",
            {
                "account_id": acc_1["id"],
                "amount": "200000.00",
                "currency": "COP",
                "description": "snapshot expense",
            },
            headers_a,
            {"Idempotency-Key": str(uuid.uuid4())},
        )

        goal = await post_json(
            client,
            "/goals",
            {"name": f"Snapshot Goal {uuid.uuid4().hex[:6]}", "target_amount": "5000000.00"},
            headers_a,
        )
        await post_json(
            client,
            f"/goals/{goal['id']}/contributions",
            {"account_id": acc_1["id"], "amount": "400000.00"},
            headers_a,
            {"Idempotency-Key": str(uuid.uuid4())},
        )

        paid_obligation = await post_json(
            client,
            "/obligations",
            {"name": f"Snapshot Paid Obligation {uuid.uuid4().hex[:6]}", "amount": "300000.00", "frequency": "monthly"},
            headers_a,
        )
        await post_json(
            client,
            "/obligations",
            {"name": f"Snapshot Pending Obligation {uuid.uuid4().hex[:6]}", "amount": "900000.00", "frequency": "monthly"},
            headers_a,
        )
        await post_json(
            client,
            f"/obligations/{paid_obligation['id']}/payments",
            {"account_id": acc_1["id"], "amount": "300000.00"},
            headers_a,
            {"Idempotency-Key": str(uuid.uuid4())},
        )

        card = await post_json(
            client,
            "/credit/cards",
            {
                "name": f"Snapshot Card {uuid.uuid4().hex[:6]}",
                "bank": "Smoke Bank",
                "credit_limit": "2000000.00",
                "cutoff_day": 15,
                "due_day": 30,
                "currency": "COP",
            },
            headers_a,
        )
        await post_json(
            client,
            f"/credit/cards/{card['id']}/purchases",
            {"amount": "500000.00", "description": "snapshot credit purchase", "installments_total": 1},
            headers_a,
            {"Idempotency-Key": str(uuid.uuid4())},
        )
        await post_json(
            client,
            f"/credit/cards/{card['id']}/payments",
            {"account_id": acc_1["id"], "amount": "200000.00"},
            headers_a,
            {"Idempotency-Key": str(uuid.uuid4())},
        )

        await post_json(
            client,
            "/transfers",
            {
                "source_account_id": acc_1["id"],
                "destination_account_id": acc_2["id"],
                "amount": "400000.00",
                "currency": "COP",
                "description": "snapshot transfer",
                "command_id": str(uuid.uuid4()),
            },
            headers_a,
        )

        response = await client.get(f"{API_URL}/intelligence/snapshot", headers=headers_a)
        response.raise_for_status()
        snapshot = response.json()
        logger.info("Snapshot user A: %s", snapshot)

        assert snapshot["period"]["timezone"] == "America/Bogota"
        assert as_decimal(snapshot["cash"]["total_balance"]) == Decimal("1900000.00")
        assert snapshot["cash"]["active_accounts_count"] >= 2
        assert as_decimal(snapshot["cashflow"]["income"]) == Decimal("3000000.00")
        assert as_decimal(snapshot["cashflow"]["expenses"]) == Decimal("200000.00")
        assert as_decimal(snapshot["cashflow"]["net_cashflow"]) == Decimal("2800000.00")
        assert as_decimal(snapshot["debt"]["credit_card_total_debt"]) == Decimal("300000.00")
        assert as_decimal(snapshot["goals"]["total_saved"]) >= Decimal("400000.00")
        assert snapshot["obligations"]["pending_count"] >= 1
        assert as_decimal(snapshot["obligations"]["pending_amount"]) >= Decimal("900000.00")
        assert as_decimal(snapshot["transfers"]["monthly_transfer_volume"]) >= Decimal("400000.00")
        assert len(snapshot["recent_activity"]) > 0

        response = await client.get(f"{API_URL}/intelligence/snapshot", headers=headers_b)
        response.raise_for_status()
        snapshot_b = response.json()
        logger.info("Snapshot user B: %s", snapshot_b)

        assert as_decimal(snapshot_b["cash"]["total_balance"]) == Decimal("0.00")
        assert as_decimal(snapshot_b["cashflow"]["income"]) == Decimal("0.00")
        assert as_decimal(snapshot_b["transfers"]["monthly_transfer_volume"]) == Decimal("0.00")

        logger.info("=== C.5 Financial Snapshot Smoke Test: PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
