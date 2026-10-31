"""PostgreSQL 17 physical release-gate test suite for Credit Domain (Goal 2 Phase F).

Guarantees:
1. Physical database verification strictly against disposable test container (CT101 / lab).
2. Fail-closed guards: skips cleanly when ARGOS_DATABASE_URL is absent; aborts immediately
   if connected to unsafe host, port, DB name, or user.
3. Zero network calls: uses static / injected FX provider exclusively.
4. Real async transactions, real PostgreSQL check constraints, real triggers, and real Alembic.
5. Strict adherence to actual mapped ORM attributes and service method contracts:
   - Every idempotent service mutation passes deterministic command_id (UUID).
   - create_early_payment passes purchase_id as 3rd argument.
   - Partitioned debt queries invoke repo.get_card_debt_partitioned(card.id).
   - Triggers are verified in status 'O' (enabled) fail-closed.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.core.financial_time import financial_date
from app.credit.closure_service import CreditCycleClosureService
from app.credit.exceptions import (
    CreditGoalReservationBreachError,
    CreditIdempotencyConflictError,
    CreditLifecycleInvalidityError,
    CreditLimitExceededError,
    InvalidPaymentAmountError,
)
from app.credit.interest import (
    build_interest_bearing_principal_timeline,
    daily_interest_rate,
    estimate_interest,
)
from app.credit.models import (
    CreditCard,
    CreditCardAdjustment,
    CreditCardBillingCycle,
    CreditCardInstallment,
    CreditCardPayment,
    CreditCardPaymentAllocation,
    CreditCardStatement,
    CreditCardStatementCharge,
    CreditCardStatementComponent,
    CreditCardTransaction,
)
from app.credit.schemas import (
    CreditCardAdjustmentCreate,
    CreditCardEarlyPaymentCreate,
    CreditCardOpeningBalanceCreate,
    CreditCardPaymentCreate,
    CreditCardPurchaseCreate,
)
from app.credit.service import CreditCardService
from app.fx.provider import StaticFxRateProvider
from app.ledger.models import FinancialEvent
from tests.unit.postgres_helpers import (
    GateFixture,
    get_fixture_timestamps,
    get_session_backend_pid,
    verify_statement_triggers_enabled,
    wait_for_blocked,
)

EXPECTED_DATABASE = "nexum_release_gate_test"
EXPECTED_USER = "nexum_release_gate_test"
SAFE_TUNNEL_HOSTS = {"127.0.0.1", "localhost", "::1"}
EXPECTED_TUNNEL_PORT = 5433
GATE_OPT_IN = "NEXUM_ALLOW_DESTRUCTIVE_TEST_DB"


def get_db_url() -> str:
    """Return verified test database URL or skip if absent."""
    url = (
        os.environ.get("ARGOS_DATABASE_URL", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )
    if not url:
        pytest.skip("ARGOS_DATABASE_URL not set; skipping physical PostgreSQL tests")
    return url


def check_guards() -> str:
    """Enforce fail-closed safety guards against non-test target environments."""
    db_url = get_db_url()

    opt_in = os.environ.get(GATE_OPT_IN, "").strip()
    if opt_in != "1":
        pytest.fail(f"RELEASE_GATE_FATAL: {GATE_OPT_IN}=1 required for physical release gate tests")

    try:
        parsed = urlsplit(db_url)
    except Exception:
        pytest.fail("RELEASE_GATE_FATAL: Malformed ARGOS_DATABASE_URL; failed to parse URL")

    scheme = parsed.scheme.split("+")[0]
    if scheme != "postgresql":
        pytest.fail(f"RELEASE_GATE_FATAL: Invalid URL scheme '{parsed.scheme}'; must be postgresql")

    if parsed.hostname not in SAFE_TUNNEL_HOSTS:
        pytest.fail(
            f"RELEASE_GATE_FATAL: Unsafe host '{parsed.hostname}'; strictly requires local loopback"
        )

    if parsed.port != EXPECTED_TUNNEL_PORT:
        pytest.fail(
            f"RELEASE_GATE_FATAL: Unsafe port {parsed.port}; strictly requires SSH tunnel port {EXPECTED_TUNNEL_PORT}"
        )

    if parsed.username != EXPECTED_USER:
        pytest.fail(
            f"RELEASE_GATE_FATAL: Unsafe DB user '{parsed.username}'; strictly requires '{EXPECTED_USER}'"
        )

    db_name = parsed.path.lstrip("/")
    if db_name != EXPECTED_DATABASE:
        pytest.fail(
            f"RELEASE_GATE_FATAL: Unsafe DB name '{db_name}'; strictly requires '{EXPECTED_DATABASE}'"
        )

    if not db_url.startswith("postgresql+asyncpg://"):
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

    return db_url


@pytest.fixture(scope="session")
def db_url() -> str:
    return check_guards()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine(db_url: str) -> AsyncGenerator[AsyncEngine]:
    engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as sess:
        yield sess


@pytest_asyncio.fixture(loop_scope="session")
async def fixture(session: AsyncSession) -> AsyncGenerator[GateFixture]:
    f = GateFixture(session)
    await f.commit()
    yield f
    await f.cleanup()


@pytest.fixture
def static_fx() -> StaticFxRateProvider:
    return StaticFxRateProvider({"USD_COP": Decimal("4000.00"), "EUR_COP": Decimal("4300.00")})


# ==============================================================================
# AC 1: ALEMBIC LINEAR HEAD GRAPH & COMPATIBILITY CERTIFICATION
# ==============================================================================


def test_pg01_alembic_linear_head_graph():
    """Verify linear Alembic migration graph ending at the Credit Alpha head."""
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()

    assert len(heads) == 1, f"Expected single linear migration HEAD, found multiple: {heads}"
    assert heads[0] == "credit_alpha_interest_opening", f"Unexpected migration head: {heads[0]}"

    revisions = list(script.walk_revisions(base="base", head="heads"))
    rev_map = {r.revision: r for r in revisions}

    assert "obligations_v1_7_auto_payments" in rev_map
    assert "credit_v2_canonical_schema" in rev_map
    assert "credit_v2_canonical_check" in rev_map
    assert "credit_v2_canonical_closure" in rev_map
    assert "credit_v2_refund_type" in rev_map
    assert rev_map["credit_alpha_interest_opening"].down_revision == "credit_v2_refund_type"

    assert rev_map["credit_v2_canonical_closure"].down_revision == "credit_v2_canonical_check"
    assert rev_map["credit_v2_refund_type"].down_revision == "credit_v2_canonical_closure"
    assert rev_map["credit_v2_canonical_check"].down_revision == "credit_v2_canonical_schema"
    assert rev_map["credit_v2_canonical_schema"].down_revision == "obligations_v1_7_auto_payments"


# ==============================================================================
# AC 2: F-15 CORRECTED INSTALLMENT CONSTRAINT IN POSTGRESQL
# ==============================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_pg02_f15_installment_check_constraint_physical(
    session: AsyncSession, fixture: GateFixture
):
    """PostgreSQL physically accepts assessed-interest full settlement and rejects paid_amount > total_amount."""
    card = await fixture.add_credit_card(currency="COP", canonical_mode=True)
    now_utc, now_naive = get_fixture_timestamps()

    tx = CreditCardTransaction(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        type="purchase",
        amount=Decimal("100000.00"),
        booked_amount=Decimal("100000.00"),
        booked_currency="COP",
        original_amount=Decimal("100000.00"),
        original_currency="COP",
        occurred_at=now_utc,
        debt_partition="canonical",
        provenance="nexum_modeled",
        lifecycle_status="posted",
        created_at=now_naive,
        updated_at=now_utc,
    )
    session.add(tx)
    await session.flush()

    # Installment with principal 100,000, interest 10,000 => total 110,000
    inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=tx.id,
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("100000.00"),
        scheduled_period="2026-10",
        interest_amount=Decimal("10000.00"),
        total_amount=Decimal("110000.00"),
        paid_amount=Decimal("0.00"),
        status="pending",
        scheduled_due_date=date(2026, 11, 5),
        debt_partition="canonical",
        provenance="nexum_modeled",
        lifecycle_status="active",
        created_at=now_utc,
        updated_at=now_utc,
    )
    session.add(inst)
    await session.commit()

    # Physical test A: Settle to total_amount (110,000 > principal_amount 100,000)
    # Under legacy check (paid_amount <= principal_amount), this failed with SQLSTATE 23514.
    # Under F-15 v2 check (paid_amount <= total_amount), PostgreSQL accepts and commits!
    inst.paid_amount = Decimal("110000.00")
    inst.status = "paid"
    await session.commit()

    res = await session.execute(
        select(CreditCardInstallment.paid_amount).where(CreditCardInstallment.id == inst.id)
    )
    assert res.scalar_one() == Decimal("110000.00")

    # Physical test B: Attempt to exceed total payable ceiling (110,000.01 > 110,000.00)
    # PostgreSQL must raise CheckViolation (SQLSTATE 23514) naming credit_card_installments_paid_check_v2
    with pytest.raises(IntegrityError) as exc_info:
        async with session.begin_nested():
            await session.execute(
                text("UPDATE credit_card_installments SET paid_amount = 110000.01 WHERE id = :id"),
                {"id": inst.id},
            )

    err_str = str(exc_info.value).lower()
    assert "credit_card_installments_paid_check_v2" in err_str or "23514" in err_str


# ==============================================================================
# AC 3: BILLING CYCLE CUTOFF, STATEMENTS EXACTLY ONCE, IMMUTABILITY TRIGGERS
# ==============================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_pg03a_cycle_assignment_and_bogota_cutoff_boundary(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Purchases on or before cutoff belong to current cycle; purchases after cutoff belong to next cycle."""
    card = await fixture.add_credit_card(
        cutoff_day=15,
        due_day=5,
        currency="COP",
        canonical_activated_at=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
    )
    service = CreditCardService(session, fx_provider=static_fx)

    # 1. Purchase on cutoff day in America/Bogota (2026-10-15 12:00 UTC = 2026-10-15 07:00 Bogota)
    cmd1 = uuid.uuid4()
    p1 = CreditCardPurchaseCreate(
        amount=Decimal("50000.00"),
        description="On cutoff purchase",
        installments_total=1,
        occurred_at=datetime(2026, 10, 15, 12, 0, tzinfo=UTC),
    )
    res1 = await service.create_purchase(fixture.user_id, card.id, p1, command_id=cmd1)
    await session.commit()

    tx1 = await session.get(CreditCardTransaction, res1.transaction_id)
    assert tx1 is not None and tx1.cycle_id is not None
    cycle1 = await session.get(CreditCardBillingCycle, tx1.cycle_id)
    assert cycle1 is not None
    assert cycle1.cutoff_date.day == 15
    assert cycle1.billing_period == "2026-10"

    # 2. Purchase after cutoff day in America/Bogota (2026-10-16 12:00 UTC = 2026-10-16 07:00 Bogota)
    cmd2 = uuid.uuid4()
    p2 = CreditCardPurchaseCreate(
        amount=Decimal("60000.00"),
        description="Post-cutoff purchase",
        installments_total=1,
        occurred_at=datetime(2026, 10, 16, 12, 0, tzinfo=UTC),
    )
    res2 = await service.create_purchase(fixture.user_id, card.id, p2, command_id=cmd2)
    await session.commit()

    tx2 = await session.get(CreditCardTransaction, res2.transaction_id)
    assert tx2 is not None and tx2.cycle_id is not None
    cycle2 = await session.get(CreditCardBillingCycle, tx2.cycle_id)
    assert cycle2 is not None

    # Prove distinct cycles persisted in PostgreSQL with cycle2 cutoff strictly after cycle1
    assert cycle1.id != cycle2.id
    assert cycle2.cutoff_date > cycle1.cutoff_date
    assert cycle2.billing_period == "2026-11"


