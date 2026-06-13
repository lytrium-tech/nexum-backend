import asyncio
import os
import uuid

import httpx
import asyncpg
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv("API_URL", "http://localhost:8000")

async def get_test_user(conn):
    user_id = await conn.fetchval("SELECT id FROM users WHERE email = 'dev@nexum.local'")
    if not user_id:
        user_id = uuid.uuid4()
        await conn.execute("INSERT INTO users (id, email, password_hash, full_name, is_active) VALUES ($1, $2, $3, $4, $5)", user_id, 'dev@nexum.local', 'hash', 'Dev', True)
        
    account_id = await conn.fetchval("SELECT id FROM accounts WHERE user_id = $1 AND name = 'Nequi'", user_id)
    if not account_id:
        account_id = uuid.uuid4()
        await conn.execute("INSERT INTO accounts (id, user_id, name, type, balance) VALUES ($1, $2, $3, $4, $5)", account_id, user_id, 'Nequi', 'cash', 1000000)

    goal_id = await conn.fetchval("SELECT id FROM goals WHERE user_id = $1 AND name = 'Viaje'", user_id)
    if not goal_id:
        goal_id = uuid.uuid4()
        await conn.execute("INSERT INTO goals (id, user_id, name, target_amount, status) VALUES ($1, $2, $3, $4, $5)", goal_id, user_id, 'Viaje', 10000000, 'active')

    return user_id

