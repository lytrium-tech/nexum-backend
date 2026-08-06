from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.core.errors import ValidationError
from app.goals.models import Goal
from app.goals.repository import GoalAccountReservation, GoalRepository
from app.goals.router import get_goal_service
from app.goals.schemas import GoalContributionCreate, GoalReleaseCreate
from app.goals.service import GoalService
from app.ledger.repository import LedgerRepository
from app.main import app
from app.users.dependencies import get_current_user_profile_dep
from app.users.schemas import UserRead


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _RecordingSession:
    def __init__(self, *, rows=None, scalar=None):
        self.rows = rows or []
        self.scalar = scalar
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if self.scalar is not None:
            return _ScalarResult(self.scalar)
        return _RowsResult(self.rows)


def _compiled_sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _goal(*, goal_id, user_id, currency, current_amount):
    return Goal(
        id=goal_id,
        user_id=user_id,
        name="Meta",
        target_amount=Decimal("1000.00"),
        current_amount=current_amount,
        currency=currency,
        is_active=True,
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _reservation(
    *,
    account_currency,
    goal_currency,
    source_reserved,
    applied_reserved,
    account_is_active=True,
):
    return GoalAccountReservation(
        account_id=uuid4(),
        account_name="Cuenta legacy",
        account_currency=account_currency,
        contributed_amount=source_reserved,
        released_amount=Decimal("0.00"),
        reserved_amount=source_reserved,
        goal_currency=goal_currency,
        applied_contributed_amount=applied_reserved,
        applied_released_amount=Decimal("0.00"),
        applied_reserved_amount=applied_reserved,
        account_is_active=account_is_active,
    )


@pytest.fixture
def repositories():
    return (
        AsyncMock(spec=GoalRepository),
        AsyncMock(spec=AccountRepository),
        AsyncMock(spec=LedgerRepository),
    )


@pytest.fixture
def service(repositories):
    return GoalService(*repositories)


@pytest.mark.asyncio
async def test_goal_detail_legacy_cop_to_usd_is_read_safe(service, repositories):
    goal_repo, _, _ = repositories
    user_id, goal_id = uuid4(), uuid4()
    goal_repo.get_by_id.return_value = _goal(
        goal_id=goal_id,
        user_id=user_id,
        currency="USD",
        current_amount=Decimal("1.00"),
    )
    goal_repo.get_period_contributions.return_value = {}
    goal_repo.get_reservations_by_account.return_value = [
        _reservation(
            account_currency="COP",
            goal_currency="USD",
            source_reserved=Decimal("4000.00"),
            applied_reserved=Decimal("1.00"),
        )
    ]

    result = await service.get_goal(user_id, goal_id)

    assert result.current_amount == Decimal("1.00")
    assert len(result.reservations_by_account) == 1
    reservation = result.reservations_by_account[0]
    assert reservation.account_currency == "COP"
    assert reservation.goal_currency == "USD"
    assert reservation.reserved_amount == Decimal("4000.00")
    assert reservation.applied_reserved_amount == Decimal("1.00")
    assert reservation.is_releasable is False
    assert reservation.release_block_reason == "currency_mismatch_legacy"
    assert (
        sum(
            (item.applied_reserved_amount for item in result.reservations_by_account),
            Decimal("0.00"),
        )
        == result.current_amount
    )
    assert (
        sum(
            (item.reserved_amount for item in result.reservations_by_account),
            Decimal("0.00"),
        )
        != result.current_amount
    )
    goal_repo.list_transactions_by_goal.assert_not_awaited()


def test_goal_detail_http_returns_200_for_legacy_cop_to_usd(service, repositories):
    goal_repo, _, _ = repositories
    user_id, goal_id = uuid4(), uuid4()
    goal_repo.get_by_id.return_value = _goal(
        goal_id=goal_id,
        user_id=user_id,
        currency="USD",
        current_amount=Decimal("1.00"),
    )
    goal_repo.get_period_contributions.return_value = {}
    goal_repo.get_reservations_by_account.return_value = [
        _reservation(
            account_currency="COP",
            goal_currency="USD",
            source_reserved=Decimal("4000.00"),
            applied_reserved=Decimal("1.00"),
        )
    ]
    user = UserRead(
        id=user_id,
        email="cross-currency@example.com",
        name="Cross Currency",
        timezone="America/Bogota",
        currency="COP",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    app.dependency_overrides[get_goal_service] = lambda: service
    app.dependency_overrides[get_current_user_profile_dep] = lambda: user
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/v1/goals/{goal_id}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    reservation = response.json()["reservations_by_account"][0]
    assert reservation["account_currency"] == "COP"
    assert reservation["goal_currency"] == "USD"
    assert reservation["reserved_amount"] == "4000.00"
    assert reservation["applied_reserved_amount"] == "1.00"
    assert reservation["is_releasable"] is False
    assert reservation["release_block_reason"] == "currency_mismatch_legacy"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("account_currency", "goal_currency", "active", "releasable", "reason"),
    [
        ("COP", "COP", True, True, None),
        ("USD", "USD", True, True, None),
        ("USD", "USD", False, False, "account_inactive"),
        ("COP", "USD", True, False, "currency_mismatch_legacy"),
        ("cop", "USD", False, False, "currency_mismatch_legacy"),
    ],
)
async def test_goal_detail_release_eligibility_and_precedence(
    service,
    repositories,
    account_currency,
    goal_currency,
    active,
    releasable,
    reason,
):
    goal_repo, _, _ = repositories
    user_id, goal_id = uuid4(), uuid4()
    goal_repo.get_by_id.return_value = _goal(
        goal_id=goal_id,
        user_id=user_id,
        currency=goal_currency,
        current_amount=Decimal("10.00"),
    )
    goal_repo.get_period_contributions.return_value = {}
    goal_repo.get_reservations_by_account.return_value = [
        _reservation(
            account_currency=account_currency,
            goal_currency=goal_currency.lower(),
            source_reserved=Decimal("10.00"),
            applied_reserved=Decimal("10.00"),
            account_is_active=active,
        )
    ]

    result = await service.get_goal(user_id, goal_id)
    reservation = result.reservations_by_account[0]

    assert reservation.account_currency == account_currency.upper()
    assert reservation.goal_currency == goal_currency.upper()
    assert reservation.is_releasable is releasable
    assert reservation.release_block_reason == reason


@pytest.mark.asyncio
async def test_repository_aggregates_source_and_applied_units_without_legacy_double_count():
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    goal_id = UUID("22222222-2222-2222-2222-222222222222")
    account_id = UUID("33333333-3333-3333-3333-333333333333")
    rows = [
        SimpleNamespace(
            account_id=account_id,
            account_name="Test 2",
            account_currency="COP",
            contributed_amount=Decimal("4000.00"),
            released_amount=Decimal("0.00"),
            reserved_amount=Decimal("4000.00"),
            goal_currency="USD",
            applied_contributed_amount=Decimal("1.00"),
            applied_released_amount=Decimal("0.00"),
            applied_reserved_amount=Decimal("1.00"),
            account_is_active=True,
        )
    ]
    session = _RecordingSession(rows=rows)
    repository = GoalRepository(session)  # type: ignore[arg-type]

    result = await repository.get_reservations_by_account(user_id, goal_id)
    sql = _compiled_sql(session.statements[0])

    assert result[0].reserved_amount == Decimal("4000.00")
    assert result[0].applied_reserved_amount == Decimal("1.00")
    assert "sum(goal_transactions.source_amount)" in sql
    assert "sum(goal_transactions.applied_amount)" in sql
    assert "IN ('allocation', 'legacy_import')" in sql
    assert " = 'release'" in sql
    assert "adjustment" not in sql
    assert "goal_contributions" not in sql
    assert "goals.currency AS goal_currency" in sql
    assert "JOIN goals ON goals.id = goal_transactions.goal_id" in sql
    assert f"goal_transactions.user_id = '{user_id}'" in sql
    assert f"accounts.user_id = '{user_id}'" in sql
    assert f"goals.user_id = '{user_id}'" in sql
    assert " LIMIT " not in sql
    assert " OFFSET " not in sql


@pytest.mark.asyncio
async def test_progress_uses_applied_amount_and_includes_legacy_import():
    user_id, goal_id = uuid4(), uuid4()
    session = _RecordingSession(scalar=Decimal("1.00"))
    repository = GoalRepository(session)  # type: ignore[arg-type]

    result = await repository.calculate_progress_by_goal(goal_id, user_id)
    sql = _compiled_sql(session.statements[0])

    assert result == Decimal("1.00")
    assert "sum(goal_transactions.applied_amount)" in sql
    assert "source_amount" not in sql
    assert "IN ('allocation', 'legacy_import')" in sql
    assert " = 'release'" in sql


@pytest.mark.asyncio
async def test_account_currency_total_uses_source_amount_and_includes_legacy_import():
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    session = _RecordingSession(rows=[("COP", Decimal("4000.00"))])
    repository = GoalRepository(session)  # type: ignore[arg-type]

    result = await repository.calculate_reserved_by_user_currency(user_id)
    sql = _compiled_sql(session.statements[0])

    assert result == {"COP": Decimal("4000.00")}
    assert "goal_transactions.source_amount" in sql
    assert "applied_amount" not in sql
    assert "legacy_import" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["contribution", "release"])
async def test_new_cross_currency_writes_remain_rejected(service, repositories, operation):
    goal_repo, account_repo, _ = repositories
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    account_repo.get_by_id_for_update.return_value = Account(
        id=account_id,
        user_id=user_id,
        name="Cuenta COP",
        type="bank",
        balance=Decimal("10000.00"),
        currency="COP",
        is_active=True,
    )
    goal_repo.get_by_id_for_update.return_value = _goal(
        goal_id=goal_id,
        user_id=user_id,
        currency="USD",
        current_amount=Decimal("1.00"),
    )

    with pytest.raises(ValidationError):
        if operation == "contribution":
            await service.create_contribution(
                user_id,
                goal_id,
                GoalContributionCreate(account_id=account_id, amount=Decimal("1.00")),
                None,
            )
        else:
            await service.create_release(
                user_id,
                goal_id,
                GoalReleaseCreate(account_id=account_id, amount=Decimal("1.00")),
                None,
            )
