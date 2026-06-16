import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.security import AuthenticatedIdentity
from app.users.models import User
from app.users.repository import UserRepository
from app.users.schemas import UserOnboardingRequest
from app.users.service import UserService


@pytest.fixture
def mock_repo():
    repo = AsyncMock(spec=UserRepository)
    repo.session = AsyncMock()
    return repo


@pytest.fixture
def service(mock_repo):
    return UserService(mock_repo)


@pytest.mark.asyncio
async def test_onboard_user_creates_new(service, mock_repo):
    auth_user = AuthenticatedIdentity(
        user_id=str(uuid.uuid4()), email="test@test.com", is_dev=False
    )
    payload = UserOnboardingRequest(name="New User")

    mock_repo.get_by_auth_id.return_value = None
    mock_user = User(
        id=uuid.uuid4(),
        auth_user_id=uuid.UUID(auth_user.user_id),
        email="test@test.com",
        name="New User",
        timezone="America/Bogota",
        currency="COP",
        status="active",
    )
    mock_repo.create_user.return_value = mock_user

    res = await service.onboard_user(auth_user, payload)
    assert res.created is True
    assert res.profile.name == "New User"


@pytest.mark.asyncio
async def test_onboard_user_idempotent(service, mock_repo):
    auth_user = AuthenticatedIdentity(
        user_id=str(uuid.uuid4()), email="test@test.com", is_dev=False
    )
    payload = UserOnboardingRequest(name="New User")

    mock_user = User(
        id=uuid.uuid4(),
        auth_user_id=uuid.UUID(auth_user.user_id),
        email="test@test.com",
        name="Existing User",
        timezone="America/Bogota",
        currency="COP",
        status="active",
    )
    mock_repo.get_by_auth_id.return_value = mock_user

    res = await service.onboard_user(auth_user, payload)
    assert res.created is False
    assert res.profile.name == "Existing User"


@pytest.mark.asyncio
async def test_onboard_user_concurrent(service, mock_repo):
    auth_user = AuthenticatedIdentity(
        user_id=str(uuid.uuid4()), email="test@test.com", is_dev=False
    )
    payload = UserOnboardingRequest(name="New User")

    mock_user = User(
        id=uuid.uuid4(),
        auth_user_id=uuid.UUID(auth_user.user_id),
        email="test@test.com",
        name="Concurrent User",
        timezone="America/Bogota",
        currency="COP",
        status="active",
    )

    mock_repo.get_by_auth_id.side_effect = [None, mock_user]
    mock_repo.create_user.side_effect = IntegrityError(None, None, None)

    res = await service.onboard_user(auth_user, payload)
    assert res.created is False
    assert res.profile.name == "Concurrent User"
