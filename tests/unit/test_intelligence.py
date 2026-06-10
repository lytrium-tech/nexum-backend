import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.intelligence.schemas import (
    IntelligenceBalanceRead,
    IntelligenceSnapshotRead,
)
from app.intelligence.service import IntelligenceService


@pytest.fixture
def mock_intelligence_repo():
    with patch("app.intelligence.service.IntelligenceRepository", autospec=True) as mock:
        yield mock


@pytest.fixture
def intelligence_service(mock_intelligence_repo):
    service = IntelligenceService(session=AsyncMock())
    service.repo = mock_intelligence_repo.return_value
    return service


@pytest.mark.asyncio
async def test_get_snapshot_success(intelligence_service, mock_intelligence_repo):
    user_id = uuid.uuid4()
    calc_at = datetime.now(UTC)
    mock_intelligence_repo.return_value.get_financial_snapshot.return_value = {
        "available_real": Decimal("1000.00"),
        "safe_money": Decimal("900.00"),
        "free_money": Decimal("800.00"),
        "pending_obligations_total": Decimal("100.00"),
        "monthly_goals_required_remaining": Decimal("100.00"),
        "monthly_cc_payment": Decimal("0.00"),
        "credit_card_debt": Decimal("0.00"),
        "calculated_at": calc_at,
    }
    mock_intelligence_repo.return_value.get_consumption_summary.return_value = {
        "cash_consumption_outflow": Decimal("200.00"),
        "credit_card_consumption_committed": Decimal("50.00"),
        "total_consumption_committed": Decimal("250.00"),
    }
    mock_intelligence_repo.return_value.get_cashflow.return_value = {
        "monthly_income": Decimal("1500.00"),
        "committed_outflow": Decimal("100.00"),
        "wealth_allocation": Decimal("50.00"),
    }

    result = await intelligence_service.get_snapshot(user_id)

    assert isinstance(result, IntelligenceSnapshotRead)
    assert result.available_real == Decimal("1000.00")
    assert result.safe_money == Decimal("900.00")
    assert result.free_money == Decimal("800.00")
    assert result.total_consumption_committed == Decimal("250.00")


@pytest.mark.asyncio
async def test_get_snapshot_empty_user(intelligence_service, mock_intelligence_repo):
    user_id = uuid.uuid4()
    mock_intelligence_repo.return_value.get_financial_snapshot.return_value = None
    mock_intelligence_repo.return_value.get_consumption_summary.return_value = None
    mock_intelligence_repo.return_value.get_cashflow.return_value = None

    result = await intelligence_service.get_snapshot(user_id)

    assert isinstance(result, IntelligenceSnapshotRead)
    assert result.available_real == Decimal("0.00")
    assert result.safe_money == Decimal("0.00")
    assert result.free_money == Decimal("0.00")
    assert result.total_consumption_committed == Decimal("0.00")


@pytest.mark.asyncio
async def test_get_balance_success(intelligence_service, mock_intelligence_repo):
    user_id = uuid.uuid4()
    mock_intelligence_repo.return_value.get_account_balances.return_value = [
        {
            "account_id": uuid.uuid4(),
            "account_name": "Checking",
            "account_type": "bank",
            "currency": "COP",
            "balance": Decimal("500.00"),
        },
        {
            "account_id": uuid.uuid4(),
            "account_name": "Savings",
            "account_type": "bank",
            "currency": "COP",
            "balance": Decimal("1000.00"),
        },
    ]

    result = await intelligence_service.get_balance(user_id)

    assert isinstance(result, IntelligenceBalanceRead)
    assert result.total_available_real == Decimal("1500.00")
    assert result.currency == "COP"
    assert len(result.accounts) == 2


@pytest.mark.asyncio
async def test_get_debt_success(intelligence_service, mock_intelligence_repo):
    user_id = uuid.uuid4()
    mock_intelligence_repo.return_value.get_credit_card_debt.return_value = [
        {
            "credit_card_id": uuid.uuid4(),
            "credit_card_name": "Visa",
            "credit_limit": Decimal("5000.00"),
            "credit_card_debt": Decimal("1500.00"),
            "monthly_cc_payment": Decimal("200.00"),
            "cutoff_day": 15,
            "due_day": 5,
        }
    ]
    mock_intelligence_repo.return_value.get_pending_obligations.return_value = [
        {
            "obligation_id": uuid.uuid4(),
            "name": "Rent",
            "amount": Decimal("1000.00"),
            "due_day": 1,
            "is_pending": True,
        },
        {
            "obligation_id": uuid.uuid4(),
            "name": "Internet",
            "amount": Decimal("100.00"),
            "due_day": 10,
            "is_pending": False,  # Should be ignored in pending_commitments
        },
    ]

    result = await intelligence_service.get_debt(user_id)

    assert result.total_estimated_credit_card_debt == Decimal("1500.00")
    assert result.total_monthly_cc_payment == Decimal("200.00")
    assert len(result.credit_cards) == 1
    assert result.credit_cards[0].estimated_available_credit == Decimal("3500.00")
    assert len(result.pending_commitments) == 1
    assert result.pending_commitments[0]["name"] == "Rent"
