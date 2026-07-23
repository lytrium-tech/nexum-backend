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
        "active_accounts_count": 2,
    }
    mock_intelligence_repo.return_value.get_cashflow_metrics.return_value = {
        "income_current_period": Decimal("1500.00"),
        "cash_expenses_current_period": Decimal("200.00"),
        "credit_card_consumption_current_period": Decimal("0.00"),
        "debt_payments_current_period": Decimal("0.00"),
        "goal_contributions_current_period": Decimal("0.00"),
        "obligation_payments_current_period": Decimal("0.00"),
    }
    mock_intelligence_repo.return_value.get_cash_metrics_by_currency.return_value = [
        {"currency": "COP", "total_balance": Decimal("1000.00"), "active_accounts_count": 2}
    ]
    mock_intelligence_repo.return_value.get_cashflow_metrics_by_currency.return_value = [
        {
            "currency": "COP",
            "income_current_period": Decimal("1500.00"),
            "cash_expenses_current_period": Decimal("200.00"),
            "credit_card_consumption_current_period": Decimal("0.00"),
            "debt_payments_current_period": Decimal("0.00"),
            "goal_contributions_current_period": Decimal("0.00"),
            "obligation_payments_current_period": Decimal("0.00"),
        }
    ]
    mock_intelligence_repo.return_value.get_historical_cashflow_metrics.return_value = {
        "historical_income": Decimal("0.00"),
        "historical_expenses": Decimal("0.00"),
    }
    mock_intelligence_repo.return_value.get_goals_metrics.return_value = {
        "active_goals_count": 1,
        "total_target": Decimal("5000.00"),
        "total_saved": Decimal("100.00"),
    }
    mock_intelligence_repo.return_value.get_obligations_metrics.return_value = {
        "pending_count": 1,
        "pending_amount": Decimal("100.00"),
    }
    mock_intelligence_repo.return_value.get_transfers_metrics.return_value = {
        "monthly_transfer_volume": Decimal("400.00")
    }
    mock_intelligence_repo.return_value.get_recent_activity.return_value = []

    with (
        patch("app.intelligence.service.CreditCardService") as mock_cc_service_class,
        patch("app.obligations.repository.ObligationRepository") as mock_obl_repo_class,
        patch("app.goals.repository.GoalRepository") as mock_goal_repo_class,
    ):
        mock_cc_service_class.return_value.get_credit_summary = AsyncMock(
            return_value=build_credit_summary(
                total_debt=Decimal("500.00"),
                billed_debt=Decimal("300.00"),
                unbilled_debt=Decimal("200.00"),
            )
        )
        mock_obl_repo_class.return_value.get_pending_period_amounts_for_snapshot = AsyncMock(
            return_value={}
        )
        mock_goal_repo_class.return_value.list_active = AsyncMock(return_value=[])
        mock_goal_repo_class.return_value.get_period_contributions = AsyncMock(return_value={})
        mock_goal_repo_class.return_value.calculate_reserved_by_user_currency = AsyncMock(
            return_value={}
        )
        result = await intelligence_service.get_snapshot(user_id)

    assert isinstance(result, IntelligenceSnapshotRead)
    assert result.cash.total_balance == Decimal("1000.00")
    assert result.cash.active_accounts_count == 2
    assert result.cashflow.income == Decimal("1500.00")
    assert result.cashflow.expenses == Decimal("200.00")
    assert result.cashflow.net_cashflow == Decimal("1300.00")
    assert result.cashflow.income_current_period == Decimal("1500.00")
    assert result.cashflow.cash_expenses_current_period == Decimal("200.00")
    assert result.cashflow.net_cashflow_current_period == Decimal("1300.00")
    assert result.debt.credit_card_total_debt == Decimal("500.00")
    assert result.debt.billed_debt == Decimal("300.00")
    assert result.debt.unbilled_debt == Decimal("200.00")
    assert result.truth.payment_required == Decimal("300.00")
    assert result.truth.committed_outflows == Decimal("300.00")
    assert result.goals.active_goals_count == 1
    assert result.obligations.pending_amount == Decimal("100.00")
    assert result.transfers.monthly_transfer_volume == Decimal("400.00")


