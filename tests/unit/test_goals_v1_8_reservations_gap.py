import json
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.dialects import postgresql

from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.core.errors import ConflictError, ForbiddenError
from app.goals.exceptions import GoalForbiddenError
from app.goals.models import Goal
from app.goals.repository import (
    GoalAccountReservation,
    GoalRepository,
)
from app.goals.router import get_goal_service
from app.goals.schemas import (
    GoalAccountReservationRead,
    GoalDetailRead,
    GoalRead,
    GoalReleaseCreate,
)
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


class _RecordingSession:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _RowsResult(self.rows)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ScalarRecordingSession:
    def __init__(self, value):
        self.value = value
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _ScalarResult(self.value)


def _compiled_sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _goal(*, goal_id, user_id, current_amount=Decimal("0.00"), status="active"):
    return Goal(
        id=goal_id,
        user_id=user_id,
        name="Meta",
        target_amount=Decimal("1000.00"),
        current_amount=current_amount,
        currency="COP",
        is_active=True,
        status=status,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _reservation(
    *,
    account_id=None,
    name="Cuenta",
    contributed=Decimal("100.00"),
    released=Decimal("0.00"),
    reserved=Decimal("100.00"),
    is_active=True,
):
    return GoalAccountReservation(
        account_id=account_id or uuid4(),
        account_name=name,
        account_currency="COP",
        contributed_amount=contributed,
        released_amount=released,
        reserved_amount=reserved,
        account_is_active=is_active,
    )


@pytest.fixture
def repositories():
    return (
        AsyncMock(spec=GoalRepository),
        AsyncMock(spec=AccountRepository),
        AsyncMock(spec=LedgerRepository),
    )


@pytest.fixture
def goal_service(repositories):
    return GoalService(*repositories)


def test_reservation_schema_is_typed_exact_and_rejects_negative_amounts():
    account_id = uuid4()
    schema = GoalAccountReservationRead.model_validate(
        {
            "account_id": account_id,
            "account_name": "Cuenta inactiva",
            "account_currency": "cop",
            "contributed_amount": Decimal("200.10"),
            "released_amount": Decimal("0.10"),
            "reserved_amount": Decimal("200.00"),
            "account_is_active": False,
        }
    )

    assert schema.account_id == account_id
    assert schema.account_currency == "COP"
    assert schema.contributed_amount == Decimal("200.10")
    assert schema.released_amount == Decimal("0.10")
    assert schema.reserved_amount == Decimal("200.00")
    assert schema.account_is_active is False
    assert set(schema.model_dump()) == set(GoalAccountReservationRead.model_fields)

    with pytest.raises(PydanticValidationError):
        GoalAccountReservationRead.model_validate(
            {
                **schema.model_dump(),
                "reserved_amount": Decimal("-0.01"),
            }
        )


@pytest.mark.asyncio
async def test_repository_uses_one_authoritative_aggregate_query_for_all_accounts():
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    goal_id = UUID("22222222-2222-2222-2222-222222222222")
    active_id = UUID("33333333-3333-3333-3333-333333333333")
    inactive_id = UUID("44444444-4444-4444-4444-444444444444")
    rows = [
        SimpleNamespace(
            account_id=active_id,
            account_name="Cuenta A",
            account_currency="COP",
            contributed_amount=Decimal("600.00"),
            released_amount=Decimal("100.00"),
            reserved_amount=Decimal("500.00"),
            account_is_active=True,
        ),
        SimpleNamespace(
            account_id=inactive_id,
            account_name="Cuenta B",
            account_currency="COP",
            contributed_amount=Decimal("200.00"),
            released_amount=Decimal("50.00"),
            reserved_amount=Decimal("150.00"),
            account_is_active=False,
        ),
    ]
    session = _RecordingSession(rows)
    repository = GoalRepository(session)  # type: ignore[arg-type]

    result = await repository.get_reservations_by_account(user_id, goal_id)
    sql = _compiled_sql(session.statements[0])

    assert len(session.statements) == 1
    assert result == [
        _reservation(
            account_id=active_id,
            name="Cuenta A",
            contributed=Decimal("600.00"),
            released=Decimal("100.00"),
            reserved=Decimal("500.00"),
        ),
        _reservation(
            account_id=inactive_id,
            name="Cuenta B",
            contributed=Decimal("200.00"),
            released=Decimal("50.00"),
            reserved=Decimal("150.00"),
            is_active=False,
        ),
    ]
    assert "sum(goal_transactions.source_amount)" in sql
    assert "applied_amount" not in sql
    assert "legacy_contribution_id" not in sql
    assert "command_id" not in sql
    assert "IN ('allocation', 'legacy_import')" in sql
    assert " = 'release'" in sql
    assert "adjustment" not in sql
    assert f"goal_transactions.user_id = '{user_id}'" in sql
    assert f"goal_transactions.goal_id = '{goal_id}'" in sql
    assert f"accounts.user_id = '{user_id}'" in sql
    main_where = sql.split("WHERE goal_transactions.goal_id", maxsplit=1)[1]
    assert "accounts.is_active" not in main_where.split("GROUP BY", maxsplit=1)[0]
    assert "HAVING" in sql
    assert " != 0" in sql
    assert " LIMIT " not in sql
    assert " OFFSET " not in sql


@pytest.mark.asyncio
async def test_total_and_account_breakdown_share_source_amount_semantics_and_ownership():
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    goal_id = UUID("22222222-2222-2222-2222-222222222222")
    session = _ScalarRecordingSession(Decimal("650.00"))
    repository = GoalRepository(session)  # type: ignore[arg-type]

    result = await repository.calculate_total_reserved_by_goal(user_id, goal_id)
    sql = _compiled_sql(session.statements[0])

    assert result == Decimal("650.00")
    assert "sum(goal_transactions.source_amount)" in sql
    assert "applied_amount" not in sql
    assert "IN ('allocation', 'legacy_import')" in sql
    assert " = 'release'" in sql
    assert f"goal_transactions.user_id = '{user_id}'" in sql
    assert f"goal_transactions.goal_id = '{goal_id}'" in sql
    assert f"accounts.user_id = '{user_id}'" in sql
    assert "JOIN accounts ON accounts.id = goal_transactions.account_id" in sql


@pytest.mark.asyncio
async def test_goal_detail_returns_two_accounts_and_preserves_inactive_reservation(
    goal_service,
    repositories,
):
    goal_repo, _, _ = repositories
    user_id, goal_id = uuid4(), uuid4()
    active_id, inactive_id = uuid4(), uuid4()
    goal_repo.get_by_id.return_value = _goal(
        goal_id=goal_id,
        user_id=user_id,
        current_amount=Decimal("550.00"),
    )
    goal_repo.get_period_contributions.return_value = {}
    goal_repo.get_reservations_by_account.return_value = [
        _reservation(
            account_id=active_id,
            name="Cuenta A",
            contributed=Decimal("400.00"),
            released=Decimal("50.00"),
            reserved=Decimal("350.00"),
        ),
        _reservation(
            account_id=inactive_id,
            name="Cuenta B",
            contributed=Decimal("200.00"),
            reserved=Decimal("200.00"),
            is_active=False,
        ),
    ]

    result = await goal_service.get_goal(user_id, goal_id)

    assert isinstance(result, GoalDetailRead)
    assert [item.account_id for item in result.reservations_by_account] == [
        active_id,
        inactive_id,
    ]
    assert (
        sum(
            (item.reserved_amount for item in result.reservations_by_account),
            Decimal("0.00"),
        )
        == result.current_amount
    )
    assert result.reservations_by_account[0].released_amount == Decimal("50.00")
    assert result.reservations_by_account[1].reserved_amount == Decimal("200.00")
    assert result.reservations_by_account[1].account_is_active is False
    goal_repo.list_transactions_by_goal.assert_not_awaited()


@pytest.mark.asyncio
async def test_goal_detail_empty_collection_is_valid_only_for_zero_progress(
    goal_service,
    repositories,
):
    goal_repo, _, _ = repositories
    user_id, goal_id = uuid4(), uuid4()
    goal_repo.get_by_id.return_value = _goal(goal_id=goal_id, user_id=user_id)
    goal_repo.get_period_contributions.return_value = {}
    goal_repo.get_reservations_by_account.return_value = []

    result = await goal_service.get_goal(user_id, goal_id)

    assert result.reservations_by_account == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reservations,current_amount",
    [
        (
            [
                _reservation(
                    contributed=Decimal("100.00"),
                    released=Decimal("150.00"),
                    reserved=Decimal("-50.00"),
                )
            ],
            Decimal("-50.00"),
        ),
        ([_reservation(reserved=Decimal("99.99"))], Decimal("100.00")),
    ],
)
async def test_goal_detail_rejects_negative_or_divergent_reservation_truth(
    goal_service,
    repositories,
    reservations,
    current_amount,
):
    goal_repo, _, _ = repositories
    user_id, goal_id = uuid4(), uuid4()
    goal_repo.get_by_id.return_value = _goal(
        goal_id=goal_id,
        user_id=user_id,
        current_amount=current_amount,
    )
    goal_repo.get_period_contributions.return_value = {}
    goal_repo.get_reservations_by_account.return_value = reservations

    with pytest.raises(ConflictError):
        await goal_service.get_goal(user_id, goal_id)


