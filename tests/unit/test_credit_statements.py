import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.credit.models import CreditCard
from app.credit.schemas import CreditCardPurchaseCreate
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
async def test_lazy_statement_generation(credit_service, mock_credit_repo, mock_ledger_service):
    """
    Test lazy statement generation after cutoff and proper installment schedule.
    This ensures logic matches Sprint 4 requirements for installments and dates.
    """
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    payload = CreditCardPurchaseCreate(amount=Decimal("1200"), installments_total=3)

    mock_card = CreditCard(
        id=card_id, user_id=user_id, credit_limit=Decimal("5000"), is_active=True, currency="COP",
        cutoff_day=15, due_day=30
    )
    mock_credit_repo.get_by_id_for_update.return_value = mock_card
    mock_credit_repo.get_card_debt.return_value = (0.0, 0.0)

    mock_event = AsyncMock()
    mock_event.id = uuid.uuid4()
    mock_event.amount = Decimal("1200")
    mock_result = AsyncMock()
    mock_result.event = mock_event
    mock_result.idempotent = False
    mock_ledger_service.record_event.return_value = mock_result

    result = await credit_service.create_purchase(user_id, card_id, payload, uuid.uuid4())

    assert result.status == "success"
    assert result.amount == Decimal("1200")
    
    mock_credit_repo.add_installments.assert_called_once()
    installments = mock_credit_repo.add_installments.call_args.args[0]
    
    assert len(installments) == 3
    # Check installment properties matching Sprint 4: interest_amount, total_amount, scheduled_due_date
    assert installments[0].principal_amount == Decimal("400.00")
    assert installments[0].interest_amount == Decimal("0.00")
    assert installments[0].total_amount == Decimal("400.00")
    
    assert installments[1].principal_amount == Decimal("400.00")
    assert installments[1].interest_amount == Decimal("0.00")
    assert installments[1].total_amount == Decimal("400.00")

    assert installments[2].principal_amount == Decimal("400.00")
    assert installments[2].interest_amount == Decimal("0.00")
    assert installments[2].total_amount == Decimal("400.00")
