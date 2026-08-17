import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.core.uow import UnitOfWork
from app.goals.models import (
    Goal,
    GoalAutoContributionRun,
    GoalAutoContributionSchedule,
    GoalTransaction,
)
from app.goals.repository import GoalRepository
from app.goals.schemas import (
    GoalAutoContributionScheduleCreate,
    GoalAutoContributionScheduleUpdate,
)
from app.goals.service import GoalService
from app.ledger.models import FinancialEvent
from app.ledger.repository import LedgerRepository
from app.users.models import User

pytestmark = pytest.mark.asyncio

EXPECTED_DATABASE = "nexum_release_gate_test"
EXPECTED_USER = "nexum_release_gate_test"
EXPECTED_REVISION = "goals_v1_9_auto_contributions"
SAFE_TUNNEL_HOSTS = {"127.0.0.1", "localhost", "::1"}
GATE_OPT_IN = "NEXUM_ALLOW_DESTRUCTIVE_TEST_DB"
MONDAY_UTC = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)


@dataclass(frozen=True)
class GateData:
    user_id: UUID
    account_id: UUID
    goal_ids: tuple[UUID, ...]
    schedule_ids: tuple[UUID, ...]
    scheduled_for: datetime


def _constraint_name(exc: IntegrityError) -> str | None:
    current: object | None = exc.orig
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        name = getattr(current, "constraint_name", None)
        if name:
            return str(name)
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return None


@pytest.fixture
async def pg_engine() -> AsyncEngine:
    database_url = os.getenv("ARGOS_DATABASE_URL")
    if not database_url:
        pytest.skip("Requires ARGOS_DATABASE_URL")
    if os.getenv(GATE_OPT_IN) != "1":
        pytest.skip(f"Requires explicit {GATE_OPT_IN}=1 opt-in")

    parsed = make_url(database_url)
    if parsed.database != EXPECTED_DATABASE:
        pytest.fail(f"Unsafe PostgreSQL target database: {parsed.database!r}")
    if parsed.host not in SAFE_TUNNEL_HOSTS:
        pytest.fail(f"Unsafe PostgreSQL target host: {parsed.host!r}; SSH tunnel required")

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
            if identity[2] != 170010:
                pytest.fail(f"Unexpected PostgreSQL version_num: {identity[2]}")

            revision = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
            if revision != EXPECTED_REVISION:
                pytest.fail(f"Unexpected Alembic revision: {revision!r}")
        yield engine
    finally:
        await engine.dispose()


def _session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _service(session: AsyncSession) -> GoalService:
    return GoalService(
        GoalRepository(session),
        AccountRepository(session),
        LedgerRepository(session),
    )


async def _seed_gate_data(
    engine: AsyncEngine,
    *,
    scheduled_for: datetime = MONDAY_UTC,
    schedules: int = 1,
    account_balance: Decimal = Decimal("1000.00"),
    goal_target: Decimal = Decimal("500.00"),
    goal_current: Decimal = Decimal("0.00"),
    goal_status: str = "active",
    goal_is_active: bool = True,
    schedule_amount: Decimal = Decimal("100.00"),
    include_schedule: bool = True,
) -> GateData:
    user_id = uuid4()
    account_id = uuid4()
    goal_ids = tuple(uuid4() for _ in range(schedules))
    schedule_ids = tuple(uuid4() for _ in range(schedules)) if include_schedule else ()
    factory = _session_factory(engine)

    async with factory() as session, session.begin():
        session.add(
            User(
                id=user_id,
                email=f"gate-{user_id}@example.invalid",
                name="Goals V1.9 Gate",
            )
        )
        await session.flush()
        session.add(
            Account(
                id=account_id,
                user_id=user_id,
                name="Synthetic gate account",
                type="bank",
                balance=account_balance,
                currency="COP",
                is_active=True,
            )
        )
        session.add_all(
            [
                Goal(
                    id=goal_id,
                    user_id=user_id,
                    name=f"Synthetic gate goal {index}",
                    target_amount=goal_target,
                    current_amount=goal_current,
                    currency="COP",
                    status=goal_status,
                    is_active=goal_is_active,
                )
                for index, goal_id in enumerate(goal_ids)
            ]
        )
        await session.flush()
        if include_schedule:
            session.add_all(
                [
                    GoalAutoContributionSchedule(
                        id=schedule_id,
                        user_id=user_id,
                        goal_id=goal_id,
                        account_id=account_id,
                        amount=schedule_amount,
                        frequency="weekly",
                        execution_day="monday",
                        timezone="UTC",
                        start_date=scheduled_for.date(),
                        status="active",
                        next_run_at=scheduled_for,
                    )
                    for schedule_id, goal_id in zip(schedule_ids, goal_ids, strict=True)
                ]
            )

    return GateData(
        user_id=user_id,
        account_id=account_id,
        goal_ids=goal_ids,
        schedule_ids=schedule_ids,
        scheduled_for=scheduled_for,
    )


