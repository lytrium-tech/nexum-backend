import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account
from app.credit.models import (
    CreditCard,
    CreditCardAdjustment,
    CreditCardAdjustmentAllocation,
    CreditCardBillingCycle,
    CreditCardEarlyPayment,
    CreditCardInstallment,
    CreditCardPayment,
    CreditCardPaymentAllocation,
    CreditCardStatement,
    CreditCardStatementCharge,
    CreditCardStatementComponent,
    CreditCardStatementFact,
    CreditCardTransaction,
)
from app.goals.enums import GoalTransactionType
from app.goals.models import Goal, GoalTransaction
from app.ledger.models import FinancialEvent
from app.obligations.enums_v17 import ObligationStatus, PeriodStatus
from app.obligations.models import (
    ExchangeRate,
    FXQuote,
    Obligation,
    ObligationAutoPaymentConfig,
    ObligationAutoPaymentRun,
    ObligationPayment,
    ObligationPeriod,
)
from app.users.models import User


def get_fixture_timestamps() -> tuple[datetime, datetime]:
    """Return synchronized (now_utc, now_naive) pair for fixture entities.

    - now_utc: timezone-aware UTC datetime (for TIMESTAMP WITH TIME ZONE / timestamptz).
    - now_naive: offset-naive UTC datetime (for historical TIMESTAMP WITHOUT TIME ZONE).
    """
    now_utc = datetime.now(UTC)
    now_naive = now_utc.replace(tzinfo=None)
    return now_utc, now_naive


class GateCleanupError(RuntimeError):
    """Raised when one or more cleanup steps in GateFixture fail."""

    def __init__(self, errors: list[tuple[str, Exception]]):
        self.errors = errors
        details = "; ".join(f"[{stage}] {type(err).__name__}" for stage, err in errors)
        super().__init__(f"GateFixture cleanup failed across {len(errors)} stage(s): {details}")


async def get_session_backend_pid(session: AsyncSession) -> int:
    """Return the PostgreSQL server backend PID for this session connection."""
    res = await session.execute(text("SELECT pg_backend_pid()"))
    return int(res.scalar_one())


