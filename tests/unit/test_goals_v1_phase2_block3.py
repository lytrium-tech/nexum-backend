import json
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from inspect import getsource, signature
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.goals.enums import GoalTransactionType
from app.goals.exceptions import GoalNotActiveError
from app.goals.router import create_release, get_goal_service
from app.goals.schemas import (
    GoalRead,
    GoalReleaseCreate,
    GoalReleaseResult,
    GoalTransactionRead,
    GoalTransactionsResponse,
)
from app.intelligence.repository import IntelligenceRepository
from app.main import app


@pytest.fixture
def mock_goal_service():
    service = AsyncMock()
    return service


@pytest.fixture
def auth_user():
    from app.users.schemas import UserRead

    return UserRead(
        id=uuid.uuid4(),
        email="test@example.com",
        name="Test",
        timezone="America/Bogota",
        currency="COP",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def test_client(mock_goal_service, auth_user):
    from app.users.dependencies import get_current_user_profile_dep

    app.dependency_overrides[get_goal_service] = lambda: mock_goal_service
    app.dependency_overrides[get_current_user_profile_dep] = lambda: auth_user

    with patch("app.goals.router.get_goal_service", return_value=mock_goal_service):
        with TestClient(app) as c:
            yield c

    app.dependency_overrides.clear()


def test_create_release_happy_path(test_client, mock_goal_service, auth_user):
    goal_id = uuid.uuid4()
    account_id = uuid.uuid4()

    mock_result = GoalReleaseResult(
        transaction_id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        goal_id=goal_id,
        account_id=account_id,
        released_amount=Decimal("50.00"),
        source_currency="COP",
        applied_amount=Decimal("50.00"),
        goal_currency="COP",
        goal_current_amount=Decimal("950.00"),
        goal_remaining_amount=Decimal("50.00"),
        goal_status="active",
        account_balance=Decimal("2000.00"),
        goal_account_reserved_amount=Decimal("450.00"),
        goal_total_reserved_amount=Decimal("950.00"),
        account_total_reserved_amount=Decimal("450.00"),
        available_balance=Decimal("1550.00"),
        idempotent=False,
        created_at=datetime.now(UTC),
    )
    mock_goal_service.create_release.return_value = mock_result

    payload = {"account_id": str(account_id), "amount": "50.00", "description": "Release parcial"}

    response = test_client.post(f"/api/v1/goals/{goal_id}/releases", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["released_amount"] == "50.00"
    assert data["goal_current_amount"] == "950.00"
    assert data["idempotent"] is False
    assert set(data) == set(GoalReleaseResult.model_fields)
    called_user_id, called_goal_id, called_payload, called_key = (
        mock_goal_service.create_release.await_args.args
    )
    assert called_user_id == auth_user.id
    assert called_goal_id == goal_id
    assert called_payload == GoalReleaseCreate(**payload)
    assert called_key is None


def test_create_release_auth_required(mock_goal_service):
    from app.core.errors import AuthenticationError
    from app.users.dependencies import get_current_user_profile_dep

    def raise_401():
        raise AuthenticationError()

    app.dependency_overrides[get_current_user_profile_dep] = raise_401

    with patch("app.goals.router.get_goal_service", return_value=mock_goal_service):
        with TestClient(app) as c:
            response = c.post(
                f"/api/v1/goals/{uuid.uuid4()}/releases",
                json={"account_id": str(uuid.uuid4()), "amount": "50.00"},
            )
            assert response.status_code == 401

    app.dependency_overrides.clear()


def test_create_release_forbidden(test_client, mock_goal_service):
    mock_goal_service.create_release.side_effect = ForbiddenError()
    response = test_client.post(
        f"/api/v1/goals/{uuid.uuid4()}/releases",
        json={"account_id": str(uuid.uuid4()), "amount": "50.00"},
    )
    assert response.status_code == 403


def test_create_release_invalid_goal_state(test_client, mock_goal_service):
    mock_goal_service.create_release.side_effect = GoalNotActiveError()
    response = test_client.post(
        f"/api/v1/goals/{uuid.uuid4()}/releases",
        json={"account_id": str(uuid.uuid4()), "amount": "50.00"},
    )
    assert response.status_code == 400


def test_create_release_not_found(test_client, mock_goal_service):
    mock_goal_service.create_release.side_effect = NotFoundError()
    response = test_client.post(
        f"/api/v1/goals/{uuid.uuid4()}/releases",
        json={"account_id": str(uuid.uuid4()), "amount": "50.00"},
    )
    assert response.status_code == 404


def test_create_release_conflict(test_client, mock_goal_service):
    mock_goal_service.create_release.side_effect = ConflictError()
    response = test_client.post(
        f"/api/v1/goals/{uuid.uuid4()}/releases",
        json={"account_id": str(uuid.uuid4()), "amount": "50.00"},
    )
    assert response.status_code == 409


def test_create_release_validation_amount(test_client, mock_goal_service):
    response = test_client.post(
        f"/api/v1/goals/{uuid.uuid4()}/releases",
        json={"account_id": str(uuid.uuid4()), "amount": "-10.00"},
    )
    assert response.status_code == 422

    response = test_client.post(
        f"/api/v1/goals/{uuid.uuid4()}/releases",
        json={"account_id": str(uuid.uuid4()), "amount": "0.00"},
    )
    assert response.status_code == 422


def test_create_release_rejects_authoritative_currency(test_client, mock_goal_service):
    response = test_client.post(
        f"/api/v1/goals/{uuid.uuid4()}/releases",
        json={
            "account_id": str(uuid.uuid4()),
            "amount": "50.00",
            "currency": "COP",
        },
    )
    assert response.status_code == 422
    mock_goal_service.create_release.assert_not_awaited()


def test_create_release_idempotent_retry(test_client, mock_goal_service):
    goal_id = uuid.uuid4()
    account_id = uuid.uuid4()
    command_id = str(uuid.uuid4())

    mock_result = GoalReleaseResult(
        transaction_id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        goal_id=goal_id,
        account_id=account_id,
        released_amount=Decimal("50.00"),
        source_currency="COP",
        applied_amount=Decimal("50.00"),
        goal_currency="COP",
        goal_current_amount=Decimal("950.00"),
        goal_remaining_amount=Decimal("50.00"),
        goal_status="active",
        account_balance=Decimal("2000.00"),
        goal_account_reserved_amount=Decimal("450.00"),
        goal_total_reserved_amount=Decimal("950.00"),
        account_total_reserved_amount=Decimal("450.00"),
        available_balance=Decimal("1550.00"),
        idempotent=True,
        created_at=datetime.now(UTC),
    )
    mock_goal_service.create_release.return_value = mock_result

    payload = {"account_id": str(account_id), "amount": "50.00", "command_id": command_id}

    response = test_client.post(
        f"/api/v1/goals/{goal_id}/releases", json=payload, headers={"Idempotency-Key": command_id}
    )
    assert response.status_code == 200
    assert response.json()["idempotent"] is True
    assert mock_goal_service.create_release.await_args.args[3] == command_id


def test_create_release_router_uses_single_session_owned_uow_boundary():
    parameters = signature(create_release).parameters
    source = getsource(create_release)

    assert "session" not in parameters
    assert "UnitOfWork" not in source
    assert "service.create_release" in source


def test_history_includes_releases(test_client, mock_goal_service):
    goal_id = uuid.uuid4()

    items = [
        GoalTransactionRead(
            id=uuid.uuid4(),
            transaction_type=GoalTransactionType.release,
            account_id=uuid.uuid4(),
            source_amount=Decimal("50.00"),
            source_currency="COP",
            applied_amount=Decimal("50.00"),
            goal_currency="COP",
            event_id=uuid.uuid4(),
            description="Release test",
            created_at=datetime.now(UTC),
            origin="native",
        )
    ]

    mock_goal_service.list_goal_transactions.return_value = GoalTransactionsResponse(
        items=items, total=1, limit=50, offset=0
    )

    response = test_client.get(f"/api/v1/goals/{goal_id}/transactions")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["transaction_type"] == "release"
    assert data["items"][0]["source_amount"] == "50.00"
    assert set(data["items"][0]) == set(GoalTransactionRead.model_fields)
    assert "command_fingerprint" not in data["items"][0]
    assert "metadata_json" not in data["items"][0]


class _MappingResult:
    def mappings(self):
        return self

    def first(self):
        return {}

    def all(self):
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        (
            "get_cashflow_metrics",
            (uuid.uuid4(), datetime.now(UTC), datetime.now(UTC) + timedelta(days=1)),
        ),
        (
            "get_cashflow_metrics_by_currency",
            (uuid.uuid4(), datetime.now(UTC), datetime.now(UTC) + timedelta(days=1)),
        ),
        ("get_historical_cashflow_metrics", (uuid.uuid4(), datetime.now(UTC))),
        ("get_cashflow", (uuid.uuid4(),)),
    ],
)
async def test_intelligence_cashflow_queries_exclude_goal_release(method_name, args):
    session = AsyncMock()
    session.execute.return_value = _MappingResult()
    repository = IntelligenceRepository(session)

    await getattr(repository, method_name)(*args)

    query = str(session.execute.await_args.args[0]).lower()
    assert "goal_release" not in query
    assert "event_type = 'income'" in query
    assert "'expense'" in query
    assert "event_type = 'goal_contribution'" in query
    assert "direction = 'outflow'" in query


