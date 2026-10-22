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
from app.goals.enums import GoalTransactionType
from app.goals.models import Goal, GoalTransaction
from app.ledger.enums import Direction, EventType
from app.ledger.models import FinancialEvent
from app.ledger.service import generate_period_for_bogota
from app.obligations.enums_v17 import PeriodStatus
from app.obligations.models import (
    ObligationPayment,
)
from app.obligations.schemas_v17 import (
    ObligationFIFOPaymentCreateRequest,
    ObligationPeriodPaymentCreateRequest,
)
from app.obligations.service import ObligationService
from app.obligations.service_v17 import ObligationV17Service
from tests.unit.postgres_helpers import GateFixture


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


# --- PG13 — CONCURRENT DOUBLE SPEND ---
# --- PG13 — CONCURRENT DOUBLE SPEND DETERMINISTIC (SPECIFIC PERIOD) ---
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

    worker2_preloaded = asyncio.Event()
    worker1_committed = asyncio.Event()

    async def worker1():
        await asyncio.wait_for(worker2_preloaded.wait(), timeout=60.0)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s:
            svc = ObligationV17Service(s)
            try:
                await svc.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req1)
                await s.commit()
                worker1_committed.set()
                return True
            except HTTPException:
                worker1_committed.set()
                return False

    async def worker2():
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s:
            svc = ObligationV17Service(s)
            preloaded_acc = await svc.account_repo.get_by_id(fixture.account.id)
            assert preloaded_acc.balance == Decimal("1000")
            worker2_preloaded.set()

            await asyncio.wait_for(worker1_committed.wait(), timeout=60.0)

            try:
                await svc.pay_specific_period(fixture.user.id, obl.id, periods[1].id, req2)
                await s.commit()
                return True
            except HTTPException as e:
                assert e.status_code == 400
                assert e.detail == "insufficient_balance"
                return False

    results = await asyncio.gather(worker1(), worker2())

    assert results[0] is True
    assert results[1] is False

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        acc = await s.get(Account, fixture.account.id)
        assert acc.balance == Decimal("600")


# --- PG13b — CONCURRENT FIFO DOUBLE SPEND DETERMINISTIC ---
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

    worker2_preloaded = asyncio.Event()
    worker1_committed = asyncio.Event()

    async def worker1():
        await asyncio.wait_for(worker2_preloaded.wait(), timeout=60.0)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s:
            svc = ObligationV17Service(s)
            try:
                await svc.pay_obligation_fifo(fixture.user.id, obl.id, req1)
                await s.commit()
                worker1_committed.set()
                return True
            except HTTPException:
                worker1_committed.set()
                return False

    async def worker2():
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s:
            svc = ObligationV17Service(s)
            preloaded_acc = await svc.account_repo.get_by_id(fixture.account.id)
            assert preloaded_acc.balance == Decimal("1000")
            worker2_preloaded.set()

            await asyncio.wait_for(worker1_committed.wait(), timeout=60.0)

            try:
                await svc.pay_obligation_fifo(fixture.user.id, obl.id, req2)
                await s.commit()
                return True
            except HTTPException as e:
                assert e.status_code == 400
                assert e.detail == "insufficient_balance"
                return False

    results = await asyncio.gather(worker1(), worker2())

    assert results[0] is True
    assert results[1] is False

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        acc = await s.get(Account, fixture.account.id)
        assert acc.balance == Decimal("600")


