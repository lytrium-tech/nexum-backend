import asyncio
import os
import uuid

import httpx

# Forzar auth bypass si es necesario localmente
os.environ["AUTH_BYPASS_ENABLED"] = "true"
os.environ["APP_ENV"] = "development"

# Asegurarnos de que NO usamos MOCK_GEMINI
os.environ["MOCK_GEMINI"] = "false"

BASE_URL = "http://127.0.0.1:8000"
USER_ID = "00000000-0000-0000-0000-000000000001"
HEADERS = {"X-User-Id": USER_ID}

async def send_message(client, msg, external_id=None, pending_action_id=None):
    payload = {
        "message": msg,
        "channel": "api",
        "external_message_id": external_id,
        "pending_action_id": pending_action_id
    }
    resp = await client.post(f"{BASE_URL}/api/v1/conversations/message", json=payload, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()

async def run_smoke():
    print("--- Inicio de Smoke Test Conversational ---")
    async with httpx.AsyncClient(timeout=30) as client:
        # 1. Crear semilla de Cash
        # Asumiremos que seed_dev.py ha corrido, pero podemos ver si Nequi existe.
        acc_resp = await client.get(f"{BASE_URL}/api/v1/accounts", headers=HEADERS)
        acc_resp.raise_for_status()
        accounts = acc_resp.json()
        if not any(a["name"] == "Nequi" for a in accounts):
            resp = await client.post(f"{BASE_URL}/api/v1/accounts", json={"name": f"Nequi {uuid.uuid4().hex[:6]}", "type": "wallet"}, headers=HEADERS)
            resp.raise_for_status()
        
        cat_resp = await client.get(f"{BASE_URL}/api/v1/categories", headers=HEADERS)
        cat_resp.raise_for_status()
        categories = cat_resp.json()
        if not any(c["name"] == "Comida" for c in categories):
            resp = await client.post(f"{BASE_URL}/api/v1/categories", json={"name": f"Comida {uuid.uuid4().hex[:6]}", "type": "expense"}, headers=HEADERS)
            resp.raise_for_status()

        # Darle fondos a Nequi
        acc_resp = await client.get(f"{BASE_URL}/api/v1/accounts", headers=HEADERS)
        accounts = acc_resp.json()
        nequi_id = next(a["id"] for a in accounts if a["name"] == "Nequi")
        await client.post(f"{BASE_URL}/api/v1/cash/income", json={
            "amount": 1000,
            "account_id": nequi_id
        }, headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())})

        # 3. Preguntar balance
        print("Preguntando: ¿Cuánto dinero tengo?")
        resp1 = await send_message(client, "¿Cuánto dinero tengo?")
        print(f"Respuesta: {resp1['response_text']}")
        assert resp1["intent"] == "ask_balance"
        assert resp1["status"] == "completed"
        
        # 4. Gasto con Nequi
        print("Enviando: Gasté 20 en comida desde Nequi")
        ext_id = str(uuid.uuid4())
        resp3 = await send_message(client, "Gasté 20 en comida desde Nequi", external_id=ext_id)
        print(f"Respuesta: {resp3['response_text']}")
        assert resp3["intent"] == "create_expense"
        assert resp3["status"] == "awaiting_confirmation"
        pending_action_id = resp3["pending_action_id"]
        
        # Verify it didn't write to DB yet
        balance_resp = await send_message(client, "¿Cuánto dinero tengo?")
        print(f"Balance medio: {balance_resp['response_text']}")
        
        # 5. Reintentar external_id
        print("Reintentando mismo external_id...")
        resp4 = await send_message(client, "Gasté 20 en comida desde Nequi", external_id=ext_id)
        assert resp4["response_text"] == resp3["response_text"]
        assert resp4["pending_action_id"] == pending_action_id
        
        # 6. Confirmar
        print("Enviando confirmación: Sí")
        resp5 = await send_message(client, "Sí", pending_action_id=pending_action_id)
        print(f"Respuesta: {resp5['response_text']}")
        assert resp5["status"] == "completed"

        # 7. Preguntar dinero libre
        print("Preguntando: ¿Cuánto dinero libre tengo?")
        resp2 = await send_message(client, "¿Cuánto dinero libre tengo?")
        print(f"Respuesta: {resp2['response_text']}")
        assert resp2["intent"] == "ask_free_money"
        assert resp2["status"] == "completed"

    print("--- Smoke Test Exitoso ---")

if __name__ == "__main__":
    asyncio.run(run_smoke())