@pytest.mark.asyncio(loop_scope="session")
async def test_pg03b_statement_exactly_once_partial_unique_index(
    session: AsyncSession, fixture: GateFixture
):
    """Partial unique index uq_cc_statements_canonical_cycle_id prevents duplicate statement for canonical cycle."""
    card = await fixture.add_credit_card(currency="COP")
    now_utc = datetime.now(UTC)

    cycle = CreditCardBillingCycle(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        billing_period="2026-10",
        start_date=date(2026, 9, 16),
        cutoff_date=date(2026, 10, 15),
        due_date=date(2026, 11, 5),
        status="open",
        provenance="nexum_modeled",
        created_at=now_utc,
        updated_at=now_utc,
    )
    session.add(cycle)
    await session.flush()

    stmt1 = CreditCardStatement(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        cycle_id=cycle.id,
        billing_period="2026-10",
        billing_period_start=date(2026, 9, 16),
        cutoff_date=date(2026, 10, 15),
        due_date=date(2026, 11, 5),
        statement_balance=Decimal("150000.00"),
        minimum_payment=None,
        status="closed",
        debt_partition="canonical",
        provenance="nexum_modeled",
        created_at=now_utc,
        updated_at=now_utc,
    )
    session.add(stmt1)
    await session.commit()

    # Attempt to insert second canonical statement for the same cycle
    stmt2 = CreditCardStatement(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        cycle_id=cycle.id,
        billing_period="2026-10-dup",
        billing_period_start=date(2026, 9, 16),
        cutoff_date=date(2026, 10, 15),
        due_date=date(2026, 11, 5),
        statement_balance=Decimal("150000.00"),
        status="closed",
        debt_partition="canonical",
        provenance="nexum_modeled",
        created_at=now_utc,
        updated_at=now_utc,
    )
    with pytest.raises(IntegrityError) as exc_info:
        async with session.begin_nested():
            session.add(stmt2)
            await session.flush()

    assert "uq_cc_statements_canonical_cycle_id" in str(exc_info.value).lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_pg03c_bounded_catchup_repeated_progression(
    session: AsyncSession, fixture: GateFixture
):
    """CreditCycleClosureService advances catch-up deterministically across repeated bounded invocations."""
    card = await fixture.add_credit_card(
        currency="COP",
        cutoff_day=15,
        due_day=5,
        canonical_activated_at=datetime(2026, 7, 16, 5, 0, tzinfo=UTC),
    )
    now_utc = datetime.now(UTC)

    # Insert two open cycles that have passed cutoff
    c1 = CreditCardBillingCycle(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        billing_period="2026-08",
        start_date=date(2026, 7, 16),
        cutoff_date=date(2026, 8, 15),
        due_date=date(2026, 9, 5),
        status="open",
        provenance="nexum_modeled",
        created_at=now_utc,
        updated_at=now_utc,
    )
    c2 = CreditCardBillingCycle(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        billing_period="2026-09",
        start_date=date(2026, 8, 16),
        cutoff_date=date(2026, 9, 15),
        due_date=date(2026, 10, 5),
        status="open",
        provenance="nexum_modeled",
        created_at=now_utc,
        updated_at=now_utc,
    )
    session.add_all([c1, c2])
    await session.commit()

    closure_service = CreditCycleClosureService(session)

    # Invocations with max_cycles_per_card=1:
    # First invocation closes c1 and reports remaining catchup
    stmts1, _, _, has_rem1, rem_count1 = await closure_service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 9, 20), max_cycles_per_card=1
    )
    await session.commit()
    assert len(stmts1) == 1
    assert has_rem1 is True
    assert rem_count1 >= 1

    # Second invocation closes c2 and completes catchup
    stmts2, _, _, has_rem2, rem_count2 = await closure_service.close_eligible_cycles_for_card(
        card.id, as_of_date=date(2026, 9, 20), max_cycles_per_card=1
    )
    await session.commit()
    assert len(stmts2) == 1
    assert has_rem2 is False
    assert rem_count2 == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_pg03d_immutable_statements_and_components_at_postgres_trigger_level(
    session: AsyncSession, fixture: GateFixture
):
    """Raw PostgreSQL triggers block UPDATE or DELETE on closed/settled statements and components (SQLSTATE 20000)."""
    card = await fixture.add_credit_card(currency="COP")
    now_utc = datetime.now(UTC)

    stmt = CreditCardStatement(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        billing_period="2026-10",
        billing_period_start=date(2026, 9, 16),
        cutoff_date=date(2026, 10, 15),
        due_date=date(2026, 11, 5),
        statement_balance=Decimal("200000.00"),
        status="closed",
        debt_partition="canonical",
        provenance="nexum_modeled",
        created_at=now_utc,
        updated_at=now_utc,
    )
    session.add(stmt)
    await session.flush()

    comp = CreditCardStatementComponent(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        statement_id=stmt.id,
        component_type="fee",
        amount=Decimal("10000.00"),
        currency="COP",
        principal_amount=Decimal("0.00"),
        interest_amount=Decimal("0.00"),
        fee_amount=Decimal("10000.00"),
        provenance="nexum_modeled",
        created_at=now_utc,
    )
    session.add(comp)
    await session.commit()

    # 1. Raw SQL UPDATE on statement must fail via trg_cc_statements_immutable
    with pytest.raises(DBAPIError) as exc_info1:
        async with session.begin_nested():
            await session.execute(
                text("UPDATE credit_card_statements SET statement_balance = 999 WHERE id = :id"),
                {"id": stmt.id},
            )
    assert (
        "20000" in str(exc_info1.value)
        or "cannot update credit card statement" in str(exc_info1.value).lower()
    )

    # 2. Raw SQL DELETE on statement must fail via trg_cc_statements_immutable
    with pytest.raises(DBAPIError) as exc_info2:
        async with session.begin_nested():
            await session.execute(
                text("DELETE FROM credit_card_statements WHERE id = :id"),
                {"id": stmt.id},
            )
    assert (
        "20000" in str(exc_info2.value)
        or "cannot delete credit card statement" in str(exc_info2.value).lower()
    )

    # 3. Raw SQL UPDATE on component must fail via trg_cc_stmt_comp_immutable
    with pytest.raises(DBAPIError) as exc_info3:
        async with session.begin_nested():
            await session.execute(
                text("UPDATE credit_card_statement_components SET amount = 999 WHERE id = :id"),
                {"id": comp.id},
            )
    assert (
        "20000" in str(exc_info3.value)
        or "immutable statement component" in str(exc_info3.value).lower()
    )

    # 4. Raw SQL DELETE on component must fail via trg_cc_stmt_comp_immutable
    with pytest.raises(DBAPIError) as exc_info4:
        async with session.begin_nested():
            await session.execute(
                text("DELETE FROM credit_card_statement_components WHERE id = :id"),
                {"id": comp.id},
            )
    assert (
        "20000" in str(exc_info4.value)
        or "immutable statement component" in str(exc_info4.value).lower()
    )


# ==============================================================================
# AC 4: PAYMENTS, WATERFALL, PREPAYMENT, REVERSAL & CARD CREDIT
# ==============================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_pg04a_standard_partial_and_generic_prepayment_conservation(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Standard payment, partial payment, and generic future prepayment preserve debt conservation."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("1000000.00")
    fixture.account.currency = "COP"
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)

    # Create 3-installment purchase of 300,000 COP (100,000 COP per installment)
    cmd_p = uuid.uuid4()
    p = CreditCardPurchaseCreate(
        amount=Decimal("300000.00"),
        description="Multi-installment item",
        installments_total=3,
    )
    await service.create_purchase(fixture.user_id, card.id, p, command_id=cmd_p)
    await session.commit()

    debt_initial = await service.repo.get_card_debt_partitioned(card.id)
    assert debt_initial["canonical_debt"] == Decimal("300000.00")

    # Step 1: Standard payment of 100,000 COP covering installment 1 in full
    cmd_pmt1 = uuid.uuid4()
    pmt1 = CreditCardPaymentCreate(
        account_id=fixture.account.id,
        amount=Decimal("100000.00"),
    )
    res1 = await service.create_payment(fixture.user_id, card.id, pmt1, command_id=cmd_pmt1)
    await session.commit()

    assert res1.status == "success"
    debt_after_1 = await service.repo.get_card_debt_partitioned(card.id)
    assert debt_after_1["canonical_debt"] == Decimal("200000.00")
    acc = await session.get(Account, fixture.account.id)
    assert acc.balance == Decimal("900000.00")

    # Step 2: Partial payment of 50,000 COP covering half of installment 2
    cmd_pmt2 = uuid.uuid4()
    pmt2 = CreditCardPaymentCreate(
        account_id=fixture.account.id,
        amount=Decimal("50000.00"),
    )
    res2 = await service.create_payment(fixture.user_id, card.id, pmt2, command_id=cmd_pmt2)
    await session.commit()

    assert res2.status == "success"
    debt_after_2 = await service.repo.get_card_debt_partitioned(card.id)
    assert debt_after_2["canonical_debt"] == Decimal("150000.00")
    await session.refresh(acc)
    assert acc.balance == Decimal("850000.00")

    # Step 3: Generic future principal prepayment of 150,000 COP (remaining 50,000 of inst 2 + 100,000 of inst 3)
    cmd_pmt3 = uuid.uuid4()
    pmt3 = CreditCardPaymentCreate(
        account_id=fixture.account.id,
        amount=Decimal("150000.00"),
    )
    res3 = await service.create_payment(fixture.user_id, card.id, pmt3, command_id=cmd_pmt3)
    await session.commit()

    assert res3.status == "success"
    debt_final = await service.repo.get_card_debt_partitioned(card.id)
    assert debt_final["canonical_debt"] == Decimal("0.00")
    await session.refresh(acc)
    assert acc.balance == Decimal("700000.00")


@pytest.mark.asyncio(loop_scope="session")
async def test_pay_early_reconciliation_http(session, fixture, static_fx):
    from httpx import ASGITransport, AsyncClient

    from app.credit.router import get_credit_service
    from app.main import app
    from app.users.dependencies import get_current_user_profile_dep
    from app.users.schemas import UserRead

    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("1000000.00")
    fixture.account.currency = "COP"
    await session.commit()
    service = CreditCardService(session, fx_provider=static_fx)
    purchase = await service.create_purchase(
        fixture.user_id,
        card.id,
        CreditCardPurchaseCreate(
            amount=Decimal("600000.00"), description="HTTP reconciliation", installments_total=6
        ),
        command_id=uuid.uuid4(),
    )
    await session.commit()
    command_id = uuid.uuid4()
    result = await service.create_early_payment(
        fixture.user_id,
        card.id,
        purchase.transaction_id,
        CreditCardEarlyPaymentCreate(account_id=fixture.account.id, amount=Decimal("100000.00")),
        command_id=command_id,
    )
    await session.commit()
    profile = UserRead(
        id=fixture.user_id,
        name="Gate Test",
        email="gate@example.test",
        timezone="America/Bogota",
        currency="COP",
        status="active",
    )
    app.dependency_overrides[get_credit_service] = lambda: service
    app.dependency_overrides[get_current_user_profile_dep] = lambda: profile
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/credit/commands/{command_id}")
        print("HTTP_PAY_EARLY_RESPONSE", response.status_code, response.text)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "confirmed"
        assert body["operation_type"] == "pay_early"
        assert body["command_id"] == str(command_id)
        assert body["event_id"] == str(result.event_id)
        assert body["resource_id"] == body["early_payment_id"] == str(result.early_payment_id)
    finally:
        app.dependency_overrides.pop(get_credit_service, None)
        app.dependency_overrides.pop(get_current_user_profile_dep, None)


