import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

async def main():
    dsn = os.environ['DATABASE_URL'].replace('+asyncpg', '')
    conn = await asyncpg.connect(dsn)
    with open('docs/sql_snapshots/c4_transfers.sql', encoding='utf-8') as f:
        sql = f.read()
    await conn.execute(sql)
    print('OK')
    await conn.close()

asyncio.run(main())
