import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cash.exceptions import InsufficientFundsError
from app.cash.schemas import CashExpenseCreate, CashIncomeCreate, EventSource
from app.cash.service import CashService
from app.core.errors import ForbiddenError, NotFoundError
from app.core.security import AuthenticatedUser


@pytest.fixture
def mock_uow():
    class MockUoW:
        def __init__(self):
            self.session = AsyncMock()

        def transaction(self):
            class TxContext:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, exc_type, exc_val, exc_tb):
                    pass

            return TxContext()

    return MockUoW()


@pytest.fixture
def mock_ledger_repo():
    return AsyncMock()


@pytest.fixture
def mock_account_repo():
    return AsyncMock()


@pytest.fixture
def mock_category_repo():
    return AsyncMock()


@pytest.fixture
def cash_service(mock_uow, mock_ledger_repo, mock_account_repo, mock_category_repo):
    return CashService(mock_uow, mock_ledger_repo, mock_account_repo, mock_category_repo)


@pytest.mark.asyncio
async def test_create_income_success(cash_service, mock_account_repo, mock_ledger_repo):
    user_id = uuid.uuid4()
    auth_user = AuthenticatedUser(user_id=str(user_id), is_dev=True)
    account_id = uuid.uuid4()
    command_id = uuid.uuid4()

    mock_account = MagicMock()
    mock_account.user_id = user_id
    mock_account.balance = Decimal("100")
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_ledger_result = MagicMock()
    mock_ledger_result.idempotent = False
    mock_ledger_result.event.id = uuid.uuid4()
    mock_ledger_result.event.occurred_at = "2026-06-09T00:00:00Z"
    mock_ledger_repo.insert_event.return_value = mock_ledger_result

    payload = CashIncomeCreate(
        account_id=account_id, amount=Decimal("50.5"), source=EventSource.API
    )

    result = await cash_service.create_income(auth_user, payload, command_id)

    assert result.status == "created"
    mock_account_repo.update_balance.assert_awaited_once_with(mock_account, Decimal("50.5"))


@pytest.mark.asyncio
async def test_create_expense_success(cash_service, mock_account_repo, mock_ledger_repo):
    user_id = uuid.uuid4()
    auth_user = AuthenticatedUser(user_id=str(user_id), is_dev=True)
    account_id = uuid.uuid4()
    command_id = uuid.uuid4()

    mock_account = MagicMock()
    mock_account.user_id = user_id
    mock_account.balance = Decimal("100")
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_ledger_result = MagicMock()
    mock_ledger_result.idempotent = False
    mock_ledger_result.event.id = uuid.uuid4()
    mock_ledger_result.event.occurred_at = "2026-06-09T00:00:00Z"
    mock_ledger_repo.insert_event.return_value = mock_ledger_result

    payload = CashExpenseCreate(account_id=account_id, amount=Decimal("40"), source=EventSource.API)

    result = await cash_service.create_expense(auth_user, payload, command_id)

    assert result.status == "created"
    mock_account_repo.update_balance.assert_awaited_once_with(mock_account, Decimal("-40"))


@pytest.mark.asyncio
async def test_create_expense_insufficient_funds(cash_service, mock_account_repo, mock_ledger_repo):
    user_id = uuid.uuid4()
    auth_user = AuthenticatedUser(user_id=str(user_id), is_dev=True)
    account_id = uuid.uuid4()
    command_id = uuid.uuid4()

    mock_account = MagicMock()
    mock_account.user_id = user_id
    mock_account.balance = Decimal("10")
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_ledger_result = MagicMock()
    mock_ledger_result.idempotent = False
    mock_ledger_repo.insert_event.return_value = mock_ledger_result

    payload = CashExpenseCreate(
        account_id=account_id,
        amount=Decimal("20"),
    )

    with pytest.raises(InsufficientFundsError):
        await cash_service.create_expense(auth_user, payload, command_id)

    mock_account_repo.update_balance.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_income_idempotent(cash_service, mock_account_repo, mock_ledger_repo):
    user_id = uuid.uuid4()
    auth_user = AuthenticatedUser(user_id=str(user_id), is_dev=True)
    account_id = uuid.uuid4()
    command_id = uuid.uuid4()

    mock_account = MagicMock()
    mock_account.user_id = user_id
    mock_account.balance = Decimal("100")
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_ledger_result = MagicMock()
    mock_ledger_result.idempotent = True
    mock_ledger_result.event.id = uuid.uuid4()
    mock_ledger_result.event.occurred_at = "2026-06-09T00:00:00Z"
    mock_ledger_repo.insert_event.return_value = mock_ledger_result

    payload = CashIncomeCreate(
        account_id=account_id,
        amount=Decimal("50"),
    )

    result = await cash_service.create_income(auth_user, payload, command_id)

    assert result.status == "idempotent_retry"
    # Balance should not be updated again
    mock_account_repo.update_balance.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_expense_account_forbidden(cash_service, mock_account_repo):
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    auth_user = AuthenticatedUser(user_id=str(user_id), is_dev=True)
    account_id = uuid.uuid4()
    command_id = uuid.uuid4()

    mock_account = MagicMock()
    mock_account.user_id = other_user_id
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    payload = CashExpenseCreate(
        account_id=account_id,
        amount=Decimal("20"),
    )

    with pytest.raises(ForbiddenError):
        await cash_service.create_expense(auth_user, payload, command_id)