def test_release_keeps_planned_free_money_stable_for_dated_goal():
    common = {
        "id": uuid.uuid4(),
        "name": "Meta",
        "target_amount": Decimal("1000.00"),
        "target_date": date.today() + timedelta(days=90),
        "currency": "COP",
        "status": "active",
        "is_active": True,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    before = GoalRead(
        **common,
        current_amount=Decimal("400.00"),
        contributed_this_period=Decimal("100.00"),
    )
    after = GoalRead(
        **common,
        current_amount=Decimal("350.00"),
        contributed_this_period=Decimal("50.00"),
    )

    before_commitment = Decimal("400.00") + before.remaining_required_this_period
    after_commitment = Decimal("350.00") + after.remaining_required_this_period

    assert before.required_this_period == after.required_this_period
    assert after.remaining_required_this_period - before.remaining_required_this_period == Decimal(
        "50.00"
    )
    assert after_commitment == before_commitment


def test_persisted_openapi_matches_runtime_release_contract():
    persisted = json.loads(Path("openapi.json").read_text(encoding="utf-8"))
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
    path = "/api/v1/goals/{goal_id}/releases"

    assert persisted == runtime
    assert list(item for item in persisted["paths"] if item == path) == [path]

    release = persisted["paths"][path]["post"]
    assert release["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GoalReleaseCreate"
    }
    assert release["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GoalReleaseResult"
    }
    assert set(release["responses"]) == {"200", "400", "401", "403", "404", "409", "422"}

    header = next(
        parameter for parameter in release["parameters"] if parameter["name"] == "Idempotency-Key"
    )
    assert header["in"] == "header"
    assert header["required"] is False

    request_schema = persisted["components"]["schemas"]["GoalReleaseCreate"]
    assert request_schema["additionalProperties"] is False
    assert set(request_schema["required"]) == {"account_id", "amount"}
    assert "currency" not in request_schema["properties"]
    assert set(persisted["components"]["schemas"]["GoalReleaseResult"]["required"]) == set(
        GoalReleaseResult.model_fields
    )
