import asyncio
import os
import uuid

import asyncpg
import httpx
from dotenv import load_dotenv

load_dotenv()


async def get_test_user_and_account(conn):
    user_id = await conn.fetchval("SELECT id FROM users WHERE email = 'dev@nexum.local'")
    if not user_id:
        user_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name, is_active) VALUES ($1, $2, $3, $4, $5)",
            user_id,
            "dev@nexum.local",
            "hash",
            "Dev",
            True,
        )

    account_id = await conn.fetchval(
        "SELECT id FROM accounts WHERE user_id = $1 AND name = 'Efectivo'", user_id
    )
    if not account_id:
        account_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO accounts (id, user_id, name, type, balance) VALUES ($1, $2, $3, $4, $5)",
            account_id,
            user_id,
            "Efectivo",
            "cash",
            1000000,
        )

    category_id = await conn.fetchval(
        "SELECT id FROM categories WHERE user_id = $1 AND name = 'Almuerzo'", user_id
    )
    if not category_id:
        category_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO categories (id, user_id, name, type) VALUES ($1, $2, $3, $4)",
            category_id,
            user_id,
            "Almuerzo",
            "expense",
        )

    return str(user_id), str(account_id), str(category_id)


async def main():
    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)

    user_id, account_id, category_id = await get_test_user_and_account(conn)

    trace_id_str = str(uuid.uuid4())
    headers = {"x-user-id": user_id, "x-trace-id": trace_id_str}

    print(f"Starting test with trace_id: {trace_id_str}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Send first message
        msg1 = "Gasté 20.000 en Almuerzo de la cuenta Efectivo"
        r1 = await client.post(
            "http://127.0.0.1:8000/api/v1/conversations/message",
            json={"message": msg1, "channel": "api"},
            headers=headers,
        )
        r1.raise_for_status()
        resp1 = r1.json()
        assert resp1["status"] == "awaiting_confirmation", resp1
        action_id = resp1["pending_action_id"]
        assert action_id is not None

        # Verify inbound message
        inbound1 = await conn.fetchrow(
            "SELECT id, trace_id FROM messages WHERE trace_id = $1 AND direction = 'inbound' ORDER BY created_at DESC LIMIT 1",
            trace_id_str,
        )
        assert inbound1 is not None
        assert str(inbound1["trace_id"]) == trace_id_str

        # Verify ai_run
        airun = await conn.fetchrow(
            "SELECT trace_id FROM ai_runs WHERE message_id = $1", inbound1["id"]
        )
        assert str(airun["trace_id"]) == trace_id_str

        # Verify pending action
        pa = await conn.fetchrow(
            "SELECT source_message_id FROM pending_actions WHERE id = $1", action_id
        )
        assert str(pa["source_message_id"]) == str(inbound1["id"])

        # 2. Send confirmation message
        msg2 = "sí"
        trace_id_str2 = str(uuid.uuid4())
        headers2 = {"x-user-id": user_id, "x-trace-id": trace_id_str2}
        r2 = await client.post(
            "http://127.0.0.1:8000/api/v1/conversations/message",
            json={"message": msg2, "channel": "api", "pending_action_id": action_id},
            headers=headers2,
        )
        r2.raise_for_status()
        resp2 = r2.json()
        assert resp2["status"] == "completed", resp2

        # Verify inbound confirmation message
        inbound2 = await conn.fetchrow(
            "SELECT id, trace_id FROM messages WHERE message = $1 AND trace_id = $2 ORDER BY created_at DESC LIMIT 1",
            msg2,
            trace_id_str2,
        )
        assert inbound2 is not None
        assert str(inbound2["trace_id"]) == trace_id_str2

        # Verify pending action confirmation_message_id
        pa2 = await conn.fetchrow(
            "SELECT confirmation_message_id FROM pending_actions WHERE id = $1", action_id
        )
        assert str(pa2["confirmation_message_id"]) == str(inbound2["id"])

        # Verify financial event
        fe = await conn.fetchrow(
            "SELECT source_message_id, raw_message FROM financial_events WHERE source_message_id = $1",
            inbound1["id"],
        )
        assert fe is not None
        assert str(fe["source_message_id"]) == str(inbound1["id"])
        assert fe["raw_message"] == msg1, f"Expected '{msg1}', got '{fe['raw_message']}'"

        # Verify outbound message has the same trace_id as the request that generated it (trace_id_str2)
        outbound2 = await conn.fetchrow(
            "SELECT trace_id FROM messages WHERE response_data->>'response_text' = '¡Operación registrada con éxito!' AND direction = 'outbound' ORDER BY created_at DESC LIMIT 1"
        )
        assert str(outbound2["trace_id"]) == trace_id_str2

        print("API Direct test...")
        r_api = await client.post(
            "http://127.0.0.1:8000/api/v1/cash/expense",
            json={
                "account_id": account_id,
                "category_id": category_id,
                "amount": 5000,
                "description": "Direct API test",
            },
            headers={"x-user-id": user_id, "Idempotency-Key": str(uuid.uuid4())},
        )
        r_api.raise_for_status()

        fe_api = await conn.fetchrow(
            "SELECT source_message_id, raw_message FROM financial_events WHERE description = 'Direct API test' ORDER BY created_at DESC LIMIT 1"
        )
        assert fe_api["source_message_id"] is None
        assert fe_api["raw_message"] is None

        print("All tests passed!")

    await conn.close()


asyncio.run(main())
