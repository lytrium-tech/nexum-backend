import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.credit.models import CreditCard, CreditCardInstallment, CreditCardTransaction


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
            SELECT 
                COALESCE(SUM(principal_amount + interest_amount - paid_amount), 0) as credit_card_debt,
                COALESCE(SUM(CASE WHEN scheduled_period = to_char((now() AT TIME ZONE 'America/Bogota'), 'YYYY-MM') THEN principal_amount + interest_amount - paid_amount ELSE 0 END), 0) as monthly_cc_payment
            FROM credit_card_installments 
            WHERE credit_card_id = :card_id AND status != 'paid'
        """)
        result = await self.session.execute(query, {"card_id": card_id})
        row = result.fetchone()
        if row:
            return float(row[0]), float(row[1])
        return 0.0, 0.0

    async def add_transaction(self, transaction: CreditCardTransaction) -> None:
        self.session.add(transaction)
        await self.session.flush()

    async def add_installments(self, installments: list[CreditCardInstallment]) -> None:
        self.session.add_all(installments)
        await self.session.flush()

    async def list_installments(
        self, card_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[CreditCardInstallment]:
        result = await self.session.execute(
            select(CreditCardInstallment)
            .where(
                CreditCardInstallment.credit_card_id == card_id,
                CreditCardInstallment.user_id == user_id,
            )
            .order_by(
                CreditCardInstallment.scheduled_period.asc(),
                CreditCardInstallment.installment_number.asc(),
            )
        )
        return list(result.scalars().all())

    async def list_pending_installments_for_update(
        self, card_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[CreditCardInstallment]:
        result = await self.session.execute(
            select(CreditCardInstallment)
            .where(
                CreditCardInstallment.credit_card_id == card_id,
                CreditCardInstallment.user_id == user_id,
                CreditCardInstallment.status != "paid",
            )
            .order_by(
                CreditCardInstallment.scheduled_period.asc(),
                CreditCardInstallment.installment_number.asc(),
            )
            .with_for_update()
        )
        return list(result.scalars().all())

    async def get_transaction_by_event(self, event_id: uuid.UUID) -> CreditCardTransaction | None:
        result = await self.session.execute(
            select(CreditCardTransaction).where(CreditCardTransaction.event_id == event_id)
        )
        return result.scalar_one_or_none()

    async def get_transaction_by_id(
        self, transaction_id: uuid.UUID
    ) -> CreditCardTransaction | None:
        result = await self.session.execute(
            select(CreditCardTransaction).where(CreditCardTransaction.id == transaction_id)
        )
        return result.scalar_one_or_none()

    async def list_installments_for_transaction_for_update(
        self, transaction_id: uuid.UUID
    ) -> list[CreditCardInstallment]:
        result = await self.session.execute(
            select(CreditCardInstallment)
            .where(CreditCardInstallment.purchase_transaction_id == transaction_id)
            .order_by(CreditCardInstallment.installment_number.asc())
            .with_for_update()
        )
        return list(result.scalars().all())

    async def get_early_payment_by_event(self, event_id: uuid.UUID):
        from app.credit.models import CreditCardEarlyPayment

        result = await self.session.execute(
            select(CreditCardEarlyPayment).where(CreditCardEarlyPayment.event_id == event_id)
        )
        return result.scalar_one_or_none()

    async def get_card_status_data(self, card_id: uuid.UUID, cycle_end_date: date) -> dict:
        query = text("""
            WITH inst AS (
                SELECT 
                    COALESCE(SUM(CASE WHEN scheduled_period <= to_char(:cycle_end::date, 'YYYY-MM') THEN principal_amount + interest_amount - paid_amount ELSE 0 END), 0) as billed,
                    COALESCE(SUM(CASE WHEN scheduled_period > to_char(:cycle_end::date, 'YYYY-MM') THEN principal_amount + interest_amount - paid_amount ELSE 0 END), 0) as unbilled
                FROM credit_card_installments
                WHERE credit_card_id = :card_id AND status != 'paid'
            )
            SELECT
                (SELECT COUNT(*) FROM credit_card_transactions WHERE type = 'purchase' AND credit_card_id = :card_id) as purchases_count,
                (SELECT COUNT(*) FROM credit_card_transactions WHERE type = 'payment' AND credit_card_id = :card_id) as payments_count,
                billed as billed_purchases,
                unbilled as unbilled_purchases,
                0 as total_payments
            FROM inst
        """)
        result = await self.session.execute(
            query, {"card_id": card_id, "cycle_end": cycle_end_date}
        )
        row = result.fetchone()
        if row:
            return {
                "purchases_count": int(row[0]),
                "payments_count": int(row[1]),
                "billed_purchases": Decimal(str(row[2])),
                "unbilled_purchases": Decimal(str(row[3])),
                "total_payments": Decimal(str(row[4])),
            }
        return {
            "purchases_count": 0,
            "payments_count": 0,
            "billed_purchases": Decimal("0.00"),
            "unbilled_purchases": Decimal("0.00"),
            "total_payments": Decimal("0.00"),
        }

    async def get_next_payment_estimate(self, card_id: uuid.UUID) -> Decimal:
        query = text("""
            SELECT COALESCE(SUM(principal_amount - paid_amount), 0) AS estimate
            FROM credit_card_installments
            WHERE credit_card_id = :card_id
              AND status != 'paid'
              AND scheduled_period = to_char((now() AT TIME ZONE 'America/Bogota'), 'YYYY-MM')
        """)
        result = await self.session.execute(query, {"card_id": card_id})
        return Decimal(str(result.scalar_one_or_none() or 0))

    async def list_statements(self, card_id: uuid.UUID) -> list:
        from app.credit.models import CreditCardStatement

        result = await self.session.execute(
            select(CreditCardStatement)
            .where(CreditCardStatement.credit_card_id == card_id)
            .order_by(CreditCardStatement.billing_period.desc())
        )
        return list(result.scalars().all())

    async def get_statement(self, card_id: uuid.UUID, period: str):
        from app.credit.models import CreditCardStatement

        result = await self.session.execute(
            select(CreditCardStatement).where(
                CreditCardStatement.credit_card_id == card_id,
                CreditCardStatement.billing_period == period,
            )
        )
        return result.scalars().first()

    async def list_unpaid_statement_charges_for_update(self, card_id: uuid.UUID) -> list:
        from app.credit.models import CreditCardStatement, CreditCardStatementCharge

        result = await self.session.execute(
            select(CreditCardStatementCharge)
            .join(CreditCardStatement)
            .where(
                CreditCardStatement.credit_card_id == card_id,
                CreditCardStatementCharge.status != "paid",
            )
            .order_by(CreditCardStatementCharge.created_at.asc())
            .with_for_update()
        )
        return list(result.scalars().all())
