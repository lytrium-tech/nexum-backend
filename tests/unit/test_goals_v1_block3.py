from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError as PydanticValidationError

from app.accounts.models import Account
from app.accounts.router import get_account_service
from app.accounts.schemas import AccountAvailabilityRead
from app.accounts.service import AccountService
from app.core.database import get_db_session
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.goals.enums import GoalTransactionType
from app.goals.exceptions import GoalAmountExceededError, GoalForbiddenError
from app.goals.models import Goal, GoalTransaction
from app.goals.repository import GoalRepository
from app.goals.router import get_goal_service
from app.goals.schemas import (
    GoalContributionResult,
    GoalTransactionRead,
    GoalTransactionsResponse,
)
from app.goals.service import GoalService
from app.intelligence.repository import IntelligenceRepository
from app.intelligence.service import IntelligenceService
from app.main import app
from app.users.dependencies import get_current_user_profile_dep
from app.users.schemas import UserRead


def make_user(user_id: UUID) -> UserRead:
    now = datetime.now(UTC)
    return UserRead(
        id=user_id,
        email="goals-v1@example.com",
        name="Goals V1",
        timezone="America/Bogota",
        currency="COP",
        status="active",
        created_at=now,
        updated_at=now,
    )


def make_account(
    *,
    user_id: UUID | None,
    balance: Decimal | None = Decimal("2000000.00"),
    active: bool = True,
    currency: str = "COP",
) -> Account:
    return Account(
        id=uuid4(),
        user_id=user_id,
        name="Cuenta",
        type="bank",
        balance=balance,
        currency=currency,
        is_active=active,
    )