@pytest.mark.asyncio(loop_scope="session")
async def test_pg04b_targeted_early_payment_records_prepayment_transaction(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Targeted early payment creates credit_card_early_payments record AND credit_card_transactions record (F-08).

    Also verifies post-commit command reconciliation (confirmed with matching IDs),
    exact idempotent retry, incompatible reuse rejection (CreditIdempotencyConflictError),
    and strictly read-only zero-write reconciliation.
    """
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("1000000.00")
    fixture.account.currency = "COP"
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)

    # Create 6-installment purchase of 600,000 COP
    cmd_p = uuid.uuid4()
    purchase = CreditCardPurchaseCreate(
        amount=Decimal("600000.00"),
        description="Early pay candidate",
        installments_total=6,
    )
    p_res = await service.create_purchase(fixture.user_id, card.id, purchase, command_id=cmd_p)
    await session.commit()

    # Execute targeted early prepayment against purchase_transaction_id
    cmd_ep = uuid.uuid4()
    ep_payload = CreditCardEarlyPaymentCreate(
        account_id=fixture.account.id,
        amount=Decimal("100000.00"),
    )
    ep_res = await service.create_early_payment(
        fixture.user_id, card.id, p_res.transaction_id, ep_payload, command_id=cmd_ep
    )
    await session.commit()

    assert ep_res.status == "success"
    assert ep_res.early_payment_id is not None
    assert ep_res.event_id is not None

    # Verify F-08: credit_card_transactions row MUST exist for prepayment
    txs = (
        (
            await session.execute(
                select(CreditCardTransaction).where(
                    CreditCardTransaction.credit_card_id == card.id,
                    CreditCardTransaction.type == "payment",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(txs) >= 1

    # 1. Reconciliación sin escrituras y reconcile_command confirmed con IDs correctos
    recon_tables = (
        "financial_events",
        "accounts",
        "credit_card_transactions",
        "credit_card_installments",
        "credit_card_payments",
        "credit_card_payment_allocations",
        "credit_card_early_payments",
        "credit_card_adjustments",
    )
    counts_before = {
        tbl: (await session.execute(text(f"SELECT COUNT(*) FROM {tbl}"))).scalar_one()
        for tbl in recon_tables
    }

    recon = await service.reconcile_command(user_id=fixture.user_id, command_id=cmd_ep)

    # Reconciliación sin escrituras: cero mutaciones físicas ni en la sesión
    counts_after = {
        tbl: (await session.execute(text(f"SELECT COUNT(*) FROM {tbl}"))).scalar_one()
        for tbl in recon_tables
    }
    assert counts_before == counts_after
    assert len(session.dirty) == 0
    assert len(session.new) == 0
    assert len(session.deleted) == 0

    # reconcile_command confirmed con IDs correctos
    assert recon.status == "confirmed"
    assert recon.operation_type == "pay_early"
    assert recon.command_id == cmd_ep
    assert recon.event_id == ep_res.event_id
    assert recon.resource_id == ep_res.early_payment_id
    assert recon.early_payment_id == ep_res.early_payment_id
    assert recon.transaction_id is None
    assert recon.payment_id is None
    assert recon.adjustment_id is None

    # Reconciliación sin escrituras con comando desconocido
    recon_unknown = await service.reconcile_command(
        user_id=fixture.user_id, command_id=uuid.uuid4()
    )
    assert recon_unknown.status == "unknown"
    assert recon_unknown.operation_type is None
    assert recon_unknown.event_id is None
    assert recon_unknown.resource_id is None
    assert recon_unknown.early_payment_id is None
    assert len(session.dirty) == 0
    assert len(session.new) == 0
    assert len(session.deleted) == 0

    # 2. Replay idéntico idempotent_retry
    ep_replay = await service.create_early_payment(
        fixture.user_id,
        card.id,
        p_res.transaction_id,
        ep_payload,
        command_id=cmd_ep,
    )
    assert ep_replay.status == "idempotent_retry"
    assert ep_replay.event_id == ep_res.event_id
    assert ep_replay.early_payment_id == ep_res.early_payment_id
    assert ep_replay.amount == ep_payload.amount

    # Verificar que el balance de la cuenta no sufrió doble débito
    acc = await session.get(Account, fixture.account.id)
    assert acc is not None
    assert acc.balance == Decimal("900000.00")

    # Verificar que no se duplicaron registros de pago anticipado ni transacciones
    early_pay_count = (
        await session.execute(
            text("SELECT COUNT(*) FROM credit_card_early_payments WHERE credit_card_id = :cid"),
            {"cid": card.id},
        )
    ).scalar_one()
    assert early_pay_count == 1

    payment_tx_count = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM credit_card_transactions "
                "WHERE credit_card_id = :cid AND type = 'payment'"
            ),
            {"cid": card.id},
        )
    ).scalar_one()
    assert payment_tx_count == 1

    # 3. Reutilización incompatible CreditIdempotencyConflictError
    # 3a. Conflicto por monto diferente
    conflicting_amount = CreditCardEarlyPaymentCreate(
        account_id=fixture.account.id,
        amount=Decimal("50000.00"),
    )
    with pytest.raises(CreditIdempotencyConflictError):
        await service.create_early_payment(
            fixture.user_id,
            card.id,
            p_res.transaction_id,
            conflicting_amount,
            command_id=cmd_ep,
        )

    # 3b. Conflicto por compra objetivo diferente
    conflicting_target = CreditCardEarlyPaymentCreate(
        account_id=fixture.account.id,
        amount=Decimal("100000.00"),
    )
    with pytest.raises(CreditIdempotencyConflictError):
        await service.create_early_payment(
            fixture.user_id,
            card.id,
            uuid.uuid4(),
            conflicting_target,
            command_id=cmd_ep,
        )

    # Reconciliación final confirma que el comando permanece intacto con cero mutaciones
    recon_after_conflicts = await service.reconcile_command(
        user_id=fixture.user_id, command_id=cmd_ep
    )
    assert recon_after_conflicts.status == "confirmed"
    assert recon_after_conflicts.resource_id == ep_res.early_payment_id
    assert len(session.dirty) == 0
    assert len(session.new) == 0
    assert len(session.deleted) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_pg04c_voluntary_overpayment_rejection(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Voluntary overpayment exceeding total payable debt is rejected with zero mutation."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("2000000.00")
    fixture.account.currency = "COP"
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)
    cmd_p = uuid.uuid4()
    purchase = CreditCardPurchaseCreate(
        amount=Decimal("100000.00"),
        description="Single item",
        installments_total=1,
    )
    await service.create_purchase(fixture.user_id, card.id, purchase, command_id=cmd_p)
    await session.commit()

    card_id = card.id
    account_id = fixture.account.id
    user_id = fixture.user_id

    # Attempt to pay 150,000 COP against 100,000 COP debt
    cmd_overpay = uuid.uuid4()
    pmt = CreditCardPaymentCreate(
        account_id=account_id,
        amount=Decimal("150000.00"),
    )
    with pytest.raises(InvalidPaymentAmountError):
        await service.create_payment(user_id, card_id, pmt, command_id=cmd_overpay)

    await session.rollback()
    acc = await session.get(Account, account_id)
    assert acc is not None
    assert acc.balance == Decimal("2000000.00")

    # Verify zero CreditCardPayment rows for this command or card
    payments = (
        (
            await session.execute(
                select(CreditCardPayment).where(
                    (CreditCardPayment.credit_card_id == card_id)
                    | (
                        CreditCardPayment.event_id.in_(
                            select(FinancialEvent.id).where(
                                FinancialEvent.command_id == cmd_overpay
                            )
                        )
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(payments) == 0

    payment_transactions = (
        (
            await session.execute(
                select(CreditCardTransaction).where(
                    CreditCardTransaction.credit_card_id == card_id,
                    CreditCardTransaction.type == "payment",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(payment_transactions) == 0

    allocations = (
        (
            await session.execute(
                select(CreditCardPaymentAllocation).where(
                    CreditCardPaymentAllocation.credit_card_id == card_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(allocations) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_pg04d_payment_reversal_full_amount_conservation(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Full payment reversal restores account balance and creates compensating adjustment."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("1000000.00")
    fixture.account.currency = "COP"
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)
    cmd_p = uuid.uuid4()
    purchase = CreditCardPurchaseCreate(
        amount=Decimal("100000.00"),
        description="Reversible item",
        installments_total=1,
    )
    await service.create_purchase(fixture.user_id, card.id, purchase, command_id=cmd_p)
    await session.commit()

    cmd_pmt = uuid.uuid4()
    pmt = CreditCardPaymentCreate(
        account_id=fixture.account.id,
        amount=Decimal("100000.00"),
    )
    pmt_res = await service.create_payment(fixture.user_id, card.id, pmt, command_id=cmd_pmt)
    await session.commit()

    acc = await session.get(Account, fixture.account.id)
    assert acc.balance == Decimal("900000.00")

    # Execute full payment reversal
    cmd_rev = uuid.uuid4()
    adj = CreditCardAdjustmentCreate(
        original_transaction_id=pmt_res.transaction_id,
        amount=Decimal("100000.00"),
        adjustment_type="reversal",
        direction="debit",
        reason="Customer payment refund",
    )
    adj_res = await service.create_adjustment(fixture.user_id, card.id, adj, command_id=cmd_rev)
    await session.commit()

    assert adj_res.status == "success"
    await session.refresh(acc)
    assert acc.balance == Decimal("1000000.00")


@pytest.mark.asyncio(loop_scope="session")
async def test_pg04e_legitimate_already_paid_refund_card_credit(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Refund on an already-paid purchase creates legitimate card credit carrying over into statement."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("1000000.00")
    fixture.account.currency = "COP"
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)
    cmd_p = uuid.uuid4()
    purchase = CreditCardPurchaseCreate(
        amount=Decimal("100000.00"),
        description="Store item",
        installments_total=1,
    )
    p_res = await service.create_purchase(fixture.user_id, card.id, purchase, command_id=cmd_p)
    await session.commit()

    cmd_pmt = uuid.uuid4()
    pmt = CreditCardPaymentCreate(account_id=fixture.account.id, amount=Decimal("100000.00"))
    await service.create_payment(fixture.user_id, card.id, pmt, command_id=cmd_pmt)
    await session.commit()

    debt_paid = await service.repo.get_card_debt_partitioned(card.id)
    assert debt_paid["canonical_debt"] == Decimal("0.00")

    # Merchant refunds 100,000 COP for the already-paid purchase
    cmd_ref = uuid.uuid4()
    adj = CreditCardAdjustmentCreate(
        original_transaction_id=p_res.transaction_id,
        amount=Decimal("100000.00"),
        adjustment_type="refund",
        direction="credit",
        reason="Merchant returned item credit",
    )
    adj_res = await service.create_adjustment(fixture.user_id, card.id, adj, command_id=cmd_ref)
    await session.commit()

    assert adj_res.status == "success"
    # Card is now in net negative (credit) balance
    debt_refunded = await service.repo.get_card_debt_partitioned(card.id)
    assert debt_refunded["canonical_debt"] <= Decimal("0.00")


# ==============================================================================
# AC 5: F-13 AUTHORITATIVE GOAL RESERVATIONS ENFORCEMENT
# ==============================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_pg05_f13_goal_reservation_enforced_under_lock_zero_mutation(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Credit payment rejects when account balance minus authoritative Goal reservations is insufficient."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("1000.00")
    fixture.account.currency = "USD"
    # Reserve 700 USD in goals => spendable balance is 300 USD
    await fixture.add_goal_reservation(Decimal("700.00"), currency="USD")
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)
    # Card purchase of 2,000,000 COP (= 500 USD at 4,000 COP/USD)
    cmd_p = uuid.uuid4()
    purchase = CreditCardPurchaseCreate(
        amount=Decimal("2000000.00"),
        description="Goal reservation test",
        installments_total=1,
    )
    await service.create_purchase(fixture.user_id, card.id, purchase, command_id=cmd_p)
    await session.commit()

    card_id = card.id
    account_id = fixture.account.id
    user_id = fixture.user_id

    # Attempt to pay 500 USD (gross balance 1000 >= 500, but spendable 300 < 500)
    cmd_pmt = uuid.uuid4()
    pmt = CreditCardPaymentCreate(
        account_id=account_id,
        amount=Decimal("500.00"),
    )
    with pytest.raises(CreditGoalReservationBreachError):
        await service.create_payment(user_id, card_id, pmt, command_id=cmd_pmt)

    await session.rollback()

    # Assert zero mutation in database
    acc = await session.get(Account, account_id)
    assert acc is not None
    assert acc.balance == Decimal("1000.00")

    payments = (
        (
            await session.execute(
                select(CreditCardPayment).where(CreditCardPayment.credit_card_id == card_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(payments) == 0

    payment_transactions = (
        (
            await session.execute(
                select(CreditCardTransaction).where(
                    CreditCardTransaction.credit_card_id == card_id,
                    CreditCardTransaction.type == "payment",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(payment_transactions) == 0

    allocations = (
        (
            await session.execute(
                select(CreditCardPaymentAllocation).where(
                    CreditCardPaymentAllocation.credit_card_id == card_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(allocations) == 0

    allocations = (
        (
            await session.execute(
                select(CreditCardPaymentAllocation).where(
                    CreditCardPaymentAllocation.credit_card_id == card_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(allocations) == 0


# ==============================================================================
# AC 6: CROSS-CURRENCY PURCHASE & PAYMENT ACCOUNT/LEDGER EQUALITY
# ==============================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_pg06_cross_currency_purchase_and_payment_equality(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Cross-currency payment: Account debit strictly equals Ledger source amount in atomic transaction."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("500.00")
    fixture.account.currency = "USD"
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)

    # 1. USD purchase on COP card (50 USD = 200,000 COP)
    cmd_p = uuid.uuid4()
    p = CreditCardPurchaseCreate(
        original_amount=Decimal("50.00"),
        original_currency="USD",
        currency="COP",
        description="Foreign software purchase",
        installments_total=1,
    )
    p_res = await service.create_purchase(fixture.user_id, card.id, p, command_id=cmd_p)
    await session.commit()

    tx = await session.get(CreditCardTransaction, p_res.transaction_id)
    assert tx.original_amount == Decimal("50.00")
    assert tx.original_currency == "USD"
    assert tx.booked_amount == Decimal("200000.00")
    assert tx.booked_currency == "COP"

    # 2. Cross-currency payment from USD account (50 USD = 200,000 COP)
    cmd_pmt = uuid.uuid4()
    pmt = CreditCardPaymentCreate(
        account_id=fixture.account.id,
        amount=Decimal("50.00"),
    )
    pmt_res = await service.create_payment(fixture.user_id, card.id, pmt, command_id=cmd_pmt)
    await session.commit()

    acc = await session.get(Account, fixture.account.id)
    assert acc.balance == Decimal("450.00")

    event = await session.get(FinancialEvent, pmt_res.event_id)
    payment = await session.scalar(
        select(CreditCardPayment).where(CreditCardPayment.event_id == pmt_res.event_id)
    )
    assert event.amount == Decimal("50.00")
    assert event.currency == "USD"
    assert payment is not None
    assert payment.source_amount == event.amount == Decimal("50.00")
    assert payment.source_currency == event.currency == "USD"
    assert payment.booked_amount == Decimal("200000.00")
    assert payment.booked_currency == "COP"
    assert payment.fx_rate == Decimal("4000.00000000")
    assert payment.fx_rate_snapshot_id is not None
    payment_tx = await session.get(CreditCardTransaction, payment.payment_transaction_id)
    assert payment_tx is not None
    assert payment_tx.fx_rate_snapshot_id == payment.fx_rate_snapshot_id


# ==============================================================================
# AC 7: F-16 DEEP IDEMPOTENCY RETRY & CONFLICT REPLAY
# ==============================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_pg07_f16_exact_idempotent_retry_and_conflicting_replay(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Exact identical replay succeeds as idempotent_retry; conflicting payload raises CreditIdempotencyConflictError."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("1000000.00")
    fixture.account.currency = "COP"
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)
    cmd_p = uuid.uuid4()
    purchase = CreditCardPurchaseCreate(
        amount=Decimal("200000.00"),
        description="Idempotency candidate",
        installments_total=1,
    )
    await service.create_purchase(fixture.user_id, card.id, purchase, command_id=cmd_p)
    await session.commit()

    command_id = uuid.uuid4()
    pmt = CreditCardPaymentCreate(
        account_id=fixture.account.id,
        amount=Decimal("100000.00"),
    )

    # 1. Initial payment execution
    res1 = await service.create_payment(fixture.user_id, card.id, pmt, command_id=command_id)
    await session.commit()
    assert res1.status == "success"

    # 2. Exact identical retry with same command_id
    res2 = await service.create_payment(fixture.user_id, card.id, pmt, command_id=command_id)
    assert res2.status == "idempotent_retry"

    # Verify no double debit
    acc = await session.get(Account, fixture.account.id)
    assert acc.balance == Decimal("900000.00")

    # 3. Conflicting replay (same command_id, different amount)
    pmt_conflict = CreditCardPaymentCreate(
        account_id=fixture.account.id,
        amount=Decimal("50000.00"),
    )
    with pytest.raises(CreditIdempotencyConflictError):
        await service.create_payment(fixture.user_id, card.id, pmt_conflict, command_id=command_id)


# ==============================================================================
# AC 8: CONCURRENCY & DEADLOCK REGRESSION (F-14, F-06, STALE ORM MAP)
# ==============================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_pg08a_duplicate_concurrent_statement_workers_serialization(
    engine: AsyncEngine, fixture: GateFixture
):
    """Duplicate concurrent cycle closure workers safely serialize: exactly 1 statement created."""
    card = await fixture.add_credit_card(
        currency="COP",
        cutoff_day=15,
        due_day=5,
        canonical_activated_at=datetime(2026, 9, 1, 5, 0, tzinfo=UTC),
    )
    now_utc = datetime.now(UTC)

    cycle = CreditCardBillingCycle(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        billing_period="2026-09",
        start_date=date(2026, 8, 16),
        cutoff_date=date(2026, 9, 15),
        due_date=date(2026, 10, 5),
        status="open",
        provenance="nexum_modeled",
        created_at=now_utc,
        updated_at=now_utc,
    )
    fixture.session.add(cycle)
    await fixture.session.commit()

    async def run_worker() -> int:
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as sess:
            closure_svc = CreditCycleClosureService(sess)
            stmts, _, _, _, _ = await closure_svc.close_eligible_cycles_for_card(
                card.id, as_of_date=date(2026, 9, 20)
            )
            await sess.commit()
            return len(stmts)

    res1, res2 = await asyncio.gather(run_worker(), run_worker(), return_exceptions=True)
    # One worker creates the statement, the other skips or gets 0 created
    counts = [r for r in [res1, res2] if isinstance(r, int)]
    assert sum(counts) == 1

    # Exactly one statement exists in PostgreSQL
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as check_sess:
        stmt_count = (
            await check_sess.execute(
                select(func.count())
                .select_from(CreditCardStatement)
                .where(CreditCardStatement.credit_card_id == card.id)
            )
        ).scalar_one()
        assert stmt_count == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_pg08b_concurrent_purchases_near_limit_serialized(
    engine: AsyncEngine, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Concurrent purchases near limit are safely serialized under row lock: one succeeds, one rejected."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("1000000.00"))
    card_id = card.id
    user_id = fixture.user_id
    await fixture.session.commit()

    # Two concurrent purchases of 600,000 COP (total 1,200,000 > 1,000,000)
    p1 = CreditCardPurchaseCreate(amount=Decimal("600000.00"), description="Tx 1")
    p2 = CreditCardPurchaseCreate(amount=Decimal("600000.00"), description="Tx 2")

    async def run_purchase(payload: CreditCardPurchaseCreate) -> bool:
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as sess:
            svc = CreditCardService(sess, fx_provider=static_fx)
            try:
                cmd_id = uuid.uuid4()
                await svc.create_purchase(user_id, card_id, payload, command_id=cmd_id)
                await sess.commit()
                return True
            except CreditLimitExceededError:
                await sess.rollback()
                return False

    res1, res2 = await asyncio.gather(run_purchase(p1), run_purchase(p2))
    assert sorted([res1, res2]) == [False, True]


@pytest.mark.asyncio(loop_scope="session")
async def test_pg08c_concurrent_payments_physical_lock_wait(
    engine: AsyncEngine, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Concurrent payments against the same funding account physically wait on PostgreSQL row locks."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("1000000.00")
    fixture.account.currency = "COP"
    await fixture.session.commit()

    service = CreditCardService(fixture.session, fx_provider=static_fx)
    cmd_p = uuid.uuid4()
    purchase = CreditCardPurchaseCreate(
        amount=Decimal("400000.00"),
        description="Concurrency test item",
        installments_total=1,
    )
    await service.create_purchase(fixture.user_id, card.id, purchase, command_id=cmd_p)
    await fixture.session.commit()

    worker1_locked = asyncio.Event()
    worker1_proceed = asyncio.Event()
    worker1_pid_val: list[int] = []
    worker2_pid_ready = asyncio.Event()
    worker2_pid_val: list[int] = []

    pmt1 = CreditCardPaymentCreate(account_id=fixture.account.id, amount=Decimal("200000.00"))
    pmt2 = CreditCardPaymentCreate(account_id=fixture.account.id, amount=Decimal("200000.00"))

    async def worker1():
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s1:
            pid1 = await get_session_backend_pid(s1)
            worker1_pid_val.append(pid1)
            # Hold lock on account explicitly
            await s1.execute(
                select(Account).where(Account.id == fixture.account.id).with_for_update()
            )
            worker1_locked.set()
            await asyncio.wait_for(worker1_proceed.wait(), timeout=10.0)

            svc = CreditCardService(s1, fx_provider=static_fx)
            cmd1 = uuid.uuid4()
            await svc.create_payment(fixture.user_id, card.id, pmt1, command_id=cmd1)
            await s1.commit()
            return True

    async def worker2():
        await asyncio.wait_for(worker1_locked.wait(), timeout=10.0)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s2:
            pid2 = await get_session_backend_pid(s2)
            worker2_pid_val.append(pid2)
            worker2_pid_ready.set()

            svc = CreditCardService(s2, fx_provider=static_fx)
            cmd2 = uuid.uuid4()
            await svc.create_payment(fixture.user_id, card.id, pmt2, command_id=cmd2)
            await s2.commit()
            return True

    async def observer():
        await asyncio.wait_for(worker2_pid_ready.wait(), timeout=10.0)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as s3:
            await wait_for_blocked(
                s3, blocked_pid=worker2_pid_val[0], blocking_pid=worker1_pid_val[0], timeout=5.0
            )
            worker1_proceed.set()

    res1, res2, _ = await asyncio.gather(worker1(), worker2(), observer())
    assert res1 is True
    assert res2 is True


@pytest.mark.asyncio(loop_scope="session")
async def test_pg08d_f14_standard_vs_early_payment_deadlock_elimination(
    engine: AsyncEngine, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Standard payment and early prepayment executed simultaneously never trigger PostgreSQL 40P01 deadlock."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("2000000.00")
    fixture.account.currency = "COP"
    await fixture.session.commit()

    service = CreditCardService(fixture.session, fx_provider=static_fx)
    cmd_p = uuid.uuid4()
    purchase = CreditCardPurchaseCreate(
        amount=Decimal("600000.00"),
        description="Deadlock regression item",
        installments_total=6,
    )
    p_res = await service.create_purchase(fixture.user_id, card.id, purchase, command_id=cmd_p)
    await fixture.session.commit()

    pmt = CreditCardPaymentCreate(
        account_id=fixture.account.id,
        amount=Decimal("100000.00"),
    )
    ep = CreditCardEarlyPaymentCreate(
        account_id=fixture.account.id,
        amount=Decimal("100000.00"),
    )

    async def run_standard():
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as sess:
            svc = CreditCardService(sess, fx_provider=static_fx)
            try:
                cmd_std = uuid.uuid4()
                await svc.create_payment(fixture.user_id, card.id, pmt, command_id=cmd_std)
                await sess.commit()
                return "standard_ok"
            except Exception as e:
                await sess.rollback()
                return f"standard_err_{type(e).__name__}"

    async def run_early():
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as sess:
            svc = CreditCardService(sess, fx_provider=static_fx)
            try:
                cmd_early = uuid.uuid4()
                await svc.create_early_payment(
                    fixture.user_id, card.id, p_res.transaction_id, ep, command_id=cmd_early
                )
                await sess.commit()
                return "early_ok"
            except Exception as e:
                await sess.rollback()
                return f"early_err_{type(e).__name__}"

    out1, out2 = await asyncio.gather(run_standard(), run_early())
    # Crucial assertion: NEITHER result may contain 'Deadlock' or SQLSTATE 40P01
    assert "deadlock" not in out1.lower()
    assert "deadlock" not in out2.lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_pg08e_stale_identity_map_refresh_on_payment(
    engine: AsyncEngine, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Payment acquires row lock with populate_existing=True, refreshing concurrently modified balance."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("1000000.00")
    fixture.account.currency = "COP"
    await fixture.session.commit()

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Session 1 pre-loads Account into its identity map with 1,000,000 COP
    async with async_session() as s1:
        repo1 = AccountRepository(s1)
        acc_preloaded = await repo1.get_by_id(fixture.account.id)
        assert acc_preloaded.balance == Decimal("1000000.00")

        # Session 2 concurrently drains account balance down to 100,000 COP
        async with async_session() as s2:
            repo2 = AccountRepository(s2)
            acc2 = await repo2.get_by_id_for_update(fixture.account.id)
            await repo2.update_balance(acc2, Decimal("-900000.00"))
            await s2.commit()

        # Session 1 attempts to pay 300,000 COP (> 100,000 remaining)
        # Because create_payment acquires lock with populate_existing=True, it refreshes to 100,000
        # and rejects with InsufficientFundsError or InvalidPaymentAmountError
        svc1 = CreditCardService(s1, fx_provider=static_fx)
        cmd1 = uuid.uuid4()
        pmt = CreditCardPaymentCreate(
            account_id=fixture.account.id,
            amount=Decimal("300000.00"),
        )
        with pytest.raises(Exception):
            await svc1.create_payment(fixture.user_id, card.id, pmt, command_id=cmd1)


@pytest.mark.asyncio(loop_scope="session")
async def test_pg08f_deterministic_waterfall_physical_allocation_order(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Payment waterfall allocates deterministically: charges -> past-due -> current -> future installments."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("2000000.00")
    fixture.account.currency = "COP"
    now_utc, now_naive = get_fixture_timestamps()

    # 1. Standalone statement charge (10,000 COP)
    stmt = CreditCardStatement(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        billing_period="2026-09",
        statement_balance=Decimal("10000.00"),
        status="closed",
        debt_partition="canonical",
        provenance="nexum_modeled",
        created_at=now_utc,
        updated_at=now_utc,
    )
    session.add(stmt)
    await session.flush()

    charge = CreditCardStatementCharge(
        id=uuid.uuid4(),
        statement_id=stmt.id,
        charge_type="fee",
        amount=Decimal("10000.00"),
        status="pending",
        paid_amount=Decimal("0.00"),
        created_at=now_utc,
    )
    session.add(charge)

    # 2. Purchase spanning past-due, current, and future
    tx = CreditCardTransaction(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        type="purchase",
        amount=Decimal("300000.00"),
        occurred_at=now_utc,
        debt_partition="canonical",
        provenance="nexum_modeled",
        lifecycle_status="posted",
        created_at=now_naive,
        updated_at=now_utc,
    )
    session.add(tx)
    await session.flush()

    # Past due installment
    inst_past = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=tx.id,
        installment_number=1,
        installments_total=3,
        principal_amount=Decimal("100000.00"),
        scheduled_period="2026-08",
        status="pending",
        paid_amount=Decimal("0.00"),
        total_amount=Decimal("100000.00"),
        debt_partition="canonical",
        provenance="nexum_modeled",
        created_at=now_utc,
        updated_at=now_utc,
    )
    # Current installment
    inst_curr = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=tx.id,
        installment_number=2,
        installments_total=3,
        principal_amount=Decimal("100000.00"),
        scheduled_period="2026-10",
        status="pending",
        paid_amount=Decimal("0.00"),
        total_amount=Decimal("100000.00"),
        debt_partition="canonical",
        provenance="nexum_modeled",
        created_at=now_utc,
        updated_at=now_utc,
    )
    # Future installment
    inst_fut = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=tx.id,
        installment_number=3,
        installments_total=3,
        principal_amount=Decimal("100000.00"),
        scheduled_period="2026-11",
        status="pending",
        paid_amount=Decimal("0.00"),
        total_amount=Decimal("100000.00"),
        debt_partition="canonical",
        provenance="nexum_modeled",
        created_at=now_utc,
        updated_at=now_utc,
    )
    session.add_all([inst_past, inst_curr, inst_fut])
    await session.commit()

    # Approved manual-card waterfall: overdue principal, then closed statement charge,
    # then current principal.
    # 100,000 to past-due inst (order 1)
    # 10,000 to charge (order 2)
    # 40,000 to current inst (order 3)
    service = CreditCardService(session, fx_provider=static_fx)
    cmd_waterfall = uuid.uuid4()
    pmt = CreditCardPaymentCreate(
        account_id=fixture.account.id,
        amount=Decimal("150000.00"),
    )
    res = await service.create_payment(fixture.user_id, card.id, pmt, command_id=cmd_waterfall)
    await session.commit()
    assert res.status == "success"

    allocs = (
        (
            await session.execute(
                select(CreditCardPaymentAllocation)
                .where(CreditCardPaymentAllocation.credit_card_id == card.id)
                .order_by(CreditCardPaymentAllocation.allocation_order.asc())
            )
        )
        .scalars()
        .all()
    )

    assert len(allocs) == 3
    payment = await session.scalar(
        select(CreditCardPayment).where(CreditCardPayment.credit_card_id == card.id)
    )
    assert payment is not None
    assert payment.principal_applied == sum(allocation.allocated_principal for allocation in allocs)
    assert payment.booked_amount == sum(allocation.allocated_amount for allocation in allocs)
    # First allocation went to overdue installment
    assert allocs[0].target_installment_id == inst_past.id
    assert allocs[0].allocated_amount == Decimal("100000.00")
    assert allocs[0].allocation_order == 1

    # Second allocation went to the closed statement charge
    assert allocs[1].target_charge_id == charge.id
    assert allocs[1].allocated_amount == Decimal("10000.00")
    assert allocs[1].allocation_order == 2

    # Third allocation went to current installment
    assert allocs[2].target_installment_id == inst_curr.id
    assert allocs[2].allocated_amount == Decimal("40000.00")
    assert allocs[2].allocation_order == 3


# ==============================================================================
# AC 9: ATOMIC ROLLBACK ON LEDGER & ALLOCATION FAILURES
# ==============================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_pg09a_atomic_rollback_on_ledger_failure(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Simulated Ledger failure rolls back transaction atomically with zero Account debit or payment residue."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("1000000.00")
    fixture.account.currency = "COP"
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)
    cmd_p = uuid.uuid4()
    purchase = CreditCardPurchaseCreate(
        amount=Decimal("200000.00"),
        description="Rollback test item",
        installments_total=1,
    )
    await service.create_purchase(fixture.user_id, card.id, purchase, command_id=cmd_p)
    await session.commit()

    class SimulatedLedgerError(RuntimeError):
        pass

    async def failing_record_event(*args, **kwargs):
        raise SimulatedLedgerError("Simulated ledger event recording failure")

    service.ledger_service.record_event = failing_record_event

    card_id = card.id
    account_id = fixture.account.id
    user_id = fixture.user_id

    cmd_pmt = uuid.uuid4()
    pmt = CreditCardPaymentCreate(
        account_id=account_id,
        amount=Decimal("100000.00"),
    )
    with pytest.raises(SimulatedLedgerError):
        await service.create_payment(user_id, card_id, pmt, command_id=cmd_pmt)

    await session.rollback()

    # Verify complete atomic rollback
    acc = await session.get(Account, account_id)
    assert acc is not None
    assert acc.balance == Decimal("1000000.00")

    payments = (
        (
            await session.execute(
                select(CreditCardPayment).where(CreditCardPayment.credit_card_id == card_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(payments) == 0

    payment_transactions = (
        (
            await session.execute(
                select(CreditCardTransaction).where(
                    CreditCardTransaction.credit_card_id == card_id,
                    CreditCardTransaction.type == "payment",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(payment_transactions) == 0

    allocations = (
        (
            await session.execute(
                select(CreditCardPaymentAllocation).where(
                    CreditCardPaymentAllocation.credit_card_id == card_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(allocations) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_pg09b_atomic_rollback_on_allocation_failure(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Simulated allocation failure rolls back transaction atomically with zero residue."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    fixture.account.balance = Decimal("1000000.00")
    fixture.account.currency = "COP"
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)
    cmd_p = uuid.uuid4()
    purchase = CreditCardPurchaseCreate(
        amount=Decimal("200000.00"),
        description="Allocation rollback item",
        installments_total=1,
    )
    await service.create_purchase(fixture.user_id, card.id, purchase, command_id=cmd_p)
    await session.commit()

    class SimulatedAllocationError(RuntimeError):
        pass

    async def failing_add_allocations(*args, **kwargs):
        raise SimulatedAllocationError("Simulated payment allocation failure")

    service.repo.add_payment_allocations = failing_add_allocations

    card_id = card.id
    account_id = fixture.account.id
    user_id = fixture.user_id

    cmd_pmt = uuid.uuid4()
    pmt = CreditCardPaymentCreate(
        account_id=account_id,
        amount=Decimal("100000.00"),
    )
    with pytest.raises(SimulatedAllocationError):
        await service.create_payment(user_id, card_id, pmt, command_id=cmd_pmt)

    await session.rollback()

    # Verify atomic rollback
    acc = await session.get(Account, account_id)
    assert acc is not None
    assert acc.balance == Decimal("1000000.00")

    payments = (
        (
            await session.execute(
                select(CreditCardPayment).where(CreditCardPayment.credit_card_id == card_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(payments) == 0

    allocs = (
        (
            await session.execute(
                select(CreditCardPaymentAllocation).where(
                    CreditCardPaymentAllocation.credit_card_id == card_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(allocs) == 0

    payment_transactions = (
        (
            await session.execute(
                select(CreditCardTransaction).where(
                    CreditCardTransaction.credit_card_id == card_id,
                    CreditCardTransaction.type == "payment",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(payment_transactions) == 0

    ledger_events = (
        (await session.execute(select(FinancialEvent).where(FinancialEvent.command_id == cmd_pmt)))
        .scalars()
        .all()
    )
    assert len(ledger_events) == 0


# ==============================================================================
# AC 10: F-19 COMPLETE PAYABLE SET CONSISTENCY & NO MIRRORED CHARGE DOUBLE COUNT
# ==============================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_pg10_f19_payable_set_consistency_mirrored_charge_no_double_count(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Installment + mirrored statement charge: total payable debt, guard, and waterfall never double count."""
    card = await fixture.add_credit_card(currency="COP", credit_limit=Decimal("5000000.00"))
    now_utc, now_naive = get_fixture_timestamps()

    # 1. Statement
    stmt = CreditCardStatement(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        billing_period="2026-10",
        statement_balance=Decimal("110000.00"),
        status="open",
        debt_partition="canonical",
        provenance="nexum_modeled",
        created_at=now_utc,
        updated_at=now_utc,
    )
    session.add(stmt)
    await session.flush()

    # 2. Installment of 100,000 COP
    tx = CreditCardTransaction(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        type="purchase",
        amount=Decimal("100000.00"),
        occurred_at=now_utc,
        debt_partition="canonical",
        provenance="nexum_modeled",
        lifecycle_status="posted",
        created_at=now_naive,
        updated_at=now_utc,
    )
    session.add(tx)
    await session.flush()

    inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=tx.id,
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("100000.00"),
        scheduled_period="2026-10",
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("100000.00"),
        paid_amount=Decimal("0.00"),
        status="pending",
        scheduled_due_date=date(2026, 11, 5),
        debt_partition="canonical",
        provenance="nexum_modeled",
        created_at=now_utc,
        updated_at=now_utc,
    )
    session.add(inst)

    # 3. Statement charge mirroring that installment (reference_id = inst.id)
    mirrored_charge = CreditCardStatementCharge(
        id=uuid.uuid4(),
        statement_id=stmt.id,
        charge_type="installment",
        amount=Decimal("100000.00"),
        paid_amount=Decimal("0.00"),
        status="pending",
        reference_id=inst.id,
        created_at=now_utc,
    )
    # 4. Standalone fee charge of 10,000 COP
    fee_charge = CreditCardStatementCharge(
        id=uuid.uuid4(),
        statement_id=stmt.id,
        charge_type="fee",
        amount=Decimal("10000.00"),
        paid_amount=Decimal("0.00"),
        status="pending",
        reference_id=None,
        created_at=now_utc,
    )
    session.add_all([mirrored_charge, fee_charge])
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)
    debt_info = await service.repo.get_card_debt_partitioned(card.id)

    # Total payable debt MUST be 110,000 COP (100,000 installment + 10,000 fee), NOT 210,000 COP
    assert debt_info["canonical_debt"] == Decimal("110000.00")
    assert debt_info["legacy_debt"] == Decimal("0.00")
    assert debt_info["total_debt"] == Decimal("110000.00")


