import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.credit.models import (
    CreditCard,
    CreditCardAdjustment,
    CreditCardAdjustmentAllocation,
    CreditCardBillingCycle,
    CreditCardInstallment,
    CreditCardPayment,
    CreditCardPaymentAllocation,
    CreditCardStatement,
    CreditCardStatementCharge,
    CreditCardStatementComponent,
    CreditCardStatementFact,
    CreditCardTransaction,
)


class CreditCardRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, card_id: uuid.UUID) -> CreditCard | None:
        result = await self.session.execute(select(CreditCard).where(CreditCard.id == card_id))
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, card_id: uuid.UUID) -> CreditCard | None:
        result = await self.session.execute(
            select(CreditCard)
            .where(CreditCard.id == card_id)
            .with_for_update()
            .execution_options(populate_existing=True)
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
        """Returns (credit_card_debt, monthly_cc_payment) directly from credit_card_installments.

        Note: credit_cards.current_debt and v_credit_card_debt view are non-authoritative
        legacy artifacts retained physically for backwards compatibility.
        """
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
            .execution_options(populate_existing=True)
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
            .execution_options(populate_existing=True)
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
                    COALESCE(SUM(CASE WHEN scheduled_period <= to_char(CAST(:cycle_end AS DATE), 'YYYY-MM') THEN principal_amount + interest_amount - paid_amount ELSE 0 END), 0) as billed,
                    COALESCE(SUM(CASE WHEN scheduled_period > to_char(CAST(:cycle_end AS DATE), 'YYYY-MM') THEN principal_amount + interest_amount - paid_amount ELSE 0 END), 0) as unbilled
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
        result = await self.session.execute(
            select(CreditCardStatement)
            .where(CreditCardStatement.credit_card_id == card_id)
            .order_by(CreditCardStatement.billing_period.desc())
        )
        return list(result.scalars().all())

    async def get_statement(self, card_id: uuid.UUID, period: str):
        result = await self.session.execute(
            select(CreditCardStatement).where(
                CreditCardStatement.credit_card_id == card_id,
                CreditCardStatement.billing_period == period,
            )
        )
        return result.scalars().first()

    async def get_statement_for_update(self, statement_id: uuid.UUID) -> CreditCardStatement | None:
        result = await self.session.execute(
            select(CreditCardStatement)
            .where(CreditCardStatement.id == statement_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_unpaid_statement_charges_for_update(self, card_id: uuid.UUID) -> list:
        result = await self.session.execute(
            select(CreditCardStatementCharge)
            .join(CreditCardStatement)
            .where(
                CreditCardStatement.credit_card_id == card_id,
                CreditCardStatementCharge.status != "paid",
            )
            .order_by(CreditCardStatementCharge.created_at.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    # ── Canonical Primitives (Goal 2 Foundation) ─────────────────────────────

    async def add_billing_cycle(self, cycle: CreditCardBillingCycle) -> None:
        self.session.add(cycle)
        await self.session.flush()

    async def get_billing_cycle(self, cycle_id: uuid.UUID) -> CreditCardBillingCycle | None:
        result = await self.session.execute(
            select(CreditCardBillingCycle).where(CreditCardBillingCycle.id == cycle_id)
        )
        return result.scalar_one_or_none()

    async def get_billing_cycle_for_update(
        self, cycle_id: uuid.UUID
    ) -> CreditCardBillingCycle | None:
        result = await self.session.execute(
            select(CreditCardBillingCycle)
            .where(CreditCardBillingCycle.id == cycle_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_billing_cycle_by_period(
        self, card_id: uuid.UUID, billing_period: str
    ) -> CreditCardBillingCycle | None:
        result = await self.session.execute(
            select(CreditCardBillingCycle).where(
                CreditCardBillingCycle.credit_card_id == card_id,
                CreditCardBillingCycle.billing_period == billing_period,
            )
        )
        return result.scalar_one_or_none()

    async def add_payment(self, payment: CreditCardPayment) -> None:
        self.session.add(payment)
        await self.session.flush()

    async def get_payment_by_id(self, payment_id: uuid.UUID) -> CreditCardPayment | None:
        result = await self.session.execute(
            select(CreditCardPayment).where(CreditCardPayment.id == payment_id)
        )
        return result.scalar_one_or_none()

    async def get_payment_for_update(self, payment_id: uuid.UUID) -> CreditCardPayment | None:
        result = await self.session.execute(
            select(CreditCardPayment)
            .where(CreditCardPayment.id == payment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_payment_by_command_id(
        self, user_id: uuid.UUID, command_id: uuid.UUID
    ) -> CreditCardPayment | None:
        result = await self.session.execute(
            select(CreditCardPayment).where(
                CreditCardPayment.user_id == user_id,
                CreditCardPayment.command_id == command_id,
            )
        )
        return result.scalar_one_or_none()

    async def add_payment_allocations(
        self, allocations: list[CreditCardPaymentAllocation]
    ) -> None:
        self.session.add_all(allocations)
        await self.session.flush()

    async def list_allocations_for_payment(
        self, payment_id: uuid.UUID
    ) -> list[CreditCardPaymentAllocation]:
        result = await self.session.execute(
            select(CreditCardPaymentAllocation)
            .where(CreditCardPaymentAllocation.payment_id == payment_id)
            .order_by(CreditCardPaymentAllocation.allocation_order.asc())
        )
        return list(result.scalars().all())

    async def add_statement_components(
        self, components: list[CreditCardStatementComponent]
    ) -> None:
        self.session.add_all(components)
        await self.session.flush()

    async def list_components_for_statement(
        self, statement_id: uuid.UUID
    ) -> list[CreditCardStatementComponent]:
        result = await self.session.execute(
            select(CreditCardStatementComponent)
            .where(CreditCardStatementComponent.statement_id == statement_id)
            .order_by(CreditCardStatementComponent.created_at.asc())
        )
        return list(result.scalars().all())

    async def add_statement_facts(
        self, facts: list[CreditCardStatementFact]
    ) -> None:
        self.session.add_all(facts)
        await self.session.flush()

    async def list_facts_for_statement(
        self, statement_id: uuid.UUID
    ) -> list[CreditCardStatementFact]:
        result = await self.session.execute(
            select(CreditCardStatementFact)
            .where(CreditCardStatementFact.statement_id == statement_id)
            .order_by(CreditCardStatementFact.created_at.asc())
        )
        return list(result.scalars().all())

    async def add_adjustment(self, adjustment: CreditCardAdjustment) -> None:
        self.session.add(adjustment)
        await self.session.flush()

    async def get_adjustment_by_id(
        self, adjustment_id: uuid.UUID
    ) -> CreditCardAdjustment | None:
        result = await self.session.execute(
            select(CreditCardAdjustment).where(CreditCardAdjustment.id == adjustment_id)
        )
        return result.scalar_one_or_none()

    async def get_adjustment_for_update(
        self, adjustment_id: uuid.UUID
    ) -> CreditCardAdjustment | None:
        result = await self.session.execute(
            select(CreditCardAdjustment)
            .where(CreditCardAdjustment.id == adjustment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_adjustment_by_command_id(
        self, user_id: uuid.UUID, command_id: uuid.UUID
    ) -> CreditCardAdjustment | None:
        result = await self.session.execute(
            select(CreditCardAdjustment).where(
                CreditCardAdjustment.user_id == user_id,
                CreditCardAdjustment.command_id == command_id,
            )
        )
        return result.scalar_one_or_none()

    async def add_adjustment_allocations(
        self, allocations: list[CreditCardAdjustmentAllocation]
    ) -> None:
        self.session.add_all(allocations)
        await self.session.flush()

    async def list_allocations_for_adjustment(
        self, adjustment_id: uuid.UUID
    ) -> list[CreditCardAdjustmentAllocation]:
        result = await self.session.execute(
            select(CreditCardAdjustmentAllocation)
            .where(CreditCardAdjustmentAllocation.adjustment_id == adjustment_id)
            .order_by(CreditCardAdjustmentAllocation.allocation_order.asc())
        )
        return list(result.scalars().all())
