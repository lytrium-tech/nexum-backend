import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.financial_time import financial_date
from app.credit.exceptions import CreditStatementImmutableError
from app.credit.interest import ESTIMATOR_VERSION, PrincipalChange, estimate_interest
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
from app.credit.utils import get_cycle_dates_for_period


def _as_financial_date(val: date | datetime | None) -> date:
    """Safely convert date or aware/naive datetime to Bogota financial date."""
    if val is None:
        return financial_date()
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return financial_date(val)
    if isinstance(val, date):
        return val
    raise ValueError(f"Cannot convert {type(val)} to financial date")


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
        Unpaid statement charges inherit parent statement debt_partition:
        - Canonical statement charges (s.debt_partition = 'canonical') belong to canonical_debt.
        - Legacy/unknown statement charges (s.debt_partition != 'canonical' or NULL) belong to legacy_debt.
        Charges mirroring installments (sc.reference_id matching an installment) are excluded from both partitions.
        monthly_cc_payment reflects remaining canonical monthly obligations (net of credit adjustment
        allocations) plus isolated legacy monthly compatibility obligations.
        """
        query = text("""
            WITH inst AS (
                SELECT
                    COALESCE(SUM(CASE WHEN debt_partition = 'canonical' THEN COALESCE(opening_balance_amount, principal_amount) + COALESCE(assessed_interest_amount, interest_amount) - paid_amount ELSE 0 END), 0) as canonical_inst_debt,
                    COALESCE(SUM(CASE WHEN debt_partition != 'canonical' OR debt_partition IS NULL THEN principal_amount + interest_amount - paid_amount ELSE 0 END), 0) as legacy_debt,
                    COALESCE(SUM(CASE WHEN debt_partition = 'canonical' AND scheduled_period = to_char((now() AT TIME ZONE 'America/Bogota'), 'YYYY-MM') THEN COALESCE(opening_balance_amount, principal_amount) + COALESCE(assessed_interest_amount, interest_amount) - paid_amount ELSE 0 END), 0) as canonical_monthly,
                    COALESCE(SUM(CASE WHEN (debt_partition != 'canonical' OR debt_partition IS NULL) AND scheduled_period = to_char((now() AT TIME ZONE 'America/Bogota'), 'YYYY-MM') THEN principal_amount + interest_amount - paid_amount ELSE 0 END), 0) as legacy_monthly
                FROM credit_card_installments
                WHERE credit_card_id = :card_id AND status != 'paid'
            ),
            chg AS (
                SELECT
                    COALESCE(SUM(CASE WHEN s.debt_partition = 'canonical' THEN sc.amount - sc.paid_amount ELSE 0 END), 0) as canonical_charges_debt,
                    COALESCE(SUM(CASE WHEN s.debt_partition != 'canonical' OR s.debt_partition IS NULL THEN sc.amount - sc.paid_amount ELSE 0 END), 0) as legacy_charges_debt
                FROM credit_card_statement_charges sc
                JOIN credit_card_statements s ON sc.statement_id = s.id
                WHERE s.credit_card_id = :card_id
                  AND sc.status != 'paid'
                  AND (sc.reference_id IS NULL OR sc.reference_id NOT IN (
                      SELECT id FROM credit_card_installments WHERE credit_card_id = :card_id
                  ))
            ),
            adj AS (
                SELECT
                    COALESCE(SUM(CASE WHEN a.direction = 'credit' THEN aa.allocated_amount ELSE -aa.allocated_amount END), 0) as net_adj_credit,
                    COALESCE(SUM(CASE WHEN a.direction = 'credit' AND i.scheduled_period = to_char((now() AT TIME ZONE 'America/Bogota'), 'YYYY-MM') THEN aa.allocated_amount ELSE 0 END), 0) as monthly_adj_credit
                FROM credit_card_adjustment_allocations aa
                JOIN credit_card_adjustments a ON aa.adjustment_id = a.id
                LEFT JOIN credit_card_installments i ON aa.target_installment_id = i.id
                WHERE aa.credit_card_id = :card_id AND a.status = 'posted'
            )
            SELECT
                inst.canonical_inst_debt + chg.canonical_charges_debt - adj.net_adj_credit as canonical_debt,
                inst.legacy_debt + chg.legacy_charges_debt as legacy_debt,
                inst.canonical_inst_debt + chg.canonical_charges_debt - adj.net_adj_credit + inst.legacy_debt + chg.legacy_charges_debt as total_debt,
                inst.canonical_monthly - adj.monthly_adj_credit + inst.legacy_monthly as monthly_cc_payment
            FROM inst, chg, adj
        """)
        result = await self.session.execute(query, {"card_id": card_id})
        row = result.fetchone()
        if row:
            c_debt = Decimal(str(row[0]))
            l_debt = Decimal(str(row[1]))
            tot_debt = Decimal(str(row[2]))
            monthly = Decimal(str(row[3]))
            return {
                "canonical_debt": c_debt,
                "legacy_debt": l_debt,
                "total_debt": tot_debt,
                "monthly_cc_payment": monthly,
            }
        return {
            "canonical_debt": Decimal("0.00"),
            "legacy_debt": Decimal("0.00"),
            "total_debt": Decimal("0.00"),
            "monthly_cc_payment": Decimal("0.00"),
        }

    async def get_card_financial_state(self, card: CreditCard) -> dict[str, Decimal]:
        """Return partitioned modeled debt and available credit in the card currency."""
        debt = await self.get_card_debt_partitioned(card.id)
        return {
            **debt,
            "modeled_debt": debt["total_debt"],
            "available_credit": card.credit_limit - debt["total_debt"],
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
                CreditCardInstallment.created_at.asc(),
                CreditCardInstallment.id.asc(),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def list_installments_for_transaction(
        self, transaction_id: uuid.UUID
    ) -> list[CreditCardInstallment]:
        result = await self.session.execute(
            select(CreditCardInstallment)
            .where(CreditCardInstallment.purchase_transaction_id == transaction_id)
            .order_by(
                CreditCardInstallment.installment_number.asc(),
                CreditCardInstallment.created_at.asc(),
                CreditCardInstallment.id.asc(),
            )
        )
        return list(result.scalars().all())

    async def get_transaction_by_id_for_update(
        self, transaction_id: uuid.UUID
    ) -> CreditCardTransaction | None:
        result = await self.session.execute(
            select(CreditCardTransaction)
            .where(CreditCardTransaction.id == transaction_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_payment_by_transaction_id(
        self, transaction_id: uuid.UUID
    ) -> CreditCardPayment | None:
        result = await self.session.execute(
            select(CreditCardPayment).where(
                CreditCardPayment.payment_transaction_id == transaction_id
            )
        )
        return result.scalar_one_or_none()

    async def get_payment_by_event_for_user(
        self, event_id: uuid.UUID, user_id: uuid.UUID
    ) -> CreditCardPayment | None:
        result = await self.session.execute(
            select(CreditCardPayment)
            .join(CreditCard, CreditCard.id == CreditCardPayment.credit_card_id)
            .where(
                CreditCardPayment.event_id == event_id,
                CreditCardPayment.user_id == user_id,
                CreditCard.user_id == user_id,
            )
            .execution_options(autoflush=False)
        )
        return result.scalar_one_or_none()

    async def list_adjustments_for_transaction(
        self, transaction_id: uuid.UUID
    ) -> list[CreditCardAdjustment]:
        result = await self.session.execute(
            select(CreditCardAdjustment)
            .where(CreditCardAdjustment.original_transaction_id == transaction_id)
            .order_by(CreditCardAdjustment.occurred_at.asc(), CreditCardAdjustment.id.asc())
        )
        return list(result.scalars().all())

    async def get_adjustment_by_event(self, event_id: uuid.UUID) -> CreditCardAdjustment | None:
        result = await self.session.execute(
            select(CreditCardAdjustment).where(CreditCardAdjustment.event_id == event_id)
        )
        return result.scalar_one_or_none()

    async def get_adjustment_by_event_for_user(
        self, event_id: uuid.UUID, user_id: uuid.UUID
    ) -> CreditCardAdjustment | None:
        result = await self.session.execute(
            select(CreditCardAdjustment)
            .join(CreditCard, CreditCard.id == CreditCardAdjustment.credit_card_id)
            .where(
                CreditCardAdjustment.event_id == event_id,
                CreditCardAdjustment.user_id == user_id,
            )
            .where(CreditCard.user_id == user_id)
            .execution_options(autoflush=False)
        )
        return result.scalar_one_or_none()

    async def list_credit_adjustment_allocations_for_installment(
        self, installment_id: uuid.UUID
    ) -> list[CreditCardAdjustmentAllocation]:
        result = await self.session.execute(
            select(CreditCardAdjustmentAllocation)
            .join(
                CreditCardAdjustment,
                CreditCardAdjustmentAllocation.adjustment_id == CreditCardAdjustment.id,
            )
            .where(
                CreditCardAdjustmentAllocation.target_installment_id == installment_id,
                CreditCardAdjustment.status == "posted",
                CreditCardAdjustment.direction == "credit",
            )
        )
        return list(result.scalars().all())

    async def list_credit_adjustment_allocations_for_charge(
        self, charge_id: uuid.UUID
    ) -> list[CreditCardAdjustmentAllocation]:
        result = await self.session.execute(
            select(CreditCardAdjustmentAllocation)
            .join(
                CreditCardAdjustment,
                CreditCardAdjustmentAllocation.adjustment_id == CreditCardAdjustment.id,
            )
            .where(
                CreditCardAdjustmentAllocation.target_charge_id == charge_id,
                CreditCardAdjustment.status == "posted",
                CreditCardAdjustment.direction == "credit",
            )
        )
        return list(result.scalars().all())

    async def get_charge_by_id_for_update(
        self, charge_id: uuid.UUID
    ) -> CreditCardStatementCharge | None:
        result = await self.session.execute(
            select(CreditCardStatementCharge)
            .where(CreditCardStatementCharge.id == charge_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_installment_by_id_for_update(
        self, installment_id: uuid.UUID
    ) -> CreditCardInstallment | None:
        result = await self.session.execute(
            select(CreditCardInstallment)
            .where(CreditCardInstallment.id == installment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_installment_by_id(
        self, installment_id: uuid.UUID
    ) -> CreditCardInstallment | None:
        result = await self.session.execute(
            select(CreditCardInstallment).where(CreditCardInstallment.id == installment_id)
        )
        return result.scalar_one_or_none()

    async def get_transaction_by_event(self, event_id: uuid.UUID) -> CreditCardTransaction | None:
        result = await self.session.execute(
            select(CreditCardTransaction).where(CreditCardTransaction.event_id == event_id)
        )
        return result.scalar_one_or_none()

    async def get_transaction_by_event_for_user(
        self, event_id: uuid.UUID, user_id: uuid.UUID
    ) -> CreditCardTransaction | None:
        result = await self.session.execute(
            select(CreditCardTransaction)
            .join(CreditCard, CreditCard.id == CreditCardTransaction.credit_card_id)
            .where(
                CreditCardTransaction.event_id == event_id,
                CreditCardTransaction.user_id == user_id,
                CreditCard.user_id == user_id,
            )
            .execution_options(autoflush=False)
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

    async def get_early_payment_by_event_for_user(self, event_id: uuid.UUID, user_id: uuid.UUID):
        from app.credit.models import CreditCardEarlyPayment

        result = await self.session.execute(
            select(CreditCardEarlyPayment)
            .join(CreditCard, CreditCard.id == CreditCardEarlyPayment.credit_card_id)
            .where(
                CreditCardEarlyPayment.event_id == event_id,
                CreditCardEarlyPayment.user_id == user_id,
                CreditCard.user_id == user_id,
            )
            .execution_options(autoflush=False)
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
        projection = await self.get_next_payment_projection(card_id)
        return projection["estimated_total"] if projection else Decimal("0.00")

    async def get_installment_settlement(
        self, installment_id: uuid.UUID
    ) -> tuple[Decimal, Decimal, Decimal]:
        result = await self.session.execute(
            text("""
            SELECT
                COALESCE((SELECT SUM(a.allocated_principal) FROM credit_card_payment_allocations a
                    JOIN credit_card_payments p ON p.id = a.payment_id
                    WHERE a.target_installment_id = :id AND p.status = 'posted'), 0),
                COALESCE((SELECT SUM(a.allocated_interest) FROM credit_card_payment_allocations a
                    JOIN credit_card_payments p ON p.id = a.payment_id
                    WHERE a.target_installment_id = :id AND p.status = 'posted'), 0),
                COALESCE((SELECT SUM(a.allocated_principal) FROM credit_card_adjustment_allocations a
                    JOIN credit_card_adjustments j ON j.id = a.adjustment_id
                    WHERE a.target_installment_id = :id AND j.status = 'posted' AND j.direction = 'credit'), 0)
        """),
            {"id": installment_id},
        )
        return tuple(Decimal(str(v)) for v in result.one())

    async def get_card_principal_changes(
        self, card_id: uuid.UUID, transaction_id: uuid.UUID | None = None
    ) -> list[PrincipalChange]:
        """Fetch all canonical dated financial facts that alter interest-bearing principal."""
        changes: list[PrincipalChange] = []

        # 1. Purchases & Opening Balances
        tx_query = text("""
            SELECT id, type, occurred_at, COALESCE(booked_amount, amount) AS amount,
                   zero_interest, metadata, lifecycle_status
            FROM credit_card_transactions
            WHERE credit_card_id = :card_id
              AND lifecycle_status IN ('posted', 'reversed')
              AND type IN ('purchase', 'opening_balance')
              AND (CAST(:tx_id AS uuid) IS NULL OR id = :tx_id)
            ORDER BY occurred_at ASC, created_at ASC
        """)
        tx_result = await self.session.execute(
            tx_query, {"card_id": card_id, "tx_id": transaction_id}
        )
        for row in tx_result.fetchall():
            tx_id, tx_type, occurred_at, amount, zero_interest, meta, _lifecycle = row
            eff_date = _as_financial_date(occurred_at)
            amount_dec = Decimal(str(amount or 0))
            if tx_type == "opening_balance":
                meta_dict = meta if isinstance(meta, dict) else {}
                assumption = meta_dict.get("interest_projection_assumption")
                if meta_dict.get("composition_known") is True:
                    principal_amt = meta_dict.get("principal_amount")
                    amount_dec = (
                        Decimal(str(principal_amt))
                        if principal_amt is not None
                        else Decimal("0.00")
                    )
                elif assumption in (
                    "unknown_composition_treated_as_interest_bearing",
                    "opening_balance_treated_as_interest_bearing_principal",
                ):
                    amount_dec = amount_dec
                else:
                    amount_dec = Decimal("0.00")
                if amount_dec > Decimal("0.00"):
                    changes.append(
                        PrincipalChange(
                            effective_date=eff_date,
                            amount=amount_dec,
                            zero_interest=False,
                            event_type="opening_balance",
                            order_priority=10,
                            identifier=str(tx_id),
                        )
                    )
            else:
                changes.append(
                    PrincipalChange(
                        effective_date=eff_date,
                        amount=amount_dec,
                        zero_interest=bool(zero_interest),
                        event_type="purchase",
                        order_priority=20,
                        identifier=str(tx_id),
                    )
                )

        # 2. Payment allocations to principal (posted & reversed)
        pay_query = text("""
            SELECT pa.id, p.payment_date, pa.allocated_principal, p.status,
                   rev.occurred_at AS reversal_occurred_at
            FROM credit_card_payment_allocations pa
            JOIN credit_card_payments p ON p.id = pa.payment_id
            JOIN credit_card_installments i ON i.id = pa.target_installment_id
            JOIN credit_card_transactions t ON t.id = i.purchase_transaction_id
            LEFT JOIN credit_card_adjustments rev ON rev.original_transaction_id = p.payment_transaction_id
                 AND rev.adjustment_type = 'reversal'
                 AND rev.direction = 'debit'
                 AND rev.status = 'posted'
            WHERE pa.credit_card_id = :card_id
              AND pa.allocated_principal > 0
              AND p.status IN ('posted', 'reversed')
              AND COALESCE(t.zero_interest, false) IS FALSE
              AND (CAST(:tx_id AS uuid) IS NULL OR i.purchase_transaction_id = :tx_id)
            ORDER BY p.payment_date ASC, pa.allocation_order ASC
        """)
        pay_result = await self.session.execute(
            pay_query, {"card_id": card_id, "tx_id": transaction_id}
        )
        for row in pay_result.fetchall():
            pa_id, p_date, alloc_princ, p_status, rev_occurred_at = row
            p_eff_date = _as_financial_date(p_date)
            princ_amt = Decimal(str(alloc_princ))
            # Historical reduction on payment date
            changes.append(
                PrincipalChange(
                    effective_date=p_eff_date,
                    amount=-princ_amt,
                    zero_interest=False,
                    event_type="payment",
                    order_priority=40,
                    identifier=str(pa_id),
                )
            )
            # If reversed, restoration on reversal date (actual compensating fact required, no fallback!)
            if p_status == "reversed" and rev_occurred_at is not None:
                rev_date = _as_financial_date(rev_occurred_at)
                changes.append(
                    PrincipalChange(
                        effective_date=rev_date,
                        amount=princ_amt,
                        zero_interest=False,
                        event_type="reversal",
                        order_priority=30,
                        identifier=f"{pa_id}_reversal",
                    )
                )

        # 3. Refund allocations to principal (posted & reversed)
        adj_query = text("""
            SELECT aa.id, a.id AS adjustment_id, a.occurred_at, a.status,
                   aa.allocated_principal,
                   rev.occurred_at AS rev_occurred_at
            FROM credit_card_adjustment_allocations aa
            JOIN credit_card_adjustments a ON a.id = aa.adjustment_id
            JOIN credit_card_installments i ON i.id = aa.target_installment_id
            JOIN credit_card_transactions t ON t.id = i.purchase_transaction_id
            LEFT JOIN credit_card_adjustments rev ON rev.reversal_of_adjustment_id = a.id
                 AND rev.adjustment_type = 'reversal'
                 AND rev.direction = 'debit'
                 AND rev.status = 'posted'
            WHERE aa.credit_card_id = :card_id
              AND a.direction = 'credit'
              AND a.status IN ('posted', 'reversed')
              AND aa.allocated_principal > 0
              AND COALESCE(t.zero_interest, false) IS FALSE
              AND (CAST(:tx_id AS uuid) IS NULL OR i.purchase_transaction_id = :tx_id)
            ORDER BY a.occurred_at ASC, aa.allocation_order ASC
        """)
        adj_result = await self.session.execute(
            adj_query, {"card_id": card_id, "tx_id": transaction_id}
        )
        for row in adj_result.fetchall():
            aa_id, _adj_id, occurred_at, a_status, alloc_princ, rev_occurred_at = row
            eff_date = _as_financial_date(occurred_at)
            princ_amt = Decimal(str(alloc_princ))
            # Reduction on refund date
            changes.append(
                PrincipalChange(
                    effective_date=eff_date,
                    amount=-princ_amt,
                    zero_interest=False,
                    event_type="refund",
                    order_priority=50,
                    identifier=str(aa_id),
                )
            )
            # If reversed, restoration on reversal date (actual compensating fact required, no fallback!)
            if (
                a_status == "reversed" or rev_occurred_at is not None
            ) and rev_occurred_at is not None:
                rev_date = _as_financial_date(rev_occurred_at)
                changes.append(
                    PrincipalChange(
                        effective_date=rev_date,
                        amount=princ_amt,
                        zero_interest=False,
                        event_type="reversal",
                        order_priority=30,
                        identifier=f"{aa_id}_reversal",
                    )
                )

        return changes

    async def get_next_payment_projection(self, card_id: uuid.UUID) -> dict | None:
        query = text("""
            WITH inst_alloc AS (
                SELECT pa.target_installment_id,
                       SUM(pa.allocated_principal) AS principal_paid,
                       SUM(pa.allocated_interest) AS interest_paid
                FROM credit_card_payment_allocations pa
                JOIN credit_card_payments p ON p.id = pa.payment_id
                WHERE pa.target_installment_id IS NOT NULL
                  AND p.status = 'posted'
                GROUP BY pa.target_installment_id
            ), inst_adj AS (
                SELECT aa.target_installment_id,
                       SUM(aa.allocated_principal) AS principal_adj
                FROM credit_card_adjustment_allocations aa
                JOIN credit_card_adjustments a ON a.id = aa.adjustment_id
                WHERE aa.target_installment_id IS NOT NULL
                  AND a.status = 'posted' AND a.direction = 'credit'
                GROUP BY aa.target_installment_id
            ), charge_alloc AS (
                SELECT pa.target_charge_id, SUM(pa.allocated_fees) AS fees_paid
                FROM credit_card_payment_allocations pa
                JOIN credit_card_payments p ON p.id = pa.payment_id
                WHERE pa.target_charge_id IS NOT NULL
                  AND p.status = 'posted'
                GROUP BY pa.target_charge_id
            ), obligations AS (
                SELECT COALESCE(i.scheduled_due_date, to_date(i.scheduled_period || '-01', 'YYYY-MM-DD')) AS due_date,
                       GREATEST(0, COALESCE(i.opening_balance_amount, i.principal_amount) - COALESCE(a.principal_paid, i.paid_amount, 0) - COALESCE(adj.principal_adj, 0)) AS principal,
                       GREATEST(0, COALESCE(i.assessed_interest_amount, i.interest_amount) - COALESCE(a.interest_paid, 0)) AS assessed,
                       0::numeric AS charges,
                       i.provenance,
                       (t.type = 'opening_balance' AND (t.metadata->>'composition_known' = 'false' OR t.metadata->>'composition_known' IS NULL)) AS composition_unknown
                FROM credit_card_installments i
                LEFT JOIN inst_alloc a ON a.target_installment_id = i.id
                LEFT JOIN inst_adj adj ON adj.target_installment_id = i.id
                JOIN credit_card_transactions t ON t.id = i.purchase_transaction_id
                WHERE i.credit_card_id = :card_id
                  AND i.status NOT IN ('paid', 'cancelled') AND i.lifecycle_status = 'active'
                UNION ALL
                SELECT s.due_date,
                       0::numeric, 0::numeric,
                       GREATEST(0, c.amount - COALESCE(a.fees_paid, c.paid_amount, 0)),
                       s.provenance, false
                FROM credit_card_statement_charges c
                JOIN credit_card_statements s ON s.id = c.statement_id
                LEFT JOIN charge_alloc a ON a.target_charge_id = c.id
                WHERE s.credit_card_id = :card_id AND c.status != 'paid'
                  AND (c.reference_id IS NULL OR c.reference_id NOT IN (
                      SELECT id FROM credit_card_installments WHERE credit_card_id = :card_id
                  ))
            ), dated AS (
                SELECT due_date,
                       SUM(principal) AS principal,
                       SUM(assessed) AS assessed,
                       SUM(charges) AS charges,
                       MIN(provenance) AS provenance,
                       BOOL_OR(composition_unknown) AS composition_unknown
                FROM obligations WHERE due_date IS NOT NULL
                GROUP BY due_date
                HAVING SUM(principal + assessed + charges) > 0
            )
            SELECT due_date, principal, assessed, charges, provenance, composition_unknown
            FROM dated
            ORDER BY due_date ASC
        """)
        result = await self.session.execute(query, {"card_id": card_id})

        if hasattr(result, "fetchall"):
            rows = result.fetchall()
        elif hasattr(result, "fetchone"):
            single = result.fetchone()
            rows = [single] if single is not None else []
        else:
            rows = []

        if not rows:
            return None

        today = financial_date()

        # Multi-row result: apply 3-tier chronological selection
        overdue = [r for r in rows if r[0] < today]
        due_today = [r for r in rows if r[0] == today]
        upcoming = [r for r in rows if r[0] > today]

        if overdue:
            selected = min(overdue, key=lambda r: r[0])
            status = "overdue"
        elif due_today:
            selected = due_today[0]
            status = "due_today"
        elif upcoming:
            selected = min(upcoming, key=lambda r: r[0])
            status = "upcoming"
        else:
            return None

        due_date, principal, assessed, charges, prov, comp_unknown = selected
        scheduled_principal = Decimal(str(principal or 0))
        assessed_interest = Decimal(str(assessed or 0))
        known_charges = Decimal(str(charges or 0))

        # Reconstruct estimated interest ON READ using canonical timeline
        card = await self.get_by_id(card_id)
        rate = (
            card.monthly_interest_rate_pct
            if card and hasattr(card, "monthly_interest_rate_pct")
            else getattr(card, "monthly_interest_rate", None)
        ) or Decimal("0.00")

        estimated_interest = Decimal("0.00")
        if rate > Decimal("0.00") and card and card.cutoff_day and card.due_day:
            if card.due_day > card.cutoff_day:
                c_year, c_month = due_date.year, due_date.month
            else:
                c_month = 12 if due_date.month == 1 else due_date.month - 1
                c_year = due_date.year - 1 if due_date.month == 1 else due_date.year
            start_date, _, _ = get_cycle_dates_for_period(
                c_year, c_month, card.cutoff_day, card.due_day
            )
            period_start = start_date
            period_end = due_date
            if period_end > period_start:
                changes = await self.get_card_principal_changes(card_id)
                estimated_interest = estimate_interest(changes, rate, period_start, period_end)

        estimated_total = (
            scheduled_principal + assessed_interest + estimated_interest + known_charges
        )
        return {
            "due_date": due_date,
            "status": status,
            "scheduled_principal": scheduled_principal,
            "assessed_interest": assessed_interest,
            "estimated_interest": estimated_interest,
            "known_charges": known_charges,
            "estimated_total": estimated_total,
            "provenance": (
                "nexum_estimated"
                if estimated_interest > Decimal("0.00")
                else str(prov or "manual_input")
            ),
            "confidence": "low" if comp_unknown else "medium",
            "estimator_version": ESTIMATOR_VERSION
            if estimated_interest > Decimal("0.00")
            else None,
        }

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

    async def list_unpaid_statement_charges_for_update(
        self, card_id: uuid.UUID
    ) -> list[CreditCardStatementCharge]:
        subq_inst_ids = (
            select(CreditCardInstallment.id)
            .where(CreditCardInstallment.credit_card_id == card_id)
            .scalar_subquery()
        )
        result = await self.session.execute(
            select(CreditCardStatementCharge)
            .join(CreditCardStatement)
            .where(
                CreditCardStatement.credit_card_id == card_id,
                CreditCardStatement.debt_partition == "canonical",
                CreditCardStatementCharge.status != "paid",
                (CreditCardStatementCharge.reference_id.is_(None))
                | (CreditCardStatementCharge.reference_id.notin_(subq_inst_ids)),
            )
            .order_by(
                CreditCardStatementCharge.created_at.asc(), CreditCardStatementCharge.id.asc()
            )
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
            .order_by(
                CreditCardStatementCharge.created_at.asc(), CreditCardStatementCharge.id.asc()
            )
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