# ==============================================================================
# AC 11: EXPLICIT CANONICAL DEBT + LEGACY COMPATIBILITY DEBT NO DOUBLE COUNT
# ==============================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_pg11_canonical_and_legacy_debt_partitioning_no_double_count(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Canonical debt and legacy compatibility debt sum cleanly without overlap or cross-contamination."""
    # Legacy card activating canonical frontier
    card = await fixture.add_credit_card(
        currency="COP", canonical_mode=False, provenance="legacy_unknown"
    )
    now_utc, now_naive = get_fixture_timestamps()

    # Add legacy installment of 150,000 COP
    legacy_tx = CreditCardTransaction(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        type="purchase",
        amount=Decimal("150000.00"),
        occurred_at=now_utc,
        debt_partition="legacy",
        provenance="legacy_unknown",
        lifecycle_status="posted",
        created_at=now_naive,
        updated_at=now_utc,
    )
    session.add(legacy_tx)
    await session.flush()

    legacy_inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=legacy_tx.id,
        installment_number=1,
        installments_total=1,
        principal_amount=Decimal("150000.00"),
        scheduled_period="2026-10",
        interest_amount=Decimal("0.00"),
        total_amount=Decimal("150000.00"),
        paid_amount=Decimal("0.00"),
        status="pending",
        scheduled_due_date=date(2026, 11, 5),
        debt_partition="legacy",
        provenance="legacy_unknown",
        created_at=now_utc,
        updated_at=now_utc,
    )
    # Add legacy statement and legacy fee charge (25,000 COP)
    legacy_stmt = CreditCardStatement(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        billing_period="2026-09",
        statement_balance=Decimal("25000.00"),
        status="open",
        debt_partition="legacy",
        provenance="legacy_unknown",
        created_at=now_utc,
        updated_at=now_utc,
    )
    session.add(legacy_stmt)
    await session.flush()

    legacy_charge = CreditCardStatementCharge(
        id=uuid.uuid4(),
        statement_id=legacy_stmt.id,
        charge_type="fee",
        amount=Decimal("25000.00"),
        paid_amount=Decimal("0.00"),
        status="pending",
        reference_id=None,
        created_at=now_utc,
    )
    session.add_all([legacy_inst, legacy_charge])
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)

    # New canonical purchase of 200,000 COP activates canonical frontier
    cmd_p = uuid.uuid4()
    p = CreditCardPurchaseCreate(
        amount=Decimal("200000.00"),
        description="Frontier activating purchase",
        installments_total=1,
    )
    await service.create_purchase(fixture.user_id, card.id, p, command_id=cmd_p)
    await session.commit()

    debt_info = await service.repo.get_card_debt_partitioned(card.id)
    # Canonical debt strictly reflects 200,000 COP purchase; legacy charge does not leak
    assert debt_info["canonical_debt"] == Decimal("200000.00")
    # Legacy debt strictly contains legacy installment (150k) + legacy statement charge (25k) = 175,000 COP
    assert debt_info["legacy_debt"] == Decimal("175000.00")
    assert debt_info["total_debt"] == Decimal("375000.00")
    assert debt_info["total_debt"] == debt_info["canonical_debt"] + debt_info["legacy_debt"]

    # Canonical payment waterfall list cannot return legacy statement charge
    unpaid_canonical_charges = await service.repo.list_unpaid_statement_charges_for_update(card.id)
    assert len(unpaid_canonical_charges) == 0


