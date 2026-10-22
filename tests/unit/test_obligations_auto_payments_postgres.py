"""Physical PostgreSQL test specification for Obligation Automatic Payments (V1.7).

GUARDED FIXTURE FOR CODEX PHYSICAL GATE ONLY.
Worker must NEVER run these tests (no --physical flag).
"""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, make_url, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.accounts.repository import AccountRepository
from app.core.financial_time import financial_date
from app.fx.provider import StaticFxRateProvider
from app.fx.service import FXService
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
from tests.unit.postgres_helpers import GateFixture

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