async def simulate_message(client: httpx.AsyncClient, token: str, message: str, pending_action_id: str = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": message, "channel": "api"}
    if pending_action_id:
        payload["pending_action_id"] = pending_action_id
    
    resp = await client.post(f"{API_URL}/api/v1/conversations/message", json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()

async def get_auth_token(client: httpx.AsyncClient) -> str:
    resp = await client.post(f"{API_URL}/api/v1/auth/token", data={"username": "dev@nexum.local", "password": "password"})
    if resp.status_code == 200:
        return resp.json()["access_token"]
    
    # If not exists, register
    await client.post(f"{API_URL}/api/v1/auth/register", json={"email": "dev@nexum.local", "password": "password", "full_name": "Dev User"})
    resp = await client.post(f"{API_URL}/api/v1/auth/token", data={"username": "dev@nexum.local", "password": "password"})
    return resp.json()["access_token"]

async def main():
    dsn = os.getenv("DATABASE_URL")
    if dsn and dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    user_id = await get_test_user(conn)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"x-user-id": str(user_id)}
        client.headers.update(headers)
        
        async def simulate_message(c, msg, pa_id=None):
            payload = {"message": msg, "channel": "api"}
            if pa_id:
                payload["pending_action_id"] = pa_id
            resp = await c.post(f"{API_URL}/api/v1/conversations/message", json=payload)
            resp.raise_for_status()
            return resp.json()

        print("\n--- Limpiando acciones previas ---")
        await conn.execute("UPDATE pending_actions SET status = 'cancelled' WHERE user_id = $1", user_id)

        print("\n--- Caso A: Expiración real de pending_actions ---")
        # Insertar acción expirada manualmente
        expired_id = uuid.uuid4()
        await conn.execute("""
            INSERT INTO pending_actions (id, user_id, intent, data, status, expires_at)
            VALUES ($1, $2, 'create_expense', '{}', 'awaiting_clarification', now() - interval '1 day')
        """, expired_id, user_id)
        
        # Enviar mensaje que debería ignorar la expirada
        r = await simulate_message(client, "Gasté 50 mil desde Nequi")
        assert r["status"] == "awaiting_confirmation", f"Debería estar en awaiting_confirmation, obtuvo {r['status']}"
        assert r["pending_action_id"] is not None
        assert r["pending_action_id"] != str(expired_id)
        
        print("Caso A: OK")

        print("\n--- Caso B: Cancelar todo ---")
        # Generamos otra para tener varias abiertas
        await simulate_message(client, "Ingresó 100 mil")
        r = await simulate_message(client, "cancelar")
        assert r["status"] == "cancelled"
        assert "cancelé" in r["response_text"].lower()
        
        # Verificar en DB que estén canceladas
        open_count = await conn.fetchval("SELECT count(*) FROM pending_actions WHERE user_id = $1 AND status IN ('awaiting_confirmation', 'awaiting_clarification') AND expires_at > now()", user_id)
        assert open_count == 0, f"Deberían haber 0 acciones abiertas, hay {open_count}"
        print("Caso B: OK")

        print("\n--- Caso C: Nueva intención completa reemplaza viejas ---")
        print("\n--- Caso C: Nueva intención completa reemplaza viejas ---")
        await conn.execute("UPDATE pending_actions SET status = 'cancelled' WHERE user_id = $1", user_id)
        await conn.execute("""
            INSERT INTO pending_actions (id, user_id, intent, data, status, expires_at)
            VALUES 
            ($1, $2, 'create_expense', '{}', 'awaiting_clarification', now() + interval '1 day'),
            ($3, $2, 'create_income', '{}', 'awaiting_clarification', now() + interval '1 day')
        """, uuid.uuid4(), user_id, uuid.uuid4())
        
        
        # Enviar intención completa
        r = await simulate_message(client, "Gasté 20000 desde Nequi")
        assert r["status"] == "awaiting_confirmation"
        assert "20000" in r["response_text"]
        assert "20000" in r["response_text"]
        assert "nequi" in r["response_text"].lower()
        
        open_count = await conn.fetchval("SELECT count(*) FROM pending_actions WHERE user_id = $1 AND status IN ('awaiting_confirmation', 'awaiting_clarification') AND expires_at > now()", user_id)
        assert open_count == 1, f"Debería haber 1 acción abierta, hay {open_count}"
        print("Caso C: OK")

        print("\n--- Caso D & E: Mensaje ambiguo muestra opciones y Selección ---")
        await conn.execute("UPDATE pending_actions SET status = 'cancelled' WHERE user_id = $1", user_id)
        
        # Insertar 2 acciones manualmente, una en awaiting_confirmation y otra en awaiting_clarification
        id1 = uuid.uuid4()
        id2 = uuid.uuid4()
        await conn.execute("""
            INSERT INTO pending_actions (id, user_id, intent, data, missing_fields, status, expires_at, created_at, updated_at)
            VALUES 
            ($1, $2, 'create_expense', '{"amount": 20000, "account_id": "00000000-0000-0000-0000-000000000000", "_account_name": "Nequi"}', '{}', 'awaiting_confirmation', now() + interval '1 day', now() - interval '2 min', now() - interval '1 min'),
            ($3, $2, 'create_goal_contribution', '{"amount": 500000}', '{"account_id": "¿De qué cuenta salió?"}', 'awaiting_clarification', now() + interval '1 day', now() - interval '1 min', now() - interval '1 min')
        """, id1, user_id, id2)
        
        # Intentar seleccionar opción inválida
        r = await simulate_message(client, "99")
        assert r["status"] == "error"
        assert "1." in r["response_text"]
        assert "2." in r["response_text"]
        assert "cancelar todo" in r["response_text"]
        
        # Seleccionamos la 1 (que era Gasto de 20 mil desde Nequi, que estaba en awaiting_confirmation)
        r_sel = await simulate_message(client, "1")
        # Cuando seleccionas, reanuda el pending action, si estaba en awaiting_confirmation y no envías "si", pedirá confirmación de nuevo
        assert r_sel["status"] == "awaiting_confirmation"
        assert "20000" in r_sel["response_text"]
        
        print("Caso D & E: OK")

        print("\n--- Caso F & G: Clarificación y confirmación normal funcionan ---")
        # Cancelamos las anteriores
        await simulate_message(client, "cancelar")
        
        r1 = await simulate_message(client, "Me pagaron 35000")
        print(f"r1 text: {r1['response_text']}")
        assert r1["status"] == "awaiting_clarification"
        assert "cuenta" in r1["response_text"].lower()
        
        r2 = await simulate_message(client, "Nequi", pa_id=r1["pending_action_id"])
        assert r2["status"] == "awaiting_confirmation"
        assert "35000" in r2["response_text"]
        
        r3 = await simulate_message(client, "sí", pa_id=r2["pending_action_id"])
        assert r3["status"] == "completed"
        assert "éxito" in r3["response_text"].lower()
        print("Caso F & G: OK")
        
        print("\nTodos los smokes de lifecycle pasaron! ")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())