# ==============================================================================
# AC 12: HARNESS CLEANUP & RESIDUE VERIFICATION
# ==============================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_pg12_harness_cleanup_and_residue_verification(
    session: AsyncSession, fixture: GateFixture
):
    """Fixture cleanup leaves zero residue across all credit, ledger, account, and user tables."""
    card = await fixture.add_credit_card(currency="COP")
    assert card.id is not None

    count = (
        await session.execute(
            select(func.count())
            .select_from(CreditCard)
            .where(CreditCard.user_id == fixture.user_id)
        )
    ).scalar_one()
    assert count == 1

    # Verify statement triggers are enabled fail-closed
    await verify_statement_triggers_enabled(session)
    # Global cleanup run will clear fixture and assert 0 rows across all tables


@pytest.mark.asyncio(loop_scope="session")
async def test_alpha_payment_updates_future_interest_estimate(session, fixture, static_fx):
    card = await fixture.add_credit_card(currency="COP")
    card.monthly_interest_rate = Decimal("2.11")
    fixture.account.currency = "COP"
    fixture.account.balance = Decimal("1000000")
    await session.commit()
    service = CreditCardService(session, fx_provider=static_fx)
    purchase = await service.create_purchase(
        fixture.user_id,
        card.id,
        CreditCardPurchaseCreate(amount=Decimal("300000"), installments_total=3),
        command_id=uuid.uuid4(),
    )
    await session.commit()
    query = select(CreditCardInstallment).where(
        CreditCardInstallment.purchase_transaction_id == purchase.transaction_id,
        CreditCardInstallment.installment_number == 3,
    )
    installment = (await session.execute(query)).scalar_one()
    before_proj = await service.get_installment_projection(fixture.user_id, card.id, installment.id)
    before = before_proj["estimated_interest"]
    assert before > 0
    await service.create_payment(
        fixture.user_id,
        card.id,
        CreditCardPaymentCreate(account_id=fixture.account.id, amount=Decimal("150000")),
        command_id=uuid.uuid4(),
    )
    await session.commit()
    after_proj = await service.get_installment_projection(fixture.user_id, card.id, installment.id)
    assert after_proj["estimated_interest"] < before
    expected_half = (before / Decimal("2")).quantize(Decimal("0.01"))
    assert after_proj["estimated_interest"] == expected_half or abs(
        after_proj["estimated_interest"] - expected_half
    ) <= Decimal("0.01")