async def _cleanup_gate_data(engine: AsyncEngine, user_id: UUID) -> None:
    factory = _session_factory(engine)
    statements = (
        "DELETE FROM goal_auto_contribution_runs WHERE schedule_id IN "
        "(SELECT id FROM goal_auto_contribution_schedules WHERE user_id = :user_id)",
        "DELETE FROM goal_transactions WHERE user_id = :user_id",
        "DELETE FROM financial_events WHERE user_id = :user_id",
        "DELETE FROM goal_auto_contribution_schedules WHERE user_id = :user_id",
        "DELETE FROM goals WHERE user_id = :user_id",
        "DELETE FROM accounts WHERE user_id = :user_id",
        "DELETE FROM users WHERE id = :user_id",
    )
    async with factory() as session, session.begin():
        for statement in statements:
            await session.execute(text(statement), {"user_id": user_id})


async def _counts(session: AsyncSession, user_id: UUID) -> dict[str, int]:
    queries = {
        "runs": select(func.count(GoalAutoContributionRun.id)).where(
            GoalAutoContributionRun.schedule_id.in_(
                select(GoalAutoContributionSchedule.id).where(
                    GoalAutoContributionSchedule.user_id == user_id
                )
            )
        ),
        "transactions": select(func.count(GoalTransaction.id)).where(
            GoalTransaction.user_id == user_id
        ),
        "events": select(func.count(FinancialEvent.id)).where(FinancialEvent.user_id == user_id),
    }
    return {
        name: int((await session.execute(query)).scalar_one()) for name, query in queries.items()
    }


async def test_pg01_pg05_schema_identity(pg_engine: AsyncEngine) -> None:
    factory = _session_factory(pg_engine)
    async with factory() as session:
        revision = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
        tables = set(
            (
                await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' "
                        "AND table_name LIKE 'goal_auto_contribution_%'"
                    )
                )
            ).scalars()
        )
        constraints = set(
            (
                await session.execute(
                    text(
                        "SELECT rel.relname, con.conname FROM pg_constraint con "
                        "JOIN pg_class rel ON rel.oid = con.conrelid "
                        "JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace "
                        "WHERE nsp.nspname='public' "
                        "AND rel.relname IN "
                        "('goal_auto_contribution_schedules', "
                        "'goal_auto_contribution_runs') "
                        "AND con.contype IN ('c', 'u')"
                    )
                )
            ).all()
        )
    assert revision == EXPECTED_REVISION
    assert tables == {"goal_auto_contribution_schedules", "goal_auto_contribution_runs"}
    assert constraints == {
        ("goal_auto_contribution_schedules", "chk_gacs_amount_pos"),
        ("goal_auto_contribution_schedules", "chk_gacs_frequency"),
        ("goal_auto_contribution_schedules", "chk_gacs_execution_day"),
        ("goal_auto_contribution_schedules", "chk_gacs_status"),
        ("goal_auto_contribution_schedules", "chk_gacs_pause_reason"),
        ("goal_auto_contribution_schedules", "chk_gacs_active_next_run"),
        ("goal_auto_contribution_runs", "uq_gacr_schedule_scheduled_for"),
        ("goal_auto_contribution_runs", "chk_gacr_status"),
        ("goal_auto_contribution_runs", "chk_gacr_result_code"),
        ("goal_auto_contribution_runs", "chk_gacr_conf_amount_pos"),
        ("goal_auto_contribution_runs", "chk_gacr_exec_amount_pos"),
        ("goal_auto_contribution_runs", "chk_gacr_missed_count"),
    }


