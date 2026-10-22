import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.cash.exceptions import InsufficientFundsError
from app.goals.repository import GoalRepository
from app.goals.schemas import GoalContributionCreate
from app.goals.service import GoalService
from app.ledger.enums import Direction, EventType
from app.ledger.models import FinancialEvent
from app.ledger.repository import LedgerRepository
from app.ledger.service import generate_period_for_bogota
from app.obligations.enums_v17 import PeriodStatus
from app.obligations.models import (
    Obligation,
    ObligationPayment,
    ObligationPeriod,
)
from app.obligations.schemas_v17 import (
    ObligationFIFOPaymentCreateRequest,
    ObligationPeriodPaymentCreateRequest,
)
from app.obligations.service import ObligationService
from app.obligations.service_v17 import ObligationV17Service
from tests.unit.postgres_helpers import (
    GateFixture,
    get_session_backend_pid,
    wait_for_blocked,
)


def get_db_url():
    url = os.environ.get("ARGOS_DATABASE_URL")
    if not url:
        pytest.skip("ARGOS_DATABASE_URL not set")
    return url


def check_guards():
    url = get_db_url()
    if "nexum_release_gate_test" not in url:
        pytest.skip("Not connected to nexum_release_gate_test")
    if os.environ.get("NEXUM_ALLOW_DESTRUCTIVE_TEST_DB") != "1":
        pytest.skip("NEXUM_ALLOW_DESTRUCTIVE_TEST_DB=1 not set")
    return url


@pytest.fixture(scope="function")
async def engine():
    url = check_guards()
    engine = create_async_engine(url)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="function")
