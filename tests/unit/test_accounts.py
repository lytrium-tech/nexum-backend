import uuid
from decimal import Decimal
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.accounts.enums import AccountType
from app.accounts.exceptions import AccountDuplicateError, AccountForbiddenError
from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.accounts.schemas import AccountCreate, AccountUpdate, BalanceAdjustmentCreate
from app.accounts.service import AccountService
from app.users.service import UserService
from app.ledger.repository import LedgerRepository
from app.ledger.enums import Direction, EventType


@pytest.fixture
def mock_repo():
    repo = AsyncMock(spec=AccountRepository)
    repo.session = AsyncMock()
    return repo


@pytest.fixture
def mock_ledger_repo():
    return AsyncMock(spec=LedgerRepository)


@pytest.fixture
def service(mock_repo, mock_ledger_repo):
    return AccountService(mock_repo, mock_ledger_repo)


@pytest.mark.asyncio
async def test_create_account_success(service, mock_repo, mock_ledger_repo):
    user_id = uuid.uuid4()
    payload = AccountCreate(currency="COP", name="  Mi Cuenta  ", type=AccountType.BANK)
    mock_repo.check_name_exists.return_value = False

    async def fake_create(acc):
        acc.id = uuid.uuid4()
        acc.created_at = datetime.now()
        acc.updated_at = datetime.now()
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
    payload = AccountCreate(currency="COP", name="Mi Cuenta", type=AccountType.BANK)
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
async def test_update_account_success(service, mock_repo):
    user_id = uuid.uuid4()
    account_id = uuid.uuid4()
    acc = Account(
        id=account_id, 
        user_id=user_id, 
        name="Old Name", 
        type="bank", 
        currency="COP", 
        balance=Decimal("0"), 
        is_active=True,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    mock_repo.get_by_id.return_value = acc
    mock_repo.check_name_exists.return_value = False

    res = await service.update_account(user_id, account_id, AccountUpdate(name="New Name", type=AccountType.WALLET))
    
    assert res.name == "New Name"
    assert res.type == AccountType.WALLET
    mock_repo.session.flush.assert_called_once()

@pytest.mark.asyncio
async def test_update_account_forbidden_fields():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AccountUpdate(name="New Name", is_active=False)
    with pytest.raises(ValidationError):
        AccountUpdate(name="New Name", balance=Decimal("100"))
    with pytest.raises(ValidationError):
        AccountUpdate(name="New Name", currency="USD")


@pytest.mark.asyncio
async def test_archive_account(service, mock_repo):
    user_id = uuid.uuid4()
    account_id = uuid.uuid4()
    acc = Account(
        id=account_id, 
        user_id=user_id, 
        name="Name", 
        type="bank", 
        currency="COP", 
        balance=Decimal("0"), 
        is_active=True,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    mock_repo.get_by_id.return_value = acc

    res = await service.archive_account(user_id, account_id)
    assert res.is_active is False
    mock_repo.session.flush.assert_called_once()


@pytest.mark.asyncio
async def test_restore_account(service, mock_repo):
    user_id = uuid.uuid4()
    account_id = uuid.uuid4()
    acc = Account(
        id=account_id, 
        user_id=user_id, 
        name="Name", 
        type="bank", 
        currency="COP", 
        balance=Decimal("0"), 
        is_active=False,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    mock_repo.get_by_id.return_value = acc

    res = await service.restore_account(user_id, account_id)
    assert res.is_active is True
    mock_repo.session.flush.assert_called_once()


@pytest.mark.asyncio
async def test_delete_account_not_supported(service):
    user_id = uuid.uuid4()
    account_id = uuid.uuid4()
    with pytest.raises(ValueError, match="La eliminación de cuentas no está soportada"):
        await service.delete_account(user_id, account_id)


@pytest.mark.asyncio
async def test_balance_adjustment(service, mock_repo, mock_ledger_repo):
    user_id = uuid.uuid4()
    account_id = uuid.uuid4()
    acc = Account(
        id=account_id, 
        user_id=user_id, 
        name="Name", 
        type="bank", 
        currency="COP", 
        balance=Decimal("100"), 
        is_active=True,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    mock_repo.get_by_id_for_update.return_value = acc

    payload = BalanceAdjustmentCreate(target_balance=Decimal("150"), reason="Test reason", idempotency_key=uuid.uuid4())
    res = await service.create_balance_adjustment(user_id, account_id, payload)
    
    assert res.balance == Decimal("150")
    assert acc.balance == Decimal("150")
    mock_ledger_repo.insert_event.assert_called_once()
    mock_repo.session.flush.assert_called_once()

    args, _ = mock_ledger_repo.insert_event.call_args
    event_create = args[0]
    assert event_create.amount == Decimal("50")
    assert event_create.direction == Direction.INFLOW
