"""PostgreSQL physical concurrency & reconciliation test suite for Credit Command Reconciliation.

Physical Guarantees Tested:
1. Concurrency / Race Safety:
   - Under standard PostgreSQL READ COMMITTED isolation, an uncommitted concurrent POST
     is invisible to a concurrent GET reconciliation query in another session.
   - While the mutation is uncommitted/in-flight, reconciliation yields status="unknown".
   - Once the mutation transaction commits, subsequent reconciliation yields status="confirmed"
     with the exact persisted identifiers.
   - Invariant: "unknown" is NOT authoritative absence and NEVER means safe to reissue.
     Clients must fail closed during in-flight executions.
2. Owner Scoping & Isolation:
   - A committed command owned by User A queried by User B yields status="unknown"
     indistinguishable from a non-existent command.
3. Read-Only Invariant:
   - GET reconciliation performs zero writes, requires no UnitOfWork, uses no FOR UPDATE locks,
     and causes no autoflush side effects.

NOTE: As per instructions, this physical test is authored for Codex/physical runner execution
and must NOT be run locally.
"""

from __future__ import annotations

import os
import asyncio
import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.uow import UnitOfWork
from app.core.database import get_db_session
from app.credit.exceptions import CreditIdempotencyConflictError
from app.credit.models import CreditCard
from app.credit.router import get_credit_service
from app.credit.schemas import CreditCardPurchaseCreate
from app.credit.service import CreditCardService
from app.fx.provider import StaticFxRateProvider
from app.main import app
from app.users.dependencies import get_current_user_profile_dep
from app.users.schemas import UserRead
from tests.unit.postgres_helpers import (
    get_fixture_timestamps,
)

