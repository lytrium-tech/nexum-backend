"""CreditCycleClosureService: Scheduled and idempotent cycle closure with immutable statements.

Implements Credit Goal 2 Phase C:
- Determines eligible canonical billing cycles as of financial_date() (America/Bogota).
- Closes eligible canonical cycles exactly once and materializes immutable statement snapshots.
- Finite, bounded catch-up from the canonical activation frontier; repeated runs advance deterministically
  over unclosed eligible periods until has_remaining_catchup=False.
- Conservation: statement balance reconciles exclusively from typed immutable components under a single sign rule:
    statement_balance = sum(debit component magnitudes) - sum(credit component magnitudes)
- Auditable timeline accounting:
    1. Prior balance at cycle start reflects only allocations/effects effective before that cycle start (start_date).
    2. Adjustments within the current cycle appear exactly once as current-cycle credit/debit components (no double counting).
    3. Payments/adjustments after cutoff do not affect the closing snapshot.
    4. Payments received strictly equals actual payments within the statement window, never conflated with refunds.
- Unknown minimum payment remains None; estimated interest is strictly excluded from components and balance.
- Batch processing uses lock-free candidate discovery and per-card transaction isolation.
- Idempotent under retries and serialized under card-level row locking.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.financial_time import financial_date
from app.credit.exceptions import (
    CreditCardInactiveError,
    CreditCardNotFoundError,
)
from app.credit.models import (
    DEBT_PARTITION_CANONICAL,
    PROVENANCE_NEXUM_MODELED,
    CreditCard,
    CreditCardBillingCycle,
    CreditCardStatement,
    CreditCardStatementComponent,
)
from app.credit.repository import CreditCardRepository
from app.credit.schemas import CreditCardCycleClosureSummary
from app.credit.utils import determine_cycle_for_date, get_cycle_dates_for_period

logger = structlog.get_logger(__name__)


class CreditCycleClosureService:
    def __init__(
        self,
        session: AsyncSession,
        repo: CreditCardRepository | None = None,
    ) -> None:
        self.session = session
        self.repo = repo or getattr(session, "repo", None) or CreditCardRepository(session)

    def _get_financial_date(self, target_dt: datetime | None = None) -> date:
        """Returns the canonical financial date in America/Bogota."""
        return financial_date(target_dt)

    async def close_all_eligible_cycles(
        self,
        as_of_date: date | None = None,
        batch_size: int = 50,
        user_id: uuid.UUID | None = None,
        card_id: uuid.UUID | None = None,
        session_factory: Callable[[], AsyncSession] | None = None,
        max_cycles_per_card: int = 24,
    ) -> CreditCardCycleClosureSummary:
        """Batch closure of eligible canonical billing cycles across cards.

        Uses lock-free discovery of candidate cards and isolates each card's
        processing in its own transaction when session_factory is provided.
        One card failure never rolls back prior successful cards.
        """
        target_date = as_of_date or self._get_financial_date()
        summary = CreditCardCycleClosureSummary(as_of_date=target_date)

        logger.info(
            "credit_cycle_closure_batch_started",
            as_of_date=target_date.isoformat(),
            batch_size=batch_size,
            user_id=str(user_id) if user_id else None,
            card_id=str(card_id) if card_id else None,
        )

        # 1. Lock-free card ID discovery (no write locks held across cards)
        if card_id:
            card_ids = [card_id]
        else:
            card_ids = await self.repo.list_active_canonical_card_ids(user_id)

        summary.cards_inspected = len(card_ids)
        cards_batch = card_ids[:batch_size]

        # Multi-card safety: reject running multiple cards in a single shared session
        # without session_factory to prevent partial rollback corruption and stale ORM state.
        if session_factory is None and len(cards_batch) > 1:
            raise ValueError(
                "session_factory is required for multi-card batch cycle closure to guarantee "
                "independent per-card transaction isolation and prevent partial rollback corruption."
            )

        for cid in cards_batch:
            card_summary = {
                "card_id": str(cid),
                "cycles_closed": 0,
                "statements_created": 0,
                "statements_skipped": 0,
                "components_created": 0,
                "has_remaining_catchup": False,
                "remaining_catchup_cycles": 0,
                "error": None,
            }
            try:
                if session_factory is not None:
                    async with session_factory() as per_card_session:
                        per_card_service = CreditCycleClosureService(per_card_session)
                        (
                            stmts,
                            comps,
                            skipped,
                            has_remaining,
                            remaining_count,
                        ) = await per_card_service.close_eligible_cycles_for_card(
                            cid, target_date, max_cycles_per_card=max_cycles_per_card
                        )
                        await per_card_session.commit()
                else:
                    (
                        stmts,
                        comps,
                        skipped,
                        has_remaining,
                        remaining_count,
                    ) = await self.close_eligible_cycles_for_card(
                        cid, target_date, max_cycles_per_card=max_cycles_per_card
                    )
                    await self.session.flush()

                closed_count = len(stmts)
                created_stmts = closed_count
                comp_count = len(comps)

                summary.cycles_closed += closed_count
                summary.cycles_eligible += closed_count + remaining_count
                summary.statements_created += created_stmts
                summary.statements_skipped += skipped
                summary.components_created += comp_count
                if has_remaining:
                    summary.has_remaining_catchup = True
                    summary.remaining_catchup_cycles += remaining_count

                card_summary["cycles_closed"] = closed_count
                card_summary["statements_created"] = created_stmts
                card_summary["statements_skipped"] = skipped
                card_summary["components_created"] = comp_count
                card_summary["has_remaining_catchup"] = has_remaining
                card_summary["remaining_catchup_cycles"] = remaining_count

            except Exception as exc:
                logger.error(
                    "credit_cycle_closure_card_failed",
                    card_id=str(cid),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                summary.technical_failures += 1
                card_summary["error"] = type(exc).__name__
                if session_factory is None:
                    await self.session.rollback()

            summary.card_summaries.append(card_summary)

        logger.info(
            "credit_cycle_closure_batch_completed",
            as_of_date=target_date.isoformat(),
            cards_inspected=summary.cards_inspected,
            cycles_closed=summary.cycles_closed,
            statements_created=summary.statements_created,
            statements_skipped=summary.statements_skipped,
            components_created=summary.components_created,
            has_remaining_catchup=summary.has_remaining_catchup,
            remaining_catchup_cycles=summary.remaining_catchup_cycles,
            technical_failures=summary.technical_failures,
        )
        return summary

    async def close_eligible_cycles_for_card(
        self,
        card_id: uuid.UUID,
        as_of_date: date | None = None,
        max_cycles_per_card: int = 24,
    ) -> tuple[list[CreditCardStatement], list[CreditCardStatementComponent], int, bool, int]:
        """Closes all eligible canonical cycles for a single card up to as_of_date.

        Builds candidates from unclosed eligible periods, allowing repeated invocations
        to advance forward deterministically until has_remaining_catchup=False.
        Returns:
            (created_statements, created_components, skipped_cycles_count, has_remaining_catchup, remaining_catchup_cycles)
        """
        target_date = as_of_date or self._get_financial_date()
        # Acquire card lock
        card = await self.repo.get_by_id_for_update(card_id)
        if not card:
            raise CreditCardNotFoundError()
        if not card.is_active:
            raise CreditCardInactiveError()

        # Catch-up / frontier guard: Only cards in canonical_mode with canonical_activated_at
        # are eligible for canonical cycle closure. Never walk backward or synthesize legacy cycles.
        if not card.canonical_mode or not card.canonical_activated_at:
            logger.info(
                "credit_card_skipped_non_canonical",
                card_id=str(card.id),
                canonical_mode=card.canonical_mode,
            )
            return [], [], 0, False, 0

        # Determine activation boundary
        activation_date = self._get_financial_date(card.canonical_activated_at)
        start_period, _, _, _ = determine_cycle_for_date(
            activation_date, card.cutoff_day, card.due_day
        )
        start_year, start_month = int(start_period[:4]), int(start_period[5:7])

        # Generate all eligible periods whose cutoff date has elapsed as of target_date
        cur_year, cur_month = start_year, start_month
        max_year, max_month = target_date.year, target_date.month

        eligible_periods: list[tuple[str, date, date, date]] = []
        while (cur_year, cur_month) <= (max_year, max_month):
            c_start, c_cutoff, c_due = get_cycle_dates_for_period(
                cur_year, cur_month, card.cutoff_day, card.due_day
            )

            # Cycle is eligible ONLY if cutoff_date has strictly passed as of target_date
            if c_cutoff >= target_date:
                # Cutoff has not elapsed yet; this and subsequent cycles remain open
                break

            period_str = f"{cur_year:04d}-{cur_month:02d}"
            eligible_periods.append((period_str, c_start, c_cutoff, c_due))

            # Advance to next month
            if cur_month == 12:
                cur_year += 1
                cur_month = 1
            else:
                cur_month += 1

        # Query already closed periods so that catch-up progresses across batches
        closed_periods = await self.repo.list_closed_canonical_periods_for_card(card.id)

        # Retain only genuinely unclosed eligible periods
        unclosed_eligible = [p for p in eligible_periods if p[0] not in closed_periods]
        total_unclosed = len(unclosed_eligible)

        if total_unclosed == 0:
            return [], [], len(eligible_periods), False, 0

        periods_to_process = unclosed_eligible[:max_cycles_per_card]
        remaining_catchup_cycles = max(0, total_unclosed - len(periods_to_process))
        has_remaining_catchup = remaining_catchup_cycles > 0

        created_statements: list[CreditCardStatement] = []
        created_components: list[CreditCardStatementComponent] = []
        skipped_count = 0

        for period_str, c_start, c_cutoff, c_due in periods_to_process:
            stmt, comps, was_closed = await self._close_single_cycle(
                card=card,
                period_str=period_str,
                start_date=c_start,
                cutoff_date=c_cutoff,
                due_date=c_due,
            )

            if was_closed:
                created_statements.append(stmt)
                created_components.extend(comps)
            else:
                skipped_count += 1

        return (
            created_statements,
            created_components,
            skipped_count,
            has_remaining_catchup,
            remaining_catchup_cycles,
        )

    async def _close_single_cycle(
        self,
        card: CreditCard,
        period_str: str,
        start_date: date,
        cutoff_date: date,
        due_date: date,
    ) -> tuple[CreditCardStatement | None, list[CreditCardStatementComponent], bool]:
        """Closes a single billing cycle and generates its immutable statement snapshot.

        Reconciles strictly from typed components under single sign rule:
        statement_balance = sum(debit component magnitudes) - sum(credit component magnitudes)

        Temporal rollover invariants:
        - A closed statement's statement_balance is already the net snapshot through its cutoff.
        - Next cycle begins the day after cutoff; prior_balance (debit) or prior_credit (credit)
          rolls over the previous net balance exactly once without double counting allocations.
        - Payments/refunds/adjustments whose effective date is in the new cycle window [start_date, cutoff_date]
          appear once as new-cycle components and reduce/increase the statement balance.
        - Facts occurring after cutoff do not affect this closing snapshot.
        - Non-negative component magnitudes: negative prior balance carries explicitly as prior_credit.

        Returns (statement, components, was_newly_closed).
        """
        # 1. Lock billing cycle row
        cycle = await self.repo.get_billing_cycle_by_period_for_update(card.id, period_str)
        if not cycle:
            # Cycle did not exist yet (e.g. zero activity); materialize canonically
            cycle = CreditCardBillingCycle(
                id=uuid.uuid4(),
                user_id=card.user_id,
                credit_card_id=card.id,
                billing_period=period_str,
                start_date=start_date,
                cutoff_date=cutoff_date,
                due_date=due_date,
                status="open",
                provenance=PROVENANCE_NEXUM_MODELED,
            )
            await self.repo.add_billing_cycle(cycle)

        # Idempotency check: if cycle already closed, return existing statement snapshot
        if cycle.status in ("closed", "settled"):
            existing_statement = await self.repo.get_statement(card.id, period_str)
            if existing_statement and existing_statement.status == "closed":
                logger.info(
                    "credit_cycle_already_closed",
                    card_id=str(card.id),
                    billing_period=period_str,
                    cycle_id=str(cycle.id),
                )
                return existing_statement, [], False

        statement_id = uuid.uuid4()
        now_utc = datetime.now(UTC)

        debit_components: list[CreditCardStatementComponent] = []
        credit_components: list[CreditCardStatementComponent] = []

        # 2. Prior balance component (Canonical net snapshot from previous statement)
        previous_balance_amount = Decimal("0.00")
        prev_stmt = await self.repo.get_previous_canonical_statement(card.id, period_str)
        if prev_stmt is not None:
            previous_balance_amount = prev_stmt.statement_balance
            if prev_stmt.statement_balance > Decimal("0.00"):
                # Net debit balance rolls over as debit prior_balance component
                prior_comp = CreditCardStatementComponent(
                    id=uuid.uuid4(),
                    user_id=card.user_id,
                    credit_card_id=card.id,
                    statement_id=statement_id,
                    component_type="prior_balance",
                    amount=prev_stmt.statement_balance,
                    currency=card.currency,
                    principal_amount=prev_stmt.statement_balance,
                    interest_amount=Decimal("0.00"),
                    fee_amount=Decimal("0.00"),
                    transaction_id=None,
                    installment_id=None,
                    statement_charge_id=None,
                    provenance=PROVENANCE_NEXUM_MODELED,
                    description=f"Saldo anterior ({prev_stmt.billing_period})",
                )
                debit_components.append(prior_comp)
            elif prev_stmt.statement_balance < Decimal("0.00"):
                # Net credit balance rolls over as explicit credit prior_credit component without clamping
                prior_credit_amount = abs(prev_stmt.statement_balance)
                prior_comp = CreditCardStatementComponent(
                    id=uuid.uuid4(),
                    user_id=card.user_id,
                    credit_card_id=card.id,
                    statement_id=statement_id,
                    component_type="prior_credit",
                    amount=prior_credit_amount,
                    currency=card.currency,
                    principal_amount=prior_credit_amount,
                    interest_amount=Decimal("0.00"),
                    fee_amount=Decimal("0.00"),
                    transaction_id=None,
                    installment_id=None,
                    statement_charge_id=None,
                    provenance=PROVENANCE_NEXUM_MODELED,
                    description=f"Saldo a favor anterior ({prev_stmt.billing_period})",
                )
                credit_components.append(prior_comp)

        # 3. Canonical installments scheduled for this exact period
        installments = await self.repo.list_installments_for_period_for_update(card.id, period_str)
        new_purchases_total = Decimal("0.00")
        billed_installments_total = Decimal("0.00")
        assessed_interest_total = Decimal("0.00")

        for inst in installments:
            if inst.cycle_id is None:
                inst.cycle_id = cycle.id

            inst_principal = inst.principal_amount
            # Estimated interest is strictly excluded; only assessed interest enters statement
            inst_interest = inst.assessed_interest_amount or Decimal("0.00")
            inst_fee = Decimal("0.00")
            inst_total = inst_principal + inst_interest + inst_fee

            billed_installments_total += inst_principal
            assessed_interest_total += inst_interest

            if inst.installment_number == 1:
                new_purchases_total += inst_principal

            comp = CreditCardStatementComponent(
                id=uuid.uuid4(),
                user_id=card.user_id,
                credit_card_id=card.id,
                statement_id=statement_id,
                component_type="installment",
                amount=inst_total,
                currency=card.currency,
                principal_amount=inst_principal,
                interest_amount=inst_interest,
                fee_amount=inst_fee,
                transaction_id=inst.purchase_transaction_id,
                installment_id=inst.id,
                statement_charge_id=None,
                provenance=inst.provenance,
                description=f"Cuota {inst.installment_number}/{inst.installments_total}",
            )
            debit_components.append(comp)

        # 4. Canonical posted adjustments within the exact cycle boundaries [start_date, cutoff_date]
        adjustments = await self.repo.list_adjustments_for_cycle_for_update(
            card.id, start_date, cutoff_date
        )
        for adj in adjustments:
            adj_amount = adj.booked_amount  # Positive magnitude
            comp_type = "adjustment"
            is_credit = True

            if adj.adjustment_type == "refund":
                comp_type = "refund"
                is_credit = True
            elif adj.adjustment_type == "reversal":
                comp_type = "reversal"
                is_credit = True
            elif adj.adjustment_type in ("chargeback", "statement_credit"):
                comp_type = "adjustment"
                is_credit = True
            elif adj.adjustment_type == "manual_adjustment":
                # Manual adjustments MUST have explicit direction; do not guess historical direction
                if adj.direction not in ("debit", "credit"):
                    raise ValueError(
                        f"Canonical manual adjustment {adj.id} has ambiguous or missing direction '{adj.direction}'"
                    )
                comp_type = "adjustment"
                is_credit = adj.direction == "credit"

            adj_comp = CreditCardStatementComponent(
                id=uuid.uuid4(),
                user_id=card.user_id,
                credit_card_id=card.id,
                statement_id=statement_id,
                component_type=comp_type,
                amount=adj_amount,
                currency=card.currency,
                principal_amount=adj_amount,
                interest_amount=Decimal("0.00"),
                fee_amount=Decimal("0.00"),
                transaction_id=adj.original_transaction_id,
                installment_id=None,
                statement_charge_id=None,
                provenance=adj.provenance,
                description=adj.reason or f"Ajuste {adj.adjustment_type}",
            )
            if is_credit:
                credit_components.append(adj_comp)
            else:
                debit_components.append(adj_comp)

        # 5. Canonical posted payments within the exact cycle window [start_date, cutoff_date]
        cycle_payments = await self.repo.list_payments_for_cycle_for_update(
            card.id, start_date, cutoff_date
        )
        payments_received_total = Decimal("0.00")
        for pay in cycle_payments:
            payments_received_total += pay.booked_amount
            interest_part = (
                pay.interest_applied if pay.interest_applied is not None else Decimal("0.00")
            )
            fee_part = pay.fee_applied if pay.fee_applied is not None else Decimal("0.00")
            if pay.principal_applied is not None and pay.principal_applied > 0:
                principal_part = pay.principal_applied
            else:
                principal_part = pay.booked_amount - (interest_part + fee_part)

            pay_comp = CreditCardStatementComponent(
                id=uuid.uuid4(),
                user_id=card.user_id,
                credit_card_id=card.id,
                statement_id=statement_id,
                component_type="payment",
                amount=pay.booked_amount,
                currency=card.currency,
                principal_amount=principal_part,
                interest_amount=interest_part,
                fee_amount=fee_part,
                transaction_id=pay.payment_transaction_id,
                installment_id=None,
                statement_charge_id=None,
                provenance=pay.provenance,
                description=f"Pago recibido ({pay.payment_date})",
            )
            credit_components.append(pay_comp)

        # 6. Canonical accounting reconciliation:
        # statement_balance = sum(debit magnitudes) - sum(credit magnitudes)
        all_components = debit_components + credit_components
        sum_debits = sum((c.amount for c in debit_components), Decimal("0.00"))
        sum_credits = sum((c.amount for c in credit_components), Decimal("0.00"))
        statement_balance = sum_debits - sum_credits

        # Modeled statements keep minimum_payment=None (dead lookup removed).
        # Issuer/manual facts remain separately authoritative when explicitly created.
        minimum_payment = None

        # Build immutable closed statement snapshot
        statement = CreditCardStatement(
            id=statement_id,
            user_id=card.user_id,
            credit_card_id=card.id,
            billing_period=period_str,
            billing_period_start=start_date,
            cutoff_date=cutoff_date,
            due_date=due_date,
            previous_balance=previous_balance_amount,
            new_purchases=new_purchases_total,
            billed_installments=billed_installments_total,
            fees_total=Decimal("0.00"),
            interest_total=assessed_interest_total,
            payments_received=payments_received_total,
            statement_balance=statement_balance,
            minimum_payment=minimum_payment,
            status="closed",
            frozen_at=now_utc,
            cycle_id=cycle.id,
            currency=card.currency,
            assessed_interest=assessed_interest_total,
            estimated_interest=Decimal("0.00"),
            provenance=PROVENANCE_NEXUM_MODELED,
            debt_partition=DEBT_PARTITION_CANONICAL,
        )

        await self.repo.add_statement(statement)

        if all_components:
            await self.repo.add_statement_components(all_components)

        # Close cycle
        cycle.status = "closed"
        cycle.updated_at = now_utc

        logger.info(
            "credit_cycle_closed_successfully",
            card_id=str(card.id),
            billing_period=period_str,
            cycle_id=str(cycle.id),
            statement_id=str(statement.id),
            statement_balance=str(statement_balance),
            components_count=len(all_components),
        )

        return statement, all_components, True
