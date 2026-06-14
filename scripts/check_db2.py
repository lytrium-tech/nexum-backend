import asyncio
from app.core.database import init_engine, get_db_session
from sqlalchemy import text

async def main():
    await init_engine()
    async for s in get_db_session():
        res = await s.execute(text("SELECT id, amount FROM transfers"))
        print("TRANSFERS:", res.all())
        
        res2 = await s.execute(text("SELECT name, balance FROM accounts WHERE name IN ('Nequi', 'Bancolombia')"))
        print("ACCOUNTS:", res2.all())
        break

asyncio.run(main())