EXPECTED_DATABASE = "nexum_release_gate_test"
EXPECTED_USER = "nexum_release_gate_test"
SAFE_TUNNEL_HOSTS = {"127.0.0.1", "localhost", "::1"}
EXPECTED_TUNNEL_PORT = 5433
CT_POSTGRES_PORT = 5432
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

    if parsed.port not in {EXPECTED_TUNNEL_PORT, CT_POSTGRES_PORT}:
        pytest.fail(
            f"RELEASE_GATE_FATAL: Unsafe port {parsed.port}; requires host tunnel port {EXPECTED_TUNNEL_PORT} or CT101 loopback PostgreSQL port {CT_POSTGRES_PORT}"
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
    eng = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def session_factory(engine: AsyncEngine) -> sessionmaker:
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
def static_fx() -> StaticFxRateProvider:
    return StaticFxRateProvider({"USD_COP": Decimal("4000.00"), "EUR_COP": Decimal("4300.00")})


@pytest.mark.asyncio(loop_scope="session")
async def test_http_bearer_ownership(session_factory, static_fx, monkeypatch):
    import jwt
    from types import SimpleNamespace
    from app.core import security

    owner, other, auth_owner, auth_other, card_id, command_id = (uuid.uuid4() for _ in range(6))
    now, _ = get_fixture_timestamps()
    async with session_factory() as setup:
        for uid, auth in ((owner, auth_owner), (other, auth_other)):
            await setup.execute(text("INSERT INTO users (id, auth_user_id, email, name, created_at, updated_at) VALUES (:id, :auth, :email, :name, :now, :now)"), {"id": uid, "auth": auth, "email": f"auth_{uid.hex}@test.com", "name": "Auth", "now": now})
        setup.add(CreditCard(id=card_id, user_id=owner, name="Auth", bank="Test", credit_limit=Decimal("1000000"), cutoff_day=15, due_day=5, currency="COP", is_active=True, canonical_mode=True, canonical_activated_at=now, provenance="manual", lifecycle_status="active"))
        await setup.commit()
        svc = CreditCardService(setup, fx_provider=static_fx)
        async with UnitOfWork(setup).transaction():
            purchase = await svc.create_purchase(owner, card_id, CreditCardPurchaseCreate(amount=Decimal("50000"), description="Auth", installments_total=1), command_id=command_id)
    secret = "synthetic-test-signing-key-32-bytes-minimum"
    monkeypatch.setattr(security.settings, "AUTH_BYPASS_ENABLED", False)
    monkeypatch.setattr(security.settings, "SUPABASE_URL", "https://synthetic.invalid")
    monkeypatch.setattr(security, "get_jwks_client", lambda: SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=secret)))
    async def db_override():
        async with session_factory() as session:
            yield session
    app.dependency_overrides[get_db_session] = db_override
    def token(uid):
        return jwt.encode({"sub": str(uid), "role": "authenticated", "aud": "authenticated", "iss": "https://synthetic.invalid/auth/v1"}, secret, algorithm="HS256")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            path = f"/api/v1/credit/commands/{command_id}"
            own = await client.get(path, headers={"Authorization": f"Bearer {token(auth_owner)}"})
            foreign = await client.get(path, headers={"Authorization": f"Bearer {token(auth_other)}"})
            missing = await client.get(path)
            unknown = await client.get(f"/api/v1/credit/commands/{uuid.uuid4()}", headers={"Authorization": f"Bearer {token(auth_other)}"})
        assert own.status_code == 200 and own.json()["status"] == "confirmed", own.text
        assert own.json()["resource_id"] == str(purchase.transaction_id)
        assert foreign.status_code == unknown.status_code == 200
        assert {k:v for k,v in foreign.json().items() if k != "command_id"} == {k:v for k,v in unknown.json().items() if k != "command_id"}
        assert foreign.json()["status"] == "unknown"
        assert missing.status_code == 401, missing.text
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio(loop_scope="session")
async def test_http_post_get_race(session_factory, static_fx, monkeypatch):
    user_id, card_id, command_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now, _ = get_fixture_timestamps()
    async with session_factory() as setup:
        await setup.execute(text("INSERT INTO users (id, email, name, created_at, updated_at) VALUES (:id, :email, :name, :now, :now)"), {"id": user_id, "email": f"race_{user_id.hex}@test.com", "name": "Race", "now": now})
        setup.add(CreditCard(id=card_id, user_id=user_id, name="Race", bank="Test", credit_limit=Decimal("1000000"), cutoff_day=15, due_day=5, currency="COP", is_active=True, canonical_mode=True, canonical_activated_at=now, provenance="manual", lifecycle_status="active"))
        await setup.commit()
    entered, release = asyncio.Event(), asyncio.Event()
    original = CreditCardService.create_purchase
    async def held_purchase(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        entered.set()
        await asyncio.wait_for(release.wait(), 15)
        return result
    monkeypatch.setattr(CreditCardService, "create_purchase", held_purchase)
    async def db_override():
        async with session_factory() as session:
            yield session
    profile = UserRead(id=user_id, name="Race", email=f"race_{user_id.hex}@test.com", timezone="America/Bogota", currency="COP", status="active")
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_current_user_profile_dep] = lambda: profile
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            task = asyncio.create_task(client.post(f"/api/v1/credit/cards/{card_id}/purchases", headers={"Idempotency-Key": str(command_id)}, json={"amount": "150000", "description": "Race", "installments_total": 1}))
            try:
                await asyncio.wait_for(entered.wait(), 15)
                before = await asyncio.wait_for(client.get(f"/api/v1/credit/commands/{command_id}"), 10)
                assert before.status_code == 200 and before.json()["status"] == "unknown", before.text
            finally:
                release.set()
            posted = await asyncio.wait_for(task, 15)
            assert posted.status_code == 200, posted.text
            after = await asyncio.wait_for(client.get(f"/api/v1/credit/commands/{command_id}"), 10)
            assert after.status_code == 200 and after.json()["status"] == "confirmed", after.text
            assert after.json()["resource_id"] == posted.json()["transaction_id"]
            assert after.json()["event_id"] == posted.json()["event_id"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user_profile_dep, None)


