import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

async def main():
    dsn = os.environ['DATABASE_URL'].replace('+asyncpg', '')
    conn = await asyncpg.connect(dsn)
    val = await conn.fetchval("SELECT pg_get_viewdef('v_credit_card_debt');")
    print("VIEW DEFINITION:")
    print(val)
    await conn.close()

asyncio.run(main())
