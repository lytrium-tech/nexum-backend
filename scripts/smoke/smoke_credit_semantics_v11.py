import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_engine, init_engine
from app.users.models import User

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

API_URL = "http://localhost:8000/api/v1"


async def setup_test_user(label: str) -> uuid.UUID:
    await init_engine()
    async_session = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        user = User(
            email=f"credit_semantics_{label}_{uuid.uuid4().hex[:10]}@example.com",
            name=f"Credit Semantics {label}",
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
        "X-Test-Email": f"credit_semantics_{user_id}@example.com",
    }


def as_decimal(value) -> Decimal:
    return Decimal(str(value))


async def mark_purchase_as_billed(transaction_id: str) -> None:
    await init_engine()
    async_session = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        past = datetime.now(UTC) - timedelta(days=60)
        await session.execute(
            text("""
                UPDATE credit_card_transactions
                SET occurred_at = :past
                WHERE id = :transaction_id
            """),
            {"past": past, "transaction_id": transaction_id},
        )
        await session.commit()


async def run_smoke():
    logger.info("=== STARTING SMOKE TEST: CREDIT SEMANTICS V1.1 ===")
    user_a = await setup_test_user("a")
    user_b = await setup_test_user("b")
    headers_a = headers_for(user_a)
    headers_b = headers_for(user_b)

    async with httpx.AsyncClient(timeout=20.0) as client:
        health = await client.get("http://localhost:8000/health")
        health.raise_for_status()

        logger.info("1. Crear cuenta con saldo inicial")
        r = await client.post(
            f"{API_URL}/accounts",
            json={
                "name": f"Credit Cash {uuid.uuid4().hex[:6]}",
                "type": "bank",
                "initial_balance": "1000000.00",
            },
            headers=headers_a,
        )
        r.raise_for_status()
        account_id = r.json()["id"]

        balance_before = await client.get(f"{API_URL}/intelligence/balance", headers=headers_a)
        balance_before.raise_for_status()
        cash_before = as_decimal(balance_before.json()["total_available_real"])

        logger.info("2. Crear tarjeta con límite y metadata")
        r = await client.post(
            f"{API_URL}/credit/cards",
            json={
                "name": f"Semantics {uuid.uuid4().hex[:6]}",
                "bank": "Nexum Bank",
                "credit_limit": "1000000.00",
                "cutoff_day": 15,
                "due_day": 30,
                "management_fee": "31000.00",
                "monthly_interest_rate": "2.00",
                "annual_interest_rate": "18.00",
                "network": "visa",
                "franchise": "gold",
            },
            headers=headers_a,
        )
        r.raise_for_status()
        card = r.json()
        card_id = card["id"]
        assert card["management_fee"] == "31000.00"
        assert card["network"] == "visa"

        logger.info("3. Crear compra a una cuota y validar idempotencia")
        purchase_key = str(uuid.uuid4())
        r = await client.post(
            f"{API_URL}/credit/cards/{card_id}/purchases",
            json={"amount": "100000.00", "installments_total": 1, "description": "Billed purchase"},
            headers={**headers_a, "Idempotency-Key": purchase_key},
        )
        r.raise_for_status()
        one_purchase = r.json()
        assert as_decimal(one_purchase["current_debt"]) == Decimal("100000.00")
        one_tx_id = one_purchase["transaction_id"]

        retry = await client.post(
            f"{API_URL}/credit/cards/{card_id}/purchases",
            json={"amount": "100000.00", "installments_total": 1, "description": "Billed purchase"},
            headers={**headers_a, "Idempotency-Key": purchase_key},
        )
        retry.raise_for_status()
        assert retry.json()["status"] == "idempotent_retry"

        await mark_purchase_as_billed(one_tx_id)

        logger.info("4. Crear compra a varias cuotas")
        r = await client.post(
            f"{API_URL}/credit/cards/{card_id}/purchases",
            json={
                "amount": "300000.00",
                "installments_total": 2,
                "description": "Installments purchase",
            },
            headers={**headers_a, "Idempotency-Key": str(uuid.uuid4())},
        )
        r.raise_for_status()
        assert as_decimal(r.json()["current_debt"]) == Decimal("400000.00")

        logger.info("5. Validar schedule y que no baja cash al comprar")
        r = await client.get(f"{API_URL}/credit/cards/{card_id}/installments", headers=headers_a)
        r.raise_for_status()
        installments = r.json()
        assert len(installments) == 3
        principal_sum = sum(
            (as_decimal(i["principal_amount"]) for i in installments), Decimal("0.00")
        )
        assert principal_sum == Decimal("400000.00")

        balance_after_purchase = await client.get(
            f"{API_URL}/intelligence/balance", headers=headers_a
        )
        balance_after_purchase.raise_for_status()
        assert as_decimal(balance_after_purchase.json()["total_available_real"]) == cash_before

        logger.info("6. Validar debt semantics")
        r = await client.get(f"{API_URL}/credit/cards/{card_id}/status", headers=headers_a)
        r.raise_for_status()
        status = r.json()
        assert as_decimal(status["current_debt"]) == Decimal("400000.00")
        assert as_decimal(status["available_credit"]) == Decimal("600000.00")
        assert as_decimal(status["billed_debt"]) == Decimal("100000.00")
        assert as_decimal(status["unbilled_debt"]) == Decimal("300000.00")
        assert as_decimal(status["payment_required"]) == Decimal("100000.00")
        assert as_decimal(status["next_payment_estimate"]) > Decimal("0.00")
        assert status["statement_balance"] is None
        assert status["data_quality"]["next_payment_estimate"] == "estimated"

        logger.info("7. Validar committed_outflows")
        snap = await client.get(f"{API_URL}/intelligence/snapshot", headers=headers_a)
        snap.raise_for_status()
        truth = snap.json()["truth"]
        assert as_decimal(truth["payment_required"]) == Decimal("100000.00")
        assert as_decimal(truth["committed_outflows"]) >= Decimal("100000.00")

        logger.info("8. Pagar tarjeta y validar cash/deuda")
        payment_key = str(uuid.uuid4())
        r = await client.post(
            f"{API_URL}/credit/cards/{card_id}/payments",
            json={"account_id": account_id, "amount": "100000.00"},
            headers={**headers_a, "Idempotency-Key": payment_key},
        )
        r.raise_for_status()
        payment = r.json()
        assert as_decimal(payment["current_debt"]) == Decimal("300000.00")

        payment_retry = await client.post(
            f"{API_URL}/credit/cards/{card_id}/payments",
            json={"account_id": account_id, "amount": "100000.00"},
            headers={**headers_a, "Idempotency-Key": payment_key},
        )
        payment_retry.raise_for_status()
        assert payment_retry.json()["status"] == "idempotent_retry"

        balance_after_payment = await client.get(
            f"{API_URL}/intelligence/balance", headers=headers_a
        )
        balance_after_payment.raise_for_status()
        assert as_decimal(
            balance_after_payment.json()["total_available_real"]
        ) == cash_before - Decimal("100000.00")

        r = await client.get(f"{API_URL}/credit/cards/{card_id}/status", headers=headers_a)
        r.raise_for_status()
        status_after_payment = r.json()
        assert as_decimal(status_after_payment["current_debt"]) == Decimal("300000.00")
        assert as_decimal(status_after_payment["payment_required"]) == Decimal("0.00")

        logger.info("9. Intentar sobrepago")
        overpay = await client.post(
            f"{API_URL}/credit/cards/{card_id}/payments",
            json={"account_id": account_id, "amount": "400000.00"},
            headers={**headers_a, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert overpay.status_code == 409, overpay.text

        logger.info("10. Validar ledger/history")
        summary = await client.get(f"{API_URL}/ledger/summary", headers=headers_a)
        summary.raise_for_status()
        ledger_summary = summary.json()
        assert as_decimal(ledger_summary["total_credit_card_purchases"]) >= Decimal("400000.00")
        assert as_decimal(ledger_summary["total_credit_card_payments"]) >= Decimal("100000.00")
        assert as_decimal(ledger_summary["total_expense"]) == Decimal("0")

        logger.info("11. Validar ownership")
        forbidden = await client.get(f"{API_URL}/credit/cards/{card_id}/status", headers=headers_b)
        assert forbidden.status_code == 404
        forbidden = await client.post(
            f"{API_URL}/credit/cards/{card_id}/payments",
            json={"account_id": account_id, "amount": "1.00"},
            headers={**headers_b, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert forbidden.status_code in (403, 404)

        logger.info("=== SMOKE TEST: CREDIT SEMANTICS V1.1 PASSED ===")


if __name__ == "__main__":
    asyncio.run(run_smoke())
