import uuid
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account
from app.goals.enums import GoalTransactionType
from app.goals.models import Goal, GoalTransaction
from app.ledger.models import FinancialEvent
from app.obligations.enums_v17 import ObligationStatus, PeriodStatus
from app.obligations.models import (
    ExchangeRate,
    FXQuote,
    Obligation,
    ObligationPayment,
    ObligationPeriod,
)
from app.users.models import User


class GateCleanupError(RuntimeError):
    """Raised when one or more cleanup steps in GateFixture fail."""

    def __init__(self, errors: list[tuple[str, Exception]]):
        self.errors = errors
        details = "; ".join(f"[{stage}] {type(err).__name__}: {str(err)}" for stage, err in errors)
        super().__init__(f"GateFixture cleanup failed across {len(errors)} stage(s): {details}")


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
        self, base_currency="USD", quote_currency="EUR", rate=Decimal("1.0")
    ):
        from datetime import UTC, datetime, timedelta

        er = ExchangeRate(
            id=uuid.uuid4(),
            base_currency=base_currency,
            quote_currency=quote_currency,
            rate=rate,
            provider="gate_test",
            fetched_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        self.session.add(er)
        await self.session.flush()
        return er

    async def cleanup(self):
        errors: list[tuple[str, Exception]] = []
        try:
            await self.session.rollback()
        except Exception as exc:
            errors.append(("initial_rollback", exc))

        uid = self.user_id
        stages = [
            ("goal_transactions", delete(GoalTransaction).where(GoalTransaction.user_id == uid)),
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
            ("users", delete(User).where(User.id == uid)),
        ]

        has_delete_error = False
        for stage_name, stmt in stages:
            try:
                await self.session.execute(stmt)
            except Exception as exc:
                has_delete_error = True
                errors.append((stage_name, exc))

        if not has_delete_error and not errors:
            try:
                await self.session.commit()
            except Exception as exc:
                errors.append(("commit", exc))
                try:
                    await self.session.rollback()
                except Exception as rb_exc:
                    errors.append(("post_commit_rollback", rb_exc))
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
            ("goal_transactions", delete(GoalTransaction).where(GoalTransaction.user_id.in_(uids))),
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
            ("users", delete(User).where(User.id.in_(uids))),
        ]

        has_delete_error = False
        for stage_name, delete_stmt in stages:
            try:
                await session.execute(delete_stmt)
            except Exception as exc:
                has_delete_error = True
                errors.append((stage_name, exc))

        if not has_delete_error and not errors:
            try:
                await session.commit()
            except Exception as exc:
                errors.append(("commit", exc))
                try:
                    await session.rollback()
                except Exception as rb_exc:
                    errors.append(("post_commit_rollback", rb_exc))
        else:
            try:
                await session.rollback()
            except Exception as rb_exc:
                errors.append(("error_rollback", rb_exc))
    elif not errors:
        try:
            await session.commit()
        except Exception as exc:
            errors.append(("commit", exc))

    if errors:
        raise GateCleanupError(errors)