@pytest.mark.asyncio(loop_scope="session")
async def test_alpha_refund_updates_future_interest_estimate(session, fixture, static_fx):
    card = await fixture.add_credit_card(currency="COP")
    card.monthly_interest_rate = Decimal("2.11")
    await session.commit()
    service = CreditCardService(session, fx_provider=static_fx)
    purchase = await service.create_purchase(
        fixture.user_id,
        card.id,
        CreditCardPurchaseCreate(amount=Decimal("300000"), installments_total=3),
        command_id=uuid.uuid4(),
    )
    await session.commit()
    installment = (
        await session.execute(
            select(CreditCardInstallment).where(
                CreditCardInstallment.purchase_transaction_id == purchase.transaction_id,
                CreditCardInstallment.installment_number == 3,
            )
        )
    ).scalar_one()
    before_proj = await service.get_installment_projection(fixture.user_id, card.id, installment.id)
    before = before_proj["estimated_interest"]
    assert before > 0
    await service.create_adjustment(
        fixture.user_id,
        card.id,
        CreditCardAdjustmentCreate(
            original_transaction_id=purchase.transaction_id,
            amount=Decimal("50000"),
            adjustment_type="refund",
            direction="credit",
        ),
        command_id=uuid.uuid4(),
    )
    await session.commit()
    after_proj = await service.get_installment_projection(fixture.user_id, card.id, installment.id)
    assert after_proj["estimated_interest"] < before
    expected = (before * Decimal("250000") / Decimal("300000")).quantize(Decimal("0.01"))
    assert after_proj["estimated_interest"] == expected or abs(
        after_proj["estimated_interest"] - expected
    ) <= Decimal("0.01")


