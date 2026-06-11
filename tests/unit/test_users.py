import uuid
from unittest.mock import AsyncMock

import pytest

from app.core.errors import NotFoundError
from app.core.security import AuthenticatedIdentity
from app.users.models import User
from app.users.repository import UserRepository
from app.users.service import UserService


@pytest.fixture
def mock_repo():
    return AsyncMock(spec=UserRepository)


@pytest.fixture
def service(mock_repo):
    return UserService(mock_repo)


@pytest.mark.asyncio
async def test_get_current_user_profile_dev_bypass_success(service, mock_repo):
    user_id = uuid.uuid4()
    mock_user = User(
        id=user_id, status="active", name="Dev User", timezone="America/Bogota", currency="COP"
    )
    mock_repo.get_by_id.return_value = mock_user

    auth_user = AuthenticatedIdentity(user_id=str(user_id), is_dev=True)
    result = await service.get_current_user_profile(auth_user)

    assert result.id == user_id
    assert result.name == "Dev User"
    mock_repo.get_by_id.assert_called_once_with(user_id)


@pytest.mark.asyncio
async def test_get_current_user_profile_auth_success(service, mock_repo):
    internal_id = uuid.uuid4()
    auth_id = uuid.uuid4()
    mock_user = User(
        id=internal_id,
        auth_user_id=auth_id,
        status="active",
        name="Auth User",
        timezone="America/Bogota",
        currency="COP",
    )
    mock_repo.get_by_auth_id.return_value = mock_user

    auth_user = AuthenticatedIdentity(user_id=str(auth_id), is_dev=False)
    result = await service.get_current_user_profile(auth_user)

    assert result.id == internal_id
    assert result.name == "Auth User"
    mock_repo.get_by_auth_id.assert_called_once_with(str(auth_id))


@pytest.mark.asyncio
async def test_get_current_user_profile_not_found(service, mock_repo):
    mock_repo.get_by_auth_id.return_value = None
    auth_user = AuthenticatedIdentity(user_id=str(uuid.uuid4()), is_dev=False)

    with pytest.raises(NotFoundError):
        await service.get_current_user_profile(auth_user)