@pytest.mark.asyncio
async def test_foreign_goal_is_rejected_before_reservation_query(goal_service, repositories):
    goal_repo, _, _ = repositories
    user_id, goal_id = uuid4(), uuid4()
    goal_repo.get_by_id.return_value = _goal(goal_id=goal_id, user_id=uuid4())

    with pytest.raises(GoalForbiddenError):
        await goal_service.get_goal(user_id, goal_id)

    goal_repo.get_reservations_by_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_goal_list_does_not_query_or_serialize_account_breakdown(
    goal_service,
    repositories,
):
    goal_repo, _, _ = repositories
    user_id, goal_id = uuid4(), uuid4()
    goal_repo.list_active.return_value = [
        _goal(goal_id=goal_id, user_id=user_id, current_amount=Decimal("10.00"))
    ]
    goal_repo.get_period_contributions.return_value = {}

    result = await goal_service.list_goals(user_id)

    assert len(result) == 1
    assert isinstance(result[0], GoalRead)
    assert not isinstance(result[0], GoalDetailRead)
    assert "reservations_by_account" not in result[0].model_dump()
    goal_repo.get_reservations_by_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_inactive_account_remains_forbidden_for_release(goal_service, repositories):
    goal_repo, account_repo, _ = repositories
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    account_repo.get_by_id_for_update.return_value = Account(
        id=account_id,
        user_id=user_id,
        name="Cuenta inactiva",
        type="bank",
        balance=Decimal("100.00"),
        currency="COP",
        is_active=False,
    )
    goal_repo.get_by_id_for_update.return_value = _goal(
        goal_id=goal_id,
        user_id=user_id,
        current_amount=Decimal("100.00"),
    )

    with pytest.raises(ForbiddenError):
        await goal_service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(account_id=account_id, amount=Decimal("10.00")),
            None,
        )