@pytest.mark.asyncio
async def test_get_snapshot_empty_user(intelligence_service, mock_intelligence_repo):
    user_id = uuid.uuid4()
    mock_intelligence_repo.return_value.get_cash_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_cashflow_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_cash_metrics_by_currency.return_value = []
    mock_intelligence_repo.return_value.get_cashflow_metrics_by_currency.return_value = []
    mock_intelligence_repo.return_value.get_historical_cashflow_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_goals_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_obligations_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_transfers_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_recent_activity.return_value = []

    with (
        patch("app.intelligence.service.CreditCardService") as mock_cc_service_class,
        patch("app.obligations.repository.ObligationRepository") as mock_obl_repo_class,
        patch("app.goals.repository.GoalRepository") as mock_goal_repo_class,
    ):
        mock_cc_service_class.return_value.get_credit_summary = AsyncMock(
            return_value=build_credit_summary()
        )
        mock_obl_repo_class.return_value.get_pending_period_amounts_for_snapshot = AsyncMock(
            return_value={}
        )
        mock_goal_repo_class.return_value.list_active = AsyncMock(return_value=[])
        mock_goal_repo_class.return_value.get_period_contributions = AsyncMock(return_value={})
        mock_goal_repo_class.return_value.calculate_reserved_by_user_currency = AsyncMock(
            return_value={}
        )
        result = await intelligence_service.get_snapshot(user_id)

    assert isinstance(result, IntelligenceSnapshotRead)
    assert result.cash.total_balance == Decimal("0.00")
    assert result.cashflow.income == Decimal("0.00")
    assert result.cashflow.expenses == Decimal("0.00")
    assert result.cashflow.net_cashflow == Decimal("0.00")
    assert result.debt.credit_card_total_debt == Decimal("0.00")


@pytest.mark.asyncio
async def test_get_snapshot_transfers_do_not_change_cashflow(
    intelligence_service, mock_intelligence_repo
):
    user_id = uuid.uuid4()
    mock_intelligence_repo.return_value.get_cash_metrics.return_value = {
        "total_balance": Decimal("1200.00"),
        "active_accounts_count": 2,
    }
    mock_intelligence_repo.return_value.get_cashflow_metrics.return_value = {
        "income_current_period": Decimal("1000.00"),
        "cash_expenses_current_period": Decimal("200.00"),
    }
    mock_intelligence_repo.return_value.get_cash_metrics_by_currency.return_value = [
        {"currency": "COP", "total_balance": Decimal("1000.00")}
    ]
    mock_intelligence_repo.return_value.get_cashflow_metrics_by_currency.return_value = [
        {
            "currency": "COP",
            "income_current_period": Decimal("1000.00"),
            "cash_expenses_current_period": Decimal("200.00"),
        }
    ]
    mock_intelligence_repo.return_value.get_historical_cashflow_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_goals_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_obligations_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_transfers_metrics.return_value = {
        "monthly_transfer_volume": Decimal("400.00")
    }
    mock_intelligence_repo.return_value.get_recent_activity.return_value = []

    with (
        patch("app.intelligence.service.CreditCardService") as mock_cc_service_class,
        patch("app.obligations.repository.ObligationRepository") as mock_obl_repo_class,
        patch("app.goals.repository.GoalRepository") as mock_goal_repo_class,
    ):
        mock_cc_service_class.return_value.get_credit_summary = AsyncMock(
            return_value=build_credit_summary()
        )
        mock_obl_repo_class.return_value.get_pending_period_amounts_for_snapshot = AsyncMock(
            return_value={}
        )
        mock_goal_repo_class.return_value.list_active = AsyncMock(return_value=[])
        mock_goal_repo_class.return_value.get_period_contributions = AsyncMock(return_value={})
        mock_goal_repo_class.return_value.calculate_reserved_by_user_currency = AsyncMock(
            return_value={}
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
    with patch("app.credit.service.CreditCardService") as mock_cc_service_class:
        mock_cc_service_instance = mock_cc_service_class.return_value

        from datetime import date

        from app.credit.schemas import CreditCardStatusRead, CreditSummaryRead

        mock_cc_service_instance.get_credit_summary = AsyncMock(
            return_value=CreditSummaryRead(
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
                ],
            )
        )

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


@pytest.mark.asyncio
async def test_committed_outflows_excludes_paid_obligations(
    intelligence_service, mock_intelligence_repo
):
    """committed_outflows must only include remaining_amount for pending/partial obligations.
    Paid obligations must contribute 0. Already-executed payments must not count as commitment."""

    user_id = uuid.uuid4()

    mock_intelligence_repo.return_value.get_cash_metrics.return_value = {
        "total_balance": Decimal("5000.00"),
        "active_accounts_count": 1,
    }
    mock_intelligence_repo.return_value.get_cashflow_metrics.return_value = {
        "income_current_period": Decimal("3000.00"),
        "cash_expenses_current_period": Decimal("500.00"),
    }
    mock_intelligence_repo.return_value.get_cash_metrics_by_currency.return_value = [
        {"currency": "COP", "total_balance": Decimal("5000.00")}
    ]
    mock_intelligence_repo.return_value.get_cashflow_metrics_by_currency.return_value = [
        {
            "currency": "COP",
            "income_current_period": Decimal("3000.00"),
            "cash_expenses_current_period": Decimal("500.00"),
        }
    ]
    mock_intelligence_repo.return_value.get_historical_cashflow_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_goals_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_obligations_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_transfers_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_recent_activity.return_value = []

    with (
        patch("app.intelligence.service.CreditCardService") as mock_cc_service_class,
        patch("app.obligations.repository.ObligationRepository") as mock_obl_repo_class,
        patch("app.goals.repository.GoalRepository") as mock_goal_repo_class,
    ):
        mock_cc_service_class.return_value.get_credit_summary = AsyncMock(
            return_value=build_credit_summary()
        )
        mock_obl_repo_class.return_value.get_pending_period_amounts_for_snapshot = AsyncMock(
            return_value={"COP": Decimal("550.00")}
        )
        mock_goal_repo_class.return_value.list_active = AsyncMock(return_value=[])
        mock_goal_repo_class.return_value.get_period_contributions = AsyncMock(return_value={})
        mock_goal_repo_class.return_value.calculate_reserved_by_user_currency = AsyncMock(
            return_value={}
        )

        result = await intelligence_service.get_snapshot(user_id)

    # Expected committed_outflows:
    # paid obligation: 0 (fully paid, period_status=paid)
    # pending obligation: 300 (not paid at all, period_status=pending)
    # partial obligation: 250 (400 - 150, period_status=partial)
    # Total obligations in committed: 550
    # No credit card debt, no goals -> committed_outflows = 550
    assert result.truth.committed_outflows == Decimal("550")
    # free_money = 5000 - 550 = 4450
    assert result.truth.free_money == Decimal("4450")


