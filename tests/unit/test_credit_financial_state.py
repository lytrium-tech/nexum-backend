import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.credit.models import CreditCard
from app.credit.repository import CreditCardRepository
from app.credit.service import CreditCardService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("debt", "available"),
    [("700000", "300000"), ("1200000", "-200000")],
)
async def test_financial_state_uses_partitioned_debt_without_clamping(debt, available):
    card = CreditCard(id=uuid.uuid4(), credit_limit=Decimal("1000000"))
    repository = CreditCardRepository(AsyncMock())
    partition = {
        "canonical_debt": Decimal(debt),
        "legacy_debt": Decimal("0"),
        "total_debt": Decimal(debt),
        "monthly_cc_payment": Decimal("0"),
    }
    repository.get_card_debt_partitioned = AsyncMock(return_value=partition)

    state = await repository.get_card_financial_state(card)

    repository.get_card_debt_partitioned.assert_awaited_once_with(card.id)
    assert state == {
        **partition,
        "modeled_debt": Decimal(debt),
        "available_credit": Decimal(available),
    }


@pytest.mark.asyncio
async def test_card_reads_and_summary_use_canonical_state_preserving_metadata():
    card = CreditCard(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Card",
        bank="Bank",
        credit_limit=Decimal("1000"),
        cutoff_day=15,
        due_day=5,
        currency="COP",
        is_active=True,
        canonical_mode=True,
    )
    service = CreditCardService(AsyncMock())
    service.repo = AsyncMock()
    service.repo.get_by_id.return_value = card
    service.repo.get_all_for_user.return_value = [card]
    service.repo.get_card_status_data.return_value = {
        "purchases_count": 2,
        "payments_count": 1,
        "billed_purchases": Decimal("100"),
        "unbilled_purchases": Decimal("200"),
        "total_payments": Decimal("0"),
    }
    service.repo.get_next_payment_estimate.return_value = Decimal("0")
    service.repo.get_card_financial_state.return_value = {
        "modeled_debt": Decimal("1200"),
        "available_credit": Decimal("-200"),
    }

    status = await service.get_card_status(card.user_id, card.id)
    read = await service.get_card(card.user_id, card.id)
    listed = await service.list_cards(card.user_id)
    summary = await service.get_credit_summary(card.user_id)

    assert all(
        x.total_debt == Decimal("1200") and x.available_credit == Decimal("-200")
        for x in (status, read, listed[0], summary.cards[0])
    )
    assert summary.total_debt == Decimal("1200")
    assert summary.total_available_credit == Decimal("-200")
    assert (status.purchases_count, status.payments_count) == (2, 1)
    assert (status.billed_debt, status.unbilled_debt) == (Decimal("100"), Decimal("200"))
    assert status.total_debt != status.billed_debt + status.unbilled_debt
