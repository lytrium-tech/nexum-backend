import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()


async def main():
    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)

    with open("docs/sql_snapshots/pre_fase_b_traceability.sql") as f:
        sql = f.read()

    await conn.execute(sql)
    print("Migration applied successfully")

    await conn.close()


asyncio.run(main())
