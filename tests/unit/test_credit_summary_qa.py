import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.credit.models import CreditCard
from app.credit.service import CreditCardService


@pytest.fixture
def credit_service():
    repo = AsyncMock()
    service = CreditCardService(AsyncMock())
    service.repo = repo
    return service


@pytest.mark.asyncio
async def test_calculate_card_status_no_debt(credit_service):
    card = CreditCard(
        id=uuid.uuid4(), user_id=uuid.uuid4(), name="Test Card", bank="Bank", credit_limit=Decimal("5000"), currency="COP", cutoff_day=15, due_day=30
    )
    credit_service.repo.get_card_status_data.return_value = {
        "purchases_count": 0,
        "payments_count": 0,
        "billed_purchases": Decimal("0.00"),
        "unbilled_purchases": Decimal("0.00"),
        "total_payments": Decimal("0.00"),
    }
    credit_service.repo.get_next_payment_estimate.return_value = Decimal("0.00")

    status = await credit_service._calculate_card_status(card)
    assert status.current_debt == Decimal("0.00")
    assert status.billed_debt == Decimal("0.00")
    assert status.available_credit == Decimal("5000.00")


@pytest.mark.asyncio
async def test_calculate_card_status_pay_early(credit_service):
    card = CreditCard(
        id=uuid.uuid4(), user_id=uuid.uuid4(), name="Test Card", bank="Bank", credit_limit=Decimal("5000"), currency="COP", cutoff_day=15, due_day=30
    )
    credit_service.repo.get_card_status_data.return_value = {
        "purchases_count": 1,
        "payments_count": 0,
        "billed_purchases": Decimal("600.00"),
        "unbilled_purchases": Decimal("0.00"),
        "total_payments": Decimal("0.00"),
    }
    credit_service.repo.get_next_payment_estimate.return_value = Decimal("600.00")

    status = await credit_service._calculate_card_status(card)
    assert status.current_debt == Decimal("600.00")
    assert status.billed_debt == Decimal("600.00")
    assert status.available_credit == Decimal("4400.00")