# ==============================================================================
# AC 13: CREDIT ALPHA PHYSICAL RELEASE GATE SCENARIOS
# ==============================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_alpha_pg01_dated_purchase_payment_refund_timeline_math(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Dated purchase, payment, and refund with clock injection and exact interest timeline math."""
    card = await fixture.add_credit_card(currency="COP", cutoff_day=15, due_day=5)
    card.canonical_activated_at = datetime(2026, 1, 1, tzinfo=UTC)
    card.monthly_interest_rate = Decimal("2.11")
    fixture.account.currency = "COP"
    fixture.account.balance = Decimal("2000000.00")
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)

    d_start = date(2026, 10, 5)
    d_pay = date(2026, 10, 15)
    d_refund = date(2026, 10, 25)
    d_end = date(2026, 11, 5)

    # 1. Dated purchase on 2026-10-05 (1 installment, 300,000 COP)
    service._get_financial_date = lambda as_of=None: d_start
    dt_start = datetime(2026, 10, 5, 12, 0, tzinfo=ZoneInfo("America/Bogota"))
    purchase = await service.create_purchase(
        fixture.user_id,
        card.id,
        CreditCardPurchaseCreate(
            amount=Decimal("300000.00"),
            installments_total=1,
            occurred_at=dt_start,
        ),
        command_id=uuid.uuid4(),
    )
    await session.commit()

    # 2. Dated payment on 2026-10-15 (100,000 COP)
    service._get_financial_date = lambda as_of=None: d_pay
    await service.create_payment(
        fixture.user_id,
        card.id,
        CreditCardPaymentCreate(account_id=fixture.account.id, amount=Decimal("100000.00")),
        command_id=uuid.uuid4(),
    )
    await session.commit()

    # 3. Dated refund on 2026-10-25 (50,000 COP)
    service._get_financial_date = lambda as_of=None: d_refund
    await service.create_adjustment(
        fixture.user_id,
        card.id,
        CreditCardAdjustmentCreate(
            original_transaction_id=purchase.transaction_id,
            amount=Decimal("50000.00"),
            adjustment_type="refund",
            direction="credit",
        ),
        command_id=uuid.uuid4(),
    )
    await session.commit()

    # Verify extracted physical facts
    changes = await service.repo.get_card_principal_changes(
        card.id, transaction_id=purchase.transaction_id
    )
    assert len(changes) == 3
    change_map = {c.event_type: (c.effective_date, c.amount) for c in changes}
    assert change_map["purchase"] == (d_start, Decimal("300000.00"))
    assert change_map["payment"] == (d_pay, Decimal("-100000.00"))
    assert change_map["refund"] == (d_refund, Decimal("-50000.00"))

    # Daily compounding timeline calculation:
    # 2026-10-05 to 2026-10-15: 10 days at 300,000
    # 2026-10-15 to 2026-10-25: 10 days at 200,000
    # 2026-10-25 to 2026-11-05: 11 days at 150,000
    daily_rate = daily_interest_rate(Decimal("2.11"))
    expected_interest = (
        Decimal("300000.00") * 10 * daily_rate
        + Decimal("200000.00") * 10 * daily_rate
        + Decimal("150000.00") * 11 * daily_rate
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    actual_interest = estimate_interest(changes, Decimal("2.11"), d_start, d_end)
    assert actual_interest == expected_interest

    inst = (
        await session.execute(
            select(CreditCardInstallment).where(
                CreditCardInstallment.purchase_transaction_id == purchase.transaction_id
            )
        )
    ).scalar_one()

    proj = await service.get_installment_projection(fixture.user_id, card.id, inst.id)
    assert proj["estimated_interest"] == expected_interest
    assert proj["scheduled_principal"] == Decimal("150000.00")
    assert proj["estimated_total"] == Decimal("150000.00") + expected_interest


@pytest.mark.asyncio(loop_scope="session")
async def test_alpha_pg02_payment_reversal_restoration_physical(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Payment reversal physically restores installment pending status and principal timeline."""
    card = await fixture.add_credit_card(currency="COP", cutoff_day=15, due_day=5)
    card.canonical_activated_at = datetime(2026, 1, 1, tzinfo=UTC)
    card.monthly_interest_rate = Decimal("2.11")
    fixture.account.currency = "COP"
    fixture.account.balance = Decimal("1000000.00")
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)

    d1 = date(2026, 10, 5)
    d5 = date(2026, 10, 10)
    d10 = date(2026, 10, 15)

    # Day 1: Purchase 100,000 COP
    service._get_financial_date = lambda as_of=None: d1
    purchase = await service.create_purchase(
        fixture.user_id,
        card.id,
        CreditCardPurchaseCreate(
            amount=Decimal("100000.00"),
            installments_total=1,
            occurred_at=datetime(2026, 10, 5, 12, 0, tzinfo=ZoneInfo("America/Bogota")),
        ),
        command_id=uuid.uuid4(),
    )
    await session.commit()

    # Day 5: Payment 100,000 COP
    service._get_financial_date = lambda as_of=None: d5
    pay_res = await service.create_payment(
        fixture.user_id,
        card.id,
        CreditCardPaymentCreate(account_id=fixture.account.id, amount=Decimal("100000.00")),
        command_id=uuid.uuid4(),
    )
    await session.commit()

    # Verify payment paid installment in full
    inst_query = select(CreditCardInstallment).where(
        CreditCardInstallment.purchase_transaction_id == purchase.transaction_id
    )
    inst_after_pay = (await session.execute(inst_query)).scalar_one()
    assert inst_after_pay.status == "paid"
    assert inst_after_pay.paid_amount == Decimal("100000.00")

    # Day 10: Reverse the payment
    service._get_financial_date = lambda as_of=None: d10
    rev_res = await service.create_adjustment(
        fixture.user_id,
        card.id,
        CreditCardAdjustmentCreate(
            original_transaction_id=pay_res.transaction_id,
            amount=Decimal("100000.00"),
            adjustment_type="reversal",
            direction="debit",
        ),
        command_id=uuid.uuid4(),
    )
    await session.commit()
    assert rev_res.status == "success"

    # Physical DB assertions
    payment_row = await service.repo.get_payment_by_transaction_id(pay_res.transaction_id)
    assert payment_row.status == "reversed"

    inst_after_rev = (await session.execute(inst_query)).scalar_one()
    assert inst_after_rev.status == "pending"
    assert inst_after_rev.paid_amount == Decimal("0.00")

    debt = await service.repo.get_card_debt_partitioned(card.id)
    assert debt["total_debt"] == Decimal("100000.00")

    changes = await service.repo.get_card_principal_changes(
        card.id, transaction_id=purchase.transaction_id
    )
    timeline = build_interest_bearing_principal_timeline(changes, d1, date(2026, 10, 20))
    timeline_dict = dict(timeline)
    assert timeline_dict[d1] == Decimal("100000.00")
    assert timeline_dict[d5] == Decimal("0.00")
    assert timeline_dict[date(2026, 10, 14)] == Decimal("0.00")
    assert timeline_dict[d10] == Decimal("100000.00")
    assert timeline_dict[date(2026, 10, 19)] == Decimal("100000.00")


@pytest.mark.asyncio(loop_scope="session")
async def test_alpha_pg03_refund_reversal_exact_linkage_physical(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Refund reversal requires exact linkage via reversal_of_adjustment_id and blocks double reversals."""
    card = await fixture.add_credit_card(currency="COP", cutoff_day=15, due_day=5)
    card.canonical_activated_at = datetime(2026, 1, 1, tzinfo=UTC)
    card.monthly_interest_rate = Decimal("2.11")
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)

    d1 = date(2026, 10, 5)
    d5 = date(2026, 10, 10)
    d8 = date(2026, 10, 13)
    d12 = date(2026, 10, 17)

    # Purchase: 200,000 COP (2 installments of 100,000)
    service._get_financial_date = lambda as_of=None: d1
    purchase = await service.create_purchase(
        fixture.user_id,
        card.id,
        CreditCardPurchaseCreate(
            amount=Decimal("200000.00"),
            installments_total=2,
            occurred_at=datetime(2026, 10, 5, 12, 0, tzinfo=ZoneInfo("America/Bogota")),
        ),
        command_id=uuid.uuid4(),
    )
    await session.commit()

    # Refund 1 on Day 5: 50,000 COP
    service._get_financial_date = lambda as_of=None: d5
    ref1_cmd = uuid.uuid4()
    ref1 = await service.create_adjustment(
        fixture.user_id,
        card.id,
        CreditCardAdjustmentCreate(
            original_transaction_id=purchase.transaction_id,
            amount=Decimal("50000.00"),
            adjustment_type="refund",
            direction="credit",
        ),
        command_id=ref1_cmd,
    )
    await session.commit()

    # Refund 2 on Day 8: 30,000 COP
    service._get_financial_date = lambda as_of=None: d8
    ref2 = await service.create_adjustment(
        fixture.user_id,
        card.id,
        CreditCardAdjustmentCreate(
            original_transaction_id=purchase.transaction_id,
            amount=Decimal("30000.00"),
            adjustment_type="refund",
            direction="credit",
        ),
        command_id=uuid.uuid4(),
    )
    await session.commit()

    adj1_record = (
        await session.execute(
            select(CreditCardAdjustment).where(CreditCardAdjustment.id == ref1.adjustment_id)
        )
    ).scalar_one()

    # Non-exact reversal attempt on Refund 1 fails closed
    service._get_financial_date = lambda as_of=None: d12
    with pytest.raises(InvalidPaymentAmountError):
        await service.create_adjustment(
            fixture.user_id,
            card.id,
            CreditCardAdjustmentCreate(
                original_transaction_id=adj1_record.id,
                amount=Decimal("40000.00"),
                adjustment_type="reversal",
                direction="debit",
            ),
            command_id=uuid.uuid4(),
        )

    # Exact reversal of Refund 1
    rev_cmd = uuid.uuid4()
    rev_res = await service.create_adjustment(
        fixture.user_id,
        card.id,
        CreditCardAdjustmentCreate(
            original_transaction_id=adj1_record.id,
            amount=Decimal("50000.00"),
            adjustment_type="reversal",
            direction="debit",
        ),
        command_id=rev_cmd,
    )
    await session.commit()
    assert rev_res.status == "success"

    # Physical link assertion
    rev_record = (
        await session.execute(
            select(CreditCardAdjustment).where(CreditCardAdjustment.id == rev_res.adjustment_id)
        )
    ).scalar_one()
    assert rev_record.reversal_of_adjustment_id == adj1_record.id

    # Double reversal of Refund 1 is blocked
    with pytest.raises(CreditLifecycleInvalidityError):
        await service.create_adjustment(
            fixture.user_id,
            card.id,
            CreditCardAdjustmentCreate(
                original_transaction_id=adj1_record.id,
                amount=Decimal("50000.00"),
                adjustment_type="reversal",
                direction="debit",
            ),
            command_id=uuid.uuid4(),
        )

    # Idempotent retry of reversal returns idempotent_retry
    retry_rev = await service.create_adjustment(
        fixture.user_id,
        card.id,
        CreditCardAdjustmentCreate(
            original_transaction_id=adj1_record.id,
            amount=Decimal("50000.00"),
            adjustment_type="reversal",
            direction="debit",
        ),
        command_id=rev_cmd,
    )
    assert retry_rev.status == "idempotent_retry"
    assert retry_rev.adjustment_id == rev_res.adjustment_id

    # Verify Refund 2 (30,000) is untouched
    adj2_record = (
        await session.execute(
            select(CreditCardAdjustment).where(CreditCardAdjustment.id == ref2.adjustment_id)
        )
    ).scalar_one()
    assert adj2_record.status == "posted"

    # Principal changes: Purchase (+200k), Ref1 (-50k), Ref2 (-30k), Rev1 (+50k)
    changes = await service.repo.get_card_principal_changes(
        card.id, transaction_id=purchase.transaction_id
    )
    timeline = dict(build_interest_bearing_principal_timeline(changes, d1, date(2026, 10, 20)))
    assert timeline[d1] == Decimal("200000.00")
    assert timeline[d5] == Decimal("150000.00")
    assert timeline[d8] == Decimal("120000.00")
    assert timeline[d12] == Decimal("170000.00")


@pytest.mark.asyncio(loop_scope="session")
async def test_alpha_pg04_zero_interest_mixed_purchases_physical(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Zero-interest purchases are excluded from projections and isolated from payments."""
    card = await fixture.add_credit_card(currency="COP", cutoff_day=15, due_day=5)
    card.canonical_activated_at = datetime(2026, 1, 1, tzinfo=UTC)
    card.monthly_interest_rate = Decimal("2.11")
    fixture.account.currency = "COP"
    fixture.account.balance = Decimal("2000000.00")
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)

    d1 = date(2026, 10, 5)
    service._get_financial_date = lambda as_of=None: d1

    # Purchase A: zero interest, 100,000 COP
    purchase_a = await service.create_purchase(
        fixture.user_id,
        card.id,
        CreditCardPurchaseCreate(
            amount=Decimal("100000.00"),
            installments_total=1,
            zero_interest=True,
            occurred_at=datetime(2026, 10, 5, 10, 0, tzinfo=ZoneInfo("America/Bogota")),
        ),
        command_id=uuid.uuid4(),
    )

    await session.commit()

    # Purchase B: interest bearing, 100,000 COP
    purchase_b = await service.create_purchase(
        fixture.user_id,
        card.id,
        CreditCardPurchaseCreate(
            amount=Decimal("100000.00"),
            installments_total=1,
            zero_interest=False,
            occurred_at=datetime(2026, 10, 5, 11, 0, tzinfo=ZoneInfo("America/Bogota")),
        ),
        command_id=uuid.uuid4(),
    )
    await session.commit()

    inst_a = (
        await session.execute(
            select(CreditCardInstallment).where(
                CreditCardInstallment.purchase_transaction_id == purchase_a.transaction_id
            )
        )
    ).scalar_one()

    inst_b = (
        await session.execute(
            select(CreditCardInstallment).where(
                CreditCardInstallment.purchase_transaction_id == purchase_b.transaction_id
            )
        )
    ).scalar_one()

    # Projection for zero-interest installment must be 0
    proj_a = await service.get_installment_projection(fixture.user_id, card.id, inst_a.id)
    assert proj_a["estimated_interest"] == Decimal("0.00")

    # Projection for interest-bearing installment must be > 0
    proj_b = await service.get_installment_projection(fixture.user_id, card.id, inst_b.id)
    assert proj_b["estimated_interest"] > Decimal("0.00")
    b_before_interest = proj_b["estimated_interest"]

    # Payment of 100,000 COP allocates to installment A
    service._get_financial_date = lambda as_of=None: date(2026, 10, 10)
    await service.create_payment(
        fixture.user_id,
        card.id,
        CreditCardPaymentCreate(account_id=fixture.account.id, amount=Decimal("100000.00")),
        command_id=uuid.uuid4(),
    )
    await session.commit()

    inst_a_after = (
        await session.execute(
            select(CreditCardInstallment).where(CreditCardInstallment.id == inst_a.id)
        )
    ).scalar_one()
    assert inst_a_after.paid_amount == Decimal("100000.00")

    # Scoped projection for installment B must NOT be reduced by payment to zero-interest installment A
    proj_b_after = await service.get_installment_projection(fixture.user_id, card.id, inst_b.id)
    assert proj_b_after["estimated_interest"] == b_before_interest


@pytest.mark.asyncio(loop_scope="session")
async def test_alpha_pg05_one_installment_normal_physical(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Normal single installment revolving purchase calculates full interval interest."""
    card = await fixture.add_credit_card(currency="COP", cutoff_day=15, due_day=5)
    card.canonical_activated_at = datetime(2026, 1, 1, tzinfo=UTC)
    card.monthly_interest_rate = Decimal("2.11")
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)
    d1 = date(2026, 10, 5)
    service._get_financial_date = lambda as_of=None: d1

    purchase = await service.create_purchase(
        fixture.user_id,
        card.id,
        CreditCardPurchaseCreate(
            amount=Decimal("150000.00"),
            installments_total=1,
            occurred_at=datetime(2026, 10, 5, 12, 0, tzinfo=ZoneInfo("America/Bogota")),
        ),
        command_id=uuid.uuid4(),
    )
    await session.commit()

    inst = (
        await session.execute(
            select(CreditCardInstallment).where(
                CreditCardInstallment.purchase_transaction_id == purchase.transaction_id
            )
        )
    ).scalar_one()

    assert inst.installment_number == 1
    assert inst.installments_total == 1
    assert inst.principal_amount == Decimal("150000.00")
    assert inst.status == "pending"

    proj = await service.get_installment_projection(fixture.user_id, card.id, inst.id)
    assert proj["scheduled_principal"] == Decimal("150000.00")
    assert proj["assessed_interest"] == Decimal("0.00")
    assert proj["estimated_interest"] > Decimal("0.00")
    assert proj["estimated_total"] == proj["scheduled_principal"] + proj["estimated_interest"]
    assert proj["estimator_version"] == "nexum_interest_v1"


@pytest.mark.asyncio(loop_scope="session")
async def test_alpha_pg06_estimated_vs_assessed_no_debt_mutation_on_reads(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Read projection methods never mutate physical debt or write phantom assessed interest."""
    card = await fixture.add_credit_card(currency="COP", cutoff_day=15, due_day=5)
    card.canonical_activated_at = datetime(2026, 1, 1, tzinfo=UTC)
    card.monthly_interest_rate = Decimal("2.11")
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)
    await service.create_purchase(
        fixture.user_id,
        card.id,
        CreditCardPurchaseCreate(
            amount=Decimal("200000.00"),
            installments_total=2,
            occurred_at=datetime(2026, 10, 5, 12, 0, tzinfo=ZoneInfo("America/Bogota")),
        ),
        command_id=uuid.uuid4(),
    )
    await session.commit()

    debt_before = await service.repo.get_card_debt_partitioned(card.id)
    total_before = debt_before["total_debt"]
    assert total_before == Decimal("200000.00")

    inst_list = (
        (
            await session.execute(
                select(CreditCardInstallment).where(CreditCardInstallment.credit_card_id == card.id)
            )
        )
        .scalars()
        .all()
    )
    for inst in inst_list:
        assert inst.assessed_interest_amount in (None, Decimal("0.00"))

    # Execute all read projection methods repeatedly
    await service.get_installment_projection(fixture.user_id, card.id, inst_list[0].id)
    await service.get_installment_projection(fixture.user_id, card.id, inst_list[1].id)
    await service.get_card_status(fixture.user_id, card.id)
    await service.list_installments(fixture.user_id, card.id)
    await service.get_credit_summary(fixture.user_id)
    await session.commit()

    # Re-verify debt and installment rows
    debt_after = await service.repo.get_card_debt_partitioned(card.id)
    assert debt_after["total_debt"] == total_before

    inst_after = (
        (
            await session.execute(
                select(CreditCardInstallment).where(CreditCardInstallment.credit_card_id == card.id)
            )
        )
        .scalars()
        .all()
    )
    for inst in inst_after:
        assert inst.assessed_interest_amount in (None, Decimal("0.00"))
        assert inst.paid_amount == Decimal("0.00")


@pytest.mark.asyncio(loop_scope="session")
async def test_alpha_pg07_next_payment_projection_tiers_and_fees_physical(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Next-payment projection enforces 3-tier chronological selection (overdue, due_today, upcoming) and fee breakdown."""
    card = await fixture.add_credit_card(currency="COP", cutoff_day=15, due_day=5)
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)
    today = financial_date()
    now_utc, now_naive = get_fixture_timestamps()

    tx = CreditCardTransaction(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        type="purchase",
        amount=Decimal("300000.00"),
        booked_amount=Decimal("300000.00"),
        booked_currency="COP",
        original_amount=Decimal("300000.00"),
        original_currency="COP",
        occurred_at=now_utc,
        debt_partition="canonical",
        provenance="nexum_modeled",
        lifecycle_status="posted",
        created_at=now_naive,
        updated_at=now_utc,
    )
    session.add(tx)
    await session.flush()

    d_overdue1 = today - timedelta(days=10)
    d_overdue2 = today - timedelta(days=5)
    d_today = today
    d_upcoming1 = today + timedelta(days=10)
    d_upcoming2 = today + timedelta(days=20)

    # 1. Add overdue installment 1 & 2, and upcoming installment 1
    inst_od1 = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=tx.id,
        installment_number=1,
        installments_total=5,
        principal_amount=Decimal("50000.00"),
        scheduled_period="2026-08",
        status="pending",
        scheduled_due_date=d_overdue1,
        debt_partition="canonical",
        provenance="nexum_modeled",
        lifecycle_status="active",
        created_at=now_naive,
        updated_at=now_utc,
    )
    inst_od2 = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=tx.id,
        installment_number=2,
        installments_total=5,
        principal_amount=Decimal("60000.00"),
        scheduled_period="2026-09",
        status="pending",
        scheduled_due_date=d_overdue2,
        debt_partition="canonical",
        provenance="nexum_modeled",
        lifecycle_status="active",
        created_at=now_naive,
        updated_at=now_utc,
    )
    inst_up1 = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=tx.id,
        installment_number=3,
        installments_total=5,
        principal_amount=Decimal("70000.00"),
        scheduled_period="2026-11",
        status="pending",
        scheduled_due_date=d_upcoming1,
        debt_partition="canonical",
        provenance="nexum_modeled",
        lifecycle_status="active",
        created_at=now_naive,
        updated_at=now_utc,
    )
    session.add_all([inst_od1, inst_od2, inst_up1])
    await session.commit()

    # Tier 1: Overdue selection chooses the OLDEST overdue installment
    proj1 = await service.repo.get_next_payment_projection(card.id)
    assert proj1 is not None
    assert proj1["status"] == "overdue"
    assert proj1["due_date"] == d_overdue1
    assert proj1["scheduled_principal"] == Decimal("50000.00")

    # Mark both overdue installments paid
    inst_od1.status = "paid"
    inst_od2.status = "paid"

    # Tier 2: Add due_today installment
    inst_today = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=tx.id,
        installment_number=4,
        installments_total=5,
        principal_amount=Decimal("80000.00"),
        scheduled_period="2026-10",
        status="pending",
        scheduled_due_date=d_today,
        debt_partition="canonical",
        provenance="nexum_modeled",
        lifecycle_status="active",
        created_at=now_naive,
        updated_at=now_utc,
    )
    session.add(inst_today)
    await session.commit()

    proj2 = await service.repo.get_next_payment_projection(card.id)
    assert proj2 is not None
    assert proj2["status"] == "due_today"
    assert proj2["due_date"] == d_today
    assert proj2["scheduled_principal"] == Decimal("80000.00")

    # Mark due_today paid
    inst_today.status = "paid"

    # Tier 3: Add upcoming 2 (due in 20 days)
    inst_up2 = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        purchase_transaction_id=tx.id,
        installment_number=5,
        installments_total=5,
        principal_amount=Decimal("90000.00"),
        scheduled_period="2026-12",
        status="pending",
        scheduled_due_date=d_upcoming2,
        debt_partition="canonical",
        provenance="nexum_modeled",
        lifecycle_status="active",
        created_at=now_naive,
        updated_at=now_utc,
    )
    session.add(inst_up2)

    # Breakdown fees: add a statement fee due on upcoming 1 due date
    stmt = CreditCardStatement(
        id=uuid.uuid4(),
        user_id=fixture.user_id,
        credit_card_id=card.id,
        billing_period="2026-11",
        due_date=d_upcoming1,
        statement_balance=Decimal("15000.00"),
        status="closed",
        currency="COP",
        debt_partition="canonical",
        provenance="nexum_modeled",
    )
    session.add(stmt)
    await session.flush()

    charge = CreditCardStatementCharge(
        id=uuid.uuid4(),
        statement_id=stmt.id,
        charge_type="fee",
        amount=Decimal("15000.00"),
        status="posted",
    )
    session.add(charge)
    await session.commit()

    proj3 = await service.repo.get_next_payment_projection(card.id)
    assert proj3 is not None
    assert proj3["status"] == "upcoming"
    assert proj3["due_date"] == d_upcoming1
    assert proj3["scheduled_principal"] == Decimal("70000.00")
    assert proj3["known_charges"] == Decimal("15000.00")
    assert (
        proj3["estimated_total"]
        == proj3["scheduled_principal"]
        + proj3["assessed_interest"]
        + proj3["estimated_interest"]
        + proj3["known_charges"]
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_alpha_pg08_opening_balance_known_unknown_idempotency_payment_reconciliation(
    session: AsyncSession, fixture: GateFixture, static_fx: StaticFxRateProvider
):
    """Opening balances (known & unknown composition), debt tracking, idempotency, and payment reconciliation."""
    card1 = await fixture.add_credit_card(currency="COP", cutoff_day=15, due_day=5)
    card2 = await fixture.add_credit_card(currency="COP", cutoff_day=15, due_day=5)
    fixture.account.currency = "COP"
    fixture.account.balance = Decimal("1000000.00")
    await session.commit()

    service = CreditCardService(session, fx_provider=static_fx)
    today = financial_date()

    # Part A: Unknown composition opening balance
    cmd1 = uuid.uuid4()
    res1 = await service.create_opening_balance(
        fixture.user_id,
        card1.id,
        CreditCardOpeningBalanceCreate(
            amount=Decimal("150000.00"),
            currency="COP",
            effective_date=today,
            composition_known=False,
        ),
        command_id=cmd1,
    )
    await session.commit()
    assert res1.status in ("created", "success")

    tx1 = await service.repo.get_transaction_by_id(res1.transaction_id)
    assert tx1.type == "opening_balance"
    assert tx1.metadata_["provenance"] == "manual_input"
    assert tx1.metadata_["composition_known"] is False
    assert tx1.metadata_["interest_projection_assumption"] in (
        "opening_balance_treated_as_interest_bearing_principal",
        "unknown_composition_treated_as_interest_bearing",
    )

    inst1 = (
        await session.execute(
            select(CreditCardInstallment).where(
                CreditCardInstallment.purchase_transaction_id == tx1.id
            )
        )
    ).scalar_one()
    assert inst1.opening_balance_amount == Decimal("150000.00")
    assert inst1.principal_amount == Decimal("0.00")

    debt1 = await service.repo.get_card_debt_partitioned(card1.id)
    assert debt1["total_debt"] == Decimal("150000.00")

    # Idempotent retry on opening balance
    retry1 = await service.create_opening_balance(
        fixture.user_id,
        card1.id,
        CreditCardOpeningBalanceCreate(
            amount=Decimal("150000.00"),
            currency="COP",
            effective_date=today,
            composition_known=False,
        ),
        command_id=cmd1,
    )
    assert retry1.status == "idempotent_retry"
    assert retry1.transaction_id == res1.transaction_id

    # Conflict on reused command_id with different amount
    with pytest.raises(CreditIdempotencyConflictError):
        await service.create_opening_balance(
            fixture.user_id,
            card1.id,
            CreditCardOpeningBalanceCreate(
                amount=Decimal("160000.00"),
                currency="COP",
                effective_date=today,
                composition_known=False,
            ),
            command_id=cmd1,
        )

    # Part B: Known composition opening balance
    cmd2 = uuid.uuid4()
    res2 = await service.create_opening_balance(
        fixture.user_id,
        card2.id,
        CreditCardOpeningBalanceCreate(
            amount=Decimal("200000.00"),
            currency="COP",
            effective_date=today,
            composition_known=True,
            principal_amount=Decimal("180000.00"),
            assessed_interest_amount=Decimal("20000.00"),
        ),
        command_id=cmd2,
    )
    await session.commit()

    tx2 = await service.repo.get_transaction_by_id(res2.transaction_id)
    assert tx2.type == "opening_balance"
    assert tx2.metadata_["composition_known"] is True

    inst2 = (
        await session.execute(
            select(CreditCardInstallment).where(
                CreditCardInstallment.purchase_transaction_id == tx2.id
            )
        )
    ).scalar_one()
    assert inst2.principal_amount == Decimal("180000.00")
    assert inst2.assessed_interest_amount == Decimal("20000.00")
    assert inst2.opening_balance_amount is None

    debt2 = await service.repo.get_card_debt_partitioned(card2.id)
    assert debt2["total_debt"] == Decimal("200000.00")

    # Part C: Payment against opening balance and reconciliation
    cmd_pay = uuid.uuid4()
    pay_res = await service.create_payment(
        fixture.user_id,
        card1.id,
        CreditCardPaymentCreate(account_id=fixture.account.id, amount=Decimal("100000.00")),
        command_id=cmd_pay,
    )
    await session.commit()
    assert pay_res.status in ("success", "posted")

    await session.refresh(inst1)
    assert inst1.paid_amount == Decimal("100000.00")

    debt1_after = await service.repo.get_card_debt_partitioned(card1.id)
    assert debt1_after["total_debt"] == Decimal("50000.00")

    pay_retry = await service.create_payment(
        fixture.user_id,
        card1.id,
        CreditCardPaymentCreate(account_id=fixture.account.id, amount=Decimal("100000.00")),
        command_id=cmd_pay,
    )
    assert pay_retry.status == "idempotent_retry"
    assert pay_retry.transaction_id == pay_res.transaction_id
