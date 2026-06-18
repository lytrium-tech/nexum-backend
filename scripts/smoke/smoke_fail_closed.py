import asyncio
import json
import logging
import os
import uuid

import asyncpg
import httpx
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

API_URL = "http://127.0.0.1:8000/api/v1"
BASE_URL = "http://127.0.0.1:8000"
AUTH_HEADERS = {"Authorization": "Bearer dev_bypass_lytrium_dev"}
if not DB_URL:
    raise ValueError("DATABASE_URL no encontrada en .env")
# asyncpg no soporta postgresql+asyncpg://
DB_URL = DB_URL.replace("postgresql+asyncpg://", "postgresql://")


async def run_smoke():
    logging.info("Iniciando prueba Smoke de Fail-Closed Conversacional...")

    # 1. Health check
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/health")
        if resp.status_code != 200:
            logging.error("API no está arriba.")
            return
        logging.info("FastAPI Health OK.")

        # 2. Insert fake pending action directly in DB
        action_id = str(uuid.uuid4())
        # We need the dev user ID
        user_resp = await client.get(f"{API_URL}/users/me", headers=AUTH_HEADERS)
        if user_resp.status_code != 200:
            logging.error("No se pudo obtener el usuario dev.")
            return
        user_id = user_resp.json()["id"]

        logging.info(f"Insertando pending_action falso con id {action_id}")
        conn = await asyncpg.connect(DB_URL)
        await conn.execute(
            """
            INSERT INTO pending_actions (id, user_id, intent, data, missing_fields, status)
            VALUES ($1, $2, $3, $4, $5, $6)
        """,
            action_id,
            user_id,
            "unsupported_intent",
            json.dumps({"amount": 500}),
            None,
            "awaiting_confirmation",
        )
        await conn.close()

        # 3. Confirm
        confirm_payload = {
            "channel": "api",
            "message": "sí",
            "pending_action_id": action_id,
            "external_message_id": f"smoke-fail-closed-confirm-{uuid.uuid4()}",
        }
        logging.info("Enviando confirmación 'sí' para un intent no soportado...")
        resp_conf = await client.post(
            f"{API_URL}/conversations/message", json=confirm_payload, headers=AUTH_HEADERS
        )
        data_conf = resp_conf.json()
        logging.info(f"Respuesta de confirmación: {data_conf}")

        # 4. Verify error and message
        if data_conf.get(
            "status"
        ) == "error" and "No se registró ningún movimiento" in data_conf.get("response_text", ""):
            logging.info(
                "Fail-Closed OK: La confirmación devolvió error controlado y el mensaje esperado."
            )
        else:
            logging.error(f"Fail-Closed FALLÓ: Estado devuelto {data_conf.get('status')}")
            assert False, "Fail-closed falló"


if __name__ == "__main__":
    asyncio.run(run_smoke())