@pytest.mark.asyncio
async def test_create_income_account_not_found(cash_service, mock_account_repo):
    auth_user = AuthenticatedUser(user_id=str(uuid.uuid4()), is_dev=True)
    mock_account_repo.get_by_id_for_update.return_value = None

    payload = CashIncomeCreate(
        account_id=uuid.uuid4(),
        amount=Decimal("20"),
    )

    with pytest.raises(NotFoundError):
        await cash_service.create_income(auth_user, payload, uuid.uuid4())


def test_cash_schemas_reject_negative_amount():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CashExpenseCreate(account_id=uuid.uuid4(), amount=Decimal("-10"))


def test_cash_schemas_reject_zero_amount():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CashIncomeCreate(account_id=uuid.uuid4(), amount=Decimal("0"))


@pytest.mark.asyncio
async def test_validate_category_global_accepted(
    cash_service, mock_account_repo, mock_ledger_repo, mock_category_repo
):
    user_id = uuid.uuid4()
    auth_user = AuthenticatedUser(user_id=str(user_id), is_dev=True)
    category_id = uuid.uuid4()

    mock_account = MagicMock()
    mock_account.user_id = user_id
    mock_account.balance = Decimal("100")
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_category = MagicMock()
    mock_category.user_id = None
    mock_category_repo.get_by_id.return_value = mock_category

    mock_ledger_result = MagicMock()
    mock_ledger_result.idempotent = False
    mock_ledger_result.event.id = uuid.uuid4()
    mock_ledger_result.event.occurred_at = "2026-06-09T00:00:00Z"
    mock_ledger_repo.insert_event.return_value = mock_ledger_result

    payload = CashIncomeCreate(
        account_id=uuid.uuid4(), amount=Decimal("10"), category_id=category_id
    )
    await cash_service.create_income(auth_user, payload, uuid.uuid4())
    mock_category_repo.get_by_id.assert_awaited_once_with(category_id)


@pytest.mark.asyncio
async def test_validate_category_private_own_accepted(
    cash_service, mock_account_repo, mock_ledger_repo, mock_category_repo
):
    user_id = uuid.uuid4()
    auth_user = AuthenticatedUser(user_id=str(user_id), is_dev=True)
    category_id = uuid.uuid4()

    mock_account = MagicMock()
    mock_account.user_id = user_id
    mock_account.balance = Decimal("100")
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_category = MagicMock()
    mock_category.user_id = user_id
    mock_category_repo.get_by_id.return_value = mock_category

    mock_ledger_result = MagicMock()
    mock_ledger_result.idempotent = False
    mock_ledger_result.event.id = uuid.uuid4()
    mock_ledger_result.event.occurred_at = "2026-06-09T00:00:00Z"
    mock_ledger_repo.insert_event.return_value = mock_ledger_result

    payload = CashIncomeCreate(
        account_id=uuid.uuid4(), amount=Decimal("10"), category_id=category_id
    )
    await cash_service.create_income(auth_user, payload, uuid.uuid4())


@pytest.mark.asyncio
async def test_validate_category_private_other_rejected(
    cash_service, mock_account_repo, mock_category_repo
):
    user_id = uuid.uuid4()
    auth_user = AuthenticatedUser(user_id=str(user_id), is_dev=True)
    category_id = uuid.uuid4()

    mock_account = MagicMock()
    mock_account.user_id = user_id
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_category = MagicMock()
    mock_category.user_id = uuid.uuid4()
    mock_category_repo.get_by_id.return_value = mock_category

    payload = CashIncomeCreate(
        account_id=uuid.uuid4(), amount=Decimal("10"), category_id=category_id
    )
    with pytest.raises(ForbiddenError) as exc:
        await cash_service.create_income(auth_user, payload, uuid.uuid4())
    assert "No tienes permisos sobre esta categoría privada" in exc.value.message


@pytest.mark.asyncio
async def test_validate_category_not_found_rejected(
    cash_service, mock_account_repo, mock_category_repo
):
    user_id = uuid.uuid4()
    auth_user = AuthenticatedUser(user_id=str(user_id), is_dev=True)
    category_id = uuid.uuid4()

    mock_account = MagicMock()
    mock_account.user_id = user_id
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_category_repo.get_by_id.return_value = None

    payload = CashIncomeCreate(
        account_id=uuid.uuid4(), amount=Decimal("10"), category_id=category_id
    )
    with pytest.raises(NotFoundError) as exc:
        await cash_service.create_income(auth_user, payload, uuid.uuid4())
    assert "Categoría no encontrada" in exc.value.message


@pytest.mark.asyncio
async def test_validate_category_none_accepted(
    cash_service, mock_account_repo, mock_ledger_repo, mock_category_repo
):
    user_id = uuid.uuid4()
    auth_user = AuthenticatedUser(user_id=str(user_id), is_dev=True)

    mock_account = MagicMock()
    mock_account.user_id = user_id
    mock_account.balance = Decimal("100")
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_ledger_result = MagicMock()
    mock_ledger_result.idempotent = False
    mock_ledger_result.event.id = uuid.uuid4()
    mock_ledger_result.event.occurred_at = "2026-06-09T00:00:00Z"
    mock_ledger_repo.insert_event.return_value = mock_ledger_result

    payload = CashIncomeCreate(account_id=uuid.uuid4(), amount=Decimal("10"), category_id=None)
    await cash_service.create_income(auth_user, payload, uuid.uuid4())
    mock_category_repo.get_by_id.assert_not_awaited()
