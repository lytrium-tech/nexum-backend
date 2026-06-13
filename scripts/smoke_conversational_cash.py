import asyncio
import logging
import uuid

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

BASE_URL = "http://localhost:8000"
API_URL = f"{BASE_URL}/api/v1"

AUTH_HEADERS = {"Authorization": "Bearer dev_bypass_lytrium_dev"}

async def run_smoke():
    logging.info("Iniciando prueba Smoke Conversacional Income/Expense...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        cat_name = f"SmokeCat_{uuid.uuid4().hex[:8]}"
        await client.post(f"{API_URL}/categories", json={"name": cat_name, "type": "expense"}, headers=AUTH_HEADERS)
        
        # Create account
        acc_name = f"Cuenta_Smoke_{uuid.uuid4().hex[:8]}"
        await client.post(f"{API_URL}/accounts", json={"name": acc_name, "type": "bank"}, headers=AUTH_HEADERS)
        
        # Add funds to account so the expense test doesn't fail!
        # First we need the account ID.
        accounts_resp = await client.get(f"{API_URL}/accounts", headers=AUTH_HEADERS)
        acc_id = None
        for a in accounts_resp.json():
            if a["name"] == acc_name:
                acc_id = a["id"]
                break
        
        if acc_id:
            await client.patch(f"{API_URL}/accounts/{acc_id}/balance", json={"amount": 1000000.0}, headers=AUTH_HEADERS)

        # 1. Income Conversational
        msg_payload_inc = {
            "channel": "api",
            "message": f"Me pagaron 500000 en mi cuenta {acc_name}",
            "external_message_id": f"smoke-conv-inc-{uuid.uuid4()}"
        }
        resp = await client.post(f"{API_URL}/conversations/message", json=msg_payload_inc, headers=AUTH_HEADERS)
        data = resp.json()
        logging.info(f"Income Request: {data}")
        pending_action_id = data.get("pending_action_id")
        
        # Confirm income
        if pending_action_id:
            confirm_payload = {
                "channel": "api",
                "message": "sí",
                "pending_action_id": pending_action_id,
                "external_message_id": f"smoke-conv-inc-conf-{uuid.uuid4()}"
            }
            resp_conf = await client.post(f"{API_URL}/conversations/message", json=confirm_payload, headers=AUTH_HEADERS)
            logging.info(f"Income Confirm: {resp_conf.json()}")
            
            # Double confirm
            confirm_payload2 = {
                "channel": "api",
                "message": "sí",
                "pending_action_id": pending_action_id,
                "external_message_id": f"smoke-conv-inc-conf2-{uuid.uuid4()}"
            }
            resp_conf2 = await client.post(f"{API_URL}/conversations/message", json=confirm_payload2, headers=AUTH_HEADERS)
            logging.info(f"Income Double Confirm: {resp_conf2.json()}")
        
        # 2. Expense Conversational
        msg_payload_exp = {
            "channel": "api",
            "message": f"Gasté 5000 en {cat_name} pagado con {acc_name}",
            "external_message_id": f"smoke-conv-exp-{uuid.uuid4()}"
        }
        resp = await client.post(f"{API_URL}/conversations/message", json=msg_payload_exp, headers=AUTH_HEADERS)
        data = resp.json()
        logging.info(f"Expense Request: {data}")
        pending_action_id_exp = data.get("pending_action_id")
        
        # Confirm expense
        if pending_action_id_exp:
            confirm_payload = {
                "channel": "api",
                "message": "sí",
                "pending_action_id": pending_action_id_exp,
                "external_message_id": f"smoke-conv-exp-conf-{uuid.uuid4()}"
            }
            resp_conf = await client.post(f"{API_URL}/conversations/message", json=confirm_payload, headers=AUTH_HEADERS)
            logging.info(f"Expense Confirm: {resp_conf.json()}")

if __name__ == "__main__":
    asyncio.run(run_smoke())