async def wait_for_blocked(
    observer_session: AsyncSession,
    blocked_pid: int,
    blocking_pid: int | None = None,
    timeout: float = 5.0,
    interval: float = 0.05,
) -> bool:
    """Poll pg_stat_activity and pg_blocking_pids until blocked_pid is confirmed waiting on a lock.

    Returns True when lock blockage is physically verified in PostgreSQL.
    Raises TimeoutError if blocked_pid is not observed waiting within timeout.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if blocking_pid is not None:
            stmt = text("SELECT :blocking_pid = ANY(pg_blocking_pids(:blocked_pid))")
            res = await observer_session.execute(
                stmt, {"blocked_pid": blocked_pid, "blocking_pid": blocking_pid}
            )
            is_blocked = bool(res.scalar())
        else:
            stmt = text("SELECT coalesce(array_length(pg_blocking_pids(:blocked_pid), 1), 0) > 0")
            res = await observer_session.execute(stmt, {"blocked_pid": blocked_pid})
            is_blocked = bool(res.scalar())

        if is_blocked:
            return True
        await asyncio.sleep(interval)
    raise TimeoutError(f"PID {blocked_pid} was not observed blocked on lock within {timeout}s")


async def _set_statement_triggers_enabled(session: AsyncSession, enabled: bool) -> None:
    """Enable or disable immutable statement triggers for synthetic cleanup safely.

    Fails closed immediately on any error; never swallows trigger modification failures.
    """
    action = "ENABLE" if enabled else "DISABLE"
    for tbl, trg in [
        ("credit_card_statement_components", "trg_cc_stmt_comp_immutable"),
        ("credit_card_statements", "trg_cc_statements_immutable"),
    ]:
        await session.execute(text(f"ALTER TABLE {tbl} {action} TRIGGER {trg};"))


async def verify_statement_triggers_enabled(session: AsyncSession) -> bool:
    """Verify that immutable statement triggers are present and enabled ('O') in PostgreSQL.

    Raises RuntimeError fail-closed if any trigger is absent or not in status 'O' (enabled).
    Accepts both str ('O') and bytes (b'O') driver representations.
    """
    stmt = text(
        """
        SELECT tgname, tgenabled
        FROM pg_trigger
        WHERE tgname IN ('trg_cc_statements_immutable', 'trg_cc_stmt_comp_immutable');
        """
    )
    res = await session.execute(stmt)
    rows = dict(res.all())
    expected = {"trg_cc_statements_immutable", "trg_cc_stmt_comp_immutable"}
    if set(rows.keys()) != expected:
        missing = expected - set(rows.keys())
        raise RuntimeError(f"Missing expected immutability triggers: {missing}")
    for name, raw_status in rows.items():
        norm_status = (
            raw_status.decode("ascii", errors="replace").strip()
            if isinstance(raw_status, bytes)
            else str(raw_status).strip()
        )
        if norm_status != "O":
            raise RuntimeError(
                f"Immutability trigger '{name}' is not in enabled state 'O' (found: {raw_status!r})"
            )
    return True


class GateFixture:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.gate_id = str(uuid.uuid4())
        self.user = User(
            id=uuid.uuid4(),
            email=f"gate-test-{self.gate_id}@lytrium.tech",
            name="Gate Test",
            status="active",
        )
        self.user_id = self.user.id
        self.account = Account(
            id=uuid.uuid4(),
            user_id=self.user_id,
            currency="USD",
            balance=Decimal("0"),
            name="Gate Account",
        )
        self.account_id = self.account.id
        self.has_auto_payment = True
        self.created_exchange_rate_ids: set[uuid.UUID] = set()

    async def commit(self):
        self.session.add(self.user)
        await self.session.flush()
        self.session.add(self.account)
        await self.session.commit()

    async def add_goal_reservation(self, amount: Decimal, currency="USD"):
        goal = Goal(
            id=uuid.uuid4(),
            user_id=self.user.id,
            name=f"Gate Goal {uuid.uuid4()}",
            target_amount=Decimal("10000"),
            current_amount=amount,
            currency=currency,
            status="active",
        )
        fe = FinancialEvent(
            id=uuid.uuid4(),
            user_id=self.user.id,
            account_id=self.account.id,
            event_type="goal_contribution",
            direction="outflow",
            amount=amount,
            currency=currency,
            command_id=uuid.uuid4(),
        )
        self.session.add_all([goal, fe])
        await self.session.flush()

        tx = GoalTransaction(
            id=uuid.uuid4(),
            goal_id=goal.id,
            user_id=self.user.id,
            account_id=self.account.id,
            event_id=fe.id,
            transaction_type=GoalTransactionType.allocation.value,
            applied_amount=amount,
            source_amount=amount,
            source_currency=currency,
            goal_currency=currency,
        )
        self.session.add(tx)
        await self.session.flush()
        return goal, tx

    async def add_obligation(self, amount: Decimal, currency="USD", periods=1):
        from app.core.financial_time import financial_date
        from app.obligations.period_engine import calculate_period_bounds, generate_period_key

        today = financial_date()
        obl = Obligation(
            id=uuid.uuid4(),
            user_id=self.user.id,
            name=f"Gate Obligation {uuid.uuid4()}",
            amount_type="fixed",
            currency=currency,
            status=ObligationStatus.active.value,
            base_amount=amount,
            type="indefinite",
            frequency="monthly",
            payment_mode="fixed",
            start_date=today,
            first_due_date=today,
            interval_count=1,
        )
        self.session.add(obl)
        await self.session.flush()

        created_periods = []
        for i in range(periods):
            seq = i + 1
            p_start, p_end, p_due = calculate_period_bounds(obl, seq)
            p_key = generate_period_key(obl.frequency, p_start, seq)
            period = ObligationPeriod(
                id=uuid.uuid4(),
                obligation_id=obl.id,
                sequence_number=seq,
                status=PeriodStatus.pending_payment.value,
                period_key=p_key,
                start_date=p_start,
                end_date=p_end,
                due_date=p_due,
                currency=currency,
                amount=amount,
                paid_amount=Decimal("0"),
            )
            self.session.add(period)
            created_periods.append(period)
        await self.session.flush()
        return obl, created_periods

    async def add_credit_card(
        self,
        currency: str = "COP",
        credit_limit: Decimal = Decimal("5000000.00"),
        cutoff_day: int = 15,
        due_day: int = 5,
        payment_due_day: int | None = None,
        is_active: bool = True,
        name: str = "Gate Card",
        canonical_mode: bool = True,
        provenance: str = "nexum_modeled",
    ) -> CreditCard:
        resolved_due_day = due_day if payment_due_day is None else payment_due_day
        now_utc, now_naive = get_fixture_timestamps()
        card = CreditCard(
            id=uuid.uuid4(),
            user_id=self.user.id,
            name=name,
            bank="Gate Bank",
            currency=currency,
            credit_limit=credit_limit,
            current_debt=Decimal("0.00"),
            cutoff_day=cutoff_day,
            due_day=resolved_due_day,
            is_active=is_active,
            canonical_mode=canonical_mode,
            canonical_activated_at=now_utc if canonical_mode else None,
            provenance=provenance,
            lifecycle_status="active",
            created_at=now_naive,
            updated_at=now_utc,
        )
        self.session.add(card)
        await self.session.flush()
        return card

    async def add_transaction(
        self,
        card_id: uuid.UUID,
        amount: Decimal,
        type: str = "purchase",
        occurred_at: datetime | None = None,
        debt_partition: str = "canonical",
        provenance: str = "nexum_modeled",
        lifecycle_status: str = "posted",
        currency: str = "COP",
    ) -> CreditCardTransaction:
        now_utc, now_naive = get_fixture_timestamps()
        tx = CreditCardTransaction(
            id=uuid.uuid4(),
            user_id=self.user_id,
            credit_card_id=card_id,
            type=type,
            amount=amount,
            booked_amount=amount,
            booked_currency=currency,
            original_amount=amount,
            original_currency=currency,
            occurred_at=occurred_at or now_utc,
            debt_partition=debt_partition,
            provenance=provenance,
            lifecycle_status=lifecycle_status,
            created_at=now_naive,
            updated_at=now_utc,
        )
        self.session.add(tx)
        await self.session.flush()
        return tx

    async def add_fx_quote(
        self,
        target_amount: Decimal,
        source_amount: Decimal,
        rate: Decimal,
        from_currency="USD",
        to_currency="EUR",
    ):
        from datetime import UTC, datetime, timedelta

        quote = FXQuote(
            id=uuid.uuid4(),
            user_id=self.user.id,
            from_currency=from_currency,
            to_currency=to_currency,
            target_amount=target_amount,
            source_amount=source_amount,
            rate=rate,
            provider="gate_test",
            rate_timestamp=datetime.now(UTC),
            status="active",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        self.session.add(quote)
        await self.session.flush()
        return quote

    async def add_exchange_rate(
        self, base_currency="USD", quote_currency="EUR", rate=Decimal("1.0"), provider="gate_test"
    ):
        from datetime import UTC, datetime, timedelta

        er = ExchangeRate(
            id=uuid.uuid4(),
            base_currency=base_currency,
            quote_currency=quote_currency,
            rate=rate,
            provider=provider,
            fetched_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        self.session.add(er)
        await self.session.flush()
        self.created_exchange_rate_ids.add(er.id)
        return er

    def track_exchange_rate(self, exchange_rate_id: uuid.UUID) -> None:
        self.created_exchange_rate_ids.add(exchange_rate_id)

    async def add_auto_payment_config(
        self,
        obligation_id: uuid.UUID,
        account_id: uuid.UUID | None = None,
        enabled: bool = True,
        next_evaluation_at=None,
    ) -> ObligationAutoPaymentConfig:
        from datetime import UTC, datetime

        now_utc = datetime.now(UTC)
        self.has_auto_payment = True
        cfg = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation_id,
            user_id=self.user.id,
            account_id=account_id or self.account.id,
            enabled=enabled,
            status="enabled" if enabled else "paused",
            pause_reason=None if enabled else "user_paused",
            next_evaluation_at=next_evaluation_at,
            created_at=now_utc,
            updated_at=now_utc,
        )
        self.session.add(cfg)
        await self.session.flush()
        return cfg

    async def cleanup(self):
        errors: list[tuple[str, Exception]] = []
        try:
            while self.session.in_nested_transaction():
                await self.session.rollback()
            await self.session.rollback()
        except Exception as exc:
            errors.append(("initial_rollback", exc))

        uid = self.user_id
        has_delete_error = False

        try:
            # Temporarily disable immutability triggers for synthetic test row cleanup
            try:
                await _set_statement_triggers_enabled(self.session, False)
            except Exception as trg_dis_exc:
                has_delete_error = True
                errors.append(("disable_statement_triggers", trg_dis_exc))

            if not has_delete_error:
                stages = [
                    (
                        "credit_card_adjustment_allocations",
                        delete(CreditCardAdjustmentAllocation).where(
                            CreditCardAdjustmentAllocation.adjustment_id.in_(
                                select(CreditCardAdjustment.id).where(
                                    CreditCardAdjustment.user_id == uid
                                )
                            )
                        ),
                    ),
                    (
                        "credit_card_payment_allocations",
                        delete(CreditCardPaymentAllocation).where(
                            CreditCardPaymentAllocation.payment_id.in_(
                                select(CreditCardPayment.id).where(CreditCardPayment.user_id == uid)
                            )
                        ),
                    ),
                    (
                        "credit_card_statement_components",
                        delete(CreditCardStatementComponent).where(
                            CreditCardStatementComponent.statement_id.in_(
                                select(CreditCardStatement.id).where(
                                    CreditCardStatement.user_id == uid
                                )
                            )
                        ),
                    ),
                    (
                        "credit_card_statement_facts",
                        delete(CreditCardStatementFact).where(
                            CreditCardStatementFact.statement_id.in_(
                                select(CreditCardStatement.id).where(
                                    CreditCardStatement.user_id == uid
                                )
                            )
                        ),
                    ),
                    (
                        "credit_card_statement_charges",
                        delete(CreditCardStatementCharge).where(
                            CreditCardStatementCharge.statement_id.in_(
                                select(CreditCardStatement.id).where(
                                    CreditCardStatement.user_id == uid
                                )
                            )
                        ),
                    ),
                    (
                        "credit_card_adjustments",
                        delete(CreditCardAdjustment).where(CreditCardAdjustment.user_id == uid),
                    ),
                    (
                        "credit_card_payments",
                        delete(CreditCardPayment).where(CreditCardPayment.user_id == uid),
                    ),
                    (
                        "credit_card_early_payments",
                        delete(CreditCardEarlyPayment).where(CreditCardEarlyPayment.user_id == uid),
                    ),
                    (
                        "credit_card_installments",
                        delete(CreditCardInstallment).where(
                            CreditCardInstallment.credit_card_id.in_(
                                select(CreditCard.id).where(CreditCard.user_id == uid)
                            )
                        ),
                    ),
                    (
                        "credit_card_transactions",
                        delete(CreditCardTransaction).where(CreditCardTransaction.user_id == uid),
                    ),
                    (
                        "credit_card_statements",
                        delete(CreditCardStatement).where(CreditCardStatement.user_id == uid),
                    ),
                    (
                        "credit_card_billing_cycles",
                        delete(CreditCardBillingCycle).where(
                            CreditCardBillingCycle.credit_card_id.in_(
                                select(CreditCard.id).where(CreditCard.user_id == uid)
                            )
                        ),
                    ),
                    (
                        "credit_cards",
                        delete(CreditCard).where(CreditCard.user_id == uid),
                    ),
                    (
                        "obligation_auto_payment_runs",
                        delete(ObligationAutoPaymentRun).where(
                            ObligationAutoPaymentRun.obligation_id.in_(
                                select(Obligation.id).where(Obligation.user_id == uid)
                            )
                        ),
                    ),
                    (
                        "obligation_auto_payment_configs",
                        delete(ObligationAutoPaymentConfig).where(
                            ObligationAutoPaymentConfig.user_id == uid
                        ),
                    ),
                    (
                        "goal_transactions",
                        delete(GoalTransaction).where(GoalTransaction.user_id == uid),
                    ),
                    (
                        "obligation_payments",
                        delete(ObligationPayment).where(ObligationPayment.user_id == uid),
                    ),
                    (
                        "obligation_periods",
                        delete(ObligationPeriod).where(
                            ObligationPeriod.obligation_id.in_(
                                select(Obligation.id).where(Obligation.user_id == uid)
                            )
                        ),
                    ),
                    ("obligations", delete(Obligation).where(Obligation.user_id == uid)),
                    ("financial_events", delete(FinancialEvent).where(FinancialEvent.user_id == uid)),
                    ("goals", delete(Goal).where(Goal.user_id == uid)),
                    ("accounts", delete(Account).where(Account.user_id == uid)),
                    ("fx_quotes", delete(FXQuote).where(FXQuote.user_id == uid)),
                ]
                if self.created_exchange_rate_ids:
                    stages.append(
                        (
                            "exchange_rates",
                            delete(ExchangeRate).where(
                                ExchangeRate.id.in_(list(self.created_exchange_rate_ids))
                            ),
                        )
                    )
                stages.append(("users", delete(User).where(User.id == uid)))

                for stage_name, stmt in stages:
                    try:
                        await self.session.execute(stmt)
                    except Exception as exc:
                        has_delete_error = True
                        errors.append((stage_name, exc))
        finally:
            try:
                # If delete stages failed or session was in aborted state, roll back before re-enabling triggers
                if has_delete_error or errors:
                    try:
                        while self.session.in_nested_transaction():
                            await self.session.rollback()
                        await self.session.rollback()
                    except Exception as rb_exc:
                        errors.append(("pre_trigger_restore_rollback", rb_exc))
                await _set_statement_triggers_enabled(self.session, True)
                await verify_statement_triggers_enabled(self.session)
            except Exception as trg_exc:
                has_delete_error = True
                errors.append(("verify_statement_triggers_enabled", trg_exc))

        if not has_delete_error and not errors:
            try:
                await self.session.commit()
            except Exception as exc:
                errors.append(("commit", exc))
                try:
                    await self.session.rollback()
                except Exception as rb_exc:
                    errors.append(("post_commit_rollback", rb_exc))

            if not errors:
                # Residue verification: confirm zero synthetic records remain for this fixture
                residue_checks = [
                    (
                        "credit_card_adjustment_allocations",
                        select(func.count())
                        .select_from(CreditCardAdjustmentAllocation)
                        .where(
                            CreditCardAdjustmentAllocation.adjustment_id.in_(
                                select(CreditCardAdjustment.id).where(
                                    CreditCardAdjustment.user_id == uid
                                )
                            )
                        ),
                    ),
                    (
                        "credit_card_payment_allocations",
                        select(func.count())
                        .select_from(CreditCardPaymentAllocation)
                        .where(
                            CreditCardPaymentAllocation.payment_id.in_(
                                select(CreditCardPayment.id).where(CreditCardPayment.user_id == uid)
                            )
                        ),
                    ),
                    (
                        "credit_card_statement_components",
                        select(func.count())
                        .select_from(CreditCardStatementComponent)
                        .where(
                            CreditCardStatementComponent.statement_id.in_(
                                select(CreditCardStatement.id).where(
                                    CreditCardStatement.user_id == uid
                                )
                            )
                        ),
                    ),
                    (
                        "credit_card_statement_facts",
                        select(func.count())
                        .select_from(CreditCardStatementFact)
                        .where(
                            CreditCardStatementFact.statement_id.in_(
                                select(CreditCardStatement.id).where(
                                    CreditCardStatement.user_id == uid
                                )
                            )
                        ),
                    ),
                    (
                        "credit_card_statement_charges",
                        select(func.count())
                        .select_from(CreditCardStatementCharge)
                        .where(
                            CreditCardStatementCharge.statement_id.in_(
                                select(CreditCardStatement.id).where(
                                    CreditCardStatement.user_id == uid
                                )
                            )
                        ),
                    ),
                    (
                        "credit_card_adjustments",
                        select(func.count())
                        .select_from(CreditCardAdjustment)
                        .where(CreditCardAdjustment.user_id == uid),
                    ),
                    (
                        "credit_card_payments",
                        select(func.count())
                        .select_from(CreditCardPayment)
                        .where(CreditCardPayment.user_id == uid),
                    ),
                    (
                        "credit_card_early_payments",
                        select(func.count())
                        .select_from(CreditCardEarlyPayment)
                        .where(CreditCardEarlyPayment.user_id == uid),
                    ),
                    (
                        "credit_card_installments",
                        select(func.count())
                        .select_from(CreditCardInstallment)
                        .where(
                            CreditCardInstallment.credit_card_id.in_(
                                select(CreditCard.id).where(CreditCard.user_id == uid)
                            )
                        ),
                    ),
                    (
                        "credit_card_transactions",
                        select(func.count())
                        .select_from(CreditCardTransaction)
                        .where(CreditCardTransaction.user_id == uid),
                    ),
                    (
                        "credit_card_statements",
                        select(func.count())
                        .select_from(CreditCardStatement)
                        .where(CreditCardStatement.user_id == uid),
                    ),
                    (
                        "credit_card_billing_cycles",
                        select(func.count())
                        .select_from(CreditCardBillingCycle)
                        .where(
                            CreditCardBillingCycle.credit_card_id.in_(
                                select(CreditCard.id).where(CreditCard.user_id == uid)
                            )
                        ),
                    ),
                    (
                        "credit_cards",
                        select(func.count())
                        .select_from(CreditCard)
                        .where(CreditCard.user_id == uid),
                    ),
                    ("users", select(func.count()).select_from(User).where(User.id == uid)),
                    (
                        "accounts",
                        select(func.count()).select_from(Account).where(Account.user_id == uid),
                    ),
                    ("goals", select(func.count()).select_from(Goal).where(Goal.user_id == uid)),
                    (
                        "goal_transactions",
                        select(func.count())
                        .select_from(GoalTransaction)
                        .where(GoalTransaction.user_id == uid),
                    ),
                    (
                        "financial_events",
                        select(func.count())
                        .select_from(FinancialEvent)
                        .where(FinancialEvent.user_id == uid),
                    ),
                    (
                        "obligations",
                        select(func.count())
                        .select_from(Obligation)
                        .where(Obligation.user_id == uid),
                    ),
                    (
                        "obligation_periods",
                        select(func.count())
                        .select_from(ObligationPeriod)
                        .where(
                            ObligationPeriod.obligation_id.in_(
                                select(Obligation.id).where(Obligation.user_id == uid)
                            )
                        ),
                    ),
                    (
                        "obligation_payments",
                        select(func.count())
                        .select_from(ObligationPayment)
                        .where(ObligationPayment.user_id == uid),
                    ),
                    (
                        "obligation_auto_payment_configs",
                        select(func.count())
                        .select_from(ObligationAutoPaymentConfig)
                        .where(ObligationAutoPaymentConfig.user_id == uid),
                    ),
                    (
                        "obligation_auto_payment_runs",
                        select(func.count())
                        .select_from(ObligationAutoPaymentRun)
                        .where(
                            ObligationAutoPaymentRun.obligation_id.in_(
                                select(Obligation.id).where(Obligation.user_id == uid)
                            )
                        ),
                    ),
                    (
                        "fx_quotes",
                        select(func.count()).select_from(FXQuote).where(FXQuote.user_id == uid),
                    ),
                ]
                if self.created_exchange_rate_ids:
                    residue_checks.append(
                        (
                            "exchange_rates",
                            select(func.count())
                            .select_from(ExchangeRate)
                            .where(ExchangeRate.id.in_(list(self.created_exchange_rate_ids))),
                        )
                    )

                for table_name, count_stmt in residue_checks:
                    try:
                        count_val = (await self.session.execute(count_stmt)).scalar_one()
                        if count_val > 0:
                            errors.append(
                                (
                                    f"residue_{table_name}",
                                    RuntimeError(
                                        f"Synthetic residue detected: {table_name}={count_val} row(s) remain"
                                    ),
                                )
                            )
                    except Exception as exc:
                        errors.append((f"check_residue_{table_name}", exc))
        else:
            try:
                await self.session.rollback()
            except Exception as rb_exc:
                errors.append(("error_rollback", rb_exc))

        if errors:
            raise GateCleanupError(errors)


async def cleanup_all_gate_users(session: AsyncSession):
    errors: list[tuple[str, Exception]] = []
    try:
        while session.in_nested_transaction():
            await session.rollback()
        await session.rollback()
    except Exception as exc:
        errors.append(("initial_rollback", exc))

    try:
        stmt = select(User.id).where(User.email.like("gate-test-%"))
        res = await session.execute(stmt)
        uids = [row[0] for row in res.all()]
    except Exception as exc:
        errors.append(("select_gate_users", exc))
        uids = []

    if uids:
        stages = [
            (
                "credit_card_adjustment_allocations",
                delete(CreditCardAdjustmentAllocation).where(
                    CreditCardAdjustmentAllocation.adjustment_id.in_(
                        select(CreditCardAdjustment.id).where(
                            CreditCardAdjustment.user_id.in_(uids)
                        )
                    )
                ),
            ),
            (
                "credit_card_payment_allocations",
                delete(CreditCardPaymentAllocation).where(
                    CreditCardPaymentAllocation.payment_id.in_(
                        select(CreditCardPayment.id).where(CreditCardPayment.user_id.in_(uids))
                    )
                ),
            ),
            (
                "credit_card_statement_components",
                delete(CreditCardStatementComponent).where(
                    CreditCardStatementComponent.statement_id.in_(
                        select(CreditCardStatement.id).where(CreditCardStatement.user_id.in_(uids))
                    )
                ),
            ),
            (
                "credit_card_statement_facts",
                delete(CreditCardStatementFact).where(
                    CreditCardStatementFact.statement_id.in_(
                        select(CreditCardStatement.id).where(CreditCardStatement.user_id.in_(uids))
                    )
                ),
            ),
            (
                "credit_card_statement_charges",
                delete(CreditCardStatementCharge).where(
                    CreditCardStatementCharge.statement_id.in_(
                        select(CreditCardStatement.id).where(CreditCardStatement.user_id.in_(uids))
                    )
                ),
            ),
            (
                "credit_card_adjustments",
                delete(CreditCardAdjustment).where(CreditCardAdjustment.user_id.in_(uids)),
            ),
            (
                "credit_card_payments",
                delete(CreditCardPayment).where(CreditCardPayment.user_id.in_(uids)),
            ),
            (
                "credit_card_early_payments",
                delete(CreditCardEarlyPayment).where(CreditCardEarlyPayment.user_id.in_(uids)),
            ),
            (
                "credit_card_installments",
                delete(CreditCardInstallment).where(
                    CreditCardInstallment.credit_card_id.in_(
                        select(CreditCard.id).where(CreditCard.user_id.in_(uids))
                    )
                ),
            ),
            (
                "credit_card_transactions",
                delete(CreditCardTransaction).where(CreditCardTransaction.user_id.in_(uids)),
            ),
            (
                "credit_card_statements",
                delete(CreditCardStatement).where(CreditCardStatement.user_id.in_(uids)),
            ),
            (
                "credit_card_billing_cycles",
                delete(CreditCardBillingCycle).where(
                    CreditCardBillingCycle.credit_card_id.in_(
                        select(CreditCard.id).where(CreditCard.user_id.in_(uids))
                    )
                ),
            ),
            (
                "credit_cards",
                delete(CreditCard).where(CreditCard.user_id.in_(uids)),
            ),
            (
                "obligation_auto_payment_runs",
                delete(ObligationAutoPaymentRun).where(
                    ObligationAutoPaymentRun.obligation_id.in_(
                        select(Obligation.id).where(Obligation.user_id.in_(uids))
                    )
                ),
            ),
            (
                "obligation_auto_payment_configs",
                delete(ObligationAutoPaymentConfig).where(
                    ObligationAutoPaymentConfig.user_id.in_(uids)
                ),
            ),
            (
                "goal_transactions",
                delete(GoalTransaction).where(GoalTransaction.user_id.in_(uids)),
            ),
            (
                "obligation_payments",
                delete(ObligationPayment).where(ObligationPayment.user_id.in_(uids)),
            ),
            (
                "obligation_periods",
                delete(ObligationPeriod).where(
                    ObligationPeriod.obligation_id.in_(
                        select(Obligation.id).where(Obligation.user_id.in_(uids))
                    )
                ),
            ),
            ("obligations", delete(Obligation).where(Obligation.user_id.in_(uids))),
            ("financial_events", delete(FinancialEvent).where(FinancialEvent.user_id.in_(uids))),
            ("goals", delete(Goal).where(Goal.user_id.in_(uids))),
            ("accounts", delete(Account).where(Account.user_id.in_(uids))),
            ("fx_quotes", delete(FXQuote).where(FXQuote.user_id.in_(uids))),
            (
                "exchange_rates",
                delete(ExchangeRate).where(
                    ExchangeRate.provider.like("gate_test%")
                    | (ExchangeRate.provider == "gate_test")
                ),
            ),
            ("users", delete(User).where(User.id.in_(uids))),
        ]

        has_delete_error = False
        try:
            try:
                await _set_statement_triggers_enabled(session, False)
            except Exception as trg_dis_exc:
                has_delete_error = True
                errors.append(("disable_statement_triggers", trg_dis_exc))

            if not has_delete_error:
                for stage_name, delete_stmt in stages:
                    try:
                        await session.execute(delete_stmt)
                    except Exception as exc:
                        has_delete_error = True
                        errors.append((stage_name, exc))
        finally:
            try:
                if has_delete_error or errors:
                    try:
                        while session.in_nested_transaction():
                            await session.rollback()
                        await session.rollback()
                    except Exception as rb_exc:
                        errors.append(("pre_trigger_restore_rollback", rb_exc))
                await _set_statement_triggers_enabled(session, True)
                await verify_statement_triggers_enabled(session)
            except Exception as trg_exc:
                has_delete_error = True
                errors.append(("verify_statement_triggers_enabled", trg_exc))

        if not has_delete_error and not errors:
            try:
                await session.commit()
            except Exception as exc:
                errors.append(("commit", exc))
                try:
                    await session.rollback()
                except Exception as rb_exc:
                    errors.append(("post_commit_rollback", rb_exc))

            if not errors:
                # Residue check across gate-test users and exchange rates
                residue_checks = [
                    (
                        "gate_test_users",
                        select(func.count())
                        .select_from(User)
                        .where(User.email.like("gate-test-%")),
                    ),
                    (
                        "gate_test_credit_cards",
                        select(func.count())
                        .select_from(CreditCard)
                        .where(
                            CreditCard.user_id.in_(
                                select(User.id).where(User.email.like("gate-test-%"))
                            )
                        ),
                    ),
                    (
                        "gate_test_exchange_rates",
                        select(func.count())
                        .select_from(ExchangeRate)
                        .where(
                            ExchangeRate.provider.like("gate_test%")
                            | (ExchangeRate.provider == "gate_test")
                        ),
                    ),
                ]
                for check_name, count_stmt in residue_checks:
                    try:
                        count_val = (await session.execute(count_stmt)).scalar_one()
                        if count_val > 0:
                            errors.append(
                                (
                                    f"residue_{check_name}",
                                    RuntimeError(
                                        f"Residue detected after global cleanup: {check_name}={count_val}"
                                    ),
                                )
                            )
                    except Exception as exc:
                        errors.append((f"check_{check_name}", exc))
        else:
            try:
                while session.in_nested_transaction():
                    await session.rollback()
                await session.rollback()
            except Exception as rb_exc:
                errors.append(("error_rollback", rb_exc))
    else:
        # Also clean up any lingering gate_test exchange rates
        try:
            await session.execute(
                delete(ExchangeRate).where(
                    ExchangeRate.provider.like("gate_test%")
                    | (ExchangeRate.provider == "gate_test")
                )
            )
            await session.commit()
        except Exception as exc:
            errors.append(("exchange_rates_cleanup", exc))

    if errors:
        raise GateCleanupError(errors)
