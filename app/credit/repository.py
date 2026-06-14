import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.credit.models import CreditCard, CreditCardTransaction


class CreditCardRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, card_id: uuid.UUID) -> CreditCard | None:
        result = await self.session.execute(select(CreditCard).where(CreditCard.id == card_id))
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, card_id: uuid.UUID) -> CreditCard | None:
        result = await self.session.execute(
            select(CreditCard).where(CreditCard.id == card_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_all_for_user(self, user_id: uuid.UUID) -> list[CreditCard]:
        result = await self.session.execute(
            select(CreditCard).where(CreditCard.user_id == user_id, CreditCard.is_active)
        )
        return list(result.scalars().all())

    async def add(self, card: CreditCard) -> None:
        self.session.add(card)
        await self.session.flush()

    async def get_card_debt(self, card_id: uuid.UUID) -> tuple[float, float]:
        """Returns (credit_card_debt, monthly_cc_payment) from the view."""
        query = text("""
            SELECT credit_card_debt, monthly_cc_payment 
            FROM v_credit_card_debt 
            WHERE credit_card_id = :card_id
        """)
        result = await self.session.execute(query, {"card_id": card_id})
        row = result.fetchone()
        if row:
            return float(row[0]), float(row[1])
        return 0.0, 0.0

    async def add_transaction(self, transaction: CreditCardTransaction) -> None:
        self.session.add(transaction)
        await self.session.flush()

    async def get_transaction_by_event(self, event_id: uuid.UUID) -> CreditCardTransaction | None:
        result = await self.session.execute(
            select(CreditCardTransaction).where(CreditCardTransaction.event_id == event_id)
        )
        return result.scalar_one_or_none()

    async def get_card_status_data(self, card_id: uuid.UUID, cycle_end_date: date) -> dict:
        query = text("""
            SELECT
                COUNT(*) FILTER (WHERE type = 'purchase') as purchases_count,
                COUNT(*) FILTER (WHERE type = 'payment') as payments_count,
                COALESCE(SUM(CASE WHEN type = 'purchase' AND DATE(occurred_at AT TIME ZONE 'UTC') <= :cycle_end THEN COALESCE(total_with_interest, amount) ELSE 0 END), 0) as billed_purchases,
                COALESCE(SUM(CASE WHEN type = 'purchase' AND DATE(occurred_at AT TIME ZONE 'UTC') > :cycle_end THEN COALESCE(total_with_interest, amount) ELSE 0 END), 0) as unbilled_purchases,
                COALESCE(SUM(CASE WHEN type = 'payment' THEN amount ELSE 0 END), 0) as total_payments
            FROM credit_card_transactions
            WHERE credit_card_id = :card_id
        """)
        result = await self.session.execute(query, {"card_id": card_id, "cycle_end": cycle_end_date})
        row = result.fetchone()
        if row:
            return {
                "purchases_count": int(row[0]),
                "payments_count": int(row[1]),
                "billed_purchases": Decimal(str(row[2])),
                "unbilled_purchases": Decimal(str(row[3])),
                "total_payments": Decimal(str(row[4]))
            }
        return {
            "purchases_count": 0, "payments_count": 0,
            "billed_purchases": Decimal("0.00"), "unbilled_purchases": Decimal("0.00"), "total_payments": Decimal("0.00")
        }