def make_goal(
    *,
    user_id: UUID,
    target: Decimal = Decimal("1000000.00"),
    current: Decimal = Decimal("300000.00"),
    status: str = "active",
    currency: str = "COP",
    target_date: date | None = date(2026, 1, 1),
) -> Goal:
    now = datetime.now(UTC)
    return Goal(
        id=uuid4(),
        user_id=user_id,
        name="Meta",
        target_amount=target,
        current_amount=current,
        target_date=target_date,
        currency=currency,
        status=status,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def make_transaction(
    *,
    user_id: UUID,
    goal_id: UUID,
    legacy: bool = False,
    created_at: datetime | None = None,
) -> GoalTransaction:
    return GoalTransaction(
        id=uuid4(),
        user_id=user_id,
        goal_id=goal_id,
        account_id=uuid4(),
        event_id=uuid4(),
        transaction_type=GoalTransactionType.allocation,
        source_amount=Decimal("100.00"),
        source_currency="cop",
        applied_amount=Decimal("100.00"),
        goal_currency="cop",
        command_id=uuid4(),
        command_fingerprint="fingerprint",
        legacy_contribution_id=uuid4() if legacy else None,
        metadata_json={"private": "must-not-leak"},
        created_at=created_at or datetime.now(UTC),
    )


def make_contribution_result(*, idempotent: bool = False) -> GoalContributionResult:
    return GoalContributionResult(
        transaction_id=uuid4(),
        event_id=uuid4(),
        goal_id=uuid4(),
        account_id=uuid4(),
        source_amount=Decimal("100.00"),
        source_currency="COP",
        applied_amount=Decimal("100.00"),
        goal_currency="COP",
        goal_current_amount=Decimal("200.00"),
        goal_remaining_amount=Decimal("800.00"),
        goal_status="active",
        account_balance=Decimal("1000.00"),
        goal_reserved_amount=Decimal("200.00"),
        available_balance=Decimal("800.00"),
        idempotent=idempotent,
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_account_availability_preserves_gross_balance_and_uses_injected_repository():
    user_id = uuid4()
    account = make_account(user_id=user_id)
    account_repository = AsyncMock()
    account_repository.get_by_id.return_value = account
    goal_repository = AsyncMock()
    goal_repository.calculate_reserved_by_account.return_value = Decimal("450000.00")
    service = AccountService(account_repository, goal_repository=goal_repository)

    result = await service.get_account_availability(user_id, account.id)

    assert result.balance == Decimal("2000000.00")
    assert result.goal_reserved_amount == Decimal("450000.00")
    assert result.available_balance == Decimal("1550000.00")
    assert account.balance == Decimal("2000000.00")
    goal_repository.calculate_reserved_by_account.assert_awaited_once_with(account.id, user_id)


@pytest.mark.parametrize(
    ("account", "error_type"),
    [
        (None, NotFoundError),
        (make_account(user_id=None), NotFoundError),
        (make_account(user_id=uuid4()), NotFoundError),
        (make_account(user_id=uuid4(), active=False), ValidationError),
        (make_account(user_id=uuid4(), balance=None), ValidationError),
    ],
)
@pytest.mark.asyncio
async def test_account_availability_rejects_invalid_accounts(account, error_type):
    user_id = uuid4()
    if account is not None and account.user_id is not None and error_type is not NotFoundError:
        account.user_id = user_id
    account_repository = AsyncMock()
    account_repository.get_by_id.return_value = account
    service = AccountService(
        account_repository,
        goal_repository=AsyncMock(),
    )

    with pytest.raises(error_type):
        await service.get_account_availability(
            user_id,
            account.id if account is not None else uuid4(),
        )


@pytest.mark.parametrize("reserved", [Decimal("-1.00"), Decimal("2000000.01")])
@pytest.mark.asyncio
async def test_account_availability_exposes_reservation_integrity_errors(reserved):
    user_id = uuid4()
    account = make_account(user_id=user_id)
    account_repository = AsyncMock()
    account_repository.get_by_id.return_value = account
    goal_repository = AsyncMock()
    goal_repository.calculate_reserved_by_account.return_value = reserved
    service = AccountService(account_repository, goal_repository=goal_repository)

    with pytest.raises(ConflictError):
        await service.get_account_availability(user_id, account.id)


@pytest.mark.asyncio
async def test_reserved_query_supports_allocations_legacy_native_and_releases_only():
    result = MagicMock()
    result.scalar_one_or_none.return_value = Decimal("250.00")
    session = AsyncMock()
    session.execute.return_value = result
    repository = GoalRepository(session)

    reserved = await repository.calculate_reserved_by_account(uuid4(), uuid4())
    statement = session.execute.await_args.args[0]
    parameters = set(statement.compile().params.values())

    assert reserved == Decimal("250.00")
    assert GoalTransactionType.allocation in parameters
    assert GoalTransactionType.release in parameters
    assert GoalTransactionType.adjustment not in parameters
    assert GoalTransactionType.legacy_import not in parameters


@pytest.fixture
async def block3_http_client():
    user_id = uuid4()
    user = make_user(user_id)
    account_service = AsyncMock(spec=AccountService)
    goal_service = AsyncMock(spec=GoalService)
    session = AsyncMock()
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user_profile_dep] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: session
    app.dependency_overrides[get_account_service] = lambda: account_service
    app.dependency_overrides[get_goal_service] = lambda: goal_service
    with patch("app.goals.router.get_goal_service", return_value=goal_service):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client, user, account_service, goal_service, session
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


@pytest.mark.asyncio
async def test_availability_http_happy_path_and_authenticated_owner(block3_http_client):
    client, user, account_service, _, _ = block3_http_client
    account_id = uuid4()
    account_service.get_account_availability.return_value = AccountAvailabilityRead(
        account_id=account_id,
        currency="cop",
        balance=Decimal("1000.00"),
        goal_reserved_amount=Decimal("250.00"),
        available_balance=Decimal("750.00"),
    )

    response = await client.get(f"/api/v1/accounts/{account_id}/availability")

    assert response.status_code == 200
    assert response.json()["currency"] == "COP"
    account_service.get_account_availability.assert_awaited_once_with(user.id, account_id)


@pytest.mark.parametrize(
    ("exception", "status_code"),
    [
        (NotFoundError(message="Cuenta no encontrada."), 404),
        (ValidationError(message="Cuenta inactiva."), 422),
        (ConflictError(message="Reserva inconsistente."), 409),
    ],
)
@pytest.mark.asyncio
async def test_availability_http_maps_domain_errors(
    block3_http_client,
    exception,
    status_code,
):
    client, _, account_service, _, _ = block3_http_client
    account_service.get_account_availability.side_effect = exception

    response = await client.get(f"/api/v1/accounts/{uuid4()}/availability")

    assert response.status_code == status_code


@pytest.mark.asyncio
async def test_goal_history_sanitizes_description_and_hides_private_metadata():
    user_id = uuid4()
    goal = make_goal(user_id=user_id)
    native = make_transaction(user_id=user_id, goal_id=goal.id)
    legacy = make_transaction(user_id=user_id, goal_id=goal.id, legacy=True)
    repository = AsyncMock()
    repository.get_by_id.return_value = goal
    repository.list_transactions_by_goal.return_value = (
        [
            (native, "  Aporte\n  de   emergencia  "),
            (legacy, "Legacy Goal Conciliation"),
        ],
        2,
    )
    service = GoalService(repository, AsyncMock(), AsyncMock())

    result = await service.list_goal_transactions(user_id, goal.id, 25, 0)

    assert result.items[0].description == "Aporte de emergencia"
    assert result.items[0].source_currency == "COP"
    assert result.items[1].description is None
    assert result.items[1].origin == "legacy"
    assert "command_id" not in result.items[0].model_dump()
    assert "command_fingerprint" not in result.items[0].model_dump()
    assert "metadata_json" not in result.items[0].model_dump()


@pytest.mark.asyncio
async def test_goal_history_enforces_goal_ownership():
    user_id = uuid4()
    repository = AsyncMock()
    repository.get_by_id.return_value = make_goal(user_id=uuid4())
    service = GoalService(repository, AsyncMock(), AsyncMock())

    with pytest.raises(GoalForbiddenError):
        await service.list_goal_transactions(user_id, uuid4())

    repository.list_transactions_by_goal.assert_not_awaited()


@pytest.mark.asyncio
async def test_goal_history_http_validates_pagination(block3_http_client):
    client, user, _, goal_service, _ = block3_http_client
    goal_id = uuid4()
    goal_service.list_goal_transactions.return_value = GoalTransactionsResponse(
        items=[],
        total=0,
        limit=25,
        offset=5,
    )

    response = await client.get(f"/api/v1/goals/{goal_id}/transactions?limit=25&offset=5")
    assert response.status_code == 200
    goal_service.list_goal_transactions.assert_awaited_once_with(
        user.id,
        goal_id,
        25,
        5,
    )

    for query in ("limit=0", "limit=101", "offset=-1"):
        invalid = await client.get(f"/api/v1/goals/{goal_id}/transactions?{query}")
        assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_contribution_http_passes_idempotency_header_and_commits(block3_http_client):
    client, user, _, goal_service, session = block3_http_client
    result = make_contribution_result()
    goal_service.create_contribution.return_value = result
    command_id = uuid4()
    payload = {
        "account_id": str(result.account_id),
        "amount": "100.00",
        "command_id": str(command_id),
    }

    response = await client.post(
        f"/api/v1/goals/{result.goal_id}/contributions",
        json=payload,
        headers={"Idempotency-Key": str(command_id)},
    )

    assert response.status_code == 200
    command = goal_service.create_contribution.await_args.args[2]
    goal_service.create_contribution.assert_awaited_once_with(
        user.id,
        result.goal_id,
        command,
        str(command_id),
    )
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.parametrize(
    ("exception", "status_code"),
    [
        (ConflictError(message="Idempotency-Key incompatible."), 409),
        (GoalAmountExceededError(), 409),
        (ValidationError(message="Moneda incompatible."), 422),
    ],
)
@pytest.mark.asyncio
async def test_contribution_http_maps_domain_errors_and_rolls_back(
    block3_http_client,
    exception,
    status_code,
):
    client, _, _, goal_service, session = block3_http_client
    goal_service.create_contribution.side_effect = exception

    response = await client.post(
        f"/api/v1/goals/{uuid4()}/contributions",
        json={
            "account_id": str(uuid4()),
            "amount": "100.00",
            "command_id": str(uuid4()),
        },
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == status_code
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_contribution_http_returns_historical_retry_after_completion(
    block3_http_client,
):
    client, _, _, goal_service, _ = block3_http_client
    result = make_contribution_result(idempotent=True)
    result.goal_status = "completed"
    result.goal_current_amount = Decimal("1000.00")
    result.goal_remaining_amount = Decimal("0.00")
    goal_service.create_contribution.return_value = result

    response = await client.post(
        f"/api/v1/goals/{result.goal_id}/contributions",
        json={
            "account_id": str(result.account_id),
            "amount": "100.00",
            "command_id": str(uuid4()),
        },
    )

    assert response.status_code == 200
    assert response.json()["idempotent"] is True
    assert response.json()["goal_status"] == "completed"


def make_query_result(rows):
    result = MagicMock()
    result.all.return_value = rows
    return result


@pytest.mark.asyncio
async def test_period_contributions_use_aware_inclusive_exclusive_boundaries_without_double_count():
    user_id = uuid4()
    goal_id = uuid4()
    session = AsyncMock()
    session.execute.side_effect = [
        make_query_result([(goal_id, Decimal("150.00"))]),
        make_query_result([(goal_id, Decimal("50.00"))]),
    ]
    repository = GoalRepository(session)
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 8, 1, tzinfo=UTC)

    totals = await repository.get_period_contributions(user_id, start, end)
    transaction_query = str(session.execute.await_args_list[0].args[0]).lower()
    legacy_query = str(session.execute.await_args_list[1].args[0]).lower()

    assert totals == {goal_id: Decimal("200.00")}
    assert "created_at >=" in transaction_query
    assert "created_at <" in transaction_query
    assert "not in" in legacy_query
    assert "legacy_contribution_id is not null" in legacy_query


@pytest.mark.asyncio
async def test_period_contributions_reject_naive_or_invalid_boundaries():
    repository = GoalRepository(AsyncMock())

    with pytest.raises(ValueError):
        await repository.get_period_contributions(
            uuid4(),
            datetime(2026, 7, 1),
            datetime(2026, 8, 1),
        )
    with pytest.raises(ValueError):
        await repository.get_period_contributions(
            uuid4(),
            datetime(2026, 8, 1, tzinfo=UTC),
            datetime(2026, 7, 1, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_cashflow_queries_keep_legacy_outflow_and_exclude_neutral_and_adjustments():
    mapping = MagicMock()
    mapping.first.return_value = {}
    result = MagicMock()
    result.mappings.return_value = mapping
    session = AsyncMock()
    session.execute.return_value = result
    repository = IntelligenceRepository(session)

    await repository.get_cashflow_metrics(
        uuid4(),
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 8, 1, tzinfo=UTC),
    )
    metrics_sql = str(session.execute.await_args.args[0]).lower()
    assert "event_type = 'goal_contribution' and direction = 'outflow'" in metrics_sql
    assert "event_type = 'income'" in metrics_sql
    assert "balance_adjustment" not in metrics_sql

    await repository.get_cashflow(uuid4())
    endpoint_sql = str(session.execute.await_args.args[0]).lower()
    assert "event_type = 'goal_contribution'" in endpoint_sql
    assert "direction = 'outflow'" in endpoint_sql
    assert "balance_adjustment" not in endpoint_sql
    assert "v_cashflow_current_month" not in endpoint_sql


async def build_snapshot(
    *,
    balance_by_currency: dict[str, Decimal],
    reserved_by_currency: dict[str, Decimal],
    goals: list[Goal],
    contributions: dict[UUID, Decimal],
):
    service = IntelligenceService(session=AsyncMock())
    service.repo = AsyncMock()
    total_balance = sum(balance_by_currency.values(), Decimal("0.00"))
    service.repo.get_cash_metrics.return_value = {
        "total_balance": total_balance,
        "active_accounts_count": len(balance_by_currency),
        "currency_count": len(balance_by_currency),
    }
    service.repo.get_cash_metrics_by_currency.return_value = [
        {"currency": currency, "total_balance": amount}
        for currency, amount in balance_by_currency.items()
    ]
    service.repo.get_cashflow_metrics.return_value = {"currency_count": len(balance_by_currency)}
    service.repo.get_cashflow_metrics_by_currency.return_value = []
    service.repo.get_historical_cashflow_metrics.return_value = {}
    service.repo.get_goals_metrics.return_value = {
        "active_goals_count": len(goals),
        "total_target": sum((goal.target_amount for goal in goals), Decimal("0.00")),
        "total_saved": sum((goal.current_amount for goal in goals), Decimal("0.00")),
    }
    service.repo.get_obligations_metrics.return_value = {}
    service.repo.get_transfers_metrics.return_value = {}
    service.repo.get_recent_activity.return_value = []

    with (
        patch("app.intelligence.service.CreditCardService") as credit_service,
        patch("app.obligations.repository.ObligationRepository") as obligation_repository,
        patch("app.goals.repository.GoalRepository") as goal_repository,
    ):
        credit_service.return_value.get_credit_summary = AsyncMock(
            return_value=SimpleNamespace(cards=[])
        )
        obligation_repository.return_value.get_pending_period_amounts_for_snapshot = AsyncMock(
            return_value={}
        )
        goal_repository.return_value.list_active = AsyncMock(return_value=goals)
        goal_repository.return_value.get_period_contributions = AsyncMock(
            return_value=contributions
        )
        goal_repository.return_value.calculate_reserved_by_user_currency = AsyncMock(
            return_value=reserved_by_currency
        )
        return await service.get_snapshot(uuid4())


@pytest.mark.asyncio
async def test_free_money_case_a_discount_reserved_and_future_need_once():
    user_id = uuid4()
    goal = make_goal(user_id=user_id)
    snapshot = await build_snapshot(
        balance_by_currency={"COP": Decimal("2000000.00")},
        reserved_by_currency={"COP": Decimal("300000.00")},
        goals=[goal],
        contributions={goal.id: Decimal("300000.00")},
    )

    assert snapshot.truth.goals_required_this_period == Decimal("700000.00")
    assert snapshot.truth.committed_outflows == Decimal("1000000.00")
    assert snapshot.truth.free_money == Decimal("1000000.00")


@pytest.mark.asyncio
async def test_free_money_case_b_completed_goal_only_keeps_existing_reservation():
    user_id = uuid4()
    goal = make_goal(
        user_id=user_id,
        current=Decimal("1000000.00"),
        status="completed",
    )
    snapshot = await build_snapshot(
        balance_by_currency={"COP": Decimal("2000000.00")},
        reserved_by_currency={"COP": Decimal("1000000.00")},
        goals=[goal],
        contributions={goal.id: Decimal("1000000.00")},
    )

    assert snapshot.truth.goals_required_this_period == Decimal("0.00")
    assert snapshot.truth.free_money == Decimal("1000000.00")


@pytest.mark.asyncio
async def test_free_money_case_c_two_flexible_goals_sum_reservations_once():
    user_id = uuid4()
    goals = [
        make_goal(
            user_id=user_id,
            target=Decimal("1000000.00"),
            current=Decimal("300000.00"),
            target_date=None,
        ),
        make_goal(
            user_id=user_id,
            target=Decimal("500000.00"),
            current=Decimal("200000.00"),
            target_date=None,
        ),
    ]
    snapshot = await build_snapshot(
        balance_by_currency={"COP": Decimal("2000000.00")},
        reserved_by_currency={"COP": Decimal("500000.00")},
        goals=goals,
        contributions={goal.id: goal.current_amount for goal in goals},
    )

    assert snapshot.truth.goals_required_this_period == Decimal("0.00")
    assert snapshot.truth.free_money == Decimal("1500000.00")


@pytest.mark.asyncio
async def test_multicurrency_snapshot_never_sums_cop_and_usd_without_fx():
    snapshot = await build_snapshot(
        balance_by_currency={
            "COP": Decimal("2000000.00"),
            "USD": Decimal("10.00"),
        },
        reserved_by_currency={
            "COP": Decimal("300000.00"),
            "USD": Decimal("2.00"),
        },
        goals=[],
        contributions={},
    )

    assert snapshot.truth.available_real == Decimal("0.00")
    assert snapshot.truth.free_money == Decimal("0.00")
    assert "cross_currency_global_totals_disabled" in snapshot.truth.calculation_warnings
    assert snapshot.totals_by_currency["COP"].free_money == Decimal("1700000.00")
    assert snapshot.totals_by_currency["USD"].free_money == Decimal("8.00")
    assert snapshot.estimated_totals is None


def test_block3_schemas_enforce_currency_and_aware_timestamps():
    transaction = GoalTransactionRead(
        id=uuid4(),
        transaction_type=GoalTransactionType.allocation,
        account_id=uuid4(),
        source_amount=Decimal("1.00"),
        source_currency="cop",
        applied_amount=Decimal("1.00"),
        goal_currency="cop",
        event_id=uuid4(),
        description=None,
        created_at=datetime.now(UTC),
        origin="native",
    )
    assert transaction.source_currency == "COP"

    with pytest.raises(PydanticValidationError):
        GoalTransactionRead(
            **{
                **transaction.model_dump(),
                "created_at": datetime.now(),
            }
        )


def test_block3_runtime_openapi_contract():
    schema = app.openapi()
    availability = schema["paths"]["/api/v1/accounts/{account_id}/availability"]["get"]
    history = schema["paths"]["/api/v1/goals/{goal_id}/transactions"]["get"]
    contribution = schema["paths"]["/api/v1/goals/{goal_id}/contributions"]["post"]

    assert availability["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AccountAvailabilityRead"
    }
    assert set(availability["responses"]) == {"200", "401", "404", "409", "422"}
    assert set(history["responses"]) == {"200", "401", "403", "404", "422"}
    assert set(contribution["responses"]) == {
        "200",
        "401",
        "403",
        "404",
        "409",
        "422",
    }
    parameters = {parameter["name"]: parameter for parameter in history["parameters"]}
    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["limit"]["schema"]["maximum"] == 100
    assert parameters["offset"]["schema"]["minimum"] == 0
    assert any(
        parameter["name"] == "Idempotency-Key" and parameter["in"] == "header"
        for parameter in contribution["parameters"]
    )
    transaction_schema = schema["components"]["schemas"]["GoalTransactionRead"]
    assert "command_id" not in transaction_schema["properties"]
    assert "command_fingerprint" not in transaction_schema["properties"]
    assert "metadata_json" not in transaction_schema["properties"]
