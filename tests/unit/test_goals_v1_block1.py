import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.accounts.models import Account
from app.goals.enums import GoalTransactionType
from app.goals.models import Goal, GoalTransaction
from app.goals.repository import GoalRepository
from app.ledger.models import FinancialEvent
from app.users.models import User


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _RecordingSession:
    def __init__(self, value):
        self.value = value
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _ScalarResult(self.value)


def test_goal_transaction_enum_is_exact():
    assert {item.value for item in GoalTransactionType} == {
        "allocation",
        "release",
        "adjustment",
        "legacy_import",
    }


def test_goal_transaction_orm_contract():
    table = GoalTransaction.__table__
    columns = table.columns

    assert set(columns.keys()) == {
        "id",
        "goal_id",
        "account_id",
        "user_id",
        "event_id",
        "transaction_type",
        "source_amount",
        "source_currency",
        "applied_amount",
        "goal_currency",
        "command_id",
        "command_fingerprint",
        "legacy_contribution_id",
        "metadata_json",
        "created_at",
    }
    assert columns.source_amount.type.precision == 14
    assert columns.source_amount.type.scale == 2
    assert columns.applied_amount.type.precision == 14
    assert columns.applied_amount.type.scale == 2
    assert columns.source_currency.type.length == 3
    assert columns.goal_currency.type.length == 3
    assert columns.event_id.nullable is True
    assert columns.created_at.type.timezone is True

    foreign_keys = {fk.parent.name: (fk.target_fullname, fk.ondelete) for fk in table.foreign_keys}
    assert foreign_keys == {
        "goal_id": ("goals.id", "RESTRICT"),
        "account_id": ("accounts.id", "RESTRICT"),
        "user_id": ("users.id", "RESTRICT"),
        "event_id": ("financial_events.id", "RESTRICT"),
        "legacy_contribution_id": ("goal_contributions.id", "RESTRICT"),
    }

    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert checks == {
        "chk_gtx_source_pos": "source_amount > 0",
        "chk_gtx_applied_pos": "applied_amount > 0",
        "chk_gtx_event_id": ("(transaction_type = 'legacy_import') OR (event_id IS NOT NULL)"),
        "chk_gtx_fingerprint": (
            "(command_id IS NULL AND command_fingerprint IS NULL) OR "
            "(command_id IS NOT NULL AND command_fingerprint IS NOT NULL)"
        ),
    }

    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert unique_constraints == {
        "uq_gtx_command_id": ("command_id",),
        "uq_gtx_legacy_id": ("legacy_contribution_id",),
    }
    assert {index.name for index in table.indexes} == {
        "ix_goal_transactions_goal_id",
        "ix_goal_transactions_account_id",
        "ix_goal_transactions_user_id",
        "ix_goal_transactions_created_at",
        "ix_goal_transactions_tx_type",
    }


@pytest.mark.asyncio
async def test_reserved_query_filters_owner_account_and_transaction_types():
    session = _RecordingSession(Decimal("75.00"))
    repository = GoalRepository(session)
    account_id = uuid.uuid4()
    user_id = uuid.uuid4()

    result = await repository.calculate_reserved_by_account(account_id, user_id)

    compiled = session.statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    sql = str(compiled)
    assert result == Decimal("75.00")
    assert "goal_transactions.account_id" in sql
    assert "goal_transactions.user_id" in sql
    assert "IN ('allocation', 'legacy_import')" in sql
    assert "goal_transactions.transaction_type = 'release'" in sql


@pytest.mark.asyncio
async def test_progress_query_filters_owner_goal_and_transaction_types():
    session = _RecordingSession(Decimal("125.00"))
    repository = GoalRepository(session)
    goal_id = uuid.uuid4()
    user_id = uuid.uuid4()

    result = await repository.calculate_progress_by_goal(goal_id, user_id)

    compiled = session.statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    sql = str(compiled)
    assert result == Decimal("125.00")
    assert "goal_transactions.goal_id" in sql
    assert "goal_transactions.user_id" in sql
    assert "goal_transactions.transaction_type IN ('allocation', 'legacy_import')" in sql
    assert "goal_transactions.transaction_type = 'release'" in sql


