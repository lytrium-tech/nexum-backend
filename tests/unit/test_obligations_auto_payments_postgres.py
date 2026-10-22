"""Physical PostgreSQL test specification for Obligation Automatic Payments (V1.7).

GUARDED FIXTURE FOR CODEX PHYSICAL GATE ONLY.
Worker must NEVER run these tests (no --physical flag).
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, make_url, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.core.financial_time import financial_date
from app.fx.provider import StaticFxRateProvider
from app.fx.service import FXService
from app.goals.models import Goal
from app.ledger.models import FinancialEvent
from app.obligations.auto_repository import ObligationAutoPaymentRepository
from app.obligations.auto_service import ObligationAutoPaymentService
from app.obligations.enums_v17 import PeriodStatus
from app.obligations.models import (
    Obligation,
    ObligationAutoPaymentConfig,
    ObligationAutoPaymentRun,
    ObligationPayment,
    ObligationPeriod,
)
from app.obligations.schemas_v17 import ObligationPeriodPaymentCreateRequest
from app.obligations.service_v17 import ObligationV17Service
from tests.unit.postgres_helpers import (
    GateFixture,
    get_session_backend_pid,
    wait_for_blocked,
)

EXPECTED_DATABASE = "nexum_release_gate_test"
EXPECTED_USER = "nexum_release_gate_test"
EXPECTED_REVISION = "obligations_v1_7_auto_payments"
SAFE_TUNNEL_HOSTS = {"127.0.0.1", "localhost", "::1"}
GATE_OPT_IN = "NEXUM_ALLOW_DESTRUCTIVE_TEST_DB"


def check_guards() -> str:
    database_url = os.environ.get("ARGOS_DATABASE_URL")
    if not database_url:
        pytest.skip("ARGOS_DATABASE_URL not set")
    if os.environ.get(GATE_OPT_IN) != "1":
        pytest.skip(f"Requires explicit {GATE_OPT_IN}=1 opt-in")

    parsed = make_url(database_url)
    if parsed.database != EXPECTED_DATABASE:
        pytest.fail(f"Unsafe PostgreSQL target database: {parsed.database!r}")
    if parsed.host not in SAFE_TUNNEL_HOSTS:
        pytest.fail(f"Unsafe PostgreSQL target host: {parsed.host!r}; SSH tunnel required")

    return database_url


@pytest.fixture(scope="function")
async def pg_engine() -> AsyncEngine:
    database_url = check_guards()
    engine = create_async_engine(database_url, echo=False)
    try:
        async with engine.connect() as connection:
            identity = (
                await connection.execute(
                    text(
                        "SELECT current_database(), current_user, "
                        "current_setting('server_version_num')::integer"
                    )
                )
            ).one()
            if identity[0] != EXPECTED_DATABASE or identity[1] != EXPECTED_USER:
                pytest.fail(
                    "Unsafe live PostgreSQL identity: "
                    f"database={identity[0]!r}, user={identity[1]!r}"
                )
            if identity[2] < 170000:
                pytest.fail(f"Unexpected PostgreSQL version_num: {identity[2]}")

            revision = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
            if revision != EXPECTED_REVISION:
                pytest.fail(
                    f"Unexpected Alembic revision: {revision!r}; expected {EXPECTED_REVISION!r}"
                )
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(scope="function")
async def session(pg_engine: AsyncEngine):
    async_session = sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        yield s


@pytest.fixture(scope="function")
async def fixture(session: AsyncSession):
    fix = GateFixture(session)
    fix.has_auto_payment = True
    await fix.commit()
    try:
        yield fix
    finally:
        await fix.cleanup()
        # Verify residue check: all tables must have 0 rows for this test user
        residue_checks = {
            "runs": select(func.count(ObligationAutoPaymentRun.id)).where(
                ObligationAutoPaymentRun.obligation_id.in_(
                    select(Obligation.id).where(Obligation.user_id == fix.user_id)
                )
            ),
            "configs": select(func.count(ObligationAutoPaymentConfig.id)).where(
                ObligationAutoPaymentConfig.user_id == fix.user_id
            ),
            "payments": select(func.count(ObligationPayment.id)).where(
                ObligationPayment.user_id == fix.user_id
            ),
            "events": select(func.count(FinancialEvent.id)).where(
                FinancialEvent.user_id == fix.user_id
            ),
        }
        for name, query in residue_checks.items():
            count = (await session.execute(query)).scalar_one()
            if count > 0:
                pytest.fail(f"Cleanup left residue in {name} for user {fix.user_id}: count={count}")


def _build_auto_service(session: AsyncSession) -> ObligationAutoPaymentService:
    v17_service = ObligationV17Service(session)
    fx_service = FXService(
        provider=StaticFxRateProvider({("USD", "COP"): Decimal("4000.00")}), session=session
    )
    account_repo = AccountRepository(session)
    auto_repo = ObligationAutoPaymentRepository(session)
    return ObligationAutoPaymentService(
        session=session,
        v17_service=v17_service,
        fx_service=fx_service,
        account_repo=account_repo,
        repo=auto_repo,
    )


# --- PG01: SCHEMA INTEGRITY ---
@pytest.mark.asyncio
async def test_physical_auto_payment_schema(session: AsyncSession):
    """Verify that auto payment configs and runs tables exist in PG17 with proper constraints."""
    res = await session.execute(
        text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
            "AND table_name IN ('obligation_auto_payment_configs', 'obligation_auto_payment_runs')"
        )
    )
    tables = {r[0] for r in res.fetchall()}
    assert "obligation_auto_payment_configs" in tables
    assert "obligation_auto_payment_runs" in tables

    # Verify attempt_key unique index
    idx_res = await session.execute(
        text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'obligation_auto_payment_runs' "
            "AND indexname = 'uq_oapr_attempt_key'"
        )
    )
    assert idx_res.scalar_one_or_none() is not None


# --- PG02: ATOMIC PAYMENT EXECUTION ---
@pytest.mark.asyncio
async def test_physical_auto_payment_atomic_execution(session: AsyncSession, fixture: GateFixture):
    """Verify atomic payment execution: Account debited, period marked paid, ObligationPayment and FinancialEvent created."""
    fixture.account.balance = Decimal("1000.00")
    fixture.account.currency = "USD"
    obl, periods = await fixture.add_obligation(Decimal("300.00"), currency="USD", periods=1)
    await fixture.add_auto_payment_config(obligation_id=obl.id, account_id=fixture.account.id)
    await fixture.commit()

    service = _build_auto_service(session)
    now_utc = datetime.now(UTC)
    summary = await service.process_due_auto_payments(as_of=now_utc)

    assert summary.selected == 1
    assert summary.succeeded == 1
    assert summary.failed == 0
    assert summary.technical_failures == 0

    await session.refresh(fixture.account)
    await session.refresh(periods[0])

    # Account debited exactly
    assert fixture.account.balance == Decimal("700.00")
    # Period marked fully paid
    assert periods[0].paid_amount == Decimal("300.00")
    assert periods[0].status == PeriodStatus.paid.value

    # Obligation payment record
    payments = (
        (
            await session.execute(
                select(ObligationPayment).where(ObligationPayment.user_id == fixture.user_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(payments) == 1
    assert payments[0].amount == Decimal("300.00")

    # Financial event with automatic channel metadata
    events = (
        (
            await session.execute(
                select(FinancialEvent).where(FinancialEvent.user_id == fixture.user_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].amount == Decimal("300.00")
    assert events[0].direction == "outflow"
    assert events[0].metadata_.get("channel") == "automatic"

    # Succeeded run record
    runs = (
        (
            await session.execute(
                select(ObligationAutoPaymentRun).where(
                    ObligationAutoPaymentRun.obligation_id == obl.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(runs) == 1
    assert runs[0].status == "succeeded"
    assert runs[0].result_code == "success"
    assert runs[0].amount == Decimal("300.00")


# --- PG03: DUPLICATE SCHEDULER DEDUPLICATION ---
@pytest.mark.asyncio
async def test_physical_auto_payment_duplicate_scheduler(
    session: AsyncSession, fixture: GateFixture
):
    """Verify duplicate scheduler execution on the same canonical date deduplicates safely without second debit."""
    fixture.account.balance = Decimal("1000.00")
    obl, periods = await fixture.add_obligation(Decimal("300.00"), currency="USD", periods=1)
    await fixture.add_auto_payment_config(obligation_id=obl.id, account_id=fixture.account.id)
    await fixture.commit()

    service = _build_auto_service(session)
    now_utc = datetime.now(UTC)

    # First run succeeds
    s1 = await service.process_due_auto_payments(as_of=now_utc)
    assert s1.succeeded == 1

    # Second run on same day deduplicates
    s2 = await service.process_due_auto_payments(as_of=now_utc)
    assert s2.succeeded == 0

    await session.refresh(fixture.account)
    # Balance must remain exactly 700 (no double debit)
    assert fixture.account.balance == Decimal("700.00")

    # Only 1 payment record
    payments = (
        (
            await session.execute(
                select(ObligationPayment).where(ObligationPayment.user_id == fixture.user_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(payments) == 1


# --- PG04: MANUAL PAYMENT BEFORE AUTO EVALUATION ---
@pytest.mark.asyncio
async def test_physical_auto_payment_after_manual_settlement(
    session: AsyncSession, fixture: GateFixture
):
    """A subsequent auto evaluation cannot repay a period settled manually."""
    fixture.account.balance = Decimal("1000.00")
    obl, periods = await fixture.add_obligation(Decimal("400.00"), currency="USD", periods=1)
    await fixture.add_auto_payment_config(obligation_id=obl.id, account_id=fixture.account.id)
    await fixture.commit()

    # Manual payment pays period first
    v17_service = ObligationV17Service(session)
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("400.00"),
        source_account_id=fixture.account.id,
        idempotency_key="pg-manual-race",
    )
    await v17_service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)
    await session.commit()

    # Now auto payment scheduler runs
    service = _build_auto_service(session)
    now_utc = datetime.now(UTC)
    summary = await service.process_due_auto_payments(as_of=now_utc)

    assert summary.succeeded == 0
    assert summary.skipped == 1

    await session.refresh(fixture.account)
    # Balance was debited only once by manual payment
    assert fixture.account.balance == Decimal("600.00")

    # Period remains fully paid
    await session.refresh(periods[0])
    assert periods[0].paid_amount == Decimal("400.00")
    assert periods[0].status == PeriodStatus.paid.value

    # The paid period is no longer an open due period.
    runs = (
        (
            await session.execute(
                select(ObligationAutoPaymentRun).where(
                    ObligationAutoPaymentRun.obligation_id == obl.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(runs) == 1
    assert runs[0].status == "skipped"
    assert runs[0].result_code == "no_due_periods"


# --- PG05: INSUFFICIENT BALANCE WITH GOAL RESERVATION ---
@pytest.mark.asyncio
async def test_physical_auto_payment_insufficient_with_goal_reservation(
    session: AsyncSession, fixture: GateFixture
):
    """Verify active goal reservations reduce available balance, skipping auto payment with zero debit."""
    fixture.account.balance = Decimal("1000.00")
    # Reserve 800 USD for goals -> available balance is only 200 USD
    await fixture.add_goal_reservation(Decimal("800.00"))
    # Obligation needs 500 USD
    obl, periods = await fixture.add_obligation(Decimal("500.00"), currency="USD", periods=1)
    await fixture.add_auto_payment_config(obligation_id=obl.id, account_id=fixture.account.id)
    await fixture.commit()

    service = _build_auto_service(session)
    now_utc = datetime.now(UTC)
    summary = await service.process_due_auto_payments(as_of=now_utc)

    assert summary.selected == 1
    assert summary.succeeded == 0
    assert summary.skipped == 1

    await session.refresh(fixture.account)
    await session.refresh(periods[0])

    # Account gross balance is NOT touched
    assert fixture.account.balance == Decimal("1000.00")
    # Period is untouched
    assert periods[0].paid_amount == Decimal("0")
    assert periods[0].status == PeriodStatus.pending_payment.value

    # No obligation payment created
    payments = (
        (
            await session.execute(
                select(ObligationPayment).where(ObligationPayment.user_id == fixture.user_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(payments) == 0

    # Skipped run recorded
    runs = (
        (
            await session.execute(
                select(ObligationAutoPaymentRun).where(
                    ObligationAutoPaymentRun.obligation_id == obl.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(runs) == 1
    assert runs[0].status == "skipped"
    assert runs[0].result_code == "insufficient_balance"


# --- PG06: UNDEFINED AMOUNT SKIPS WITHOUT DEBIT ---
@pytest.mark.asyncio
async def test_physical_auto_payment_undefined_amount(session: AsyncSession, fixture: GateFixture):
    """Verify period in pending_amount_definition is skipped without debit."""
    fixture.account.balance = Decimal("1000.00")
    obl, periods = await fixture.add_obligation(Decimal("500.00"), currency="USD", periods=1)
    # Set period amount to None and pending_amount_definition
    periods[0].amount = None
    periods[0].status = PeriodStatus.pending_amount_definition.value
    await fixture.add_auto_payment_config(obligation_id=obl.id, account_id=fixture.account.id)
    await fixture.commit()

    service = _build_auto_service(session)
    now_utc = datetime.now(UTC)
    summary = await service.process_due_auto_payments(as_of=now_utc)

    assert summary.selected == 1
    assert summary.succeeded == 0
    assert summary.skipped == 1

    await session.refresh(fixture.account)
    assert fixture.account.balance == Decimal("1000.00")

    runs = (
        (
            await session.execute(
                select(ObligationAutoPaymentRun).where(
                    ObligationAutoPaymentRun.obligation_id == obl.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(runs) == 1
    assert runs[0].status == "skipped"
    assert runs[0].result_code == "period_amount_not_defined"


# --- PG07: PARTIAL REMAINING PAYMENT ---
@pytest.mark.asyncio
async def test_physical_auto_payment_partial_remaining(session: AsyncSession, fixture: GateFixture):
    """Verify auto payment pays only the remaining amount on a partially paid period."""
    fixture.account.balance = Decimal("1000.00")
    obl, periods = await fixture.add_obligation(Decimal("500.00"), currency="USD", periods=1)
    # Apply prior partial payment of 200 USD manually
    v17_service = ObligationV17Service(session)
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("200.00"),
        source_account_id=fixture.account.id,
        idempotency_key="pg-prior-partial",
    )
    await v17_service.pay_specific_period(fixture.user.id, obl.id, periods[0].id, req)
    await session.commit()

    await fixture.add_auto_payment_config(obligation_id=obl.id, account_id=fixture.account.id)
    await fixture.commit()

    service = _build_auto_service(session)
    now_utc = datetime.now(UTC)
    summary = await service.process_due_auto_payments(as_of=now_utc)

    assert summary.succeeded == 1

    await session.refresh(fixture.account)
    await session.refresh(periods[0])

    # Account was 1000 - 200 (manual) - 300 (auto remaining) = 500
    assert fixture.account.balance == Decimal("500.00")
    assert periods[0].paid_amount == Decimal("500.00")
    assert periods[0].status == PeriodStatus.paid.value

    # Run recorded remaining amount
    runs = (
        (
            await session.execute(
                select(ObligationAutoPaymentRun).where(
                    ObligationAutoPaymentRun.obligation_id == obl.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(runs) == 1
    assert runs[0].status == "succeeded"
    assert runs[0].amount == Decimal("300.00")


# --- PG08: FUTURE PERIOD NOT ADVANCE PAID ---
@pytest.mark.asyncio
async def test_physical_auto_payment_future_period_not_paid(
    session: AsyncSession, fixture: GateFixture
):
    """Verify periods due in the future are never paid in advance."""
    fixture.account.balance = Decimal("1000.00")
    obl, periods = await fixture.add_obligation(Decimal("300.00"), currency="USD", periods=1)
    # Move period due date into future
    today = financial_date()
    periods[0].due_date = today + timedelta(days=15)
    await fixture.add_auto_payment_config(obligation_id=obl.id, account_id=fixture.account.id)
    await fixture.commit()

    service = _build_auto_service(session)
    now_utc = datetime.now(UTC)
    summary = await service.process_due_auto_payments(as_of=now_utc)

    assert summary.selected == 1
    assert summary.succeeded == 0
    assert summary.skipped == 1

    await session.refresh(fixture.account)
    await session.refresh(periods[0])

    # Account untouched
    assert fixture.account.balance == Decimal("1000.00")
    assert periods[0].paid_amount == Decimal("0")

    runs = (
        (
            await session.execute(
                select(ObligationAutoPaymentRun).where(
                    ObligationAutoPaymentRun.obligation_id == obl.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(runs) == 1
    assert runs[0].status == "skipped"
    assert runs[0].result_code == "no_due_periods"


@pytest.mark.asyncio
async def test_physical_auto_payment_materializes_due_period_without_frontend(
    session: AsyncSession, fixture: GateFixture
):
    """The scheduler advances recurring periods even when no client has listed them."""
    fixture.account.balance = Decimal("1000.00")
    obligation, periods = await fixture.add_obligation(Decimal("300.00"), currency="USD", periods=1)
    await session.execute(delete(ObligationPeriod).where(ObligationPeriod.id == periods[0].id))
    await fixture.add_auto_payment_config(obligation_id=obligation.id)
    await fixture.commit()

    summary = await _build_auto_service(session).process_due_auto_payments(as_of=datetime.now(UTC))
    assert summary.succeeded == 1
    await session.refresh(fixture.account)
    assert fixture.account.balance == Decimal("700.00")
    created_periods = (
        (
            await session.execute(
                select(ObligationPeriod).where(ObligationPeriod.obligation_id == obligation.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(created_periods) >= 1
    assert sum(period.paid_amount for period in created_periods) == Decimal("300.00")


@pytest.mark.asyncio
async def test_physical_auto_payment_ignores_manual_legacy_key_collision(
    session: AsyncSession, fixture: GateFixture
):
    """A manual key resembling the old auto key cannot block the remaining payment."""
    fixture.account.balance = Decimal("1000.00")
    obligation, periods = await fixture.add_obligation(Decimal("500.00"), currency="USD", periods=1)
    await fixture.commit()
    manual = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("100.00"),
        source_account_id=fixture.account.id,
        idempotency_key=f"auto:{obligation.id}:{periods[0].id}",
    )
    await ObligationV17Service(session).pay_specific_period(
        fixture.user_id, obligation.id, periods[0].id, manual
    )
    await session.commit()
    await fixture.add_auto_payment_config(obligation_id=obligation.id)
    await fixture.commit()

    summary = await _build_auto_service(session).process_due_auto_payments(as_of=datetime.now(UTC))
    assert summary.succeeded == 1
    await session.refresh(fixture.account)
    await session.refresh(periods[0])
    assert fixture.account.balance == Decimal("500.00")
    assert periods[0].paid_amount == Decimal("500.00")
    payments = (
        (
            await session.execute(
                select(ObligationPayment).where(ObligationPayment.obligation_id == obligation.id)
            )
        )
        .scalars()
        .all()
    )
    assert sorted(payment.amount for payment in payments) == [
        Decimal("100.00"),
        Decimal("400.00"),
    ]
    assert sum(payment.idempotency_key is None for payment in payments) == 1


# --- PG10: CONCURRENT MANUAL VS AUTOMATIC PAYMENT (GENUINE OVERLAP) ---
@pytest.mark.asyncio
async def test_physical_auto_payment_concurrent_manual_vs_auto_same_due_period(
    pg_engine: AsyncEngine, fixture: GateFixture
):
    """Verify manual payment and auto payment concurrently targeting same period do not double debit."""
    fixture.account.balance = Decimal("1000.00")
    obl, periods = await fixture.add_obligation(Decimal("500.00"), currency="USD", periods=1)
    await fixture.add_auto_payment_config(obligation_id=obl.id, account_id=fixture.account.id)
    await fixture.commit()

    manual_locked = asyncio.Event()
    manual_proceed = asyncio.Event()
    worker1_pid_val: list[int] = []
    worker2_pid_ready = asyncio.Event()
    worker2_pid_val: list[int] = []

    req_manual = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500.00"),
        source_account_id=fixture.account.id,
        idempotency_key="manual-vs-auto-manual-key",
    )

    async def worker_manual():
        async_session = sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s1:
            pid1 = await get_session_backend_pid(s1)
            worker1_pid_val.append(pid1)
            # Hold lock on Obligation root (canonical root -> period -> account lock hierarchy)
            stmt = select(Obligation).where(Obligation.id == obl.id).with_for_update()
            await s1.execute(stmt)
            manual_locked.set()

            # Wait for observer to confirm auto processor is blocked
            await asyncio.wait_for(manual_proceed.wait(), timeout=10.0)

            v17_svc = ObligationV17Service(s1)
            try:
                await v17_svc.pay_specific_period(
                    fixture.user.id, obl.id, periods[0].id, req_manual
                )
                await s1.commit()
                return True
            except Exception:
                await s1.rollback()
                return False

    async def worker_auto():
        await asyncio.wait_for(manual_locked.wait(), timeout=10.0)
        async_session = sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s2:
            pid2 = await get_session_backend_pid(s2)
            worker2_pid_val.append(pid2)
            worker2_pid_ready.set()

            service = _build_auto_service(s2)
            summary = await service.process_due_auto_payments(as_of=datetime.now(UTC))
            return summary

    async def observer():
        await asyncio.wait_for(worker2_pid_ready.wait(), timeout=10.0)
        pid2 = worker2_pid_val[0]
        async_session = sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s3:
            await wait_for_blocked(
                s3, blocked_pid=pid2, blocking_pid=worker1_pid_val[0], timeout=5.0
            )
            manual_proceed.set()

    task1 = asyncio.create_task(worker_manual())
    task2 = asyncio.create_task(worker_auto())
    task_obs = asyncio.create_task(observer())

    res_man, res_auto, _ = await asyncio.gather(task1, task2, task_obs)
    assert res_man is True
    # Auto processor sees period was already paid by manual winner, skips
    assert res_auto.skipped >= 1
    assert res_auto.succeeded == 0

    # Observe final state from third session
    async_session = sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s3:
        acc = await s3.get(Account, fixture.account.id)
        assert acc.balance == Decimal("500.00")  # Exactly 1 debit of 500.00

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
        assert payments[0].amount == Decimal("500.00")


# --- PG11: CONCURRENT DUPLICATE AUTOMATIC PROCESSORS (SKIP LOCKED) ---
@pytest.mark.asyncio
async def test_physical_auto_payment_concurrent_duplicate_processors(
    pg_engine: AsyncEngine, fixture: GateFixture
):
    """Verify concurrent auto processor instances do not duplicate processing due to SKIP LOCKED."""
    fixture.account.balance = Decimal("1000.00")
    obl, periods = await fixture.add_obligation(Decimal("400.00"), currency="USD", periods=1)
    await fixture.add_auto_payment_config(obligation_id=obl.id, account_id=fixture.account.id)
    await fixture.commit()

    now_utc = datetime.now(UTC)
    w1_claimed = asyncio.Event()
    w1_proceed = asyncio.Event()

    async def worker1():
        async_session = sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s1:
            repo1 = ObligationAutoPaymentRepository(s1)
            config1 = await repo1.get_next_eligible_config_for_update(as_of=now_utc)
            assert config1 is not None
            assert config1.obligation_id == obl.id
            w1_claimed.set()

            # Wait until worker2 has tried claiming
            await asyncio.wait_for(w1_proceed.wait(), timeout=10.0)

            service1 = _build_auto_service(s1)
            summary1 = await service1.process_due_auto_payments(as_of=now_utc)
            await s1.commit()
            return summary1

    async def worker2():
        await asyncio.wait_for(w1_claimed.wait(), timeout=10.0)
        async_session = sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s2:
            repo2 = ObligationAutoPaymentRepository(s2)
            # SKIP LOCKED guarantees worker2 skips the locked config
            config2 = await repo2.get_next_eligible_config_for_update(as_of=now_utc)
            assert config2 is None
            w1_proceed.set()

            service2 = _build_auto_service(s2)
            summary2 = await service2.process_due_auto_payments(as_of=now_utc)
            return summary2

    res1, res2 = await asyncio.gather(worker1(), worker2())
    assert res1.succeeded == 1
    assert res2.selected == 0

    # Observe from third session: exactly 1 payment, 1 run, 1 debit
    async_session = sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s3:
        acc = await s3.get(Account, fixture.account.id)
        assert acc.balance == Decimal("600.00")

        runs = (
            (
                await s3.execute(
                    select(ObligationAutoPaymentRun).where(
                        ObligationAutoPaymentRun.obligation_id == obl.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(runs) == 1
        assert runs[0].status == "succeeded"

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


# --- PG12: ROLLBACK AFTER REAL DEBIT TECHNICAL FAILURE ---
@pytest.mark.asyncio
async def test_physical_auto_payment_rollback_after_debit_technical_failure(
    pg_engine: AsyncEngine, fixture: GateFixture
):
    """Verify that technical failures after account debit rollback financial state and persist failure run."""
    fixture.account.balance = Decimal("1000.00")
    goal, _ = await fixture.add_goal_reservation(Decimal("200.00"), currency="USD")
    obl, periods = await fixture.add_obligation(Decimal("500.00"), currency="USD", periods=1)
    await fixture.add_auto_payment_config(obligation_id=obl.id, account_id=fixture.account.id)
    await fixture.commit()

    async_session_factory = sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session_factory() as s:
        service = _build_auto_service(s)

        # Inject controlled failure in v17_service.ledger_service.record_event
        # AFTER asserting flushed Account debit is visible in that session
        async def failing_record_event(*args, **kwargs):
            acc = await s.get(Account, fixture.account.id)
            assert acc.balance == Decimal("500.00")  # Debit flushed in session: 1000 - 500
            raise RuntimeError("Simulated crash during ledger record_event")

        service.v17_service.ledger_service.record_event = failing_record_event

        now_utc = datetime.now(UTC)
        summary = await service.process_due_auto_payments(
            as_of=now_utc, session_factory=async_session_factory
        )

        assert summary.technical_failures == 1
        assert summary.failed == 1

    # In third session: verify complete financial rollback, goal reservation unchanged, and failure run persisted
    async with async_session_factory() as s3:
        acc = await s3.get(Account, fixture.account.id)
        assert acc.balance == Decimal("1000.00")  # Balance restored

        # Goal reservation untouched
        g = await s3.get(Goal, goal.id)
        assert g.current_amount == Decimal("200.00")

        p0 = await s3.get(ObligationPeriod, periods[0].id)
        assert p0.paid_amount == Decimal("0.00")  # Period untouched
        assert p0.status == PeriodStatus.pending_payment.value

        payments = (
            (
                await s3.execute(
                    select(ObligationPayment).where(ObligationPayment.user_id == fixture.user_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(payments) == 0

        events = (
            (
                await s3.execute(
                    select(FinancialEvent).where(
                        FinancialEvent.user_id == fixture.user_id,
                        FinancialEvent.event_type == "obligation_payment",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 0

        runs = (
            (
                await s3.execute(
                    select(ObligationAutoPaymentRun).where(
                        ObligationAutoPaymentRun.obligation_id == obl.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(runs) == 1
        assert runs[0].status == "failed"
        assert runs[0].result_code == "technical_error"


# --- PG13: MIGRATION SCHEMA CHECKS, CONSTRAINTS, AND INDEXES ---
@pytest.mark.asyncio
async def test_physical_migration_schema_checks_and_indexes(
    session: AsyncSession, fixture: GateFixture
):
    """Assert new auto payment tables, check constraints, unique constraints, and indexes exist in PostgreSQL."""
    # 1. Verify tables exist
    tables = (
        (
            await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name IN "
                    "('obligation_auto_payment_configs', 'obligation_auto_payment_runs');"
                )
            )
        )
        .scalars()
        .all()
    )
    assert set(tables) == {
        "obligation_auto_payment_configs",
        "obligation_auto_payment_runs",
    }

    # 2. Setup valid parent entities for constraint testing
    obl, _ = await fixture.add_obligation(Decimal("200.00"), currency="USD", periods=1)
    cfg = await fixture.add_auto_payment_config(obligation_id=obl.id, account_id=fixture.account.id)
    await fixture.commit()

    obl_id = obl.id
    cfg_id = cfg.id
    user_id = fixture.user_id
    account_id = fixture.account.id

    # 3. Verify check constraint chk_oapc_status rejects invalid status with valid FKs
    bad_config = ObligationAutoPaymentConfig(
        id=uuid.uuid4(),
        obligation_id=obl_id,
        user_id=user_id,
        account_id=account_id,
        enabled=True,
        status="invalid_status",
    )
    session.add(bad_config)
    with pytest.raises(IntegrityError) as exc_info:
        await session.flush()
    assert "chk_oapc_status" in str(exc_info.value).lower()
    await session.rollback()

    # 4. Verify check constraint chk_oapr_status rejects invalid status with valid FKs
    bad_run = ObligationAutoPaymentRun(
        id=uuid.uuid4(),
        config_id=cfg_id,
        obligation_id=obl_id,
        attempt_key=f"test-bad-status-{uuid.uuid4()}",
        canonical_date=datetime.now(UTC).date(),
        status="bogus_status",
        result_code="bogus",
        executed_at=datetime.now(UTC),
    )
    session.add(bad_run)
    with pytest.raises(IntegrityError) as exc_info:
        await session.flush()
    assert "chk_oapr_status" in str(exc_info.value).lower()
    await session.rollback()

    # 5. Verify unique constraint uq_oapr_attempt_key
    shared_attempt_key = f"uq-attempt-{uuid.uuid4()}"
    run1 = ObligationAutoPaymentRun(
        id=uuid.uuid4(),
        config_id=cfg_id,
        obligation_id=obl_id,
        attempt_key=shared_attempt_key,
        canonical_date=datetime.now(UTC).date(),
        status="succeeded",
        result_code="paid",
        executed_at=datetime.now(UTC),
    )
    run2 = ObligationAutoPaymentRun(
        id=uuid.uuid4(),
        config_id=cfg_id,
        obligation_id=obl_id,
        attempt_key=shared_attempt_key,
        canonical_date=datetime.now(UTC).date(),
        status="succeeded",
        result_code="paid",
        executed_at=datetime.now(UTC),
    )
    session.add(run1)
    await session.flush()
    session.add(run2)
    with pytest.raises(IntegrityError) as exc_info:
        await session.flush()
    assert "uq_oapr_attempt_key" in str(exc_info.value).lower()
    await session.rollback()

    # 6. Verify all 8 indexes exist in pg_indexes
    indexes = (
        (
            await session.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = 'public' AND tablename IN "
                    "('obligation_auto_payment_configs', 'obligation_auto_payment_runs');"
                )
            )
        )
        .scalars()
        .all()
    )
    expected_indexes = {
        "ix_obligation_auto_payment_configs_status_next_eval",
        "ix_obligation_auto_payment_configs_user_id",
        "ix_obligation_auto_payment_configs_account_id",
        "ix_obligation_auto_payment_runs_config_id",
        "ix_obligation_auto_payment_runs_obligation_id",
        "ix_obligation_auto_payment_runs_canonical_date",
        "ix_obligation_auto_payment_runs_created_at",
        "ix_obligation_auto_payment_runs_payment_id",
    }
    for idx in expected_indexes:
        assert idx in indexes, f"Index {idx} missing from PostgreSQL schema"


# --- PG14: HISTORIC NULLABLE ACCOUNT PAYMENT READABILITY ---
@pytest.mark.asyncio
async def test_physical_historic_nullable_account_payment_readability(
    session: AsyncSession, fixture: GateFixture
):
    """Verify historical payments with null account_id are readable by ORM and services without crash."""
    fixture.account.balance = Decimal("1000.00")
    obl, periods = await fixture.add_obligation(Decimal("300.00"), currency="USD", periods=1)
    await fixture.commit()

    # Create historical payment with account_id = None
    historical_payment = ObligationPayment(
        id=uuid.uuid4(),
        obligation_id=obl.id,
        obligation_period_id=periods[0].id,
        user_id=fixture.user_id,
        account_id=None,  # Historic nullable Account
        financial_event_id=None,
        amount=Decimal("300.00"),
        currency="USD",
        source_amount=Decimal("300.00"),
        source_currency="USD",
        paid_at=datetime.now(UTC).replace(tzinfo=None),
        idempotency_key=f"hist-{uuid.uuid4()}",
    )
    session.add(historical_payment)
    await session.commit()

    # Verify readable via SQLAlchemy
    read_p = (
        await session.execute(
            select(ObligationPayment).where(ObligationPayment.id == historical_payment.id)
        )
    ).scalar_one()
    assert read_p.id == historical_payment.id
    assert read_p.account_id is None

    # Verify readable through V17 service payment history
    svc = ObligationV17Service(session)
    history = await svc.list_payments_for_obligation(fixture.user.id, obl.id)
    assert history.total >= 1
    found = [p for p in history.items if p.id == historical_payment.id]
    assert len(found) == 1
    assert found[0].account_id is None
