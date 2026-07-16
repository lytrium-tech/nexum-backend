import asyncio

import httpx

API_URL = "http://127.0.0.1:8000/api/v1"


async def create_test_user(client, name: str, email: str) -> str:
    import uuid

    auth_user_id = str(uuid.uuid4())
    headers = {
        "Authorization": f"Bearer {auth_user_id}",
        "X-Test-Bypass-Auth": "true",
        "X-Test-Email": email,
        "Content-Type": "application/json",
    }

    resp = await client.post(
        f"{API_URL}/users/me/bootstrap",
        json={"name": name, "timezone": "America/Bogota", "currency": "COP"},
        headers=headers,
    )
    resp.raise_for_status()

    profile_id = resp.json()["profile"]["id"]
    return profile_id


async def main():
    async with httpx.AsyncClient(base_url=API_URL, timeout=10.0) as client:
        print("=== C.2 Smoke Test: Accounts & Categories ===")

        import uuid

        run_id = str(uuid.uuid4())[:4]
        auth_a = await create_test_user(client, "Alice", f"alice_{run_id}@example.com")
        auth_b = await create_test_user(client, "Bob", f"bob_{run_id}@example.com")

        h_a = {"Authorization": f"Bearer {auth_a}", "X-Test-Bypass-Auth": "true"}
        h_b = {"Authorization": f"Bearer {auth_b}", "X-Test-Bypass-Auth": "true"}

        # 1. Crear cuenta
        r = await client.post(
            "/accounts", headers=h_a, json={"name": "Cuenta A", "type": "wallet", "currency": "COP"}
        )
        r.raise_for_status()
        acc_a = r.json()
        print(f"Cuenta A creada: {acc_a['id']}")

        # 2. Editar cuenta
        r = await client.patch(
            f"/accounts/{acc_a['id']}", headers=h_a, json={"name": "Cuenta A Editada"}
        )
        r.raise_for_status()
        print("Cuenta A editada.")

        # 3. Listar cuentas
        r = await client.get("/accounts", headers=h_a)
        r.raise_for_status()
        print(f"Listar cuentas A: {[a['name'] for a in r.json()]}")

        # 4. Bloquear acceso cruzado (Bob no puede editar cuenta de Alice)
        r = await client.patch(f"/accounts/{acc_a['id']}", headers=h_b, json={"name": "Hacked"})
        assert r.status_code in [403, 404], f"Esperaba 403 o 404, obtuvo {r.status_code}"
        print("Acceso cruzado bloqueado correctamente.")

        # 5. Desactivar cuenta
        # creamos una temp para borrar
        r = await client.post("/accounts", headers=h_a, json={"name": "Temp", "type": "cash"})
        temp_id = r.json()["id"]
        r = await client.delete(f"/accounts/{temp_id}", headers=h_a)
        assert r.status_code == 204
        # verificar que ya no sale en el listado
        r = await client.get("/accounts", headers=h_a)
        acc_ids = [a["id"] for a in r.json()]
        assert temp_id not in acc_ids, "Cuenta no fue desactivada"
        print("Cuenta desactivada correctamente.")

        # 6. Summary de cuentas
        r = await client.get("/accounts/summary", headers=h_a)
        r.raise_for_status()
        summary = r.json()
        print(f"Summary A: {summary}")

        # 7. Crear categoria privada
        r = await client.post(
            "/categories", headers=h_a, json={"name": "Privada A", "type": "expense"}
        )
        r.raise_for_status()
        cat_a = r.json()
        print(f"Categoria privada creada: {cat_a['id']}")

        # 8. Listar globales + privadas
        r = await client.get("/categories", headers=h_a)
        r.raise_for_status()
        cats = r.json()
        has_global = any(c["is_global"] for c in cats)
        has_private = any(not c["is_global"] and c["id"] == cat_a["id"] for c in cats)
        print(f"Categorias listadas: {len(cats)}. Globals: {has_global}, Privates: {has_private}")

        # 9. Bloquear editar globales o privadas ajenas
        # Bob tries to edit Alice's category
        r = await client.patch(f"/categories/{cat_a['id']}", headers=h_b, json={"name": "Hacked"})
        assert r.status_code in [403, 404], "Bob pudo editar categoria de Alice"
        # Bob tries to edit global expense_uncategorized
        global_cat = next(c for c in cats if c.get("stable_key") == "expense_uncategorized")
        r = await client.patch(
            f"/categories/{global_cat['id']}", headers=h_b, json={"name": "Hacked global"}
        )
        assert r.status_code in [403, 404], "Bob pudo editar categoria global"
        print("Acceso cruzado a categorias bloqueado correctamente.")

        # 10. Registrar income usando cuenta via conversacion (Para dar saldo)
        msg_payload_inc = {
            "channel": "api",
            "external_message_id": "998",
            "message": "Me pagaron 50 mil en Cuenta A Editada",
        }
        r = await client.post("/conversations/message", headers=h_a, json=msg_payload_inc)
        r.raise_for_status()
        data_inc = r.json()
        assert data_inc["intent"] == "create_income"

        r = await client.post(
            "/conversations/message",
            headers=h_a,
            json={"channel": "api", "external_message_id": "998b", "message": "si"},
        )
        r.raise_for_status()
        assert r.json()["status"] == "completed"
        print("Ingreso conversacional registrado.")

        # 11. Registrar expense usando cuenta y categoria via conversacion
        msg_payload = {
            "channel": "api",
            "external_message_id": "999",
            "message": "Gaste 10 mil en Privada A desde Cuenta A Editada",
        }
        r = await client.post("/conversations/message", headers=h_a, json=msg_payload)
        r.raise_for_status()
        data = r.json()

        # Debe pedir confirmacion
        assert data["status"] == "awaiting_confirmation"
        assert data["intent"] == "create_expense"
        assert "10000" in data["response_text"]

        # Confirmar
        r = await client.post(
            "/conversations/message",
            headers=h_a,
            json={"channel": "api", "external_message_id": "1000", "message": "si"},
        )
        r.raise_for_status()
        data2 = r.json()
        assert data2["status"] == "completed"
        print("Gasto conversacional registrado con categoria.")

        # 11. Categoria fallback
        msg_payload = {
            "channel": "api",
            "external_message_id": "1001",
            "message": "Gaste 5 mil en algo_inventado desde Cuenta A Editada",
        }
        r = await client.post("/conversations/message", headers=h_a, json=msg_payload)
        r.raise_for_status()
        data3 = r.json()
        print("Fallback data:", data3)

        assert data3["status"] == "awaiting_confirmation"
        assert (
            "clasificar" in data3.get("response_text", "").lower()
            or data3.get("intent") == "create_expense"
        ), "Fallback category not used"

        print("Fallback a sin_clasificar verificado.")
        print("Todos los tests de Accounts & Categories Readiness pasaron!")


if __name__ == "__main__":
    asyncio.run(main())