@pytest.mark.parametrize(
    ("changes", "expected_constraints"),
    [
        ({"amount": Decimal("0.00")}, {"chk_gacs_amount_pos"}),
        (
            {"frequency": "daily"},
            {"chk_gacs_frequency", "chk_gacs_execution_day"},
        ),
        ({"execution_day": "noday"}, {"chk_gacs_execution_day"}),
        ({"next_run_at": None}, {"chk_gacs_active_next_run"}),
    ],
)
async def test_pg06_constraint_matrix(
    pg_engine: AsyncEngine,
    changes: dict[str, object],
    expected_constraints: set[str],
) -> None:
    data = await _seed_gate_data(pg_engine, include_schedule=False)
    factory = _session_factory(pg_engine)
    values: dict[str, object] = {
        "amount": Decimal("100.00"),
        "frequency": "weekly",
        "execution_day": "monday",
        "next_run_at": MONDAY_UTC,
    }
    values.update(changes)
    try:
        async with factory() as session:
            session.add(
                GoalAutoContributionSchedule(
                    id=uuid4(),
                    user_id=data.user_id,
                    goal_id=data.goal_ids[0],
                    account_id=data.account_id,
                    amount=values["amount"],
                    frequency=values["frequency"],
                    execution_day=values["execution_day"],
                    timezone="UTC",
                    status="active",
                    next_run_at=values["next_run_at"],
                )
            )
            with pytest.raises(IntegrityError) as raised:
                await session.flush()
            assert _constraint_name(raised.value) in expected_constraints
            await session.rollback()
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg07_partial_unique(pg_engine: AsyncEngine) -> None:
    data = await _seed_gate_data(pg_engine)
    factory = _session_factory(pg_engine)
    try:
        async with factory() as session:
            session.add(
                GoalAutoContributionSchedule(
                    id=uuid4(),
                    user_id=data.user_id,
                    goal_id=data.goal_ids[0],
                    account_id=data.account_id,
                    amount=Decimal("10.00"),
                    frequency="weekly",
                    execution_day="monday",
                    timezone="UTC",
                    status="paused",
                    next_run_at=None,
                )
            )
            with pytest.raises(IntegrityError) as raised:
                await session.flush()
            assert _constraint_name(raised.value) == "ix_goal_auto_contrib_goal_id_active"
            await session.rollback()
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg08_run_occurrence_unique(pg_engine: AsyncEngine) -> None:
    data = await _seed_gate_data(pg_engine)
    factory = _session_factory(pg_engine)
    try:
        async with factory() as session:
            session.add_all(
                [
                    GoalAutoContributionRun(
                        id=uuid4(),
                        schedule_id=data.schedule_ids[0],
                        scheduled_for=data.scheduled_for,
                        status="skipped",
                        result_code="insufficient_available_balance",
                        configured_amount=Decimal("100.00"),
                        missed_occurrences_count=0,
                    )
                    for _ in range(2)
                ]
            )
            with pytest.raises(IntegrityError) as raised:
                await session.flush()
            assert _constraint_name(raised.value) == "uq_gacr_schedule_scheduled_for"
            await session.rollback()
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg09_crud_is_physical_and_financially_neutral(pg_engine: AsyncEngine) -> None:
    data = await _seed_gate_data(pg_engine, include_schedule=False)
    factory = _session_factory(pg_engine)
    try:
        async with factory() as session:
            service = _service(session)
            before = await _counts(session, data.user_id)
            async with UnitOfWork(session).transaction():
                created = await service.put_auto_contribution_schedule(
                    data.user_id,
                    data.goal_ids[0],
                    GoalAutoContributionScheduleCreate(
                        account_id=data.account_id,
                        amount=Decimal("100.00"),
                        frequency="weekly",
                        execution_day="monday",
                        timezone="UTC",
                    ),
                )
            read = await service.get_auto_contribution_schedule(data.user_id, data.goal_ids[0])
            assert read.id == created.id
            async with UnitOfWork(session).transaction():
                patched = await service.patch_auto_contribution_schedule(
                    data.user_id,
                    data.goal_ids[0],
                    GoalAutoContributionScheduleUpdate(amount=Decimal("75.00")),
                )
            assert patched.amount == Decimal("75.00")
            async with UnitOfWork(session).transaction():
                paused = await service.pause_auto_contribution_schedule(
                    data.user_id, data.goal_ids[0]
                )
            assert paused.status == "paused"
            async with UnitOfWork(session).transaction():
                resumed = await service.resume_auto_contribution_schedule(
                    data.user_id, data.goal_ids[0]
                )
            assert resumed.status == "active"
            async with UnitOfWork(session).transaction():
                await service.delete_auto_contribution_schedule(data.user_id, data.goal_ids[0])
            status = (
                await session.execute(
                    select(GoalAutoContributionSchedule.status).where(
                        GoalAutoContributionSchedule.id == created.id
                    )
                )
            ).scalar_one()
            assert status == "cancelled"
            assert await _counts(session, data.user_id) == before
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg10_success_financial_matrix(pg_engine: AsyncEngine) -> None:
    data = await _seed_gate_data(pg_engine, goal_current=Decimal("100.00"))
    factory = _session_factory(pg_engine)
    try:
        async with factory() as session:
            account_before = await session.get(Account, data.account_id)
            goal_before = await session.get(Goal, data.goal_ids[0])
            assert account_before is not None and goal_before is not None
            balance_before = account_before.balance
            current_before = goal_before.current_amount
            reserved_before = await GoalRepository(session).calculate_reserved_by_account(
                data.account_id, data.user_id
            )
            summary = await _service(session).process_due_auto_contributions(
                MONDAY_UTC + timedelta(hours=1), batch_size=1
            )
            session.expire_all()
            account_after = await session.get(Account, data.account_id)
            goal_after = await session.get(Goal, data.goal_ids[0])
            reserved_after = await GoalRepository(session).calculate_reserved_by_account(
                data.account_id, data.user_id
            )
            counts = await _counts(session, data.user_id)
            event = (
                await session.execute(
                    select(FinancialEvent).where(FinancialEvent.user_id == data.user_id)
                )
            ).scalar_one()
            history = await _service(session).list_goal_transactions(data.user_id, data.goal_ids[0])
            ledger_summary = await LedgerRepository(session).get_summary(data.user_id)

        assert summary.succeeded == 1 and summary.technical_failures == 0
        assert account_after is not None and goal_after is not None
        assert balance_before == account_after.balance == Decimal("1000.00")
        assert reserved_before == Decimal("0.00")
        assert reserved_after == Decimal("100.00")
        assert balance_before - reserved_before == Decimal("1000.00")
        assert account_after.balance - reserved_after == Decimal("900.00")
        assert current_before == Decimal("100.00")
        assert goal_after.current_amount == Decimal("200.00")
        assert counts == {"runs": 1, "transactions": 1, "events": 1}
        assert event.direction == "neutral" and event.event_type == "goal_contribution"
        assert history.items[0].origin == "native"
        assert history.items[0].channel == "automatic"
        assert ledger_summary["total_income"] == Decimal("0")
        assert ledger_summary["total_expense"] == Decimal("0")
        assert ledger_summary["net_cashflow"] == Decimal("0")
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg11_final_partial(pg_engine: AsyncEngine) -> None:
    data = await _seed_gate_data(pg_engine, goal_current=Decimal("460.00"))
    factory = _session_factory(pg_engine)
    try:
        async with factory() as session:
            summary = await _service(session).process_due_auto_contributions(
                MONDAY_UTC + timedelta(hours=1), batch_size=1
            )
            run = (
                await session.execute(
                    select(GoalAutoContributionRun).where(
                        GoalAutoContributionRun.schedule_id == data.schedule_ids[0]
                    )
                )
            ).scalar_one()
            goal = await session.get(Goal, data.goal_ids[0])
            schedule = await session.get(GoalAutoContributionSchedule, data.schedule_ids[0])
        assert summary.succeeded == 1
        assert run.executed_amount == Decimal("40.00")
        assert goal is not None and goal.current_amount == Decimal("500.00")
        assert schedule is not None and schedule.pause_reason == "goal_completed"
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg12_insufficient_available_balance(pg_engine: AsyncEngine) -> None:
    data = await _seed_gate_data(
        pg_engine,
        account_balance=Decimal("100.00"),
        schedule_amount=Decimal("200.00"),
    )
    factory = _session_factory(pg_engine)
    try:
        async with factory() as session:
            summary = await _service(session).process_due_auto_contributions(
                MONDAY_UTC + timedelta(hours=1), batch_size=1
            )
            run = (
                await session.execute(
                    select(GoalAutoContributionRun).where(
                        GoalAutoContributionRun.schedule_id == data.schedule_ids[0]
                    )
                )
            ).scalar_one()
            counts = await _counts(session, data.user_id)
        assert summary.skipped == 1 and summary.technical_failures == 0
        assert run.result_code == "insufficient_available_balance"
        assert run.executed_amount is None
        assert counts == {"runs": 1, "transactions": 0, "events": 0}
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg13_lifecycle_skip(pg_engine: AsyncEngine) -> None:
    data = await _seed_gate_data(
        pg_engine,
        goal_current=Decimal("500.00"),
        goal_status="completed",
    )
    factory = _session_factory(pg_engine)
    try:
        async with factory() as session:
            summary = await _service(session).process_due_auto_contributions(
                MONDAY_UTC + timedelta(hours=1), batch_size=1
            )
            schedule = await session.get(GoalAutoContributionSchedule, data.schedule_ids[0])
            counts = await _counts(session, data.user_id)
        assert summary.skipped == 1
        assert schedule is not None and schedule.status == "paused"
        assert schedule.pause_reason == "goal_completed"
        assert counts == {"runs": 1, "transactions": 0, "events": 0}
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg14_physical_replay(pg_engine: AsyncEngine) -> None:
    data = await _seed_gate_data(pg_engine)
    factory = _session_factory(pg_engine)
    try:
        async with factory() as session:
            await _service(session).process_due_auto_contributions(
                MONDAY_UTC + timedelta(hours=1), batch_size=1
            )
            before = await _counts(session, data.user_id)
        async with factory() as session:
            replay = await _service(session).execute_auto_contribution_occurrence(
                data.schedule_ids[0],
                data.scheduled_for,
                MONDAY_UTC + timedelta(hours=2),
            )
            after = await _counts(session, data.user_id)
        assert replay.idempotent is True
        assert before == after == {"runs": 1, "transactions": 1, "events": 1}
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg15_latest_only(pg_engine: AsyncEngine) -> None:
    first_due = MONDAY_UTC - timedelta(days=21)
    data = await _seed_gate_data(pg_engine, scheduled_for=first_due)
    factory = _session_factory(pg_engine)
    try:
        async with factory() as session:
            summary = await _service(session).process_due_auto_contributions(
                MONDAY_UTC + timedelta(hours=1), batch_size=1
            )
            run = (
                await session.execute(
                    select(GoalAutoContributionRun).where(
                        GoalAutoContributionRun.schedule_id == data.schedule_ids[0]
                    )
                )
            ).scalar_one()
            counts = await _counts(session, data.user_id)
        assert summary.succeeded == 1 and summary.missed_occurrences == 3
        assert run.scheduled_for == MONDAY_UTC
        assert run.missed_occurrences_count == 3
        assert counts == {"runs": 1, "transactions": 1, "events": 1}
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg16_stale_cursor(pg_engine: AsyncEngine) -> None:
    data = await _seed_gate_data(pg_engine)
    factory = _session_factory(pg_engine)
    try:
        async with factory() as session:
            await _service(session).process_due_auto_contributions(
                MONDAY_UTC + timedelta(hours=1), batch_size=1
            )
            before = await _counts(session, data.user_id)
            await session.execute(
                text(
                    "UPDATE goal_auto_contribution_schedules "
                    "SET status='active', pause_reason=NULL, next_run_at=:cursor "
                    "WHERE id=:schedule_id"
                ),
                {"cursor": data.scheduled_for, "schedule_id": data.schedule_ids[0]},
            )
            await session.commit()
        async with factory() as session:
            summary = await _service(session).process_due_auto_contributions(
                MONDAY_UTC + timedelta(hours=1), batch_size=1
            )
            after = await _counts(session, data.user_id)
            schedule = await session.get(GoalAutoContributionSchedule, data.schedule_ids[0])
        assert summary.reconciled == 1
        assert before == after == {"runs": 1, "transactions": 1, "events": 1}
        assert schedule is not None and schedule.next_run_at > MONDAY_UTC
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg17_two_independent_connections_skip_locked(pg_engine: AsyncEngine) -> None:
    data = await _seed_gate_data(pg_engine, schedules=2)
    factory = _session_factory(pg_engine)
    try:
        async with factory() as session_a, factory() as session_b:
            pid_a = (await session_a.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            pid_b = (await session_b.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            locked_a = await GoalRepository(
                session_a
            ).get_next_due_auto_contribution_schedule_for_update(MONDAY_UTC + timedelta(hours=1))
            locked_b = await asyncio.wait_for(
                GoalRepository(session_b).get_next_due_auto_contribution_schedule_for_update(
                    MONDAY_UTC + timedelta(hours=1)
                ),
                timeout=2,
            )
            assert pid_a != pid_b
            assert locked_a is not None and locked_b is not None
            assert locked_a.id != locked_b.id
            await session_b.rollback()
            await session_a.rollback()
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg18_two_physical_processors(pg_engine: AsyncEngine) -> None:
    data = await _seed_gate_data(pg_engine, schedules=2)
    factory = _session_factory(pg_engine)
    try:
        async with factory() as session_a, factory() as session_b:
            summaries = await asyncio.gather(
                _service(session_a).process_due_auto_contributions(
                    MONDAY_UTC + timedelta(hours=1), batch_size=1
                ),
                _service(session_b).process_due_auto_contributions(
                    MONDAY_UTC + timedelta(hours=1), batch_size=1
                ),
            )
        async with factory() as session:
            counts = await _counts(session, data.user_id)
            distinct_occurrences = (
                await session.execute(
                    select(
                        func.count(
                            func.distinct(
                                GoalAutoContributionRun.schedule_id,
                                GoalAutoContributionRun.scheduled_for,
                            )
                        )
                    ).where(GoalAutoContributionRun.schedule_id.in_(data.schedule_ids))
                )
            ).scalar_one()
            reserved = await GoalRepository(session).calculate_reserved_by_account(
                data.account_id, data.user_id
            )
        assert [summary.selected for summary in summaries] == [1, 1]
        assert [summary.succeeded for summary in summaries] == [1, 1]
        assert all(summary.technical_failures == 0 for summary in summaries)
        assert counts == {"runs": 2, "transactions": 2, "events": 2}
        assert distinct_occurrences == 2
        assert reserved == Decimal("200.00")
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg19_same_occurrence_race(pg_engine: AsyncEngine) -> None:
    data = await _seed_gate_data(pg_engine)
    factory = _session_factory(pg_engine)
    try:
        async with factory() as session_a, factory() as session_b:
            repository_a = GoalRepository(session_a)
            repository_b = GoalRepository(session_b)
            service_a = GoalService(
                repository_a, AccountRepository(session_a), LedgerRepository(session_a)
            )
            service_b = GoalService(
                repository_b, AccountRepository(session_b), LedgerRepository(session_b)
            )
            barrier = asyncio.Barrier(2)

            def gate_first_lookup(repository: GoalRepository):
                original = repository.get_auto_contribution_run
                first = True

                async def gated(schedule_id: UUID, scheduled_for: datetime):
                    nonlocal first
                    result = await original(schedule_id, scheduled_for)
                    if first:
                        first = False
                        await barrier.wait()
                    return result

                repository.get_auto_contribution_run = gated

            gate_first_lookup(repository_a)
            gate_first_lookup(repository_b)
            results = await asyncio.gather(
                service_a.execute_auto_contribution_occurrence(
                    data.schedule_ids[0], data.scheduled_for, MONDAY_UTC + timedelta(hours=1)
                ),
                service_b.execute_auto_contribution_occurrence(
                    data.schedule_ids[0], data.scheduled_for, MONDAY_UTC + timedelta(hours=1)
                ),
            )
        async with factory() as session:
            counts = await _counts(session, data.user_id)
            reserved = await GoalRepository(session).calculate_reserved_by_account(
                data.account_id, data.user_id
            )
        assert sorted(result.idempotent for result in results) == [False, True]
        assert counts == {"runs": 1, "transactions": 1, "events": 1}
        assert reserved == Decimal("100.00")
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg20_physical_uow_rollback(pg_engine: AsyncEngine) -> None:
    data = await _seed_gate_data(pg_engine)
    factory = _session_factory(pg_engine)
    try:
        async with factory() as session:
            schedule_before = await session.get(GoalAutoContributionSchedule, data.schedule_ids[0])
            goal_before = await session.get(Goal, data.goal_ids[0])
            assert schedule_before is not None and goal_before is not None
            next_run_before = schedule_before.next_run_at
            current_before = goal_before.current_amount
            repository = GoalRepository(session)
            service = GoalService(repository, AccountRepository(session), LedgerRepository(session))

            async def fail_run_insert(run: GoalAutoContributionRun):
                raise RuntimeError(f"fault after financial writes: {run.schedule_id}")

            repository.create_auto_contribution_run = fail_run_insert
            summary = await service.process_due_auto_contributions(
                MONDAY_UTC + timedelta(hours=1), batch_size=1
            )
        async with factory() as session:
            counts = await _counts(session, data.user_id)
            schedule_after = await session.get(GoalAutoContributionSchedule, data.schedule_ids[0])
            goal_after = await session.get(Goal, data.goal_ids[0])
            reserved = await GoalRepository(session).calculate_reserved_by_account(
                data.account_id, data.user_id
            )
        assert summary.technical_failures == 1
        assert counts == {"runs": 0, "transactions": 0, "events": 0}
        assert goal_after is not None and goal_after.current_amount == current_before
        assert schedule_after is not None and schedule_after.next_run_at == next_run_before
        assert reserved == Decimal("0.00")
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)


async def test_pg21_real_module_process(pg_engine: AsyncEngine) -> None:
    data = await _seed_gate_data(pg_engine)
    factory = _session_factory(pg_engine)
    env = os.environ.copy()
    env["DATABASE_URL"] = os.environ["ARGOS_DATABASE_URL"]
    command = (
        sys.executable,
        "-m",
        "scripts.cron.process_auto_contributions",
        "--batch-size",
        "1",
    )
    try:
        due = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        due_stdout, due_stderr = await asyncio.wait_for(due.communicate(), timeout=30)
        assert due.returncode == 0, (due_stdout + due_stderr).decode(errors="replace")
        async with factory() as session:
            assert await _counts(session, data.user_id) == {
                "runs": 1,
                "transactions": 1,
                "events": 1,
            }
    finally:
        await _cleanup_gate_data(pg_engine, data.user_id)

    empty = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    empty_stdout, empty_stderr = await asyncio.wait_for(empty.communicate(), timeout=30)
    assert empty.returncode == 0, (empty_stdout + empty_stderr).decode(errors="replace")
