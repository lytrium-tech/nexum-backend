import asyncio
import os
import uuid
import httpx
from datetime import datetime, timezone

BASE_URL = os.getenv("API_URL", "http://localhost:8000/api/v1")

def get_auth_headers(user_token: str):
    return {"Authorization": f"Bearer {user_token}", "Content-Type": "application/json"}

async def create_test_user(client: httpx.AsyncClient, name: str, email: str, internal: bool = False):
    auth_user_id = str(uuid.uuid4())
    headers = {
        "Authorization": f"Bearer {auth_user_id}",
        "X-Test-Bypass-Auth": "true",
        "X-Test-Email": email,
        "Content-Type": "application/json"
    }
    
    resp = await client.post(f"{BASE_URL}/users/me/bootstrap", json={
        "name": name,
        "timezone": "America/Bogota",
        "currency": "COP"
    }, headers=headers)
    resp.raise_for_status()
    
    profile_id = resp.json()["profile"]["id"]
    return profile_id

async def main():
    async with httpx.AsyncClient() as client:
        print("=== B.5 Smoke Test: Ownership Hardening ===")
        
        run_id = str(uuid.uuid4())[:4]

        # 1. Create Users
        auth_a = await create_test_user(client, "Alice", f"alice_{run_id}@example.com")
        auth_b = await create_test_user(client, "Bob", f"bob_{run_id}@example.com")

        headers_a = {"Authorization": f"Bearer {auth_a}", "X-Test-Bypass-Auth": "true", "Content-Type": "application/json"}
        headers_b = {"Authorization": f"Bearer {auth_b}", "X-Test-Bypass-Auth": "true", "Content-Type": "application/json"}

        print(f"User A: {auth_a}")
        print(f"User B: {auth_b}")

        # 2. Account Ownership
        print("\n[Accounts] Alice creates account")
        resp = await client.post(f"{BASE_URL}/accounts", json={
            "name": f"Alice Wallet {run_id}",
            "type": "cash",
            "currency": "COP"
        }, headers=headers_a)
        resp.raise_for_status()
        acc_a_id = resp.json()["id"]

        print("[Accounts] Bob tries to read Alice's account")
        resp = await client.get(f"{BASE_URL}/accounts/{acc_a_id}", headers=headers_b)
        assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"
        print("  -> Access denied (OK)")

        print("[Accounts] Bob tries to edit Alice's account")
        resp = await client.patch(f"{BASE_URL}/accounts/{acc_a_id}", json={"name": f"Hacked {run_id}"}, headers=headers_b)
        assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"
        print("  -> Access denied (OK)")

        print("[Accounts] Bob tries to delete Alice's account")
        resp = await client.delete(f"{BASE_URL}/accounts/{acc_a_id}", headers=headers_b)
        assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"
        print("  -> Access denied (OK)")

        # 3. Categories Ownership
        print("\n[Categories] Alice creates private category")
        resp = await client.post(f"{BASE_URL}/categories", json={
            "name": f"Alice Secrets {run_id}",
            "type": "expense"
        }, headers=headers_a)
        resp.raise_for_status()
        cat_a_id = resp.json()["id"]

        print("[Categories] Bob tries to edit Alice's category")
        resp = await client.patch(f"{BASE_URL}/categories/{cat_a_id}", json={"name": f"Hacked {run_id}"}, headers=headers_b)
        assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"
        print("  -> Access denied (OK)")

        # 4. Goals Ownership
        print("\n[Goals] Alice creates a goal")
        resp = await client.post(f"{BASE_URL}/goals", json={
            "name": f"Alice Car {run_id}",
            "target_amount": 10000.0
        }, headers=headers_a)
        resp.raise_for_status()
        goal_a_id = resp.json()["id"]

        print("[Goals] Bob tries to read Alice's goal")
        resp = await client.get(f"{BASE_URL}/goals/{goal_a_id}", headers=headers_b)
        assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"
        print("  -> Access denied (OK)")

        print("[Goals] Bob tries to contribute to Alice's goal")
        # Give Bob an account first
        resp = await client.post(f"{BASE_URL}/accounts", json={
            "name": f"Bob Wallet {run_id}",
            "type": "cash",
            "currency": "COP"
        }, headers=headers_b)
        acc_b_id = resp.json()["id"]

        resp = await client.post(f"{BASE_URL}/goals/{goal_a_id}/contributions", json={
            "amount": 100.0,
            "account_id": acc_b_id
        }, headers=headers_b)
        assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"
        print("  -> Access denied (OK)")

        print("[Goals] Alice tries to contribute to Alice's goal using BOB's account")
        resp = await client.post(f"{BASE_URL}/goals/{goal_a_id}/contributions", json={
            "amount": 100.0,
            "account_id": acc_b_id
        }, headers=headers_a)
        assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"
        print("  -> Access denied (OK)")

        # 5. Obligations Ownership
        print("\n[Obligations] Alice creates obligation")
        resp = await client.post(f"{BASE_URL}/obligations", json={
            "name": f"Alice Rent {run_id}",
            "amount": 500.0,
            "frequency": "monthly"
        }, headers=headers_a)
        resp.raise_for_status()
        obl_a_id = resp.json()["id"]

        print("[Obligations] Bob tries to read Alice's obligation")
        resp = await client.get(f"{BASE_URL}/obligations/{obl_a_id}", headers=headers_b)
        assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"
        print("  -> Access denied (OK)")

        print("[Obligations] Bob tries to pay Alice's obligation")
        resp = await client.post(f"{BASE_URL}/obligations/{obl_a_id}/payments", json={
            "amount": 500.0,
            "account_id": acc_b_id
        }, headers=headers_b)
        assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"
        print("  -> Access denied (OK)")

        print("[Obligations] Alice tries to pay her obligation with Bob's account")
        resp = await client.post(f"{BASE_URL}/obligations/{obl_a_id}/payments", json={
            "amount": 500.0,
            "account_id": acc_b_id
        }, headers=headers_a)
        assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"
        print("  -> Access denied (OK)")

        # 6. Credit Cards Ownership
        print("\n[Credit Cards] Alice creates a credit card")
        resp = await client.post(f"{BASE_URL}/credit/cards", json={
            "name": f"Alice Visa {run_id}",
            "bank": "Nubank",
            "credit_limit": 2000.0,
            "cutoff_day": 15,
            "due_day": 5
        }, headers=headers_a)
        resp.raise_for_status()
        cc_a_id = resp.json()["id"]

        print("[Credit Cards] Bob tries to read Alice's credit card")
        resp = await client.get(f"{BASE_URL}/credit/cards/{cc_a_id}", headers=headers_b)
        assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"
        print("  -> Access denied (OK)")

        print("[Credit Cards] Bob tries to make a purchase on Alice's credit card")
        resp = await client.post(f"{BASE_URL}/credit/cards/{cc_a_id}/purchases", json={
            "amount": 100.0,
            "description": "Bob's TV",
            "installments_total": 1,
            "category_id": cat_a_id
        }, headers={**headers_b, "Idempotency-Key": str(uuid.uuid4())})
        assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"
        print("  -> Access denied (OK)")

        print("[Credit Cards] Alice tries to pay her CC with Bob's account")
        resp = await client.post(f"{BASE_URL}/credit/cards/{cc_a_id}/payments", json={
            "amount": 100.0,
            "account_id": acc_b_id
        }, headers={**headers_a, "Idempotency-Key": str(uuid.uuid4())})
        assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"
        print("  -> Access denied (OK)")

        print("\nAll ownership tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(main())
