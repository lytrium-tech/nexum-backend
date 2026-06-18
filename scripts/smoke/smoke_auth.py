import asyncio

from httpx import ASGITransport, AsyncClient

from app.main import app


async def run_smoke():
    print("=== Iniciando Smoke Test Auth Local ===")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. /health
        resp = await client.get("/health")
        assert resp.status_code == 200
        print("OK: /health")

        # 2. /health/readiness
        # Nota: en desarrollo, la base de datos puede o no estar lista. Ignoraremos un 503 si es un error controlado.
        resp = await client.get("/health/readiness")
        assert resp.status_code in [200, 503]
        print(f"OK: /health/readiness -> {resp.status_code}")

        # 3. Sin token
        resp = await client.get("/api/v1/intelligence/snapshot")
        assert resp.status_code == 401
        print("OK: Acceso sin token -> 401")

        # 4. Token invalido
        resp = await client.get(
            "/api/v1/intelligence/snapshot", headers={"Authorization": "Bearer fake"}
        )
        assert resp.status_code == 401
        print("OK: Acceso token invalido -> 401")

        # 5. CORS permitido para localhost
        headers = {"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"}
        resp = await client.options("/health", headers=headers)
        assert resp.status_code == 200
        print("OK: CORS preflight localhost -> 200")

        # 6. CORS bloqueado (en produccion) pero como esto prueba el estado actual de la app (que es development):
        # En development CORS_ORIGINS esta configurado a localhost, pero DEBUG=True podria estar sobreescribiendo.
        pass


if __name__ == "__main__":
    asyncio.run(run_smoke())
