import inspect
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.intelligence.repository import IntelligenceRepository
from app.intelligence.schemas import IntelligenceBalanceRead, IntelligenceSnapshotRead
from app.intelligence.service import IntelligenceService


def build_credit_summary(
    total_debt: Decimal = Decimal("0.00"),
    billed_debt: Decimal = Decimal("0.00"),
    unbilled_debt: Decimal = Decimal("0.00"),
):
    from datetime import date

    from app.credit.schemas import CreditCardStatusRead, CreditSummaryRead

    cards = []
    if total_debt or billed_debt or unbilled_debt:
        cards.append(
            CreditCardStatusRead(
                card_id=uuid.uuid4(),
                name="Visa",
                credit_limit=Decimal("5000.00"),
                total_debt=total_debt,
                billed_debt=billed_debt,
                unbilled_debt=unbilled_debt,
                monthly_cc_payment=billed_debt,
                available_credit=Decimal("5000.00") - total_debt,
                cutoff_day=15,
                payment_due_day=5,
                next_payment_due_date=date.today().isoformat(),
                purchases_count=0,
                payments_count=0,
            )
        )

    return CreditSummaryRead(
        total_credit_limit=Decimal("5000.00") if cards else Decimal("0.00"),
        total_debt=total_debt,
        total_available_credit=Decimal("5000.00") - total_debt if cards else Decimal("0.00"),
        cards=cards,
    )


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
    
    mock_intelligence_repo.return_value.get_cash_metrics.return_value = {
        "total_balance": Decimal("1000.00"),
        "active_accounts_count": 2
    }
    mock_intelligence_repo.return_value.get_cashflow_metrics.return_value = {
        "income": Decimal("1500.00"),
        "expenses": Decimal("200.00")
    }
    mock_intelligence_repo.return_value.get_goals_metrics.return_value = {
        "active_goals_count": 1,
        "total_target": Decimal("5000.00"),
        "total_saved": Decimal("100.00")
    }
    mock_intelligence_repo.return_value.get_obligations_metrics.return_value = {
        "pending_count": 1,
        "pending_amount": Decimal("100.00")
    }
    mock_intelligence_repo.return_value.get_transfers_metrics.return_value = {
        "monthly_transfer_volume": Decimal("400.00")
    }
    mock_intelligence_repo.return_value.get_recent_activity.return_value = []

    with patch("app.intelligence.service.CreditCardService") as mock_cc_service_class:
        mock_cc_service_class.return_value.get_credit_summary = AsyncMock(
            return_value=build_credit_summary(
                total_debt=Decimal("500.00"),
                billed_debt=Decimal("300.00"),
                unbilled_debt=Decimal("200.00"),
            )
        )
        result = await intelligence_service.get_snapshot(user_id)

    assert isinstance(result, IntelligenceSnapshotRead)
    assert result.cash.total_balance == Decimal("1000.00")
    assert result.cash.active_accounts_count == 2
    assert result.cashflow.income == Decimal("1500.00")
    assert result.cashflow.expenses == Decimal("200.00")
    assert result.cashflow.net_cashflow == Decimal("1300.00")
    assert result.debt.credit_card_total_debt == Decimal("500.00")
    assert result.debt.billed_debt == Decimal("300.00")
    assert result.debt.unbilled_debt == Decimal("200.00")
    assert result.goals.active_goals_count == 1
    assert result.obligations.pending_amount == Decimal("100.00")
    assert result.transfers.monthly_transfer_volume == Decimal("400.00")


@pytest.mark.asyncio
async def test_get_snapshot_empty_user(intelligence_service, mock_intelligence_repo):
    user_id = uuid.uuid4()
    mock_intelligence_repo.return_value.get_cash_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_cashflow_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_goals_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_obligations_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_transfers_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_recent_activity.return_value = []

    with patch("app.intelligence.service.CreditCardService") as mock_cc_service_class:
        mock_cc_service_class.return_value.get_credit_summary = AsyncMock(
            return_value=build_credit_summary()
        )
        result = await intelligence_service.get_snapshot(user_id)

    assert isinstance(result, IntelligenceSnapshotRead)
    assert result.cash.total_balance == Decimal("0.00")
    assert result.cashflow.income == Decimal("0.00")
    assert result.cashflow.expenses == Decimal("0.00")
    assert result.cashflow.net_cashflow == Decimal("0.00")
    assert result.debt.credit_card_total_debt == Decimal("0.00")


@pytest.mark.asyncio
async def test_get_snapshot_transfers_do_not_change_cashflow(intelligence_service, mock_intelligence_repo):
    user_id = uuid.uuid4()
    mock_intelligence_repo.return_value.get_cash_metrics.return_value = {
        "total_balance": Decimal("1200.00"),
        "active_accounts_count": 2,
    }
    mock_intelligence_repo.return_value.get_cashflow_metrics.return_value = {
        "income": Decimal("1000.00"),
        "expenses": Decimal("200.00"),
    }
    mock_intelligence_repo.return_value.get_goals_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_obligations_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_transfers_metrics.return_value = {
        "monthly_transfer_volume": Decimal("400.00")
    }
    mock_intelligence_repo.return_value.get_recent_activity.return_value = []

    with patch("app.intelligence.service.CreditCardService") as mock_cc_service_class:
        mock_cc_service_class.return_value.get_credit_summary = AsyncMock(
            return_value=build_credit_summary()
        )
        result = await intelligence_service.get_snapshot(user_id)

    assert result.cashflow.income == Decimal("1000.00")
    assert result.cashflow.expenses == Decimal("200.00")
    assert result.cashflow.net_cashflow == Decimal("800.00")
    assert result.transfers.monthly_transfer_volume == Decimal("400.00")


def test_intelligence_repository_does_not_query_legacy_transactions():
    source = inspect.getsource(IntelligenceRepository)
    assert " transactions" not in source.lower()
    assert "public.transactions" not in source.lower()


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
    
    from unittest.mock import AsyncMock, patch
    mock_session = AsyncMock()
    mock_intelligence_repo.return_value.session = mock_session
    
    # We need to mock the CreditCardService inside get_debt, or its repo.
    with patch('app.credit.service.CreditCardService') as mock_cc_service_class:
        mock_cc_service_instance = mock_cc_service_class.return_value
        
        from datetime import date

        from app.credit.schemas import CreditCardStatusRead, CreditSummaryRead
        mock_cc_service_instance.get_credit_summary = AsyncMock(return_value=CreditSummaryRead(
            total_credit_limit=Decimal("5000.00"),
            total_debt=Decimal("1500.00"),
            total_available_credit=Decimal("3500.00"),
            total_monthly_cc_payment=Decimal("200.00"),
            cards=[
                    CreditCardStatusRead(
                        card_id=uuid.uuid4(),
                        name="Visa",
                        credit_limit=Decimal("5000.00"),
                        total_debt=Decimal("1500.00"),
                        billed_debt=Decimal("1000.00"),
                        unbilled_debt=Decimal("500.00"),
                        monthly_cc_payment=Decimal("200.00"),
                        available_credit=Decimal("3500.00"),
                        cutoff_day=15,
                        payment_due_day=5,
                        next_payment_due_date=date.today().isoformat(),
                        purchases_count=0,
                        payments_count=0,
                    )
            ]
        ))
        
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