@pytest.mark.asyncio
async def test_non_legacy_count_includes_null_metadata_rows():
    session = _RecordingSession(2)
    repository = GoalRepository(session)

    result = await repository.count_non_legacy_transactions()

    compiled = session.statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    assert result == 2
    assert "goal_transactions.legacy_contribution_id IS NULL" in str(compiled)


@pytest.fixture
async def db_session():
    database_url = os.getenv("ARGOS_DATABASE_URL")
    if not database_url:
        pytest.skip("Requires ARGOS_DATABASE_URL for PostgreSQL integration")

    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.fixture
async def sample_user(db_session: AsyncSession):
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def sample_account(db_session: AsyncSession, sample_user: User):
    account = Account(
        id=uuid.uuid4(), user_id=sample_user.id, currency="COP", balance=Decimal("1000")
    )
    db_session.add(account)
    await db_session.flush()
    return account


@pytest.fixture
async def sample_goal(db_session: AsyncSession, sample_user: User):
    goal = Goal(
        id=uuid.uuid4(),
        user_id=sample_user.id,
        name="Test Goal",
        target_amount=Decimal("500"),
        current_amount=Decimal("0"),
        currency="COP",
    )
    db_session.add(goal)
    await db_session.flush()
    return goal


@pytest.fixture
async def sample_event(db_session: AsyncSession, sample_user: User, sample_account: Account):
    event = FinancialEvent(
        id=uuid.uuid4(),
        user_id=sample_user.id,
        account_id=sample_account.id,
        event_type="goal_contribution",
        direction="outflow",
        amount=Decimal("100"),
        currency="COP",
    )
    db_session.add(event)
    await db_session.flush()
    return event


@pytest.mark.asyncio
async def test_goal_transaction_model_valid(
    db_session: AsyncSession, sample_user, sample_account, sample_goal, sample_event
):
    tx = GoalTransaction(
        goal_id=sample_goal.id,
        account_id=sample_account.id,
        user_id=sample_user.id,
        event_id=sample_event.id,
        transaction_type=GoalTransactionType.allocation,
        source_amount=Decimal("100.00"),
        source_currency="COP",
        applied_amount=Decimal("100.00"),
        goal_currency="COP",
        command_id=uuid.uuid4(),
        command_fingerprint="test_fingerprint",
    )
    db_session.add(tx)
    await db_session.flush()
    assert tx.id is not None


@pytest.mark.asyncio
async def test_goal_transaction_amount_constraints(
    db_session: AsyncSession, sample_user, sample_account, sample_goal, sample_event
):
    tx = GoalTransaction(
        goal_id=sample_goal.id,
        account_id=sample_account.id,
        user_id=sample_user.id,
        event_id=sample_event.id,
        transaction_type=GoalTransactionType.allocation,
        source_amount=Decimal("0.00"),  # Should fail
        source_currency="COP",
        applied_amount=Decimal("100.00"),
        goal_currency="COP",
    )
    db_session.add(tx)
    with pytest.raises(IntegrityError) as exc:
        await db_session.flush()
    assert "chk_gtx_source_pos" in str(exc.value)


@pytest.mark.asyncio
async def test_goal_transaction_command_pair(
    db_session: AsyncSession, sample_user, sample_account, sample_goal, sample_event
):
    tx = GoalTransaction(
        goal_id=sample_goal.id,
        account_id=sample_account.id,
        user_id=sample_user.id,
        event_id=sample_event.id,
        transaction_type=GoalTransactionType.allocation,
        source_amount=Decimal("10.00"),
        source_currency="COP",
        applied_amount=Decimal("10.00"),
        goal_currency="COP",
        command_id=uuid.uuid4(),
        # missing fingerprint
    )
    db_session.add(tx)
    with pytest.raises(IntegrityError) as exc:
        await db_session.flush()
    assert "chk_gtx_fingerprint" in str(exc.value)


