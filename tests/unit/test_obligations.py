import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.accounts.models import Account
from app.obligations.exceptions import ObligationAmountMismatchError
from app.obligations.models import Obligation
from app.obligations.schemas import ObligationCreate, ObligationPaymentCreate
from app.obligations.service import ObligationService


@pytest.fixture
def mock_obligation_repo():
    return AsyncMock()


@pytest.fixture
def mock_account_repo():
    return AsyncMock()


@pytest.fixture
def mock_ledger_repo():
    return AsyncMock()


@pytest.fixture
def obligation_service(mock_obligation_repo, mock_account_repo, mock_ledger_repo):
    return ObligationService(mock_obligation_repo, mock_account_repo, mock_ledger_repo)


@pytest.mark.asyncio
async def test_create_obligation_success(obligation_service, mock_obligation_repo):
    user_id = uuid.uuid4()
    payload = ObligationCreate(
        name="  Préstamo Coche  ", amount=Decimal("1000"), due_day=15, frequency="monthly"
    )

    mock_obligation_repo.check_name_exists.return_value = False

    mock_obligation = Obligation(
        id=uuid.uuid4(),
        user_id=user_id,
        name="Préstamo Coche",
        amount=payload.amount,
        due_day=payload.due_day,
        frequency=payload.frequency,
        is_active=True,
        metadata_={},
        currency="COP",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        category_id=None,
    )
    mock_obligation_repo.create.return_value = mock_obligation

    result = await obligation_service.create_obligation(user_id, payload)

    assert result.name == "Préstamo Coche"
    assert result.amount == Decimal("1000")
    assert result.due_day == 15
    mock_obligation_repo.check_name_exists.assert_called_once_with(user_id, "prestamo coche")


@pytest.mark.asyncio
async def test_create_obligation_duplicate_name(obligation_service, mock_obligation_repo):
    user_id = uuid.uuid4()
    payload = ObligationCreate(name="Coche", amount=Decimal("1000"))

    mock_obligation_repo.check_name_exists.return_value = True

    with pytest.raises(ValueError, match="Ya existe una obligación"):
        await obligation_service.create_obligation(user_id, payload)


@pytest.mark.asyncio
async def test_delete_obligation_success(obligation_service, mock_obligation_repo):
    user_id = uuid.uuid4()
    obligation_id = uuid.uuid4()

    mock_obligation = Obligation(user_id=user_id, is_active=True, name="Test", amount=Decimal("100"))
    mock_obligation_repo.get_by_id.return_value = mock_obligation

    await obligation_service.delete_obligation(user_id, obligation_id)

    assert mock_obligation.is_active is False


@pytest.mark.asyncio
async def test_payment_success(
    obligation_service, mock_obligation_repo, mock_account_repo, mock_ledger_repo
):
    user_id = uuid.uuid4()
    obligation_id = uuid.uuid4()
    account_id = uuid.uuid4()
    payload = ObligationPaymentCreate(account_id=account_id, amount=Decimal("100"))

    mock_obligation = Obligation(
        id=obligation_id,
        user_id=user_id,
        is_active=True,
        amount=Decimal("100"),
        frequency="monthly"
    )
    mock_obligation_repo.get_by_id_for_update.return_value = mock_obligation

    mock_account = Account(id=account_id, user_id=user_id, balance=Decimal("1000"))
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_event = AsyncMock()
    mock_event.id = uuid.uuid4()
    mock_event.period = "2026-06"
    mock_result = AsyncMock()
    mock_result.event = mock_event
    mock_result.idempotent = False
    mock_ledger_repo.insert_event.return_value = mock_result

    result = await obligation_service.create_payment(user_id, obligation_id, payload, "123")

    assert result.status == "success"
    assert result.amount == Decimal("100")
    mock_account_repo.update_balance.assert_called_once_with(mock_account, Decimal("-100"))
    assert mock_obligation.is_active is True  # monthly does not deactivate


@pytest.mark.asyncio
async def test_payment_success_once_deactivates(
    obligation_service, mock_obligation_repo, mock_account_repo, mock_ledger_repo
):
    user_id = uuid.uuid4()
    obligation_id = uuid.uuid4()
    account_id = uuid.uuid4()
    payload = ObligationPaymentCreate(account_id=account_id, amount=Decimal("100"))

    mock_obligation = Obligation(
        id=obligation_id,
        user_id=user_id,
        is_active=True,
        amount=Decimal("100"),
        frequency="once"
    )
    mock_obligation_repo.get_by_id_for_update.return_value = mock_obligation

    mock_account = Account(id=account_id, user_id=user_id, balance=Decimal("1000"))
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_event = AsyncMock()
    mock_event.id = uuid.uuid4()
    mock_event.period = "2026-06"
    mock_result = AsyncMock()
    mock_result.event = mock_event
    mock_result.idempotent = False
    mock_ledger_repo.insert_event.return_value = mock_result

    await obligation_service.create_payment(user_id, obligation_id, payload, "123")

    assert mock_obligation.is_active is False  # once should deactivate


@pytest.mark.asyncio
async def test_payment_amount_mismatch(obligation_service, mock_obligation_repo, mock_account_repo):
    user_id = uuid.uuid4()
    obligation_id = uuid.uuid4()
    account_id = uuid.uuid4()
    payload = ObligationPaymentCreate(account_id=account_id, amount=Decimal("50"))

    mock_obligation = Obligation(
        id=obligation_id,
        user_id=user_id,
        is_active=True,
        amount=Decimal("100"),
    )
    mock_obligation_repo.get_by_id_for_update.return_value = mock_obligation

    with pytest.raises(ObligationAmountMismatchError):
        await obligation_service.create_payment(user_id, obligation_id, payload, None)
