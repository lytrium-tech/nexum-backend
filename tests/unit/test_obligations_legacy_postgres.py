import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.accounts.models import Account
from app.core.uow import UnitOfWork
from app.goals.repository import GoalRepository
from app.ledger.models import FinancialEvent
from app.obligations.exceptions import ObligationIdempotencyConflictDomainError
from app.obligations.models import Obligation, ObligationPayment, ObligationPeriod
from app.obligations.repository import ObligationRepository
from app.obligations.schemas import ObligationPaymentCreate
from app.obligations.service import ObligationService
from tests.unit.postgres_helpers import (
    GateFixture,
    get_session_backend_pid,
    wait_for_blocked,
)
from tests.unit.test_obligations_reservations_postgres import check_guards


@pytest.fixture(scope="function")
async def engine():
    url = check_guards()
    eng = create_async_engine(url)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="function")
async def session(engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as sess:
        res = await sess.execute(text("SELECT current_database(), current_user;"))
        db_name, db_user = res.one()
        assert db_name == "nexum_release_gate_test", f"Unsafe target DB: {db_name}"
        assert db_user == "nexum_release_gate_test", f"Unsafe target User: {db_user}"
        yield sess


@pytest.fixture(scope="function")
async def fixture(session):
    fix = GateFixture(session)
    await fix.commit()
    yield fix
    await fix.cleanup()


# --- PHYSICAL TEST 1: LEGACY SPECIFIC SUCCESSFUL DEBIT + RETRY ---
@pytest.mark.asyncio
async def test_pg_legacy_specific_successful_debit_and_retry(
    engine, session: AsyncSession, fixture: GateFixture
):
    fixture.account.balance = Decimal("1000.00")
    await fixture.add_goal_reservation(Decimal("200.00"))
    obl, periods = await fixture.add_obligation(Decimal("300.00"), periods=1)
    await fixture.commit()

    msg_id = str(uuid.uuid4())
    req = ObligationPaymentCreate(
        amount=Decimal("300.00"),
        account_id=fixture.account.id,
        source_message_id=msg_id,
    )

    # 1. Execute Specific Payment inside physical transaction
    async with UnitOfWork(session).transaction():
        service = ObligationService(ObligationRepository(session))
        res = await service.pay_specific_period(fixture.user.id, periods[0].id, req)

    # 2. Inspect from fresh session after UoW commit
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as fresh:
        acc = (
            await fresh.execute(select(Account).where(Account.id == fixture.account.id))
        ).scalar_one()
        assert acc.balance == Decimal("700.00")

        goal_repo = GoalRepository(fresh)
        reserved = await goal_repo.calculate_reserved_by_account(
            fixture.account.id, fixture.user.id
        )
        assert reserved == Decimal("200.00")
        assert (acc.balance - reserved) == Decimal("500.00")

        per = (
            await fresh.execute(
                select(ObligationPeriod).where(ObligationPeriod.id == periods[0].id)
            )
        ).scalar_one()
        assert per.paid_amount == Decimal("300.00")
        assert per.status == "paid"

        payments = list(
            (
                await fresh.execute(
                    select(ObligationPayment).where(ObligationPayment.obligation_id == obl.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(payments) == 1
        assert payments[0].amount == Decimal("300.00")
        assert payments[0].source_amount == Decimal("300.00")
        assert payments[0].user_id == fixture.user.id

        events = list(
            (
                await fresh.execute(
                    select(FinancialEvent).where(
                        FinancialEvent.source_message_id == uuid.UUID(msg_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].amount == Decimal("300.00")

    # 3. Retry same payment with same source_message_id
    async with UnitOfWork(session).transaction():
        service = ObligationService(ObligationRepository(session))
        res_retry = await service.pay_specific_period(fixture.user.id, periods[0].id, req)
        assert res_retry.id == res.id

    # 4. Verify no double-debit in fresh session
    async with async_session() as fresh:
        acc = (
            await fresh.execute(select(Account).where(Account.id == fixture.account.id))
        ).scalar_one()
        assert acc.balance == Decimal("700.00")

        payments = list(
            (
                await fresh.execute(
                    select(ObligationPayment).where(ObligationPayment.obligation_id == obl.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(payments) == 1

        events = list(
            (
                await fresh.execute(
                    select(FinancialEvent).where(
                        FinancialEvent.source_message_id == uuid.UUID(msg_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1


# --- PHYSICAL TEST 2: LEGACY FIFO MULTI-PERIOD DEBIT + RETRY ---
@pytest.mark.asyncio
async def test_pg_legacy_fifo_successful_debit_and_retry(
    engine, session: AsyncSession, fixture: GateFixture
):
    fixture.account.balance = Decimal("1000.00")
    await fixture.add_goal_reservation(Decimal("100.00"))
    obl, periods = await fixture.add_obligation(Decimal("200.00"), periods=2)
    await fixture.commit()

    msg_id = str(uuid.uuid4())
    req = ObligationPaymentCreate(
        amount=Decimal("300.00"),
        account_id=fixture.account.id,
        source_message_id=msg_id,
    )

    # 1. Execute FIFO Payment (allocates 200 to period 1, 100 to period 2)
    async with UnitOfWork(session).transaction():
        service = ObligationService(ObligationRepository(session))
        res_list = await service.pay_obligation_fifo(fixture.user.id, obl.id, req)
        assert len(res_list) == 2

    # 2. Inspect from fresh session after UoW commit
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as fresh:
        acc = (
            await fresh.execute(select(Account).where(Account.id == fixture.account.id))
        ).scalar_one()
        assert acc.balance == Decimal("700.00")

        goal_repo = GoalRepository(fresh)
        reserved = await goal_repo.calculate_reserved_by_account(
            fixture.account.id, fixture.user.id
        )
        assert reserved == Decimal("100.00")

        p1 = (
            await fresh.execute(
                select(ObligationPeriod).where(ObligationPeriod.id == periods[0].id)
            )
        ).scalar_one()
        p2 = (
            await fresh.execute(
                select(ObligationPeriod).where(ObligationPeriod.id == periods[1].id)
            )
        ).scalar_one()
        assert p1.paid_amount == Decimal("200.00")
        assert p1.status == "paid"
        assert p2.paid_amount == Decimal("100.00")
        assert p2.status == "partially_paid"

        payments = list(
            (
                await fresh.execute(
                    select(ObligationPayment)
                    .where(ObligationPayment.obligation_id == obl.id)
                    .order_by(ObligationPayment.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        assert len(payments) == 2
        assert sum(p.amount for p in payments) == Decimal("300.00")
        assert sum(p.source_amount for p in payments) == Decimal("300.00")
        assert all(p.user_id == fixture.user.id for p in payments)

        events = list(
            (
                await fresh.execute(
                    select(FinancialEvent).where(
                        FinancialEvent.source_message_id == uuid.UUID(msg_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 2
        assert sum(e.amount for e in events) == Decimal("300.00")

    # 3. Retry same FIFO payment with same source_message_id
    async with UnitOfWork(session).transaction():
        service = ObligationService(ObligationRepository(session))
        res_retry_list = await service.pay_obligation_fifo(fixture.user.id, obl.id, req)
        assert len(res_retry_list) == 2
        assert [r.id for r in res_retry_list] == [r.id for r in res_list]

    # 4. Verify no double-debit in fresh session
    async with async_session() as fresh:
        acc = (
            await fresh.execute(select(Account).where(Account.id == fixture.account.id))
        ).scalar_one()
        assert acc.balance == Decimal("700.00")

        payments = list(
            (
                await fresh.execute(
                    select(ObligationPayment).where(ObligationPayment.obligation_id == obl.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(payments) == 2


# --- PHYSICAL TEST 3: LEDGER FAILURE RESTORES PHYSICAL STATE ---
@pytest.mark.asyncio
async def test_pg_legacy_ledger_failure_restores_physical_state(
    engine, session: AsyncSession, fixture: GateFixture
):
    fixture.account.balance = Decimal("1000.00")
    obl, periods = await fixture.add_obligation(Decimal("300.00"), periods=1)
    await fixture.commit()

    identity_0 = fixture.account.id
    identity_1 = fixture.user.id
    identity_2 = periods[0].id
    identity_3 = obl.id

    msg_id = str(uuid.uuid4())
    req = ObligationPaymentCreate(
        amount=Decimal("300.00"),
        account_id=identity_0,
        source_message_id=msg_id,
    )

    class SimulatedFailure(RuntimeError):
        pass

    with pytest.raises(SimulatedFailure):
        async with UnitOfWork(session).transaction():
            service = ObligationService(ObligationRepository(session))

            async def fail_record_event(*args, **kwargs):
                raise SimulatedFailure("DB or Ledger failure after debit")

            service.ledger_service.record_event = fail_record_event
            await service.pay_specific_period(identity_1, identity_2, req)

    # In fresh session, physical rollback guaranteed: balance still 1000, 0 payments, 0 events
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as fresh:
        acc = (await fresh.execute(select(Account).where(Account.id == identity_0))).scalar_one()
        assert acc.balance == Decimal("1000.00")

        per = (
            await fresh.execute(select(ObligationPeriod).where(ObligationPeriod.id == identity_2))
        ).scalar_one()
        assert per.paid_amount == Decimal("0.00")
        assert per.status == "pending_payment"

        payments = list(
            (
                await fresh.execute(
                    select(ObligationPayment).where(ObligationPayment.obligation_id == identity_3)
                )
            )
            .scalars()
            .all()
        )
        assert len(payments) == 0

        events = list(
            (
                await fresh.execute(
                    select(FinancialEvent).where(
                        FinancialEvent.source_message_id == uuid.UUID(msg_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 0


# --- PHYSICAL TEST 4: SAME MESSAGE CONFLICT ACROSS DIFFERENT OBLIGATIONS ---
@pytest.mark.asyncio
async def test_pg_legacy_same_message_conflict_across_different_obligations(
    engine, session: AsyncSession, fixture: GateFixture
):
    fixture.account.balance = Decimal("1000.00")
    obl1, periods1 = await fixture.add_obligation(Decimal("200.00"), periods=1)
    obl2, periods2 = await fixture.add_obligation(Decimal("200.00"), periods=1)
    await fixture.commit()

    identity_0 = fixture.account.id
    identity_1 = fixture.user.id
    identity_2 = periods1[0].id
    identity_3 = periods2[0].id

    msg_id = str(uuid.uuid4())
    req1 = ObligationPaymentCreate(
        amount=Decimal("200.00"),
        account_id=identity_0,
        source_message_id=msg_id,
    )
    req2 = ObligationPaymentCreate(
        amount=Decimal("200.00"),
        account_id=identity_0,
        source_message_id=msg_id,
    )

    # 1. Pay obligation 1
    async with UnitOfWork(session).transaction():
        service = ObligationService(ObligationRepository(session))
        await service.pay_specific_period(identity_1, identity_2, req1)

    # 2. Attempt to pay obligation 2 with same source_message_id
    with pytest.raises((ObligationIdempotencyConflictDomainError, ValueError)):
        async with UnitOfWork(session).transaction():
            service = ObligationService(ObligationRepository(session))
            await service.pay_specific_period(identity_1, identity_3, req2)

    # 3. Verify in fresh session: obligation 2 untouched, balance is 800 (only 1 debit)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as fresh:
        acc = (await fresh.execute(select(Account).where(Account.id == identity_0))).scalar_one()
        assert acc.balance == Decimal("800.00")

        p2 = (
            await fresh.execute(select(ObligationPeriod).where(ObligationPeriod.id == identity_3))
        ).scalar_one()
        assert p2.paid_amount == Decimal("0.00")
        assert p2.status == "pending_payment"


# --- PHYSICAL TEST 5: LEGACY CONCURRENT DOUBLE SPEND (GENUINE OVERLAP) ---
@pytest.mark.asyncio
async def test_pg_legacy_concurrent_double_spend(engine, fixture: GateFixture):
    fixture.account.balance = Decimal("1000.00")
    await fixture.add_goal_reservation(Decimal("400.00"))
    obl, periods = await fixture.add_obligation(Decimal("400.00"), periods=2)
    await fixture.commit()

    req1 = ObligationPaymentCreate(
        amount=Decimal("400.00"),
        account_id=fixture.account.id,
        source_message_id=str(uuid.uuid4()),
    )
    req2 = ObligationPaymentCreate(
        amount=Decimal("400.00"),
        account_id=fixture.account.id,
        source_message_id=str(uuid.uuid4()),
    )

    worker1_locked = asyncio.Event()
    worker1_proceed = asyncio.Event()
    worker1_pid_val: list[int] = []
    worker2_pid_ready = asyncio.Event()
    worker2_pid_val: list[int] = []

    async def worker1():
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s1:
            pid1 = await get_session_backend_pid(s1)
            worker1_pid_val.append(pid1)
            # Hold lock on Obligation root (canonical root -> period -> account lock hierarchy)
            stmt = select(Obligation).where(Obligation.id == obl.id).with_for_update()
            await s1.execute(stmt)
            worker1_locked.set()

            await asyncio.wait_for(worker1_proceed.wait(), timeout=10.0)

            service = ObligationService(ObligationRepository(s1))
            try:
                await service.pay_specific_period(fixture.user.id, periods[0].id, req1)
                await s1.commit()
                return True
            except Exception:
                await s1.rollback()
                return False

    async def worker2():
        await asyncio.wait_for(worker1_locked.wait(), timeout=10.0)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s2:
            pid2 = await get_session_backend_pid(s2)
            worker2_pid_val.append(pid2)
            worker2_pid_ready.set()

            service = ObligationService(ObligationRepository(s2))
            try:
                await service.pay_specific_period(fixture.user.id, periods[1].id, req2)
                await s2.commit()
                return True
            except ValueError as e:
                assert "Insufficient balance" in str(e)
                await s2.rollback()
                return False

    async def observer():
        await asyncio.wait_for(worker2_pid_ready.wait(), timeout=10.0)
        pid2 = worker2_pid_val[0]
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s3:
            await wait_for_blocked(
                s3, blocked_pid=pid2, blocking_pid=worker1_pid_val[0], timeout=5.0
            )
            worker1_proceed.set()

    task1 = asyncio.create_task(worker1())
    task2 = asyncio.create_task(worker2())
    task_obs = asyncio.create_task(observer())

    res1, res2, _ = await asyncio.gather(task1, task2, task_obs)
    assert res1 is True
    assert res2 is False

    # Verify from third session
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s3:
        acc = (
            await s3.execute(select(Account).where(Account.id == fixture.account.id))
        ).scalar_one()
        assert acc.balance == Decimal("600.00")

        p0 = (
            await s3.execute(select(ObligationPeriod).where(ObligationPeriod.id == periods[0].id))
        ).scalar_one()
        assert p0.paid_amount == Decimal("400.00")
        assert p0.status == "paid"

        p1 = (
            await s3.execute(select(ObligationPeriod).where(ObligationPeriod.id == periods[1].id))
        ).scalar_one()
        assert p1.paid_amount == Decimal("0.00")
        assert p1.status == "pending_payment"