@pytest.mark.asyncio
async def test_get_snapshot_with_fx_provider(mock_intelligence_repo):
    from app.fx.provider import StaticFxRateProvider

    fx_provider = StaticFxRateProvider()
    service = IntelligenceService(session=AsyncMock(), fx_provider=fx_provider)
    service.repo = mock_intelligence_repo.return_value

    user_id = uuid.uuid4()
    mock_intelligence_repo.return_value.get_cash_metrics.return_value = {
        "total_balance": Decimal("0.00"),
        "currency_count": 2,
    }
    mock_intelligence_repo.return_value.get_cashflow_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_cash_metrics_by_currency.return_value = [
        {"currency": "COP", "total_balance": Decimal("1000.00")},
        {"currency": "USD", "total_balance": Decimal("10.00")},
    ]
    mock_intelligence_repo.return_value.get_cashflow_metrics_by_currency.return_value = []
    mock_intelligence_repo.return_value.get_historical_cashflow_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_goals_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_obligations_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_transfers_metrics.return_value = {}
    mock_intelligence_repo.return_value.get_recent_activity.return_value = []

    with (
        patch("app.intelligence.service.CreditCardService") as mock_cc_service_class,
        patch("app.obligations.repository.ObligationRepository") as mock_obl_repo_class,
        patch("app.goals.repository.GoalRepository") as mock_goal_repo_class,
        patch("app.core.config.settings") as mock_settings,
    ):
        mock_settings.FX_BASE_CURRENCY = "COP"
        mock_settings.FX_PROVIDER = "static"

        mock_cc_service_class.return_value.get_credit_summary = AsyncMock(
            return_value=build_credit_summary()
        )
        mock_obl_repo_class.return_value.get_pending_period_amounts_for_snapshot = AsyncMock(
            return_value={}
        )
        mock_goal_repo_class.return_value.list_active = AsyncMock(return_value=[])
        mock_goal_repo_class.return_value.get_period_contributions = AsyncMock(return_value={})
        mock_goal_repo_class.return_value.calculate_reserved_by_user_currency = AsyncMock(
            return_value={}
        )

        result = await service.get_snapshot(user_id)

    assert result.estimated_totals is not None
    assert result.estimated_totals.base_currency == "COP"
    assert result.estimated_totals.is_estimated is True
    # COP = 1000.00, USD = 10.00 * 4000.0 (from StaticFxRateProvider)
    # Total = 1000 + 40000 = 41000.00
    assert result.estimated_totals.estimated_total_base_currency == Decimal("41000.00")
    assert result.estimated_totals.fx_rates_used["USD_COP"] == 4000.0
    assert "cross_currency_global_totals_disabled" in result.truth.calculation_warnings


@pytest.mark.asyncio
async def test_get_pending_period_amounts_for_snapshot():
    from decimal import Decimal

    from app.obligations.repository import ObligationRepository

    session_mock = AsyncMock()

    # We will mock the result of session.execute to return specific rows
    # The query groups by currency and returns (currency, total_pending)

    class MockRow:
        def __init__(self, currency, total_pending):
            self.currency = currency
            self.total_pending = total_pending

    class MockResult:
        def all(self):
            return [
                MockRow("COP", Decimal("550.00")),
                MockRow("USD", None),  # tests null handling
            ]

    session_mock.execute.return_value = MockResult()

    repo = ObligationRepository(session_mock)
    res = await repo.get_pending_period_amounts_for_snapshot(uuid.uuid4())

    assert "COP" in res
    assert res["COP"] == Decimal("550.00")
    assert "USD" in res
    assert res["USD"] == Decimal("0.00")