@pytest.mark.asyncio
async def test_goal_transaction_repository(
    db_session: AsyncSession, sample_user, sample_account, sample_goal, sample_event
):
    repo = GoalRepository(db_session)

    # 1. Create allocation
    tx = GoalTransaction(
        goal_id=sample_goal.id,
        account_id=sample_account.id,
        user_id=sample_user.id,
        event_id=sample_event.id,
        transaction_type=GoalTransactionType.allocation,
        source_amount=Decimal("150.00"),
        source_currency="COP",
        applied_amount=Decimal("150.00"),
        goal_currency="COP",
    )
    await repo.create_transaction(tx)

    # 2. Get by ID
    fetched = await repo.get_transaction_by_id(tx.id)
    assert fetched is not None
    assert fetched.source_amount == Decimal("150.00")

    # 3. Reserve calculation
    reserve = await repo.calculate_reserved_by_account(sample_account.id, sample_user.id)
    assert reserve == Decimal("150.00")

    # 4. Progress calculation
    progress = await repo.calculate_progress_by_goal(sample_goal.id, sample_user.id)
    assert progress == Decimal("150.00")

    # 5. Add release
    tx_rel = GoalTransaction(
        goal_id=sample_goal.id,
        account_id=sample_account.id,
        user_id=sample_user.id,
        event_id=sample_event.id,
        transaction_type=GoalTransactionType.release,
        source_amount=Decimal("50.00"),
        source_currency="COP",
        applied_amount=Decimal("50.00"),
        goal_currency="COP",
    )
    await repo.create_transaction(tx_rel)

    reserve = await repo.calculate_reserved_by_account(sample_account.id, sample_user.id)
    assert reserve == Decimal("100.00")

    # 6. Add legacy import
    tx_leg = GoalTransaction(
        goal_id=sample_goal.id,
        account_id=sample_account.id,
        user_id=sample_user.id,
        event_id=None,
        transaction_type=GoalTransactionType.legacy_import,
        source_amount=Decimal("20.00"),
        source_currency="COP",
        applied_amount=Decimal("20.00"),
        goal_currency="COP",
    )
    await repo.create_transaction(tx_leg)
    # legacy_import is an authoritative imported reservation and must use
    # source_amount for Account reserve and applied_amount for Goal progress.
    reserve = await repo.calculate_reserved_by_account(sample_account.id, sample_user.id)
    assert reserve == Decimal("120.00")
    progress = await repo.calculate_progress_by_goal(sample_goal.id, sample_user.id)
    assert progress == Decimal("120.00")


@pytest.mark.asyncio
async def test_goal_transaction_unique_command_id(
    db_session: AsyncSession, sample_user, sample_account, sample_goal, sample_event
):
    cmd_id = uuid.uuid4()
    tx1 = GoalTransaction(
        goal_id=sample_goal.id,
        account_id=sample_account.id,
        user_id=sample_user.id,
        event_id=sample_event.id,
        transaction_type=GoalTransactionType.allocation,
        source_amount=Decimal("100.00"),
        source_currency="COP",
        applied_amount=Decimal("100.00"),
        goal_currency="COP",
        command_id=cmd_id,
        command_fingerprint="test",
    )
    db_session.add(tx1)
    await db_session.flush()

    tx2 = GoalTransaction(
        goal_id=sample_goal.id,
        account_id=sample_account.id,
        user_id=sample_user.id,
        event_id=sample_event.id,
        transaction_type=GoalTransactionType.allocation,
        source_amount=Decimal("100.00"),
        source_currency="COP",
        applied_amount=Decimal("100.00"),
        goal_currency="COP",
        command_id=cmd_id,
        command_fingerprint="test",
    )
    db_session.add(tx2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_schema_matches_db(db_session: AsyncSession):
    # Verifies types and constraints exist in DB matching the model
    # We query the pg_catalog directly
    result = await db_session.execute(
        text("""
        SELECT column_name, data_type, character_maximum_length, numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_name = 'goal_transactions'
    """)
    )
    cols = {row[0]: row for row in result.all()}

    assert "source_amount" in cols
    assert cols["source_amount"][1] == "numeric"
    assert cols["source_amount"][3] == 14
    assert cols["source_amount"][4] == 2

    # Enums
    result = await db_session.execute(
        text("""
        SELECT t.typname, e.enumlabel
        FROM pg_type t
        JOIN pg_enum e ON t.oid = e.enumtypid
        WHERE t.typname = 'goal_transaction_type'
    """)
    )
    enums = {row[1] for row in result.all()}
    assert enums == {"allocation", "release", "adjustment", "legacy_import"}
