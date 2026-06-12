import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

async def main():
    dsn = os.environ['DATABASE_URL'].replace('+asyncpg', '')
    conn = await asyncpg.connect(dsn)
    
    val1 = await conn.fetchval("SELECT column_name FROM information_schema.columns WHERE table_name='messages' AND column_name='trace_id';")
    val2 = await conn.fetchval("SELECT column_name FROM information_schema.columns WHERE table_name='ai_runs' AND column_name='trace_id';")
    
    print('messages.trace_id:', val1)
    print('ai_runs.trace_id:', val2)
    
    await conn.close()

asyncio.run(main())