def test_goal_detail_http_serializes_breakdown_without_changing_goal_list():
    user = UserRead(
        id=uuid4(),
        email="test@example.com",
        name="Test",
        timezone="America/Bogota",
        currency="COP",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    goal_id, account_id = uuid4(), uuid4()
    service = AsyncMock(spec=GoalService)
    service.get_goal.return_value = GoalDetailRead(
        id=goal_id,
        name="Meta",
        target_amount=Decimal("1000.00"),
        current_amount=Decimal("100.00"),
        target_date=None,
        currency="COP",
        status="active",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        reservations_by_account=[
            GoalAccountReservationRead(
                account_id=account_id,
                account_name="Cuenta",
                account_currency="COP",
                contributed_amount=Decimal("150.00"),
                released_amount=Decimal("50.00"),
                reserved_amount=Decimal("100.00"),
                account_is_active=True,
            )
        ],
    )
    app.dependency_overrides[get_goal_service] = lambda: service
    app.dependency_overrides[get_current_user_profile_dep] = lambda: user
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/v1/goals/{goal_id}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["reservations_by_account"] == [
        {
            "account_id": str(account_id),
            "account_name": "Cuenta",
            "account_currency": "COP",
            "contributed_amount": "150.00",
            "released_amount": "50.00",
            "reserved_amount": "100.00",
            "account_is_active": True,
        }
    ]
    service.get_goal.assert_awaited_once_with(user.id, goal_id)


def test_openapi_exposes_breakdown_only_on_goal_detail_contract():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; "
                "from app.main import app; "
                "print(json.dumps(app.openapi(), sort_keys=True))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    runtime = json.loads(result.stdout)
    persisted = json.loads(Path("openapi.json").read_text(encoding="utf-8"))

    assert persisted == runtime
    schemas = runtime["components"]["schemas"]
    assert "GoalAccountReservationRead" in schemas
    assert "reservations_by_account" in schemas["GoalDetailRead"]["properties"]
    assert "reservations_by_account" not in schemas["GoalRead"]["properties"]
    assert runtime["paths"]["/api/v1/goals/{goal_id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/GoalDetailRead"}
    assert runtime["paths"]["/api/v1/goals"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["items"] == {"$ref": "#/components/schemas/GoalRead"}
