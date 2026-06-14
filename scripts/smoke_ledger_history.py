import asyncio
import uuid

from httpx import AsyncClient

# Constantes para usuarios locales (ver scripts/seed_dev.py o los que generemos)
BASE_URL = "http://localhost:8000/api/v1"

async def setup_test_users():
    """Crea dos usuarios en la base de datos para pruebas de ownership."""
    import uuid

    from app.core.database import get_db_session, init_engine
    from app.users.models import User

    user1_id = uuid.uuid4()
    user2_id = uuid.uuid4()

    await init_engine()
    
    async for session in get_db_session():
        user1 = User(
            id=user1_id,
            email=f"test_ledger1_{user1_id}@example.com",
        )
        user2 = User(
            id=user2_id,
            email=f"test_ledger2_{user2_id}@example.com",
        )
        session.add_all([user1, user2])
        await session.commit()
        break
        
    return user1_id, user2_id

async def main():
    print("=== INICIANDO SMOKE TEST: LEDGER HISTORY (FASE C.3) ===")
    
    user_a, user_b = await setup_test_users()

    headers_a = {
        "Authorization": f"Bearer {user_a}",
        "X-Test-Bypass-Auth": "true",
        "X-Test-Email": f"test_ledger1_{user_a}@example.com"
    }

    headers_b = {
        "Authorization": f"Bearer {user_b}",
        "X-Test-Bypass-Auth": "true",
        "X-Test-Email": f"test_ledger2_{user_b}@example.com"
    }

    async with AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        # 1. Setup accounts y categories para A
        res = await client.post("/accounts", headers=headers_a, json={
            "name": "Nequi A",
            "type": "wallet",
            "currency": "COP"
        })
        if res.status_code != 201:
            print(f"Error creating account A: {res.status_code} - {res.text}")
        account_a = res.json()["id"]

        res = await client.post("/accounts", headers=headers_b, json={
            "name": "Nequi B",
            "type": "wallet",
            "currency": "COP"
        })

        res = await client.post("/categories", headers=headers_a, json={
            "name": "Comida A",
            "type": "expense",
            "icon": "🍔"
        })
        cat_a = res.json()["id"]

        res = await client.post("/categories", headers=headers_b, json={
            "name": "Comida B",
            "type": "expense",
            "icon": "🍕"
        })

        # 2. Registrar eventos para A usando Cash y Credit
        # Income
        res = await client.post("/cash/income", headers={**headers_a, "Idempotency-Key": str(uuid.uuid4())}, json={
            "account_id": account_a,
            "amount": 100000,
            "currency": "COP",
            "description": "Salario"
        })
        if res.status_code not in (200, 201):
            print("Error income:", res.text)

        # Expense
        res = await client.post("/cash/expense", headers={**headers_a, "Idempotency-Key": str(uuid.uuid4())}, json={
            "account_id": account_a,
            "category_id": cat_a,
            "amount": 25000,
            "currency": "COP",
            "description": "Hamburguesa"
        })
        if res.status_code not in (200, 201):
            print("Error expense:", res.text)
        
        # Credit Card (necesitamos crear una tarjeta)
        res = await client.post("/credit/cards", headers=headers_a, json={
            "name": "Nu A",
            "bank": "Nu",
            "franchise": "mastercard",
            "credit_limit": 500000,
            "currency": "COP",
            "cutoff_day": 15,
            "due_day": 5
        })
        if res.status_code != 201:
            print(f"Error creating CC A: {res.status_code} - {res.text}")
        cc_a = res.json()["id"]

        # Purchase
        res = await client.post(f"/credit/cards/{cc_a}/purchases", headers={**headers_a, "Idempotency-Key": str(uuid.uuid4())}, json={
            "amount": 150000,
            "installments": 1,
            "description": "Zapatos"
        })
        if res.status_code not in (200, 201):
            print("Error purchase:", res.text)

        # Pagamos tarjeta (payment) desde cuenta A
        res = await client.post(f"/credit/cards/{cc_a}/payments", headers={**headers_a, "Idempotency-Key": str(uuid.uuid4())}, json={
            "account_id": account_a,
            "amount": 50000
        })
        if res.status_code not in (200, 201):
            print("Error payment:", res.text)

        print("OK: Datos de prueba creados.")

        # 3. Probar GET /ledger/events
        res = await client.get("/ledger/events", headers=headers_a)
        if res.status_code != 200:
            print(f"Error GET /ledger/events: {res.status_code} - {res.text}")
        assert res.status_code == 200
        data = res.json()
        print("EVENTS:", data["items"])
        assert len(data["items"]) >= 4, "Debería haber al menos 4 eventos (income, expense, cc_purchase, cc_payment)"
        assert data["pagination"]["total"] >= 4

        # Obtener un event_id para detalle
        event_id = data["items"][0]["id"]

        # Probar GET /ledger/events/{event_id}
        res = await client.get(f"/ledger/events/{event_id}", headers=headers_a)
        assert res.status_code == 200
        assert res.json()["id"] == event_id

        # 4. Probar GET /ledger/summary
        res = await client.get("/ledger/summary", headers=headers_a)
        assert res.status_code == 200
        summary = res.json()
        assert float(summary["total_income"]) >= 100000
        assert float(summary["total_expense"]) >= 25000
        assert float(summary["total_credit_card_purchases"]) >= 150000
        assert float(summary["total_credit_card_payments"]) >= 50000
        print("OK: Ledger summary validado.")

        # 5. Probar GET /ledger/timeline
        res = await client.get("/ledger/timeline", headers=headers_a)
        assert res.status_code == 200
        timeline = res.json()
        assert len(timeline["groups"]) >= 1
        assert "date" in timeline["groups"][0]
        assert len(timeline["groups"][0]["items"]) >= 4
        print("OK: Ledger timeline validado.")

        # 6. Probar paginación
        res = await client.get("/ledger/events?limit=2&offset=0", headers=headers_a)
        assert res.status_code == 200
        data = res.json()
        assert len(data["items"]) == 2
        assert data["pagination"]["limit"] == 2
        assert data["pagination"]["offset"] == 0
        print("OK: Paginación validada.")

        # 7. Probar Ownership
        # Usuario B no debe poder ver el event_id de A
        res = await client.get(f"/ledger/events/{event_id}", headers=headers_b)
        assert res.status_code in [403, 404]

        # Usuario B no debe poder filtrar usando account_id de A
        res = await client.get(f"/ledger/events?account_id={account_a}", headers=headers_b)
        assert res.status_code in [403, 404]

        # Usuario B no debe poder filtrar usando category_id de A
        res = await client.get(f"/ledger/events?category_id={cat_a}", headers=headers_b)
        assert res.status_code in [403, 404]

        print("OK: Ownership (acceso cruzado) validado.")

        # 8. Filtros
        # Por account_id de A
        res = await client.get(f"/ledger/events?account_id={account_a}", headers=headers_a)
        assert res.status_code == 200
        data = res.json()
        for item in data["items"]:
            assert item["account"]["id"] == account_a or item["account_id"] == account_a

        # Por category_id de A
        res = await client.get(f"/ledger/events?category_id={cat_a}", headers=headers_a)
        assert res.status_code == 200
        data = res.json()
        for item in data["items"]:
            assert item["category"]["id"] == cat_a or item["category_id"] == cat_a

        print("OK: Filtros (account, category) validados.")

    print("=== SMOKE TEST: PASSED ===")

if __name__ == "__main__":
    asyncio.run(main())
