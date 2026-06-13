
with open('scripts/smoke_clarification.py', encoding='utf-8') as f:
    content = f.read()

new_header = """
async def get_test_user_id():
    import asyncpg
    dsn = os.getenv("DATABASE_URL")
    if dsn and dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        user_id = await conn.fetchval("SELECT id FROM users WHERE email = 'dev@nexum.local'")
        return str(user_id) if user_id else "1a7b96ab-4fcc-46fc-94da-46e366d23dac"
    finally:
        await conn.close()
"""

# We'll replace the synchronous headers block with dynamic headers logic.
content = content.replace("""TOKEN = os.getenv("TEST_TOKEN")

if not TOKEN:
    print("Por favor, configura la variable de entorno TEST_TOKEN")
    exit(1)

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}""", new_header)

content = content.replace("""async def run_smokes():
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:""", """async def run_smokes():
    user_id = await get_test_user_id()
    headers = {"x-user-id": user_id, "Content-Type": "application/json"}
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:""")

with open('scripts/smoke_clarification.py', 'w', encoding='utf-8') as f:
    f.write(content)
