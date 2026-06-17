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
    repo = AsyncMock()
    repo.get_card_status_data.return_value = {
        "purchases_count": 0,
        "payments_count": 0,
        "billed_purchases": Decimal("0.00"),
        "unbilled_purchases": Decimal("0.00"),
        "total_payments": Decimal("0.00"),
    }
    repo.get_next_payment_estimate.return_value = Decimal("0.00")
    repo.list_pending_installments_for_update.return_value = []
    return repo


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
    mock_credit_repo.add_installments.assert_called_once()
    installments = mock_credit_repo.add_installments.call_args.args[0]
    assert len(installments) == 1
    assert sum((i.principal_amount for i in installments), Decimal("0.00")) == Decimal("1000.00")


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
    mock_credit_repo.list_pending_installments_for_update.assert_called_once_with(card_id, user_id)


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


@pytest.mark.asyncio
async def test_card_status_ignores_persisted_current_debt(credit_service, mock_credit_repo):
    """current_debt must be computed from transactions, not credit_cards.current_debt."""
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    mock_card = CreditCard(
        id=card_id,
        user_id=user_id,
        name="Status Card",
        credit_limit=Decimal("5000"),
        current_debt=Decimal("9999"),
        cutoff_day=15,
        due_day=30,
        is_active=True,
        currency="COP",
    )
    mock_credit_repo.get_by_id.return_value = mock_card
    mock_credit_repo.get_card_status_data.return_value = {
        "purchases_count": 2,
        "payments_count": 1,
        "billed_purchases": Decimal("400"),
        "unbilled_purchases": Decimal("300"),
        "total_payments": Decimal("100"),
    }
    mock_credit_repo.get_next_payment_estimate.return_value = Decimal("250")

    status = await credit_service.get_card_status(user_id, card_id)

    assert status.current_debt == Decimal("600")
    assert status.total_debt == Decimal("600")
    assert status.available_credit == Decimal("4400")
    assert status.payment_required == Decimal("300")
    assert status.next_payment_estimate == Decimal("250")
    assert status.statement_balance is None
    assert status.data_quality["statement_balance"] == "not_available"


@pytest.mark.asyncio
async def test_installment_schedule_splits_principal_exactly(credit_service):
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    transaction_id = uuid.uuid4()

    installments = await credit_service._build_installments(
        user_id=user_id,
        card_id=card_id,
        transaction_id=transaction_id,
        amount=Decimal("100.00"),
        installments_total=3,
    )

    assert [i.installment_number for i in installments] == [1, 2, 3]
    assert [i.principal_amount for i in installments] == [
        Decimal("33.33"),
        Decimal("33.33"),
        Decimal("33.34"),
    ]
    assert sum((i.principal_amount for i in installments), Decimal("0.00")) == Decimal("100.00")


@pytest.mark.asyncio
async def test_metadata_fields_do_not_generate_charges(credit_service, mock_credit_repo):
    user_id = uuid.uuid4()
    payload = CreditCardCreate(
        name="Metadata Card",
        bank="Bank",
        credit_limit=Decimal("5000"),
        cutoff_day=15,
        due_day=30,
        management_fee=Decimal("31000"),
        monthly_interest_rate=Decimal("2.0"),
        annual_interest_rate=Decimal("18.0"),
        network="visa",
        franchise="gold",
    )

    async def mock_add(card):
        card.id = uuid.uuid4()
        card.is_active = True

    mock_credit_repo.add.side_effect = mock_add

    result = await credit_service.create_card(user_id, payload)

    assert result.management_fee == Decimal("31000")
    assert result.monthly_interest_rate == Decimal("2.0")
    assert result.annual_interest_rate == Decimal("18.0")
    assert result.network == "visa"
    assert result.franchise == "gold"
    assert result.current_debt == Decimal("0.00")
    mock_credit_repo.add_transaction.assert_not_called()
