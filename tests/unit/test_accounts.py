import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.accounts.enums import AccountType
from app.accounts.exceptions import AccountDuplicateError, AccountForbiddenError
from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.accounts.schemas import AccountCreate, AccountUpdate
from app.accounts.service import AccountService
from app.users.service import UserService


@pytest.fixture
def mock_repo():
    repo = AsyncMock(spec=AccountRepository)
    repo.session = AsyncMock()
    return repo


@pytest.fixture
def mock_user_service():
    return AsyncMock(spec=UserService)


@pytest.fixture
def service(mock_repo, mock_user_service):
    return AccountService(mock_repo, mock_user_service)


@pytest.mark.asyncio
async def test_create_account_success(service, mock_repo):
    user_id = uuid.uuid4()
    payload = AccountCreate(name="  Mi Cuenta  ", type=AccountType.BANK)
    mock_repo.check_name_exists.return_value = False

    async def fake_create(acc):
        acc.id = uuid.uuid4()
        return acc

    mock_repo.create.side_effect = fake_create

    result = await service.create_account(user_id, payload)

    assert result.name == "Mi Cuenta"
    assert result.balance == Decimal("0")
    assert result.user_id == user_id
    assert result.is_active is True
    mock_repo.check_name_exists.assert_called_once_with(user_id, "mi cuenta")


@pytest.mark.asyncio
async def test_create_account_duplicate(service, mock_repo):
    user_id = uuid.uuid4()
    payload = AccountCreate(name="Mi Cuenta", type=AccountType.BANK)
    mock_repo.check_name_exists.return_value = True

    with pytest.raises(AccountDuplicateError):
        await service.create_account(user_id, payload)


@pytest.mark.asyncio
async def test_update_account_forbidden(service, mock_repo):
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    account_id = uuid.uuid4()
    mock_repo.get_by_id.return_value = Account(id=account_id, user_id=other_user_id)

    with pytest.raises(AccountForbiddenError):
        await service.update_account(user_id, account_id, AccountUpdate(name="New"))


@pytest.mark.asyncio
async def test_delete_account_soft_delete(service, mock_repo):
    user_id = uuid.uuid4()
    account_id = uuid.uuid4()
    acc = Account(id=account_id, user_id=user_id, is_active=True)
    mock_repo.get_by_id.return_value = acc

    await service.delete_account(user_id, account_id)
    assert acc.is_active is False
    mock_repo.session.flush.assert_called_once()
