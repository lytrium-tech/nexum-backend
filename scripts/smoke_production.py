import asyncio
import os
import sys

import httpx

API_URL = "https://api.nexum.lytrium.tech"
TEST_TOKEN = os.environ.get("NEXUM_PROD_TEST_TOKEN")

async def run_smoke():
    print(f"=== Iniciando Smoke Test Productivo en {API_URL} ===")
    
    async with httpx.AsyncClient(base_url=API_URL, timeout=10.0) as client:
        # 1. /health
        resp = await client.get("/health")
        assert resp.status_code == 200, f"Health check fallo: {resp.status_code}"
        print("OK: /health")

        # 2. /health/readiness
        resp = await client.get("/health/readiness")
        assert resp.status_code == 200, f"Readiness check fallo: {resp.status_code}"
        print("OK: /health/readiness")

        # 3 y 4. /docs y /openapi.json
        resp = await client.get("/docs")
        assert resp.status_code == 404
        print("OK: /docs escondido")

        resp = await client.get("/openapi.json")
        assert resp.status_code == 404
        print("OK: /openapi.json escondido")

        # 5 y 6. Auth
        resp = await client.get("/api/v1/intelligence/snapshot")
        assert resp.status_code == 401
        print("OK: Acceso sin token bloqueado (401)")

        resp = await client.get("/api/v1/intelligence/snapshot", headers={"Authorization": "Bearer fake"})
        assert resp.status_code == 401
        print("OK: Acceso token invalido bloqueado (401)")

        # 7 y 8. CORS
        headers = {
            "Origin": "https://nexum.lytrium.tech",
            "Access-Control-Request-Method": "GET"
        }
        resp = await client.options("/health", headers=headers)
        assert resp.status_code == 200
        print("OK: CORS nexum.lytrium.tech")

        headers_evil = {
            "Origin": "https://evil.com",
            "Access-Control-Request-Method": "GET"
        }
        resp = await client.options("/health", headers=headers_evil)
        # Nginx o FastAPI bloquean el CORS, FastAPI responde 400 Bad Request
        assert resp.status_code == 400
        print("OK: CORS origen no autorizado bloqueado")

        if not TEST_TOKEN:
            print("WARN: NEXUM_PROD_TEST_TOKEN ausente. Se saltan flujos autenticados.")
            return

        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        resp = await client.get("/api/v1/intelligence/balance", headers=headers)
        assert resp.status_code == 200
        print("OK: Balance exitoso")

if __name__ == "__main__":
    try:
        asyncio.run(run_smoke())
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)