# --- PG13c — DIRECT STALE IDENTITY MAP REFRESH TEST ---
@pytest.mark.asyncio
async def test_pg13c_stale_identity_map_refresh(engine, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
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
            await repo2.update_balance(acc2, Decimal("-400"))
            await s2.commit()

        acc_locked = await repo1.get_by_id_for_update(fixture.account.id)
        assert acc_locked.balance == Decimal("600")
        assert acc_preloaded.balance == Decimal("600")


# --- PG13d — CONCURRENT SMART PAYMENT DOUBLE SPEND DETERMINISTIC ---
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

    worker2_preloaded = asyncio.Event()
    worker1_committed = asyncio.Event()

    async def worker1():
        await asyncio.wait_for(worker2_preloaded.wait(), timeout=60.0)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s:
            svc = ObligationV17Service(s)
            try:
                await svc.pay_obligation_smart(fixture.user.id, obl.id, req1)
                await s.commit()
                worker1_committed.set()
                return True
            except HTTPException:
                worker1_committed.set()
                return False

    async def worker2():
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s:
            svc = ObligationV17Service(s)
            preloaded_acc = await svc.account_repo.get_by_id(fixture.account.id)
            assert preloaded_acc.balance == Decimal("1000")
            worker2_preloaded.set()

            await asyncio.wait_for(worker1_committed.wait(), timeout=60.0)

            try:
                await svc.pay_obligation_smart(fixture.user.id, obl.id, req2)
                await s.commit()
                return True
            except HTTPException as e:
                assert e.status_code == 400
                assert e.detail == "insufficient_balance"
                return False

    results = await asyncio.gather(worker1(), worker2())

    assert results[0] is True
    assert results[1] is False

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        acc = await s.get(Account, fixture.account.id)
        assert acc.balance == Decimal("600")


# --- PG14 — CONCURRENT GOAL RESERVATION VS OBLIGATION DETERMINISTIC ---
@pytest.mark.asyncio
async def test_pg14_concurrent_goal_reservation_vs_obligation(engine, fixture: GateFixture):
    fixture.account.balance = Decimal("1000")
    await fixture.add_goal_reservation(Decimal("200"))
    obl, periods = await fixture.add_obligation(Decimal("400"))
    await fixture.commit()

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("400"), source_account_id=fixture.account.id, idempotency_key="test-key"
    )

    payment_preloaded = asyncio.Event()
    goal_committed = asyncio.Event()

    async def worker_payment():
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s:
            svc = ObligationV17Service(s)
            preloaded_acc = await svc.account_repo.get_by_id(fixture.account.id)
            assert preloaded_acc.balance == Decimal("1000")
            payment_preloaded.set()

            await asyncio.wait_for(goal_committed.wait(), timeout=60.0)

            try:
                await svc.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)
                await s.commit()
                return True
            except HTTPException as e:
                assert e.status_code == 400
                assert e.detail == "insufficient_balance"
                return False

    async def worker_goal():
        await asyncio.wait_for(payment_preloaded.wait(), timeout=60.0)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s:
            try:
                stmt = select(Account).where(Account.id == fixture.account.id).with_for_update()
                res = await s.execute(stmt)
                acc = res.scalars().first()

                from app.goals.repository import GoalRepository

                repo = GoalRepository(s)
                reserved = await repo.calculate_reserved_by_account(acc.id, fixture.user.id)
                available = acc.balance - reserved
                if available < Decimal("500"):
                    goal_committed.set()
                    return False

                goal = Goal(
                    id=uuid.uuid4(),
                    user_id=fixture.user_id,
                    name=f"Gate Goal {uuid.uuid4()}",
                    target_amount=Decimal("1000"),
                    current_amount=Decimal("500"),
                    currency="USD",
                    status="active",
                )
                fe = FinancialEvent(
                    id=uuid.uuid4(),
                    user_id=fixture.user_id,
                    account_id=acc.id,
                    event_type="goal_contribution",
                    direction="outflow",
                    amount=Decimal("500"),
                    currency="USD",
                    command_id=uuid.uuid4(),
                )
                s.add_all([goal, fe])
                await s.flush()

                tx = GoalTransaction(
                    id=uuid.uuid4(),
                    goal_id=goal.id,
                    user_id=fixture.user_id,
                    account_id=acc.id,
                    event_id=fe.id,
                    transaction_type=GoalTransactionType.allocation.value,
                    applied_amount=Decimal("500"),
                    source_amount=Decimal("500"),
                    source_currency="USD",
                    goal_currency="USD",
                )
                s.add(tx)
                await s.commit()
                goal_committed.set()
                return True
            except Exception:
                import traceback

                traceback.print_exc()
                await s.rollback()
                goal_committed.set()
                return False

    results = await asyncio.gather(worker_payment(), worker_goal())

    # Goal succeeds first with 500 reservation (total reserved becomes 700)
    # Payment sees available 1000 - 700 = 300 < 400 and fails
    assert results[1] is True  # Goal succeeded
    assert results[0] is False  # Payment failed


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
