import asyncio
import os
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv("API_URL", "http://localhost:8000")

async def get_test_user_id():
    import asyncpg
    dsn = os.getenv("DATABASE_URL")
    if dsn and dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        user_id = await conn.fetchval("SELECT id FROM users WHERE email = 'dev@nexum.local'")
        if not user_id:
            user_id = "1a7b96ab-4fcc-46fc-94da-46e366d23dac"
            
        nequi_id = await conn.fetchval("SELECT id FROM accounts WHERE user_id = $1 AND name = 'Nequi'", user_id)
        if not nequi_id:
            await conn.execute("INSERT INTO accounts (id, user_id, name, type, balance) VALUES ($1, $2, 'Nequi', 'cash', 1000000)", uuid.uuid4(), user_id)
            
        banco_id = await conn.fetchval("SELECT id FROM accounts WHERE user_id = $1 AND name = 'Bancolombia'", user_id)
        if not banco_id:
            await conn.execute("INSERT INTO accounts (id, user_id, name, type, balance) VALUES ($1, $2, 'Bancolombia', 'cash', 1000000)", uuid.uuid4(), user_id)
        
        # Ensure at least 2 goals exist
        viaje_id = await conn.fetchval("SELECT id FROM goals WHERE user_id = $1 AND name = 'Viaje'", user_id)
        if not viaje_id:
            await conn.execute("INSERT INTO goals (id, user_id, name, target_amount, status) VALUES ($1, $2, 'Viaje', 10000000, 'active')", uuid.uuid4(), user_id)
            
        em_id = await conn.fetchval("SELECT id FROM goals WHERE user_id = $1 AND name = 'Emergencia'", user_id)
        if not em_id:
            await conn.execute("INSERT INTO goals (id, user_id, name, target_amount, status) VALUES ($1, $2, 'Emergencia', 5000000, 'active')", uuid.uuid4(), user_id)
        
        # Clean up any 'Carro' goal created by previous failed runs of this test
        await conn.execute("DELETE FROM goals WHERE user_id = $1 AND name = 'Carro'", user_id)
        
        # Accounts and Goals created safely
        
        return str(user_id)
    finally:
        await conn.close()


async def send_msg(client, text, pending_action_id=None, ext_id=None):
    payload = {
        "message": text,
        "channel": "api",
        "external_message_id": ext_id or str(uuid.uuid4())
    }
    if pending_action_id:
        payload["pending_action_id"] = pending_action_id

    resp = await client.post(f"{API_URL}/api/v1/conversations/message", json=payload)
    resp.raise_for_status()
    return resp.json()

async def run_smokes():
    user_id = await get_test_user_id()
    headers = {"x-user-id": user_id, "Content-Type": "application/json"}
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        # Cancelar cualquier acción previa para empezar en limpio
        await send_msg(client, "cancelar")
        
        # Case 1: Expense
        print("--- Case 1: Expense Clarification ---")
        r1 = await send_msg(client, "Gasté 50000")
        assert r1["status"] == "awaiting_clarification"
        action_id = r1["pending_action_id"]
        r2 = await send_msg(client, "Nequi", pending_action_id=action_id)
        assert r2["status"] == "awaiting_confirmation"
        r3 = await send_msg(client, "sí", pending_action_id=action_id)
        assert r3["status"] == "completed"
        print("[OK] Expense OK")

        # Case 2: Income
        print("\n--- Case 2: Income Clarification ---")
        r1 = await send_msg(client, "Me ingresaron 300000")
        assert r1["status"] == "awaiting_clarification"
        action_id = r1["pending_action_id"]
        r2 = await send_msg(client, "Bancolombia", pending_action_id=action_id)
        assert r2["status"] == "awaiting_confirmation"
        r3 = await send_msg(client, "sí", pending_action_id=action_id)
        assert r3["status"] == "completed"
        print("[OK] Income OK")

        # Case 3: Goal contribution
        print("\n--- Case 3: Goal Contribution Clarification ---")
        r1 = await send_msg(client, "Aporté 100000")
        assert r1["status"] == "awaiting_clarification"
        action_id = r1["pending_action_id"]
        r2 = await send_msg(client, "Viaje", pending_action_id=action_id)
        assert r2["status"] == "awaiting_clarification"  # Todavía falta cuenta
        r3 = await send_msg(client, "Nequi", pending_action_id=action_id)
        assert r3["status"] == "awaiting_confirmation"
        r4 = await send_msg(client, "sí", pending_action_id=action_id)
        assert r4["status"] == "completed"
        print("[OK] Goal contribution OK")

        # Case 4: Intent Change
        print("\n--- Case 4: Intent Change ---")
        r1 = await send_msg(client, "Gasté 50000")
        assert r1["status"] == "awaiting_clarification"
        action_id = r1["pending_action_id"]
        r2 = await send_msg(client, "Mejor crea una meta Carro por 10 millones", pending_action_id=action_id)
        assert r2["status"] == "awaiting_confirmation"
        assert "Cancelé" in r2["response_text"]
        new_action_id = r2["pending_action_id"]
        r3 = await send_msg(client, "sí", pending_action_id=new_action_id)
        assert r3["status"] == "completed"
        print("[OK] Intent Change OK")

        # Case 5 deleted (tested in smoke_pending_actions_lifecycle)

        # Case 6: Idempotency
        print("\n--- Case 6: Idempotency ---")
        await send_msg(client, "cancelar")
        r1 = await send_msg(client, "Gasté 10000 de Nequi")
        print(f"r1 status: {r1['status']} - text: {r1['response_text']}")
        action_id = r1["pending_action_id"]
        r2 = await send_msg(client, "sí", pending_action_id=action_id)
        assert r2["status"] == "completed"
        # Segunda vez
        r3 = await send_msg(client, "sí", pending_action_id=action_id)
        assert r3["status"] == "completed"
        assert "ya fue ejecutada" in r3["response_text"]
        print("[OK] Idempotency OK")

        print("\n Todos los smokes interactivos pasaron!")

if __name__ == "__main__":
    asyncio.run(run_smokes())
