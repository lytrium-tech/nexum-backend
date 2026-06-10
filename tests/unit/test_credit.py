import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.accounts.models import Account
from app.credit.exceptions import (
    CreditLimitExceededError,
    InvalidPaymentAmountError,
)
from app.credit.models import CreditCard
from app.credit.schemas import (
    CreditCardCreate,
    CreditCardPaymentCreate,
    CreditCardPurchaseCreate,
)
from app.credit.service import CreditCardService


@pytest.fixture
def mock_credit_repo():
    return AsyncMock()


@pytest.fixture
def mock_ledger_service():
    return AsyncMock()


@pytest.fixture
def mock_account_repo():
    return AsyncMock()


@pytest.fixture
def credit_service(mock_credit_repo, mock_ledger_service, mock_account_repo):
    service = CreditCardService(AsyncMock())
    service.repo = mock_credit_repo
    service.ledger_service = mock_ledger_service
    service.account_repo = mock_account_repo
    return service


@pytest.mark.asyncio
async def test_create_card_success(credit_service, mock_credit_repo):
    user_id = uuid.uuid4()
    payload = CreditCardCreate(
        name="Test Card", bank="Bank", credit_limit=Decimal("5000"), cutoff_day=15, due_day=30
    )

    mock_credit_repo.get_card_debt.return_value = (0.0, 0.0)

    async def mock_add(card):
        card.id = uuid.uuid4()
        card.is_active = True

    mock_credit_repo.add.side_effect = mock_add

    result = await credit_service.create_card(user_id, payload)
    assert result.name == "Test Card"
    assert result.credit_limit == Decimal("5000")
    assert result.estimated_current_debt == Decimal("0.00")
    mock_credit_repo.add.assert_called_once()


@pytest.mark.asyncio
async def test_create_purchase_success(credit_service, mock_credit_repo, mock_ledger_service):
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    payload = CreditCardPurchaseCreate(amount=Decimal("1000"))

    mock_card = CreditCard(
        id=card_id, user_id=user_id, credit_limit=Decimal("5000"), is_active=True, currency="COP"
    )
    mock_credit_repo.get_by_id_for_update.return_value = mock_card
    mock_credit_repo.get_card_debt.return_value = (500.0, 100.0)

    mock_event = AsyncMock()
    mock_event.id = uuid.uuid4()
    mock_event.amount = Decimal("1000")
    mock_result = AsyncMock()
    mock_result.event = mock_event
    mock_result.idempotent = False
    mock_ledger_service.record_event.return_value = mock_result

    result = await credit_service.create_purchase(user_id, card_id, payload, uuid.uuid4())

    assert result.status == "success"
    assert result.amount == Decimal("1000")
    assert result.estimated_current_debt == Decimal("1500")
    assert result.estimated_available_credit == Decimal("3500")
    mock_credit_repo.add_transaction.assert_called_once()


@pytest.mark.asyncio
async def test_create_purchase_limit_exceeded(credit_service, mock_credit_repo):
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    payload = CreditCardPurchaseCreate(amount=Decimal("1000"))

    mock_card = CreditCard(
        id=card_id, user_id=user_id, credit_limit=Decimal("5000"), is_active=True, currency="COP"
    )
    mock_credit_repo.get_by_id_for_update.return_value = mock_card
    # Debt is 4500, limit is 5000, available is 500
    mock_credit_repo.get_card_debt.return_value = (4500.0, 100.0)

    with pytest.raises(CreditLimitExceededError):
        await credit_service.create_purchase(user_id, card_id, payload, uuid.uuid4())


@pytest.mark.asyncio
async def test_create_payment_success(
    credit_service, mock_credit_repo, mock_account_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    account_id = uuid.uuid4()
    payload = CreditCardPaymentCreate(account_id=account_id, amount=Decimal("200"))

    mock_account = Account(id=account_id, user_id=user_id, is_active=True, balance=Decimal("1000"))
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_card = CreditCard(
        id=card_id, user_id=user_id, credit_limit=Decimal("5000"), is_active=True, currency="COP"
    )
    mock_credit_repo.get_by_id_for_update.return_value = mock_card
    # Debt is 500
    mock_credit_repo.get_card_debt.return_value = (500.0, 100.0)

    mock_event = AsyncMock()
    mock_event.id = uuid.uuid4()
    mock_event.amount = Decimal("200")
    mock_result = AsyncMock()
    mock_result.event = mock_event
    mock_result.idempotent = False
    mock_ledger_service.record_event.return_value = mock_result

    result = await credit_service.create_payment(user_id, card_id, payload, uuid.uuid4())

    assert result.status == "success"
    assert result.amount == Decimal("200")
    assert result.estimated_current_debt == Decimal("300")
    assert result.account_balance == Decimal("800")
    assert mock_account.balance == Decimal("800")
    mock_credit_repo.add_transaction.assert_called_once()


@pytest.mark.asyncio
async def test_create_payment_overpayment(credit_service, mock_credit_repo, mock_account_repo):
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    account_id = uuid.uuid4()
    payload = CreditCardPaymentCreate(account_id=account_id, amount=Decimal("600"))

    mock_account = Account(id=account_id, user_id=user_id, is_active=True, balance=Decimal("1000"))
    mock_account_repo.get_by_id_for_update.return_value = mock_account

    mock_card = CreditCard(
        id=card_id, user_id=user_id, credit_limit=Decimal("5000"), is_active=True, currency="COP"
    )
    mock_credit_repo.get_by_id_for_update.return_value = mock_card
    # Debt is 500, trying to pay 600
    mock_credit_repo.get_card_debt.return_value = (500.0, 100.0)

    with pytest.raises(InvalidPaymentAmountError):
        await credit_service.create_payment(user_id, card_id, payload, uuid.uuid4())
