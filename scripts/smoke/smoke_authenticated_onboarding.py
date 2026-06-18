import asyncio
import os
import sys

import httpx

API_URL = "https://api.nexum.lytrium.tech"
TEST_TOKEN = os.environ.get("NEXUM_PROD_TEST_TOKEN")


async def run_smoke():
    if not TEST_TOKEN:
        print("FAIL: NEXUM_PROD_TEST_TOKEN is not set.")
        sys.exit(1)

    print(f"=== Iniciando Smoke Test Autenticado Onboarding en {API_URL} ===")

    headers = {"Authorization": f"Bearer {TEST_TOKEN}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(base_url=API_URL, headers=headers, timeout=30.0) as client:
        # 1. Bootstrap inicial
        print("\n--- 1. Bootstrap inicial ---")
        payload = {"name": "Smoke Test User", "timezone": "America/Bogota", "currency": "COP"}
        resp = await client.post("/api/v1/users/me/bootstrap", json=payload)
        print(f"Status: {resp.status_code}")
        print(f"Body: {resp.json()}")
        assert resp.status_code in (200, 201), f"Bootstrap failed: {resp.status_code}"

        # 2. Bootstrap idempotente
        print("\n--- 2. Bootstrap idempotente ---")
        resp = await client.post("/api/v1/users/me/bootstrap", json=payload)
        print(f"Status: {resp.status_code}")
        print(f"Body: {resp.json()}")
        assert resp.status_code in (200, 201), f"Idempotent Bootstrap failed: {resp.status_code}"
        data = resp.json()
        assert data.get("created") is False, "Idempotent bootstrap should return created=False"

        # 3. GET /users/me
        print("\n--- 3. GET /users/me ---")
        resp = await client.get("/api/v1/users/me")
        print(f"Status: {resp.status_code}")
        print(f"Body: {resp.json()}")
        assert resp.status_code == 200, f"GET /users/me failed: {resp.status_code}"

        # 4. Intelligence autenticado
        print("\n--- 4. Intelligence autenticado ---")
        resp = await client.get("/api/v1/intelligence/snapshot")
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            print(f"Body keys: {list(resp.json().keys())}")
        else:
            print(f"Body: {resp.text}")
        assert resp.status_code == 200, f"Snapshot failed: {resp.status_code}"

        # 5. Conversación autenticada
        print("\n--- 5. Conversación autenticada ---")
        chat_payload = {"message": "Hola, esto es un smoke test de producción."}
        resp = await client.post("/api/v1/conversations/message", json=chat_payload)
        print(f"Status: {resp.status_code}")
        body = resp.text
        print(f"Body: {body[:150]}...")
        assert resp.status_code in (200, 201), f"Conversations failed: {resp.status_code}"

    print("\n=== Smoke Test completado con éxito ===")


if __name__ == "__main__":
    try:
        asyncio.run(run_smoke())
    except AssertionError as e:
        print(f"\nFAIL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