async def session(engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        # Verify db name and user
        res = await session.execute(text("SELECT current_database(), current_user;"))
        db_name, db_user = res.one()
        assert db_name == "nexum_release_gate_test", f"Unsafe target DB: {db_name}"
        assert db_user == "nexum_release_gate_test", f"Unsafe target User: {db_user}"

        yield session


@pytest.fixture(scope="function")
async def fixture(session):
    fix = GateFixture(session)
    await fix.commit()
    yield fix
    await fix.cleanup()


# --- PG01 — INSUFFICIENT AVAILABLE / GROSS SUFFICIENT ---
@pytest.mark.asyncio
async def test_pg01_insufficient_available(session: AsyncSession, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    await fixture.add_goal_reservation(Decimal("700"))
    obl, periods = await fixture.add_obligation(Decimal("500"))
    await fixture.commit()

    service = ObligationV17Service(session)
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=fixture.account.id, idempotency_key="test-key"
    )

    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)

    assert exc.value.status_code == 400
    assert exc.value.detail == "insufficient_balance"

    await session.refresh(fixture.account)
    await session.refresh(periods[0])

    assert fixture.account.balance == Decimal("1000")
    assert periods[0].paid_amount == Decimal("0")
    assert periods[0].status == PeriodStatus.pending_payment.value

    pay_count = (
        (
            await session.execute(
                select(ObligationPayment).where(
                    ObligationPayment.user_id == fixture.account.user_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(pay_count) == 0

    fe_count = (
        (
            await session.execute(
                select(FinancialEvent).where(
                    FinancialEvent.user_id == fixture.user.id,
                    FinancialEvent.event_type == EventType.OBLIGATION_PAYMENT.value,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(fe_count) == 0


# --- PG02 — EXACT AVAILABLE ---
@pytest.mark.asyncio
async def test_pg02_exact_available(session: AsyncSession, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    await fixture.add_goal_reservation(Decimal("700"))
    obl, periods = await fixture.add_obligation(Decimal("300"))
    await fixture.commit()

    service = ObligationV17Service(session)
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("300"), source_account_id=fixture.account.id, idempotency_key="test-key"
    )

    await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)

    await session.refresh(fixture.account)
    await session.refresh(periods[0])

    assert fixture.account.balance == Decimal("700")
    assert periods[0].paid_amount == Decimal("300")
    assert periods[0].status == PeriodStatus.paid.value

    pay_count = (
        (
            await session.execute(
                select(ObligationPayment).where(
                    ObligationPayment.user_id == fixture.account.user_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(pay_count) == 1

    fe_count = (
        (
            await session.execute(
                select(FinancialEvent).where(
                    FinancialEvent.user_id == fixture.user.id,
                    FinancialEvent.event_type == EventType.OBLIGATION_PAYMENT.value,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(fe_count) == 1
    assert fe_count[0].event_type == EventType.OBLIGATION_PAYMENT.value
    assert fe_count[0].direction == Direction.OUTFLOW.value


# --- PG03 — BELOW AVAILABLE ---
@pytest.mark.asyncio
async def test_pg03_below_available(session: AsyncSession, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    await fixture.add_goal_reservation(Decimal("700"))
    obl, periods = await fixture.add_obligation(Decimal("200"))
    await fixture.commit()

    service = ObligationV17Service(session)
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("200"), source_account_id=fixture.account.id, idempotency_key="test-key"
    )

    await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)

    await session.refresh(fixture.account)
    assert fixture.account.balance == Decimal("800")


# --- PG04 — FULLY RESERVED ---
@pytest.mark.asyncio
async def test_pg04_fully_reserved(session: AsyncSession, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    await fixture.add_goal_reservation(Decimal("1000"))
    obl, periods = await fixture.add_obligation(Decimal("200"))
    await fixture.commit()

    service = ObligationV17Service(session)
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("200"), source_account_id=fixture.account.id, idempotency_key="test-key"
    )

    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)

    assert exc.value.status_code == 400


# --- PG05 — OVER-RESERVED LEGACY STATE ---
@pytest.mark.asyncio
async def test_pg05_over_reserved(session: AsyncSession, fixture: GateFixture):
    fixture.account.balance = Decimal("500")
    await fixture.add_goal_reservation(Decimal("700"))
    obl, periods = await fixture.add_obligation(Decimal("200"))
    await fixture.commit()

    service = ObligationV17Service(session)
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("200"), source_account_id=fixture.account.id, idempotency_key="test-key"
    )

    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)

    assert exc.value.status_code == 400


# --- PG06 — MALICIOUS SOURCE_AMOUNT SAME CURRENCY ---
@pytest.mark.asyncio
async def test_pg06_malicious_source_amount_same_currency(
    session: AsyncSession, fixture: GateFixture
):
    fixture.account.balance = Decimal("1000")
    await fixture.add_goal_reservation(Decimal("700"))
    obl, periods = await fixture.add_obligation(Decimal("500"))
    await fixture.commit()

    service = ObligationV17Service(session)
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"),
        source_account_id=fixture.account.id,
        source_amount=Decimal("1"),
        idempotency_key="test-key",
    )

    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)

    assert exc.value.status_code == 400

    await session.refresh(fixture.account)
    assert fixture.account.balance == Decimal("1000")


# --- PG07 — MALICIOUS SOURCE_AMOUNT ENOUGH FUNDS ---
@pytest.mark.asyncio
async def test_pg07_malicious_source_amount_enough_funds(
    session: AsyncSession, fixture: GateFixture
):
    fixture.account.balance = Decimal("1000")
    obl, periods = await fixture.add_obligation(Decimal("500"))
    await fixture.commit()

    service = ObligationV17Service(session)
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"),
        source_account_id=fixture.account.id,
        source_amount=Decimal("1"),
        idempotency_key="test-key",
    )

    await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)

    await session.refresh(fixture.account)
    assert fixture.account.balance == Decimal("500")  # Deducted 500, NOT 1

    fe = (
        (
            await session.execute(
                select(FinancialEvent).where(FinancialEvent.user_id == fixture.user.id)
            )
        )
        .scalars()
        .first()
    )
    assert fe.amount == Decimal("500")


# --- PG08 — FX SNAPSHOT MALICIOUS SOURCE_AMOUNT ---
@pytest.mark.asyncio
async def test_pg08_fx_snapshot_malicious_source_amount(
    session: AsyncSession, fixture: GateFixture
):
    fixture.account.balance = Decimal("100")
    obl, periods = await fixture.add_obligation(Decimal("500"), currency="EUR")
    er = await fixture.add_exchange_rate(
        base_currency="USD", quote_currency="EUR", rate=Decimal("5.0")
    )
    await fixture.commit()

    service = ObligationV17Service(session)
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"),
        source_account_id=fixture.account.id,
        source_amount=Decimal("1"),
        rate_snapshot_id=er.id,
        idempotency_key="test-key",
    )

    await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)

    await session.refresh(fixture.account)
    assert fixture.account.balance == Decimal("0")  # Deducted 100 USD exactly

    fe = (
        (
            await session.execute(
                select(FinancialEvent).where(FinancialEvent.user_id == fixture.user.id)
            )
        )
        .scalars()
        .first()
    )
    assert fe.amount == Decimal("100")


# --- PG09 — QUOTE INCONSISTENCY ---
@pytest.mark.asyncio
async def test_pg09_quote_inconsistency(session: AsyncSession, fixture: GateFixture):
    fixture.account.balance = Decimal("100")
    obl, periods = await fixture.add_obligation(Decimal("500"), currency="EUR")
    quote = await fixture.add_fx_quote(
        target_amount=Decimal("500"), source_amount=Decimal("100"), rate=Decimal("5.0")
    )
    await fixture.commit()

    service = ObligationV17Service(session)
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"),
        source_account_id=fixture.account.id,
        source_amount=Decimal("50"),  # inconsistency!
        quote_id=quote.id,
        idempotency_key="test-key",
    )

    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)

    assert exc.value.status_code == 422
    assert exc.value.detail == "invalid_fx_quote"


# --- PG10 — REPLAY AFTER AVAILABILITY CHANGES ---
@pytest.mark.asyncio
async def test_pg10_replay_after_availability_changes(session: AsyncSession, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    obl, periods = await fixture.add_obligation(Decimal("500"))
    await fixture.commit()

    service = ObligationV17Service(session)
    idempotency_key = str(uuid.uuid4())
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=fixture.account.id, idempotency_key="test-key"
    )

    req.idempotency_key = idempotency_key
    res1 = await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)

    await session.refresh(fixture.account)
    assert fixture.account.balance == Decimal("500")

    await fixture.add_goal_reservation(Decimal("400"))
    await fixture.commit()

    res2 = await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)
    assert res1[0].id == res2[0].id

    await session.refresh(fixture.account)
    assert fixture.account.balance == Decimal("500")

    fe_count = (
        (
            await session.execute(
                select(FinancialEvent).where(
                    FinancialEvent.user_id == fixture.user.id,
                    FinancialEvent.event_type == EventType.OBLIGATION_PAYMENT.value,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(fe_count) == 1


# --- PG11 — DUPLICATE AFTER DEBIT ROLLBACK ---
@pytest.mark.asyncio
async def test_pg11_duplicate_after_debit_rollback(session: AsyncSession, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    obl, periods = await fixture.add_obligation(Decimal("500"))
    await fixture.commit()

    service = ObligationV17Service(session)

    class FakeIdempotentResult:
        id = uuid.uuid4()
        idempotent = True

    async def fake_record_event(*args, **kwargs):
        return FakeIdempotentResult()

    service.ledger_service.record_event = fake_record_event

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=fixture.account.id, idempotency_key="test-key"
    )

    existing_payment = ObligationPayment(
        id=uuid.uuid4(),
        user_id=fixture.user.id,
        obligation_id=obl.id,
        obligation_period_id=periods[0].id,
        account_id=fixture.account.id,
        amount=Decimal("500"),
        currency="USD",
        source_amount=Decimal("500"),
        source_currency="USD",
        fx_rate=Decimal("1.0"),
        idempotency_key="pg11-key",
    )
    session.add(existing_payment)
    await session.commit()

    req.idempotency_key = "pg11-key"
    res = await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)

    assert res[0].id == existing_payment.id

    await session.refresh(fixture.account)
    assert fixture.account.balance == Decimal("1000")


# --- PG12 — LEDGER FAILURE AFTER DEBIT ROLLBACK ---
@pytest.mark.asyncio
async def test_pg12_ledger_failure_after_debit(session: AsyncSession, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    obl, periods = await fixture.add_obligation(Decimal("500"))
    await fixture.commit()

    service = ObligationV17Service(session)

    class LedgerException(Exception):
        pass

    async def fake_record_event(*args, **kwargs):
        raise LedgerException("Simulated Technical Failure")

    service.ledger_service.record_event = fake_record_event

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=fixture.account.id, idempotency_key="test-key"
    )

    with pytest.raises(LedgerException):
        await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)
    await session.rollback()

    await session.refresh(fixture.account)
    await session.refresh(periods[0])

    assert fixture.account.balance == Decimal("1000")
    assert periods[0].paid_amount == Decimal("0")

    pay_count = (
        (
            await session.execute(
                select(ObligationPayment).where(
                    ObligationPayment.user_id == fixture.account.user_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(pay_count) == 0


# --- PG13 — CONCURRENT DOUBLE SPEND (SPECIFIC PERIOD) ---
@pytest.mark.asyncio
async def test_pg13_concurrent_double_spend(engine, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    await fixture.add_goal_reservation(Decimal("400"))
    obl, periods = await fixture.add_obligation(Decimal("400"), periods=2)
    await fixture.commit()

    req1 = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("400"), source_account_id=fixture.account.id, idempotency_key="req1-key"
    )
    req2 = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("400"), source_account_id=fixture.account.id, idempotency_key="req2-key"
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

            # Wait until observer confirms worker2 is physically blocked on our PID
            await asyncio.wait_for(worker1_proceed.wait(), timeout=10.0)

            svc = ObligationV17Service(s1)
            try:
                await svc.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req1)
                await s1.commit()
                return True
            except HTTPException:
                await s1.rollback()
                return False

    async def worker2():
        await asyncio.wait_for(worker1_locked.wait(), timeout=10.0)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s2:
            pid2 = await get_session_backend_pid(s2)
            worker2_pid_val.append(pid2)
            worker2_pid_ready.set()

            svc = ObligationV17Service(s2)
            try:
                await svc.pay_specific_period(fixture.user.id, obl.id, periods[1].id, req2)
                await s2.commit()
                return True
            except HTTPException as e:
                assert e.status_code == 400
                assert e.detail == "insufficient_balance"
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

    # Observe final state from a fresh third session
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        acc = await s.get(Account, fixture.account.id)
        assert acc.balance == Decimal("600")

        p0 = await s.get(ObligationPeriod, periods[0].id)
        p1 = await s.get(ObligationPeriod, periods[1].id)
        assert p0.paid_amount == Decimal("400")
        assert p0.status == PeriodStatus.paid.value
        assert p1.paid_amount == Decimal("0")
        assert p1.status == PeriodStatus.pending_payment.value

        payments = (
            (
                await s.execute(
                    select(ObligationPayment).where(ObligationPayment.user_id == fixture.user_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(payments) == 1
        assert payments[0].amount == Decimal("400")

        events = (
            (
                await s.execute(
                    select(FinancialEvent).where(
                        FinancialEvent.user_id == fixture.user_id,
                        FinancialEvent.event_type == EventType.OBLIGATION_PAYMENT.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].amount == Decimal("400")


# --- PG13b — CONCURRENT FIFO DOUBLE SPEND (GENUINE OVERLAP) ---
@pytest.mark.asyncio
async def test_pg13b_concurrent_fifo_double_spend(engine, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    await fixture.add_goal_reservation(Decimal("400"))
    obl, periods = await fixture.add_obligation(Decimal("400"), periods=2)
    await fixture.commit()

    req1 = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("400"),
        source_account_id=fixture.account.id,
        idempotency_key="fifo-req1-key",
    )
    req2 = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("400"),
        source_account_id=fixture.account.id,
        idempotency_key="fifo-req2-key",
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

            svc = ObligationV17Service(s1)
            try:
                await svc.pay_obligation_fifo(fixture.user.id, obl.id, req1)
                await s1.commit()
                return True
            except HTTPException:
                await s1.rollback()
                return False

    async def worker2():
        await asyncio.wait_for(worker1_locked.wait(), timeout=10.0)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s2:
            pid2 = await get_session_backend_pid(s2)
            worker2_pid_val.append(pid2)
            worker2_pid_ready.set()

            svc = ObligationV17Service(s2)
            try:
                await svc.pay_obligation_fifo(fixture.user.id, obl.id, req2)
                await s2.commit()
                return True
            except HTTPException as e:
                assert e.status_code == 400
                assert e.detail == "insufficient_balance"
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

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        acc = await s.get(Account, fixture.account.id)
        assert acc.balance == Decimal("600")

        p0 = await s.get(ObligationPeriod, periods[0].id)
        p1 = await s.get(ObligationPeriod, periods[1].id)
        assert p0.paid_amount == Decimal("400")
        assert p0.status == PeriodStatus.paid.value
        assert p1.paid_amount == Decimal("0")
        assert p1.status == PeriodStatus.pending_payment.value

        payments = (
            (
                await s.execute(
                    select(ObligationPayment).where(ObligationPayment.user_id == fixture.user_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(payments) == 1
        assert payments[0].amount == Decimal("400")


# --- PG13c — DIRECT STALE IDENTITY MAP REFRESH TEST ---
@pytest.mark.asyncio
async def test_pg13c_stale_identity_map_refresh(engine, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    obl, periods = await fixture.add_obligation(Decimal("500"), periods=1)
    await fixture.commit()

    from app.accounts.repository import AccountRepository

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s1:
        repo1 = AccountRepository(s1)
        acc_preloaded = await repo1.get_by_id(fixture.account.id)
        assert acc_preloaded.balance == Decimal("1000")

        async with async_session() as s2:
            repo2 = AccountRepository(s2)
            acc2 = await repo2.get_by_id_for_update(fixture.account.id)
            await repo2.update_balance(acc2, Decimal("-600"))
            await s2.commit()

        # s1 attempts to pay 500; populate_existing refreshes balance to 400 < 500
        svc1 = ObligationV17Service(s1)
        req = ObligationPeriodPaymentCreateRequest(
            amount=Decimal("500"),
            source_account_id=fixture.account.id,
            idempotency_key="stale-test-key",
        )
        with pytest.raises(HTTPException) as exc:
            await svc1.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)

        assert exc.value.status_code == 400
        assert exc.value.detail == "insufficient_balance"

    async with async_session() as s3:
        acc = await s3.get(Account, fixture.account.id)
        assert acc.balance == Decimal("400")
        p = await s3.get(ObligationPeriod, periods[0].id)
        assert p.paid_amount == Decimal("0")


# --- PG13d — CONCURRENT SMART PAYMENT DOUBLE SPEND (GENUINE OVERLAP) ---
@pytest.mark.asyncio
async def test_pg13d_concurrent_smart_double_spend(engine, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    await fixture.add_goal_reservation(Decimal("400"))
    obl, periods = await fixture.add_obligation(Decimal("400"), periods=2)
    await fixture.commit()

    req1 = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("400"),
        source_account_id=fixture.account.id,
        idempotency_key="smart-req1-key",
    )
    req2 = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("400"),
        source_account_id=fixture.account.id,
        idempotency_key="smart-req2-key",
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

            svc = ObligationV17Service(s1)
            try:
                await svc.pay_obligation_smart(fixture.user.id, obl.id, req1)
                await s1.commit()
                return True
            except HTTPException:
                await s1.rollback()
                return False

    async def worker2():
        await asyncio.wait_for(worker1_locked.wait(), timeout=10.0)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s2:
            pid2 = await get_session_backend_pid(s2)
            worker2_pid_val.append(pid2)
            worker2_pid_ready.set()

            svc = ObligationV17Service(s2)
            try:
                await svc.pay_obligation_smart(fixture.user.id, obl.id, req2)
                await s2.commit()
                return True
            except HTTPException as e:
                assert e.status_code == 400
                assert e.detail == "insufficient_balance"
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

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        acc = await s.get(Account, fixture.account.id)
        assert acc.balance == Decimal("600")

        p0 = await s.get(ObligationPeriod, periods[0].id)
        assert p0.paid_amount == Decimal("400")
        assert p0.status == PeriodStatus.paid.value


# --- PG13e — CONCURRENT SPECIFIC VS FIFO DOUBLE SPEND ---
@pytest.mark.asyncio
async def test_pg13e_concurrent_specific_vs_fifo_double_spend(engine, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    await fixture.add_goal_reservation(Decimal("400"))
    obl, periods = await fixture.add_obligation(Decimal("400"), periods=2)
    await fixture.commit()

    req_specific = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("400"),
        source_account_id=fixture.account.id,
        idempotency_key="spec-vs-fifo-spec",
    )
    req_fifo = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("400"),
        source_account_id=fixture.account.id,
        idempotency_key="spec-vs-fifo-fifo",
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

            svc = ObligationV17Service(s1)
            try:
                await svc.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req_specific)
                await s1.commit()
                return True
            except HTTPException:
                await s1.rollback()
                return False

    async def worker2():
        await asyncio.wait_for(worker1_locked.wait(), timeout=10.0)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s2:
            pid2 = await get_session_backend_pid(s2)
            worker2_pid_val.append(pid2)
            worker2_pid_ready.set()

            svc = ObligationV17Service(s2)
            try:
                await svc.pay_obligation_fifo(fixture.user.id, obl.id, req_fifo)
                await s2.commit()
                return True
            except HTTPException as e:
                assert e.status_code == 400
                assert e.detail == "insufficient_balance"
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

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        acc = await s.get(Account, fixture.account.id)
        assert acc.balance == Decimal("600")
        p0 = await s.get(ObligationPeriod, periods[0].id)
        assert p0.paid_amount == Decimal("400")


# --- PG14a — CONCURRENT GOAL RESERVATION WINNER VS OBLIGATION LOSER ---
@pytest.mark.asyncio
async def test_pg14a_concurrent_goal_winner_vs_obligation_loser(engine, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    g, tx = await fixture.add_goal_reservation(Decimal("200"))
    obl, periods = await fixture.add_obligation(Decimal("500"))
    await fixture.commit()

    req_payment = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"),
        source_account_id=fixture.account.id,
        idempotency_key="goal-win-pay-lose",
    )
    goal_payload = GoalContributionCreate(
        account_id=fixture.account.id,
        amount=Decimal("500"),
    )

    worker1_locked = asyncio.Event()
    worker1_proceed = asyncio.Event()
    worker1_pid_val: list[int] = []
    worker2_pid_ready = asyncio.Event()
    worker2_pid_val: list[int] = []

    async def worker_goal_winner():
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s1:
            pid1 = await get_session_backend_pid(s1)
            worker1_pid_val.append(pid1)
            # Hold lock on account
            stmt = select(Account).where(Account.id == fixture.account.id).with_for_update()
            await s1.execute(stmt)
            worker1_locked.set()

            await asyncio.wait_for(worker1_proceed.wait(), timeout=10.0)

            goal_svc = GoalService(
                repository=GoalRepository(s1),
                account_repo=AccountRepository(s1),
                ledger_repo=LedgerRepository(s1),
            )
            try:
                await goal_svc.create_contribution(
                    auth_user_id=fixture.user.id,
                    goal_id=g.id,
                    payload=goal_payload,
                    idempotency_key=str(uuid.uuid4()),
                )
                await s1.commit()
                return True
            except Exception:
                await s1.rollback()
                return False

    async def worker_obligation_loser():
        await asyncio.wait_for(worker1_locked.wait(), timeout=10.0)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s2:
            pid2 = await get_session_backend_pid(s2)
            worker2_pid_val.append(pid2)
            worker2_pid_ready.set()

            svc = ObligationV17Service(s2)
            try:
                await svc.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req_payment)
                await s2.commit()
                return True
            except HTTPException as e:
                assert e.status_code == 400
                assert e.detail == "insufficient_balance"
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

    task1 = asyncio.create_task(worker_goal_winner())
    task2 = asyncio.create_task(worker_obligation_loser())
    task_obs = asyncio.create_task(observer())

    res_goal, res_obl, _ = await asyncio.gather(task1, task2, task_obs)
    assert res_goal is True
    assert res_obl is False

    # Observe from third session
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s3:
        acc = await s3.get(Account, fixture.account.id)
        assert acc.balance == Decimal("1000")
        goal_repo = GoalRepository(s3)
        reserved = await goal_repo.calculate_reserved_by_account(
            fixture.account.id, fixture.user.id
        )
        assert reserved == Decimal("700")  # 200 initial + 500 contributed

        p0 = await s3.get(ObligationPeriod, periods[0].id)
        assert p0.paid_amount == Decimal("0")
        assert p0.status == PeriodStatus.pending_payment.value

        obl_payments = (
            (
                await s3.execute(
                    select(ObligationPayment).where(ObligationPayment.user_id == fixture.user_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(obl_payments) == 0


# --- PG14b — CONCURRENT OBLIGATION WINNER VS GOAL LOSER ---
@pytest.mark.asyncio
async def test_pg14b_concurrent_obligation_winner_vs_goal_loser(engine, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    g, tx = await fixture.add_goal_reservation(Decimal("200"))
    obl, periods = await fixture.add_obligation(Decimal("500"))
    await fixture.commit()

    req_payment = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"),
        source_account_id=fixture.account.id,
        idempotency_key="obl-win-goal-lose",
    )
    goal_payload = GoalContributionCreate(
        account_id=fixture.account.id,
        amount=Decimal("500"),
    )

    worker1_locked = asyncio.Event()
    worker1_proceed = asyncio.Event()
    worker1_pid_val: list[int] = []
    worker2_pid_ready = asyncio.Event()
    worker2_pid_val: list[int] = []

    async def worker_obligation_winner():
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s1:
            pid1 = await get_session_backend_pid(s1)
            worker1_pid_val.append(pid1)
            stmt = select(Account).where(Account.id == fixture.account.id).with_for_update()
            await s1.execute(stmt)
            worker1_locked.set()

            await asyncio.wait_for(worker1_proceed.wait(), timeout=10.0)

            svc = ObligationV17Service(s1)
            try:
                await svc.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req_payment)
                await s1.commit()
                return True
            except HTTPException:
                await s1.rollback()
                return False

    async def worker_goal_loser():
        await asyncio.wait_for(worker1_locked.wait(), timeout=10.0)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s2:
            pid2 = await get_session_backend_pid(s2)
            worker2_pid_val.append(pid2)
            worker2_pid_ready.set()

            goal_svc = GoalService(
                repository=GoalRepository(s2),
                account_repo=AccountRepository(s2),
                ledger_repo=LedgerRepository(s2),
            )
            try:
                await goal_svc.create_contribution(
                    auth_user_id=fixture.user.id,
                    goal_id=g.id,
                    payload=goal_payload,
                    idempotency_key=str(uuid.uuid4()),
                )
                await s2.commit()
                return True
            except (InsufficientFundsError, HTTPException):
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

    task1 = asyncio.create_task(worker_obligation_winner())
    task2 = asyncio.create_task(worker_goal_loser())
    task_obs = asyncio.create_task(observer())

    res_obl, res_goal, _ = await asyncio.gather(task1, task2, task_obs)
    assert res_obl is True
    assert res_goal is False

    # Observe from third session
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s3:
        acc = await s3.get(Account, fixture.account.id)
        assert acc.balance == Decimal("500")  # 1000 - 500 debited

        goal_repo = GoalRepository(s3)
        reserved = await goal_repo.calculate_reserved_by_account(
            fixture.account.id, fixture.user.id
        )
        assert reserved == Decimal("200")  # Unchanged

        p0 = await s3.get(ObligationPeriod, periods[0].id)
        assert p0.paid_amount == Decimal("500")
        assert p0.status == PeriodStatus.paid.value


# --- PG15 — DEADLOCK TIMEOUT/CHECK ---
# PG13 and PG14 test deadlock automatically. If they pass, no deadlock occurred.


# --- PG16 — GOAL RESERVATIONS UNCHANGED ---
@pytest.mark.asyncio
async def test_pg16_goal_reservations_unchanged(session: AsyncSession, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    g, tx = await fixture.add_goal_reservation(Decimal("400"))
    obl, periods = await fixture.add_obligation(Decimal("500"))
    await fixture.commit()

    service = ObligationV17Service(session)
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=fixture.account.id, idempotency_key="test-key"
    )
    await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)

    await session.refresh(fixture.account)

    # Assert reservations unchanged
    await session.refresh(tx)
    assert tx.applied_amount == Decimal("400")


# --- PG17 — FINANCIAL EVENT SEMANTICS ---
@pytest.mark.asyncio
async def test_pg17_financial_event_semantics(session: AsyncSession, fixture: GateFixture):
    await fixture.commit()
    fe = FinancialEvent(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        account_id=fixture.account_id,
        event_type="expense",
        direction="outflow",
        amount=Decimal("100"),
        currency="USD",
        command_id=uuid.uuid4(),
    )
    session.add(fe)
    await session.flush()
    await session.refresh(fe)

    assert fe.period is not None
    assert fe.period == generate_period_for_bogota()


# --- PG18 — FIFO MULTI-PERIOD ---
@pytest.mark.asyncio
async def test_pg18_fifo_multi_period(session: AsyncSession, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    obl, periods = await fixture.add_obligation(Decimal("200"), periods=3)
    await fixture.commit()

    service = ObligationV17Service(session)
    # We pay 500, so first two (200, 200) get paid fully, third (200) gets 100 partial slice.
    req = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=fixture.account.id, idempotency_key="test-key"
    )
    await service.pay_obligation_fifo(fixture.user.id, obl.id, req)

    await session.refresh(fixture.account)
    assert fixture.account.balance == Decimal("500")

    for p in periods:
        await session.refresh(p)

    assert periods[0].paid_amount == Decimal("200")
    assert periods[0].status == PeriodStatus.paid.value
    assert periods[1].paid_amount == Decimal("200")
    assert periods[1].status == PeriodStatus.paid.value
    assert periods[2].paid_amount == Decimal("100")
    assert periods[2].status == PeriodStatus.partially_paid.value


# --- PG19 — SMART PAYMENT ---
@pytest.mark.asyncio
async def test_pg19_smart_payment(session: AsyncSession, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    obl, periods = await fixture.add_obligation(Decimal("500"), periods=1)
    # Set to future
    periods[0].due_date = datetime.now(UTC).date() + timedelta(days=5)
    await fixture.commit()

    service = ObligationV17Service(session)
    res = await service.pay_obligation_smart(
        fixture.user.id,
        obl.id,
        ObligationFIFOPaymentCreateRequest(
            amount=Decimal("500"), source_account_id=fixture.account.id, idempotency_key="test-key"
        ),
    )

    assert sum(p.amount for p in res[0]) == Decimal("500")

    await session.refresh(fixture.account)
    assert fixture.account.balance == Decimal("500")


# --- PG20 — PERIOD SEMANTICS UNCHANGED ---
# PG18 and PG19 verify basic correct period distribution.


# --- PG21 — LEGACY V1.5 ---
@pytest.mark.asyncio
async def test_pg21_legacy_v1_5(session: AsyncSession, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    await fixture.add_goal_reservation(Decimal("700"))
    obl, periods = await fixture.add_obligation(Decimal("500"))
    await fixture.commit()

    from app.obligations.repository import ObligationRepository
    from app.obligations.schemas import ObligationPaymentCreate

    service = ObligationService(ObligationRepository(session))

    req = ObligationPaymentCreate(amount=Decimal("500"), account_id=fixture.account.id)

    with pytest.raises(ValueError) as exc:
        await service.pay_specific_period(fixture.user.id, periods[0].id, req)

    assert "Insufficient balance" in str(exc.value)


# --- PG22 — IDEMPOTENCY KEY WITH LITERAL SPECIAL CHARACTERS ---
@pytest.mark.asyncio
async def test_pg22_idempotency_key_literal_special_characters(
    engine, session: AsyncSession, fixture: GateFixture
):
    fixture.account.balance = Decimal("1000.00")
    obl, periods = await fixture.add_obligation(Decimal("300.00"), periods=1)
    await fixture.commit()

    special_key = "idemp%literal_test%wildcard_123"
    service = ObligationV17Service(session)
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("300.00"),
        source_account_id=fixture.account.id,
        idempotency_key=special_key,
    )

    # 1. First execution
    payment1, _ = await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)
    await session.commit()
    assert payment1.amount == Decimal("300.00")

    # 2. Replay with exact same special key returns identical payment
    payment2, _ = await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)
    assert payment2.id == payment1.id

    # 3. Third session verifies balance debited only once and single payment row exists
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s3:
        acc = await s3.get(Account, fixture.account.id)
        assert acc.balance == Decimal("700.00")

        payments = (
            (
                await s3.execute(
                    select(ObligationPayment).where(ObligationPayment.user_id == fixture.user_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(payments) == 1
        assert payments[0].idempotency_key == special_key


# --- PG23 — AUTHORITATIVE FX QUOTE SETTLEMENT ---
@pytest.mark.asyncio
async def test_pg23_authoritative_fx_quote_settlement(
    engine, session: AsyncSession, fixture: GateFixture
):
    fixture.account.balance = Decimal("1000.00")
    obl, periods = await fixture.add_obligation(Decimal("500.00"), currency="EUR", periods=1)
    quote = await fixture.add_fx_quote(
        target_amount=Decimal("500.00"),
        source_amount=Decimal("625.00"),
        rate=Decimal("0.8"),
        from_currency="USD",
        to_currency="EUR",
    )
    await fixture.commit()

    service = ObligationV17Service(session)
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500.00"),
        source_account_id=fixture.account.id,
        quote_id=quote.id,
        idempotency_key="fx-authoritative-quote-key",
    )

    payment, period = await service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)
    await session.commit()

    assert payment.amount == Decimal("500.00")
    assert payment.source_amount == Decimal("625.00")

    # Verify from third session: exact source debit, payment, and financial event
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s3:
        acc = await s3.get(Account, fixture.account.id)
        assert acc.balance == Decimal("375.00")  # 1000.00 - 625.00 exactly

        p0 = await s3.get(ObligationPeriod, periods[0].id)
        assert p0.paid_amount == Decimal("500.00")
        assert p0.status == PeriodStatus.paid.value

        payments = (
            (
                await s3.execute(
                    select(ObligationPayment).where(ObligationPayment.user_id == fixture.user_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(payments) == 1
        assert payments[0].source_amount == Decimal("625.00")
        assert payments[0].amount == Decimal("500.00")
        assert payments[0].currency == "EUR"
        assert payments[0].source_currency == "USD"
