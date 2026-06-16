import asyncio
import os
import uuid
from datetime import datetime, timedelta

import httpx

from app.core.config import settings

# Variables de entorno o defaults
API_HOST = os.getenv("API_HOST", "http://localhost:8000")
DEV_USER_ID = str(settings.DEV_USER_ID)


from scripts.cleanup_dev import cleanup  # noqa: E402


async def main():
    await cleanup()
    headers = {"X-User-Id": DEV_USER_ID}
    client = httpx.AsyncClient(timeout=10.0)

    try:
        # 1. Crear Cuenta
        print("INFO: Creando cuenta...")
        res = await client.post(
            f"{API_HOST}/api/v1/accounts",
            json={
                "name": f"Cuenta Principal {uuid.uuid4().hex[:6]}",
                "type": "bank",
                "currency": "COP",
                "balance": "1000",
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text
        account_id = res.json()["id"]

        # 2. Crear Categoria
        res = await client.post(
            f"{API_HOST}/api/v1/categories",
            json={"name": f"General {uuid.uuid4().hex[:6]}", "type": "expense"},
            headers=headers,
        )
        assert res.status_code == 201
        category_id = res.json()["id"]

        # 3. Registrar Income
        print("INFO: Registrando Income...")
        res = await client.post(
            f"{API_HOST}/api/v1/cash/income",
            json={"amount": "500", "account_id": account_id},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert res.status_code == 200, res.text

        # 4. Registrar Expense
        print("INFO: Registrando Expense...")
        res = await client.post(
            f"{API_HOST}/api/v1/cash/expense",
            json={"amount": "200", "account_id": account_id, "category_id": category_id},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert res.status_code == 200, res.text

        # 5. Crear Goal y Contribution
        print("INFO: Creando Goal y Aporte...")
        target_date = (datetime.now() + timedelta(days=300)).strftime("%Y-%m-%d")
        res = await client.post(
            f"{API_HOST}/api/v1/goals",
            json={
                "name": f"Viaje {uuid.uuid4().hex[:6]}",
                "target_amount": "1000",
                "target_date": target_date,
            },
            headers=headers,
        )
        assert res.status_code == 201
        goal_id = res.json()["id"]

        res = await client.post(
            f"{API_HOST}/api/v1/goals/{goal_id}/contributions",
            json={"amount": "100", "account_id": account_id},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert res.status_code == 200

        # 6. Crear Obligacion Mensual
        print("INFO: Creando Obligation...")
        res = await client.post(
            f"{API_HOST}/api/v1/obligations",
            json={
                "name": f"Internet {uuid.uuid4().hex[:6]}",
                "amount": "50",
                "frequency": "monthly",
                "due_day": 15,
            },
            headers=headers,
        )
        assert res.status_code == 201

        # 7. Crear Tarjeta
        print("INFO: Creando Tarjeta...")
        res = await client.post(
            f"{API_HOST}/api/v1/credit/cards",
            json={
                "name": "Visa",
                "bank": "Bank",
                "credit_limit": "2000",
                "cutoff_day": 15,
                "due_day": 5,
            },
            headers=headers,
        )
        assert res.status_code == 201
        card_id = res.json()["id"]

        # 8. Compra con Tarjeta
        print("INFO: Registrando compra con tarjeta...")
        res = await client.post(
            f"{API_HOST}/api/v1/credit/cards/{card_id}/purchases",
            json={
                "amount": "300",
                "installments_total": 3,
                "description": "TV",
                "category_id": category_id,
            },
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert res.status_code == 200, res.text

        # 9. Consultar Inteligencia
        print("INFO: Consultando Intelligence Endpoints...")

        # Snapshot
        res = await client.get(f"{API_HOST}/api/v1/intelligence/snapshot", headers=headers)
        assert res.status_code == 200
        snap = res.json()
        print(f"Snapshot: {snap}")
        assert float(snap["available_real"]) == 200.0
        assert float(snap["safe_money"]) == 50.0
        assert float(snap["free_money"]) == 50.0
        assert float(snap["total_income_current_month"]) == 500.0
        assert float(snap["total_consumption_committed"]) == 500.0
        assert float(snap["wealth_allocation_current_month"]) == 100.0
        assert float(snap["pending_obligations_total"]) == 50.0
        assert float(snap["total_credit_card_debt"]) == 300.0
        assert float(snap["credit_cards_required_payment"]) == 100.0

        # Balance
        res = await client.get(f"{API_HOST}/api/v1/intelligence/balance", headers=headers)
        assert res.status_code == 200
        bal = res.json()
        assert float(bal["total_available_real"]) == 200.0

        # Cashflow
        res = await client.get(f"{API_HOST}/api/v1/intelligence/cashflow", headers=headers)
        assert res.status_code == 200
        cf = res.json()
        print(f"Cashflow: {cf}")
        assert float(cf["income"]) == 500.0
        assert float(cf["expense"]) == 200.0
        assert float(cf["cash_consumption_outflow"]) == 200.0  # expense
        assert float(cf["credit_card_consumption_committed"]) == 300.0  # cc purchase
        assert float(cf["total_consumption_committed"]) == 500.0

        # Debt
        res = await client.get(f"{API_HOST}/api/v1/intelligence/debt", headers=headers)
        assert res.status_code == 200
        debt = res.json()
        print(f"Debt: {debt}")
        assert float(debt["total_estimated_credit_card_debt"]) == 300.0
        assert float(debt["total_monthly_cc_payment"]) == 100.0
        assert len(debt["pending_commitments"]) == 1
        assert debt["pending_commitments"][0]["name"].startswith("Internet")

        # Free Money
        res = await client.get(f"{API_HOST}/api/v1/intelligence/free-money", headers=headers)
        assert res.status_code == 200
        fm = res.json()
        print(f"Free Money: {fm}")
        assert fm["available_real"] == snap["available_real"]

        print("INFO: Todas las pruebas E2E pasaron exitosamente!")

    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
