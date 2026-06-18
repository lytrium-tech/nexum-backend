import asyncio
import logging
import uuid

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

BASE_URL = "http://localhost:8000"
API_URL = f"{BASE_URL}/api/v1"
AUTH_HEADERS = {"Authorization": "Bearer dev_bypass_lytrium_dev"}


async def conversational_flow(client: httpx.AsyncClient, message: str, label: str):
    logging.info(f"--- {label} ---")
    msg_payload = {
        "channel": "api",
        "message": message,
        "external_message_id": f"smoke-conv-{uuid.uuid4().hex[:8]}",
    }
    resp = await client.post(
        f"{API_URL}/conversations/message", json=msg_payload, headers=AUTH_HEADERS
    )
    data = resp.json()
    logging.info(f"Req: {data}")
    action_id = data.get("pending_action_id")

    if action_id:
        # Confirm
        conf_payload = {
            "channel": "api",
            "message": "sí",
            "pending_action_id": action_id,
            "external_message_id": f"smoke-conv-conf-{uuid.uuid4().hex[:8]}",
        }
        resp_conf = await client.post(
            f"{API_URL}/conversations/message", json=conf_payload, headers=AUTH_HEADERS
        )
        logging.info(f"Conf: {resp_conf.json()}")

        # Double confirm for idempotency
        conf2_payload = {
            "channel": "api",
            "message": "sí",
            "pending_action_id": action_id,
            "external_message_id": f"smoke-conv-conf2-{uuid.uuid4().hex[:8]}",
        }
        resp_conf2 = await client.post(
            f"{API_URL}/conversations/message", json=conf2_payload, headers=AUTH_HEADERS
        )
        logging.info(f"Conf2 (Idempotency): {resp_conf2.json()}")
        return action_id
    return None


async def run_smoke():
    async with httpx.AsyncClient() as client:
        # Prep
        cat_name = f"Cat_{uuid.uuid4().hex[:6]}"
        acc_name = f"Acc_{uuid.uuid4().hex[:6]}"
        await client.post(
            f"{API_URL}/categories",
            json={"name": cat_name, "type": "expense"},
            headers=AUTH_HEADERS,
        )
        await client.post(
            f"{API_URL}/accounts", json={"name": acc_name, "type": "bank"}, headers=AUTH_HEADERS
        )

        # Fund the account
        msg_payload_inc = {
            "channel": "api",
            "message": f"Me ingresaron 5000000 en {acc_name}",
            "external_message_id": f"smoke-conv-fund-{uuid.uuid4().hex[:8]}",
        }
        fund_resp = await client.post(
            f"{API_URL}/conversations/message", json=msg_payload_inc, headers=AUTH_HEADERS
        )
        act_id = fund_resp.json().get("pending_action_id")
        if act_id:
            await client.post(
                f"{API_URL}/conversations/message",
                json={
                    "channel": "api",
                    "message": "sí",
                    "pending_action_id": act_id,
                    "external_message_id": str(uuid.uuid4()),
                },
                headers=AUTH_HEADERS,
            )

        # GOALS
        goal_name = f"Viaje_{uuid.uuid4().hex[:6]}"
        await conversational_flow(
            client,
            f"Quiero crear una meta llamada {goal_name} por 2000000 para 2026-12-31",
            "Create Goal",
        )

        # Check Goal
        goals_resp = await client.get(f"{API_URL}/goals", headers=AUTH_HEADERS)
        goals = goals_resp.json()
        goal = next((g for g in goals if g["name"] == goal_name), None)
        assert goal, "Goal not created"
        logging.info(f"Verified goal: {goal['name']} (Target: {goal['target_amount']})")

        # Contribute to Goal
        await conversational_flow(
            client,
            f"Aporta 100000 a la meta {goal_name} desde la cuenta {acc_name}",
            "Goal Contribution",
        )

        # Verify Goal
        goal_verify = await client.get(f"{API_URL}/goals/{goal['id']}", headers=AUTH_HEADERS)
        assert float(goal_verify.json()["current_amount"]) == 100000.0, (
            "Goal current amount incorrect"
        )

        # OBLIGATIONS
        obl_name = f"Arriendo_{uuid.uuid4().hex[:6]}"
        await conversational_flow(
            client, f"Crea una obligación mensual de {obl_name} por 900000", "Create Obligation"
        )

        obls_resp = await client.get(f"{API_URL}/obligations", headers=AUTH_HEADERS)
        obls = obls_resp.json()
        obl = next((o for o in obls if o["name"] == obl_name), None)
        assert obl, "Obligation not created"
        logging.info(f"Verified obligation: {obl['name']} (Amount: {obl['amount']})")

        await conversational_flow(
            client, f"Pagué 900000 del {obl_name} desde la cuenta {acc_name}", "Pay Obligation"
        )

        # CREDIT
        card_name = f"Nu_{uuid.uuid4().hex[:6]}"
        card_resp = await client.post(
            f"{API_URL}/credit/cards",
            json={
                "name": card_name,
                "bank": "Nu",
                "credit_limit": 5000000,
                "cutoff_day": 15,
                "due_day": 30,
            },
            headers=AUTH_HEADERS,
        )
        card_id = card_resp.json()["id"]

        # Purchase to create debt
        await client.post(
            f"{API_URL}/credit/cards/{card_id}/purchases",
            json={"amount": 300000, "installments_total": 1, "description": "Compra de prueba"},
            headers={**AUTH_HEADERS, "Idempotency-Key": str(uuid.uuid4())},
        )

        await conversational_flow(
            client, f"Pagué 300000 de la tarjeta {card_name} desde {acc_name}", "Pay Credit Card"
        )

        card_verify = await client.get(f"{API_URL}/credit/cards/{card_id}", headers=AUTH_HEADERS)
        data = card_verify.json()
        print("CARD VERIFY DATA:", data)
        assert float(data["estimated_current_debt"]) == 0.0, (
            f"Credit card debt not correctly paid. Got: {data}"
        )

        logging.info("--- E2E Conversational Domains Smoke Test COMPLETED ---")


if __name__ == "__main__":
    asyncio.run(run_smoke())
