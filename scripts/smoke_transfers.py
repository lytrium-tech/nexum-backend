"""
Smoke test manual conversacional para probar Transfers.
"""

import asyncio
from httpx import AsyncClient
from uuid import UUID

import uuid
from app.core.database import init_engine, get_db_session
from app.users.models import User

async def setup_test_users():
    user1_id = uuid.uuid4()
    await init_engine()
    
    async for session in get_db_session():
        user1 = User(
            id=user1_id,
            email=f"test_transfers_{user1_id}@example.com",
        )
        session.add_all([user1])
        await session.commit()
        break
        
    return user1_id

async def main():
    user_a = await setup_test_users()
    headers = {
        "Authorization": f"Bearer {user_a}",
        "X-Test-Bypass-Auth": "true",
        "X-Test-Email": f"test_transfers_{user_a}@example.com"
    }
    
    async with AsyncClient(base_url="http://localhost:8000", timeout=30.0) as client:

        # 1. Ensure we have 2 accounts
        resp = await client.post("/api/v1/accounts", json={"name": "Nequi", "type": "cash", "currency": "COP"}, headers=headers)
        if resp.status_code == 409:
            pass # already exists
        resp = await client.post("/api/v1/accounts", json={"name": "Bancolombia", "type": "cash", "currency": "COP"}, headers=headers)
        if resp.status_code == 409:
            pass # already exists

        # Dar saldo a Nequi
        resp = await client.post("/api/v1/conversations/message", json={"message": "me entraron 500 mil a Nequi de sueldo", "channel": "api"}, headers=headers)
        print("Ingreso Nequi:")
        print(resp.json())
        
        # Confirmar ingreso si es necesario
        pending_id = resp.json().get("pending_action_id")
        if pending_id:
            resp = await client.post("/api/v1/conversations/message", json={"message": "si", "channel": "api"}, headers=headers)
            print("Confirm Ingreso Nequi:")
            print(resp.json())

        # 2. Transferencia
        resp = await client.post("/api/v1/conversations/message", json={"message": "transferí 200 mil de Nequi a Bancolombia", "channel": "api"}, headers=headers)
        print("\nTransfers Intent:")
        print(resp.json())

        pending_id = resp.json().get("pending_action_id")
        if pending_id:
            resp = await client.post("/api/v1/conversations/message", json={"message": "si", "channel": "api"}, headers=headers)
            print("\nConfirm Transfer:")
            print(resp.json())

        # 3. Check Accounts balances
        resp = await client.get("/api/v1/accounts", headers=headers)
        print("\nAccounts:")
        for acc in resp.json():
            if acc["name"] in ["Nequi", "Bancolombia"]:
                print(f"- {acc['name']}: {acc['balance']}")
        
        # 4. Check Ledger Events
        resp = await client.get("/api/v1/ledger/events", headers=headers)
        print("\nLedger Events:")
        for ev in resp.json()["items"][:4]:
            print(f"- {ev['event_type']} | {ev['direction']} | {ev['amount']} | Account: {ev['account']['name']}")

        # 5. Check Summary
        resp = await client.get("/api/v1/ledger/summary", headers=headers)
        print("\nLedger Summary:")
        print(resp.json())

if __name__ == "__main__":
    asyncio.run(main())
