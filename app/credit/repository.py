import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.credit.exceptions import CreditStatementImmutableError
from app.credit.models import (
    DEBT_PARTITION_CANONICAL,
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

    async def get_card_debt_partitioned(self, card_id: uuid.UUID) -> dict[str, Decimal]:
        """Calculates canonical debt and legacy compatibility debt in mutually exclusive partitions.

        Guarantees:
        total_debt = canonical_debt + legacy_debt (zero double-counting).
        """
        query = text("""
            SELECT 
                COALESCE(SUM(CASE WHEN debt_partition = 'canonical' THEN principal_amount + COALESCE(assessed_interest_amount, interest_amount) - paid_amount ELSE 0 END), 0) as canonical_debt,
                COALESCE(SUM(CASE WHEN debt_partition != 'canonical' OR debt_partition IS NULL THEN principal_amount + interest_amount - paid_amount ELSE 0 END), 0) as legacy_debt,
                COALESCE(SUM(CASE WHEN scheduled_period = to_char((now() AT TIME ZONE 'America/Bogota'), 'YYYY-MM') THEN principal_amount + COALESCE(assessed_interest_amount, interest_amount) - paid_amount ELSE 0 END), 0) as monthly_cc_payment
            FROM credit_card_installments 
            WHERE credit_card_id = :card_id AND status != 'paid'
        """)
        result = await self.session.execute(query, {"card_id": card_id})
        row = result.fetchone()
        if row:
            c_debt = Decimal(str(row[0]))
            l_debt = Decimal(str(row[1]))
            monthly = Decimal(str(row[2]))
            return {
                "canonical_debt": c_debt,
                "legacy_debt": l_debt,
                "total_debt": c_debt + l_debt,
                "monthly_cc_payment": monthly,
            }
        return {
            "canonical_debt": Decimal("0.00"),
            "legacy_debt": Decimal("0.00"),
            "total_debt": Decimal("0.00"),
            "monthly_cc_payment": Decimal("0.00"),
        }

    async def get_card_debt(self, card_id: uuid.UUID) -> tuple[float, float]:
        """Returns (credit_card_debt, monthly_cc_payment) directly from credit_card_installments.

        Combines mutually exclusive canonical and legacy partitions without double-counting.
        """
        part = await self.get_card_debt_partitioned(card_id)
        return float(part["total_debt"]), float(part["monthly_cc_payment"])

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

    async def list_statements(self, card_id: uuid.UUID) -> list[CreditCardStatement]:
        result = await self.session.execute(
            select(CreditCardStatement)
            .where(CreditCardStatement.credit_card_id == card_id)
            .order_by(CreditCardStatement.billing_period.desc())
        )
        return list(result.scalars().all())

    async def get_statement(self, card_id: uuid.UUID, period: str) -> CreditCardStatement | None:
        result = await self.session.execute(
            select(CreditCardStatement).where(
                CreditCardStatement.credit_card_id == card_id,
                CreditCardStatement.billing_period == period,
            )
        )
        return result.scalars().first()

    async def get_statement_by_id(self, statement_id: uuid.UUID) -> CreditCardStatement | None:
        result = await self.session.execute(
            select(CreditCardStatement).where(CreditCardStatement.id == statement_id)
        )
        return result.scalar_one_or_none()

    async def get_statement_by_cycle_id(self, cycle_id: uuid.UUID) -> CreditCardStatement | None:
        result = await self.session.execute(
            select(CreditCardStatement).where(CreditCardStatement.cycle_id == cycle_id)
        )
        return result.scalar_one_or_none()

    async def get_statement_by_cycle_id_for_update(
        self, cycle_id: uuid.UUID
    ) -> CreditCardStatement | None:
        result = await self.session.execute(
            select(CreditCardStatement)
            .where(CreditCardStatement.cycle_id == cycle_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def add_statement(self, statement: CreditCardStatement) -> None:
        self.session.add(statement)
        await self.session.flush()

    async def save_statement(self, statement: CreditCardStatement) -> None:
        if statement.status in ("closed", "settled"):
            if statement in self.session.dirty:
                raise CreditStatementImmutableError(
                    message="Closed or settled statement snapshot is immutable and cannot be updated in place",
                    detail={"statement_id": str(statement.id), "status": statement.status},
                )
        self.session.add(statement)
        await self.session.flush()

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

    async def list_active_canonical_card_ids(
        self, user_id: uuid.UUID | None = None
    ) -> list[uuid.UUID]:
        """Queries eligible canonical card IDs without write locks.

        Used by batch processors to list candidate cards before locking each
        card independently in its own transaction.
        """
        stmt = (
            select(CreditCard.id)
            .where(
                CreditCard.is_active.is_(True),
                CreditCard.canonical_mode.is_(True),
                CreditCard.canonical_activated_at.is_not(None),
            )
            .order_by(CreditCard.created_at.asc(), CreditCard.id.asc())
        )
        if user_id is not None:
            stmt = stmt.where(CreditCard.user_id == user_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_active_canonical_cards_for_update(
        self, user_id: uuid.UUID | None = None
    ) -> list[CreditCard]:
        stmt = (
            select(CreditCard)
            .where(
                CreditCard.is_active.is_(True),
                CreditCard.canonical_mode.is_(True),
                CreditCard.canonical_activated_at.is_not(None),
            )
            .order_by(CreditCard.created_at.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user_id is not None:
            stmt = stmt.where(CreditCard.user_id == user_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_open_cycles_for_card_for_update(
        self, card_id: uuid.UUID
    ) -> list[CreditCardBillingCycle]:
        result = await self.session.execute(
            select(CreditCardBillingCycle)
            .where(
                CreditCardBillingCycle.credit_card_id == card_id,
                CreditCardBillingCycle.status == "open",
            )
            .order_by(CreditCardBillingCycle.billing_period.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def list_installments_for_period_for_update(
        self, card_id: uuid.UUID, billing_period: str
    ) -> list[CreditCardInstallment]:
        result = await self.session.execute(
            select(CreditCardInstallment)
            .where(
                CreditCardInstallment.credit_card_id == card_id,
                CreditCardInstallment.debt_partition == DEBT_PARTITION_CANONICAL,
                CreditCardInstallment.lifecycle_status == "active",
                CreditCardInstallment.scheduled_period == billing_period,
            )
            .order_by(
                CreditCardInstallment.installment_number.asc(),
                CreditCardInstallment.created_at.asc(),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def get_fact_for_statement(
        self, statement_id: uuid.UUID, fact_type: str
    ) -> CreditCardStatementFact | None:
        result = await self.session.execute(
            select(CreditCardStatementFact).where(
                CreditCardStatementFact.statement_id == statement_id,
                CreditCardStatementFact.fact_type == fact_type,
            )
        )
        return result.scalar_one_or_none()

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

    async def get_billing_cycle_by_period_for_update(
        self, card_id: uuid.UUID, billing_period: str
    ) -> CreditCardBillingCycle | None:
        result = await self.session.execute(
            select(CreditCardBillingCycle)
            .where(
                CreditCardBillingCycle.credit_card_id == card_id,
                CreditCardBillingCycle.billing_period == billing_period,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_billing_cycles_for_card(
        self, card_id: uuid.UUID
    ) -> list[CreditCardBillingCycle]:
        result = await self.session.execute(
            select(CreditCardBillingCycle)
            .where(CreditCardBillingCycle.credit_card_id == card_id)
            .order_by(CreditCardBillingCycle.billing_period.asc())
        )
        return list(result.scalars().all())

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

    async def add_payment_allocations(self, allocations: list[CreditCardPaymentAllocation]) -> None:
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

    async def add_statement_facts(self, facts: list[CreditCardStatementFact]) -> None:
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

    async def get_adjustment_by_id(self, adjustment_id: uuid.UUID) -> CreditCardAdjustment | None:
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

    async def get_previous_canonical_statement(
        self, card_id: uuid.UUID, current_period: str
    ) -> CreditCardStatement | None:
        """Finds the immediately preceding canonical closed statement for a card."""
        result = await self.session.execute(
            select(CreditCardStatement)
            .where(
                CreditCardStatement.credit_card_id == card_id,
                CreditCardStatement.debt_partition == DEBT_PARTITION_CANONICAL,
                CreditCardStatement.status == "closed",
                CreditCardStatement.billing_period < current_period,
            )
            .order_by(CreditCardStatement.billing_period.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def list_adjustments_for_cycle_for_update(
        self, card_id: uuid.UUID, start_date: date, cutoff_date: date
    ) -> list[CreditCardAdjustment]:
        """Queries posted canonical adjustments within the cycle boundaries with row locks."""
        start_dt = datetime.combine(start_date, time.min, tzinfo=UTC)
        cutoff_dt = datetime.combine(cutoff_date, time.max, tzinfo=UTC)
        result = await self.session.execute(
            select(CreditCardAdjustment)
            .where(
                CreditCardAdjustment.credit_card_id == card_id,
                CreditCardAdjustment.status == "posted",
                CreditCardAdjustment.occurred_at >= start_dt,
                CreditCardAdjustment.occurred_at <= cutoff_dt,
            )
            .order_by(CreditCardAdjustment.occurred_at.asc(), CreditCardAdjustment.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def list_payment_allocations_for_components(
        self, component_ids: list[uuid.UUID]
    ) -> list[CreditCardPaymentAllocation]:
        if not component_ids:
            return []
        result = await self.session.execute(
            select(CreditCardPaymentAllocation)
            .where(CreditCardPaymentAllocation.target_statement_component_id.in_(component_ids))
            .order_by(CreditCardPaymentAllocation.created_at.asc())
        )
        return list(result.scalars().all())

    async def list_payment_allocations_for_installments(
        self, installment_ids: list[uuid.UUID]
    ) -> list[CreditCardPaymentAllocation]:
        if not installment_ids:
            return []
        result = await self.session.execute(
            select(CreditCardPaymentAllocation)
            .where(CreditCardPaymentAllocation.target_installment_id.in_(installment_ids))
            .order_by(CreditCardPaymentAllocation.created_at.asc())
        )
        return list(result.scalars().all())

    async def list_payment_allocations_for_charges(
        self, charge_ids: list[uuid.UUID]
    ) -> list[CreditCardPaymentAllocation]:
        if not charge_ids:
            return []
        result = await self.session.execute(
            select(CreditCardPaymentAllocation)
            .where(CreditCardPaymentAllocation.target_charge_id.in_(charge_ids))
            .order_by(CreditCardPaymentAllocation.created_at.asc())
        )
        return list(result.scalars().all())

    async def list_adjustment_allocations_for_installments(
        self, installment_ids: list[uuid.UUID]
    ) -> list[CreditCardAdjustmentAllocation]:
        if not installment_ids:
            return []
        result = await self.session.execute(
            select(CreditCardAdjustmentAllocation)
            .where(CreditCardAdjustmentAllocation.target_installment_id.in_(installment_ids))
            .order_by(CreditCardAdjustmentAllocation.created_at.asc())
        )
        return list(result.scalars().all())

    async def list_statement_charges_for_statement(
        self, statement_id: uuid.UUID
    ) -> list[CreditCardStatementCharge]:
        result = await self.session.execute(
            select(CreditCardStatementCharge)
            .where(CreditCardStatementCharge.statement_id == statement_id)
            .order_by(CreditCardStatementCharge.created_at.asc())
        )
        return list(result.scalars().all())

    async def list_closed_canonical_periods_for_card(self, card_id: uuid.UUID) -> set[str]:
        """Returns the set of billing_period strings for cycles or statements that are already closed."""
        stmt_cycle = select(CreditCardBillingCycle.billing_period).where(
            CreditCardBillingCycle.credit_card_id == card_id,
            CreditCardBillingCycle.status.in_(["closed", "settled"]),
        )
        res_cycle = await self.session.execute(stmt_cycle)
        closed_periods = set(res_cycle.scalars().all())

        stmt_stmt = select(CreditCardStatement.billing_period).where(
            CreditCardStatement.credit_card_id == card_id,
            CreditCardStatement.debt_partition == DEBT_PARTITION_CANONICAL,
            CreditCardStatement.status == "closed",
        )
        res_stmt = await self.session.execute(stmt_stmt)
        closed_periods.update(res_stmt.scalars().all())
        return closed_periods

    async def list_payments_for_cycle_for_update(
        self, card_id: uuid.UUID, start_date: date, cutoff_date: date
    ) -> list[CreditCardPayment]:
        """Queries posted canonical payments within the cycle window with row locks."""
        result = await self.session.execute(
            select(CreditCardPayment)
            .where(
                CreditCardPayment.credit_card_id == card_id,
                CreditCardPayment.status == "posted",
                CreditCardPayment.payment_date >= start_date,
                CreditCardPayment.payment_date <= cutoff_date,
            )
            .order_by(CreditCardPayment.payment_date.asc(), CreditCardPayment.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())
