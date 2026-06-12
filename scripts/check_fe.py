import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

async def main():
    dsn = os.environ['DATABASE_URL'].replace('+asyncpg', '')
    conn = await asyncpg.connect(dsn)
    
    res = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name='financial_events';")
    print('financial_events columns:', [r['column_name'] for r in res])
    
    await conn.close()

asyncio.run(main())
