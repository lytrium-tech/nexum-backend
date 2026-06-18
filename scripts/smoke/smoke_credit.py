import asyncio
import os
import sys
import uuid

import httpx

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.config import settings


async def run_smoke():
    base_url = "http://localhost:8000"
    user_id = str(settings.DEV_USER_ID)
    headers = {"Authorization": f"Bearer {user_id}"}

    async with httpx.AsyncClient() as client:
        # 1. Health
        resp = await client.get(f"{base_url}/health")
        if resp.status_code != 200:
            print(resp.json())
            assert resp.status_code == 200
        print("INFO: Health check OK.")

        # 3. Create Category
        cat_payload = {"name": "Test Cat Credit", "type": "expense", "is_global": False}
        resp = await client.post(f"{base_url}/api/v1/categories", json=cat_payload, headers=headers)
        if resp.status_code != 201:
            print(resp.json())
            assert resp.status_code == 201
        cat_id = resp.json()["id"]

        # 4. Create Account
        acc_payload = {"name": "Test Acc Credit", "type": "bank", "currency": "COP", "balance": 0.0}
        resp = await client.post(f"{base_url}/api/v1/accounts", json=acc_payload, headers=headers)
        if resp.status_code != 201:
            print(resp.json())
            assert resp.status_code == 201
        acc_id = resp.json()["id"]

        # 5. Fund account
        fund_payload = {
            "account_id": acc_id,
            "amount": 1000.00,
            "category_id": cat_id,
            "description": "initial",
        }
        headers["Idempotency-Key"] = str(uuid.uuid4())
        resp = await client.post(
            f"{base_url}/api/v1/cash/income", json=fund_payload, headers=headers
        )
        if resp.status_code != 200:
            print(resp.json())
            assert resp.status_code == 200

        # 6. Create card
        card_payload = {
            "name": "Test Card",
            "bank": "Test Bank",
            "credit_limit": 1000.0,
            "cutoff_day": 10,
            "due_day": 25,
        }
        resp = await client.post(
            f"{base_url}/api/v1/credit/cards", json=card_payload, headers=headers
        )
        if resp.status_code != 201:
            print(resp.json())
            assert resp.status_code == 201
        card_id = resp.json()["id"]
        print(f"INFO: Card created: {card_id}")

        # 7. Purchase 300 to 3 installments
        purch_payload = {"amount": 300.0, "installments_total": 3, "category_id": cat_id}
        headers["Idempotency-Key"] = str(uuid.uuid4())
        resp = await client.post(
            f"{base_url}/api/v1/credit/cards/{card_id}/purchases",
            json=purch_payload,
            headers=headers,
        )
        if resp.status_code != 200:
            print(resp.json())
            assert resp.status_code == 200
        data = resp.json()
        print(
            f"INFO: Purchase OK. Debt: {data['estimated_current_debt']}, Avail: {data['estimated_available_credit']}"
        )
        assert float(data["estimated_current_debt"]) == 300.0
        assert float(data["estimated_available_credit"]) == 700.0

        # Check monthly payment via GET
        resp = await client.get(f"{base_url}/api/v1/credit/cards/{card_id}", headers=headers)
        if resp.status_code != 200:
            print(resp.json())
            assert resp.status_code == 200
        data = resp.json()
        print(f"INFO: Monthly CC Payment: {data['monthly_cc_payment']}")
        assert float(data["monthly_cc_payment"]) == 100.0

        # 9. Retry purchase
        resp = await client.post(
            f"{base_url}/api/v1/credit/cards/{card_id}/purchases",
            json=purch_payload,
            headers=headers,
        )
        if resp.status_code != 200:
            print(resp.json())
            assert resp.status_code == 200
        assert resp.json()["status"] == "idempotent_retry"

        # 10. Purchase over limit
        purch2_payload = {"amount": 800.0, "installments_total": 1}
        headers["Idempotency-Key"] = str(uuid.uuid4())
        resp = await client.post(
            f"{base_url}/api/v1/credit/cards/{card_id}/purchases",
            json=purch2_payload,
            headers=headers,
        )
        if resp.status_code != 409:
            print(resp.json())
            assert resp.status_code == 409
        print("INFO: Exceeded limit caught.")

        # 11. Payment 100
        pay_payload = {"account_id": acc_id, "amount": 100.0}
        headers["Idempotency-Key"] = str(uuid.uuid4())
        resp = await client.post(
            f"{base_url}/api/v1/credit/cards/{card_id}/payments", json=pay_payload, headers=headers
        )
        if resp.status_code != 200:
            print(resp.json())
            assert resp.status_code == 200
        data = resp.json()
        assert float(data["estimated_current_debt"]) == 200.0
        print("INFO: Payment OK.")

        # 12. Retry payment
        resp = await client.post(
            f"{base_url}/api/v1/credit/cards/{card_id}/payments", json=pay_payload, headers=headers
        )
        if resp.status_code != 200:
            print(resp.json())
            assert resp.status_code == 200
        assert resp.json()["status"] == "idempotent_retry"

        # 13. Overpayment
        pay2_payload = {"account_id": acc_id, "amount": 500.0}
        headers["Idempotency-Key"] = str(uuid.uuid4())
        resp = await client.post(
            f"{base_url}/api/v1/credit/cards/{card_id}/payments", json=pay2_payload, headers=headers
        )
        if resp.status_code != 409:
            print(resp.json())
            assert resp.status_code == 409
        print("INFO: Overpayment caught.")


if __name__ == "__main__":
    asyncio.run(run_smoke())