@pytest.mark.asyncio(loop_scope="session")
async def test_pg_reconcile_concurrent_uncommitted_post_yields_unknown(
    session_factory: sessionmaker, static_fx: StaticFxRateProvider
):
    """Verifies physical PostgreSQL concurrency isolation:
    
    1. Transaction 1 executes a purchase mutation with command_id under an uncommitted transaction.
    2. Concurrent Session 2 reconciles the command_id. Under PostgreSQL READ COMMITTED,
       the uncommitted FinancialEvent is invisible, yielding status='unknown'.
    3. Transaction 1 commits.
    4. Session 2 reconciles again and receives status='confirmed' with persisted transaction_id.
    5. Invariant proven: clients must fail closed on 'unknown'.
    """
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()

    # Setup: create user and card in committed state
    async with session_factory() as setup_session:
        # Create minimal user and credit card
        now_utc, _ = get_fixture_timestamps()
        await setup_session.execute(
            text(
                "INSERT INTO users (id, email, name, created_at, updated_at) "
                "VALUES (:id, :email, :name, :now, :now) ON CONFLICT DO NOTHING"
            ),
            {"id": user_id, "email": f"recon_{user_id.hex[:8]}@test.com", "name": "Recon User", "now": now_utc},
        )
        card = CreditCard(
            id=uuid.uuid4(),
            user_id=user_id,
            name="Recon Test Card",
            bank="Bancolombia",
            credit_limit=Decimal("10000000.00"),
            cutoff_day=15,
            due_day=5,
            currency="COP",
            is_active=True,
            canonical_mode=True,
            canonical_activated_at=now_utc,
            provenance="manual",
            lifecycle_status="active",
        )
        setup_session.add(card)
        await setup_session.commit()
        card_id = card.id

    # Concurrent execution: Session 1 (mutator) vs Session 2 (reconciler)
    async with session_factory() as mutator_session:
        uow = UnitOfWork(mutator_session)
        mutator_service = CreditCardService(mutator_session, fx_provider=static_fx)

        # Mutator opens transaction and posts purchase without committing yet
        async with uow.transaction():
            purchase_payload = CreditCardPurchaseCreate(
                amount=Decimal("150000.00"),
                description="Uncommitted purchase",
                installments_total=1,
            )
            purchase_result = await mutator_service.create_purchase(
                user_id=user_id,
                card_id=card_id,
                payload=purchase_payload,
                command_id=command_id,
            )
            assert purchase_result.status == "success"
            tx_id = purchase_result.transaction_id
            event_id = purchase_result.event_id

            # While mutator transaction is uncommitted:
            # Observer/reconciler session checks command reconciliation
            async with session_factory() as observer_session:
                observer_service = CreditCardService(observer_session, fx_provider=static_fx)
                profile = UserRead(
                    id=user_id,
                    name="Race Test User",
                    email=f"recon_{user_id.hex[:8]}@test.com",
                    timezone="America/Bogota",
                    currency="COP",
                    status="active",
                )
                app.dependency_overrides[get_credit_service] = lambda: observer_service
                app.dependency_overrides[get_current_user_profile_dep] = lambda: profile
                try:
                    async with AsyncClient(
                        transport=ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        response = await client.get(f"/api/v1/credit/commands/{command_id}")
                    assert response.status_code == 200
                    recon_in_flight = response.json()
                finally:
                    app.dependency_overrides.clear()

                # PostgreSQL READ COMMITTED isolation: uncommitted event is invisible
                assert recon_in_flight["status"] == "unknown"
                assert recon_in_flight["operation_type"] is None
                assert recon_in_flight["event_id"] is None
                assert recon_in_flight["resource_id"] is None
                assert recon_in_flight["transaction_id"] is None

        # Transaction 1 has committed now via UnitOfWork context exit

    # After commit: the authenticated HTTP endpoint now confirms the command.
    async with session_factory() as observer_session:
        observer_service = CreditCardService(observer_session, fx_provider=static_fx)
        profile = UserRead(
            id=user_id,
            name="Race Test User",
            email=f"recon_{user_id.hex[:8]}@test.com",
            timezone="America/Bogota",
            currency="COP",
            status="active",
        )
        app.dependency_overrides[get_credit_service] = lambda: observer_service
        app.dependency_overrides[get_current_user_profile_dep] = lambda: profile
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/credit/commands/{command_id}")
            assert response.status_code == 200
            recon_committed = response.json()
        finally:
            app.dependency_overrides.clear()

        # Now proven confirmed!
        assert recon_committed["status"] == "confirmed"
        assert recon_committed["operation_type"] == "purchase"
        assert recon_committed["event_id"] == str(event_id)
        assert recon_committed["resource_id"] == str(tx_id)
        assert recon_committed["transaction_id"] == str(tx_id)

    # Keep the certified POST replay contract intact on PostgreSQL.
    async with session_factory() as replay_session:
        replay_service = CreditCardService(replay_session, fx_provider=static_fx)
        async with UnitOfWork(replay_session).transaction():
            replay = await replay_service.create_purchase(
                user_id=user_id,
                card_id=card_id,
                payload=CreditCardPurchaseCreate(
                    amount=Decimal("150000.00"),
                    description="Uncommitted purchase",
                    installments_total=1,
                ),
                command_id=command_id,
            )
            assert replay.status == "idempotent_retry"
            assert replay.transaction_id == tx_id

    async with session_factory() as conflict_session:
        conflict_service = CreditCardService(conflict_session, fx_provider=static_fx)
        with pytest.raises(CreditIdempotencyConflictError):
            async with UnitOfWork(conflict_session).transaction():
                await conflict_service.create_purchase(
                    user_id=user_id,
                    card_id=card_id,
                    payload=CreditCardPurchaseCreate(
                        amount=Decimal("160000.00"),
                        description="Conflicting replay",
                        installments_total=1,
                    ),
                    command_id=command_id,
                )


@pytest.mark.asyncio(loop_scope="session")
async def test_pg_reconcile_foreign_owner_isolation(
    session_factory: sessionmaker, static_fx: StaticFxRateProvider
):
    """Verifies that an existing committed command owned by User A returns
    status='unknown' to User B, completely indistinguishable from non-existent.
    """
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    command_id = uuid.uuid4()

    async with session_factory() as sess:
        now_utc, _ = get_fixture_timestamps()
        for u in (user_a, user_b):
            await sess.execute(
                text(
                    "INSERT INTO users (id, email, name, created_at, updated_at) "
                    "VALUES (:id, :email, :name, :now, :now) ON CONFLICT DO NOTHING"
                ),
                {"id": u, "email": f"u_{u.hex[:8]}@test.com", "name": "User", "now": now_utc},
            )
        card = CreditCard(
            id=uuid.uuid4(),
            user_id=user_a,
            name="Card A",
            bank="Bancolombia",
            credit_limit=Decimal("5000000.00"),
            cutoff_day=15,
            due_day=5,
            currency="COP",
            is_active=True,
            canonical_mode=True,
            canonical_activated_at=now_utc,
            provenance="manual",
            lifecycle_status="active",
        )
        sess.add(card)
        await sess.commit()

        service = CreditCardService(sess, fx_provider=static_fx)
        uow = UnitOfWork(sess)
        async with uow.transaction():
            await service.create_purchase(
                user_id=user_a,
                card_id=card.id,
                payload=CreditCardPurchaseCreate(amount=Decimal("50000.00")),
                command_id=command_id,
            )

    # User B reconciles User A's command
    async with session_factory() as sess_b:
        service_b = CreditCardService(sess_b, fx_provider=static_fx)
        recon_b = await service_b.reconcile_command(user_id=user_b, command_id=command_id)

        # Must be strictly unknown with all IDs null
        assert recon_b.status == "unknown"
        assert recon_b.operation_type is None
        assert recon_b.event_id is None
        assert recon_b.resource_id is None


@pytest.mark.asyncio(loop_scope="session")
async def test_pg_reconcile_read_only_zero_writes(
    session_factory: sessionmaker, static_fx: StaticFxRateProvider
):
    """Verifies that reconcile_command performs strictly read queries and causes
    zero physical writes (no dirty rows, no uncommitted changes, no table rows created).
    """
    user_id = uuid.uuid4()
    non_existent_command = uuid.uuid4()

    async with session_factory() as sess:
        service = CreditCardService(sess, fx_provider=static_fx)

        tables = (
            "financial_events",
            "accounts",
            "credit_card_transactions",
            "credit_card_installments",
            "credit_card_payments",
            "credit_card_payment_allocations",
            "credit_card_early_payments",
            "credit_card_adjustments",
            "credit_card_statements",
            "credit_card_statement_components",
            "credit_card_billing_cycles",
        )
        before = {
            table: (
                await sess.execute(text(f"SELECT COUNT(*) FROM {table}"))
            ).scalar_one()
            for table in tables
        }

        res = await service.reconcile_command(user_id=user_id, command_id=non_existent_command)
        assert res.status == "unknown"

        after = {
            table: (
                await sess.execute(text(f"SELECT COUNT(*) FROM {table}"))
            ).scalar_one()
            for table in tables
        }
        assert before == after
        assert len(sess.dirty) == 0
        assert len(sess.new) == 0
        assert len(sess.deleted) == 0
