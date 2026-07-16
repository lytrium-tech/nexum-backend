import asyncio
import logging
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_engine, init_engine
from app.users.models import User

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

API_URL = "http://localhost:8000/api/v1"


async def setup_test_user() -> uuid.UUID:
    await init_engine()
    async_session = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        user = User(
            email=f"cat_smoke_{uuid.uuid4().hex[:10]}@example.com",
            name="Categories Smoke User",
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
        "X-Test-Email": f"cat_smoke_{user_id}@example.com",
    }


async def run_smoke():
    async with httpx.AsyncClient() as client:
        user_id = await setup_test_user()
        headers = headers_for(user_id)

        logger.info("1. Validar que expense_uncategorized existe y es global")
        resp = await client.get(f"{API_URL}/categories", headers=headers)
        resp.raise_for_status()
        categories = resp.json()
        sin_clasificar = next(
            (c for c in categories if c.get("stable_key") == "expense_uncategorized"), None
        )
        assert sin_clasificar is not None, "Falta expense_uncategorized"
        assert sin_clasificar["is_global"] is True, "expense_uncategorized debe ser global"

        logger.info("2. Intentar editar categoría global (debería fallar)")
        resp = await client.patch(
            f"{API_URL}/categories/{sin_clasificar['id']}",
            json={"name": "Sin Clasificar Editado"},
            headers=headers,
        )
        assert resp.status_code == 403, f"Esperado 403, obtuvo {resp.status_code}"

        logger.info("3. Crear categoría privada")
        cat_name = f"Transporte_{uuid.uuid4().hex[:4]}"
        resp = await client.post(
            f"{API_URL}/categories", json={"name": cat_name, "type": "expense"}, headers=headers
        )
        resp.raise_for_status()
        cat = resp.json()
        cat_id = cat["id"]
        assert cat["is_active"] is True

        logger.info("4. Verla activa")
        resp = await client.get(f"{API_URL}/categories", headers=headers)
        categories = resp.json()
        assert any(c["id"] == cat_id for c in categories), "Categoría no aparece"

        logger.info("5. Desactivarla via /archive")
        resp = await client.post(f"{API_URL}/categories/{cat_id}/archive", headers=headers)
        resp.raise_for_status()

        logger.info("6. Confirmar que GET normal no la devuelve")
        resp = await client.get(f"{API_URL}/categories", headers=headers)
        categories = resp.json()
        assert not any(c["id"] == cat_id for c in categories), "Categoría inactiva devuelta"

        logger.info("7. Confirmar que include_inactive=true sí la devuelve")
        resp = await client.get(f"{API_URL}/categories?include_inactive=true", headers=headers)
        categories = resp.json()
        assert any(c["id"] == cat_id for c in categories), "include_inactive no funciona"

        logger.info("8. Intentar crear duplicado cuando está inactiva")
        resp = await client.post(
            f"{API_URL}/categories", json={"name": cat_name, "type": "expense"}, headers=headers
        )
        assert resp.status_code == 409, f"Esperado 409 por duplicado, obtuvo {resp.status_code}"

        logger.info("9. Reactivarla via /restore")
        resp = await client.post(f"{API_URL}/categories/{cat_id}/restore", headers=headers)
        resp.raise_for_status()

        logger.info("10. Confirmar que vuelve a GET normal")
        resp = await client.get(f"{API_URL}/categories", headers=headers)
        categories = resp.json()
        assert any(c["id"] == cat_id for c in categories), "Categoría reactivada no devuelta"

        logger.info("11. Desactivarla via PATCH legacy (is_active=False)")
        resp = await client.patch(
            f"{API_URL}/categories/{cat_id}", json={"is_active": False}, headers=headers
        )
        resp.raise_for_status()

        logger.info("12. Validar inactiva de nuevo")
        resp = await client.get(f"{API_URL}/categories", headers=headers)
        categories = resp.json()
        assert not any(c["id"] == cat_id for c in categories), (
            "Categoría activa pese a PATCH legacy"
        )

        logger.info(
            "13. DELETE deprecated (debería archivar silenciosamente si ya lo está o no fallar)"
        )
        resp = await client.delete(f"{API_URL}/categories/{cat_id}", headers=headers)
        resp.raise_for_status()

        logger.info("Todos los asserts de Categories Lifecycle pasaron. OK!")


if __name__ == "__main__":
    asyncio.run(run_smoke())
