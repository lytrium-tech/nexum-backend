import asyncio

from sqlalchemy import text

from app.core.database import close_engine, get_db_session, init_engine


async def migrate():
    await init_engine()
    async for session in get_db_session():
        await session.execute(
            text(
                "ALTER TABLE public.financial_events DROP CONSTRAINT IF EXISTS financial_events_type_check"
            )
        )
        await session.execute(
            text("""
            ALTER TABLE public.financial_events ADD CONSTRAINT financial_events_type_check 
            CHECK (event_type = ANY (ARRAY['income', 'expense', 'credit_card_purchase', 'credit_card_payment', 'obligation_payment', 'goal_contribution', 'manual_adjustment', 'transfer_out', 'transfer_in', 'opening_balance', 'balance_adjustment']))
        """)
        )
        await session.commit()
    await close_engine()


if __name__ == "__main__":
    asyncio.run(migrate())
