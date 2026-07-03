import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.accounts.models import Account
from app.obligations.exceptions import (
    ObligationPaymentExceedsBalanceError,
    ObligationPeriodAmountRequiredError,
)
from app.obligations.models import Obligation, ObligationPeriod
from app.obligations.schemas import ObligationPaymentCreate
from app.obligations.service import ObligationService


@pytest.fixture
def mock_repo():
    return AsyncMock()


@pytest.fixture
def mock_account_repo():
    repo = AsyncMock()
    # default account setup
    acc = Account(
        id=uuid.uuid4(), user_id=uuid.uuid4(), currency="COP", balance=Decimal("10000.00")
    )
    repo.get_by_id_for_update.return_value = acc
    return repo


@pytest.fixture
def mock_ledger_service():
    service = AsyncMock()
    # Setup default return value
    from unittest.mock import MagicMock

    event_result = MagicMock()
    event_result.idempotent = False
    event_result.event.id = uuid.uuid4()
    event_result.event.amount = Decimal("100.00")
    event_result.event.currency = "COP"
    service.record_event.return_value = event_result
    return service


@pytest.fixture
def base_user_id():
    return uuid.uuid4()


@pytest.fixture
def service(mock_repo, mock_account_repo, mock_ledger_service):
    s = ObligationService(mock_repo)
    s.account_repo = mock_account_repo
    s.ledger_service = mock_ledger_service
    # Mock PeriodEngine syncing since we don't need real db calls in unit tests
    s._calculate_fx_and_amounts = AsyncMock(
        return_value=(Decimal("100.00"), Decimal("100.00"), Decimal("1.0"), "mock", datetime.now())
    )
    return s


@pytest.mark.asyncio
async def test_pay_specific_period_partial(service, base_user_id, mock_repo, mock_account_repo):
    acc = mock_account_repo.get_by_id_for_update.return_value
    acc.user_id = base_user_id
    acc.balance = Decimal("10000.00")

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = period
    mock_repo.session.execute.return_value = mock_res
    obligation = Obligation(id=period.obligation_id, user_id=base_user_id, currency="COP")
    mock_repo.get_by_id.return_value = obligation

    service._calculate_fx_and_amounts.return_value = (
        Decimal("40.00"),
        Decimal("40.00"),
        Decimal("1.0"),
        None,
        None,
    )

    payload = ObligationPaymentCreate(account_id=acc.id, amount=Decimal("40.00"), currency="COP")

    await service.pay_specific_period(base_user_id, period.id, payload)

    assert period.paid_amount == Decimal("40.00")
    assert period.status == "partially_paid"
    mock_repo.session.add.assert_called_once()  # ObligationPayment added


@pytest.mark.asyncio
async def test_pay_specific_period_full(service, base_user_id, mock_repo, mock_account_repo):
    acc = mock_account_repo.get_by_id_for_update.return_value
    acc.user_id = base_user_id

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        paid_amount=Decimal("10.00"),
        status="partially_paid",
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = period
    mock_repo.session.execute.return_value = mock_res
    obligation = Obligation(id=period.obligation_id, user_id=base_user_id, currency="COP")
    mock_repo.get_by_id.return_value = obligation

    service._calculate_fx_and_amounts.return_value = (
        Decimal("90.00"),
        Decimal("90.00"),
        Decimal("1.0"),
        None,
        None,
    )

    payload = ObligationPaymentCreate(account_id=acc.id, amount=Decimal("90.00"), currency="COP")
    await service.pay_specific_period(base_user_id, period.id, payload)

    assert period.paid_amount == Decimal("100.00")
    assert period.status == "paid"


@pytest.mark.asyncio
async def test_pay_specific_period_overpayment_rejected(
    service, base_user_id, mock_repo, mock_account_repo
):
    acc = mock_account_repo.get_by_id_for_update.return_value
    acc.user_id = base_user_id

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = period
    mock_repo.session.execute.return_value = mock_res
    mock_repo.get_by_id.return_value = Obligation(
        id=period.obligation_id, user_id=base_user_id, currency="COP"
    )

    service._calculate_fx_and_amounts.return_value = (
        Decimal("150.00"),
        Decimal("150.00"),
        Decimal("1.0"),
        None,
        None,
    )

    payload = ObligationPaymentCreate(account_id=acc.id, amount=Decimal("150.00"), currency="COP")
    with pytest.raises(ObligationPaymentExceedsBalanceError):
        await service.pay_specific_period(base_user_id, period.id, payload)


@pytest.mark.asyncio
async def test_pay_specific_period_blocks_variable_undefined(
    service, base_user_id, mock_repo, mock_account_repo
):
    acc = mock_account_repo.get_by_id_for_update.return_value
    acc.user_id = base_user_id

    period = ObligationPeriod(
        id=uuid.uuid4(), obligation_id=uuid.uuid4(), amount=None, status="pending_amount_definition"
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = period
    mock_repo.session.execute.return_value = mock_res
    mock_repo.get_by_id.return_value = Obligation(
        id=period.obligation_id, user_id=base_user_id, currency="COP"
    )

    payload = ObligationPaymentCreate(account_id=acc.id, amount=Decimal("50.00"), currency="COP")
    with pytest.raises(ObligationPeriodAmountRequiredError):
        await service.pay_specific_period(base_user_id, period.id, payload)


@pytest.mark.asyncio
async def test_pay_fifo_pays_overdue_before_current(
    service, base_user_id, mock_repo, mock_account_repo
):
    acc = mock_account_repo.get_by_id_for_update.return_value
    acc.user_id = base_user_id

    obligation = Obligation(id=uuid.uuid4(), user_id=base_user_id, currency="COP")
    mock_repo.get_by_id.return_value = obligation

    # Two periods: one overdue (seq 1), one pending (seq 2)
    p1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="overdue",
    )
    p2 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )

    # We need to mock sync_periods so it doesn't try to use the DB
    with patch("app.obligations.service.PeriodEngine") as MockEngine:
        mock_engine_instance = MockEngine.return_value
        mock_engine_instance.sync_periods = AsyncMock()

        # Mock the result of the periods query
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [p1, p2]
        mock_repo.session.execute.return_value = mock_result

        # Calculate FX returns 150 for applied
        service._calculate_fx_and_amounts.return_value = (
            Decimal("150.00"),
            Decimal("150.00"),
            Decimal("1.0"),
            None,
            None,
        )

        payload = ObligationPaymentCreate(
            account_id=acc.id, amount=Decimal("150.00"), currency="COP"
        )
        await service.pay_obligation_fifo(base_user_id, obligation.id, payload)

        assert p1.paid_amount == Decimal("100.00")
        assert p1.status == "paid"

        assert p2.paid_amount == Decimal("50.00")
        assert p2.status == "partially_paid"

        assert service.ledger_service.record_event.call_count == 2


@pytest.mark.asyncio
async def test_pay_fifo_rejects_overpayment(service, base_user_id, mock_repo, mock_account_repo):
    acc = mock_account_repo.get_by_id_for_update.return_value
    acc.user_id = base_user_id

    obligation = Obligation(id=uuid.uuid4(), user_id=base_user_id, currency="COP")
    mock_repo.get_by_id.return_value = obligation

    p1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )

    with patch("app.obligations.service.PeriodEngine") as MockEngine:
        MockEngine.return_value.sync_periods = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [p1]
        mock_repo.session.execute.return_value = mock_result

        # Total debt is 100, but applied amount is 150
        service._calculate_fx_and_amounts.return_value = (
            Decimal("150.00"),
            Decimal("150.00"),
            Decimal("1.0"),
            None,
            None,
        )

        payload = ObligationPaymentCreate(
            account_id=acc.id, amount=Decimal("150.00"), currency="COP"
        )
        with pytest.raises(ObligationPaymentExceedsBalanceError):
            await service.pay_obligation_fifo(base_user_id, obligation.id, payload)


@pytest.mark.asyncio
async def test_pay_full_with_null_amount_calculates_balance(
    service, base_user_id, mock_repo, mock_account_repo
):
    # Unmock _calculate_fx_and_amounts to test real logic
    real_service = ObligationService(mock_repo)
    real_service.account_repo = mock_account_repo
    real_service.ledger_service = service.ledger_service

    acc = mock_account_repo.get_by_id_for_update.return_value
    acc.user_id = base_user_id
    acc.currency = "COP"

    obligation = Obligation(id=uuid.uuid4(), user_id=base_user_id, currency="COP")
    mock_repo.get_by_id.return_value = obligation

    p1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("75.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )

    with patch("app.obligations.service.PeriodEngine") as MockEngine:
        MockEngine.return_value.sync_periods = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [p1]
        mock_repo.session.execute.return_value = mock_result

        payload = ObligationPaymentCreate(account_id=acc.id, amount=None)
        await real_service.pay_obligation_fifo(base_user_id, obligation.id, payload)

        assert p1.paid_amount == Decimal("75.00")
        assert p1.status == "paid"

        # Check event payload
        event_arg = real_service.ledger_service.record_event.call_args[0][0]
        assert event_arg.amount == Decimal("75.00")
        assert event_arg.currency == "COP"


@pytest.mark.asyncio
@patch("app.obligations.service.get_fx_rate")
async def test_pay_cross_currency_cop_to_usd(
    mock_get_fx, service, base_user_id, mock_repo, mock_account_repo
):
    real_service = ObligationService(mock_repo)
    real_service.account_repo = mock_account_repo
    real_service.ledger_service = service.ledger_service

    acc = mock_account_repo.get_by_id_for_update.return_value
    acc.user_id = base_user_id
    acc.currency = "COP"
    acc.balance = Decimal("100000.00")

    obligation = Obligation(id=uuid.uuid4(), user_id=base_user_id, currency="USD")
    mock_repo.get_by_id.return_value = obligation

    p1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("20.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )

    mock_get_fx.return_value = {
        "fx_rate": Decimal("0.00025"),  # 1 COP = 0.00025 USD (4000 COP/USD)
        "rate_source": "mock",
        "rate_timestamp": datetime.now(),
    }

    with patch("app.obligations.service.PeriodEngine") as MockEngine:
        MockEngine.return_value.sync_periods = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [p1]
        mock_repo.session.execute.return_value = mock_result

        # User wants to pay 40000 COP
        payload = ObligationPaymentCreate(
            account_id=acc.id, amount=Decimal("40000.00"), currency="COP"
        )
        await real_service.pay_obligation_fifo(base_user_id, obligation.id, payload)

        # 40000 COP * 0.00025 = 10.00 USD
        assert p1.paid_amount == Decimal("10.00")
        assert p1.status == "partially_paid"

        event_arg = real_service.ledger_service.record_event.call_args[0][0]
        assert event_arg.amount == Decimal("40000.00")
        assert event_arg.currency == "COP"
        assert event_arg.metadata["applied_amount"] == "10.00"


@pytest.mark.asyncio
async def test_pay_insufficient_funds_doesnt_change_period(
    service, base_user_id, mock_repo, mock_account_repo
):
    acc = mock_account_repo.get_by_id_for_update.return_value
    acc.user_id = base_user_id
    acc.balance = Decimal("10.00")

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = period
    mock_repo.session.execute.return_value = mock_res
    mock_repo.get_by_id.return_value = Obligation(
        id=period.obligation_id, user_id=base_user_id, currency="COP"
    )

    service._calculate_fx_and_amounts.return_value = (
        Decimal("50.00"),
        Decimal("50.00"),
        Decimal("1.0"),
        None,
        None,
    )

    payload = ObligationPaymentCreate(account_id=acc.id, amount=Decimal("50.00"), currency="COP")

    with pytest.raises(ValueError, match="Insufficient balance"):
        await service.pay_specific_period(base_user_id, period.id, payload)

    assert period.paid_amount == Decimal("0.00")
    assert period.status == "pending_payment"


@pytest.mark.asyncio
async def test_pay_unpayable_states(service, base_user_id, mock_repo, mock_account_repo):
    acc = mock_account_repo.get_by_id_for_update.return_value
    acc.user_id = base_user_id

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        paid_amount=Decimal("100.00"),
        status="paid",
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = period
    mock_repo.session.execute.return_value = mock_res
    mock_repo.get_by_id.return_value = Obligation(
        id=period.obligation_id, user_id=base_user_id, currency="COP"
    )

    payload = ObligationPaymentCreate(account_id=acc.id, amount=Decimal("50.00"), currency="COP")

    with pytest.raises(ValueError, match="Period is not in a payable state"):
        await service.pay_specific_period(base_user_id, period.id, payload)


@pytest.mark.asyncio
async def test_pay_preview_specific_period_same_currency(
    service, base_user_id, mock_repo, mock_account_repo
):
    acc = mock_account_repo.get_by_id.return_value
    acc.id = uuid.uuid4()
    acc.user_id = base_user_id
    acc.balance = Decimal("10000.00")
    acc.currency = "COP"

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = period
    mock_repo.session.execute.return_value = mock_res
    obligation = Obligation(id=period.obligation_id, user_id=base_user_id, currency="COP")
    mock_repo.get_by_id.return_value = obligation

    from app.obligations.schemas import ObligationPaymentPreviewCreate

    payload = ObligationPaymentPreviewCreate(account_id=acc.id, amount=Decimal("40.00"))

    # We mock _calculate_fx_and_amounts
    service._calculate_fx_and_amounts.return_value = (
        Decimal("40.00"),
        Decimal("40.00"),
        Decimal("1.0"),
        None,
        None,
    )

    preview = await service.pay_preview_specific_period(base_user_id, period.id, payload)

    assert preview.requested_amount == Decimal("40.00")
    assert preview.applied_amount == Decimal("40.00")
    assert preview.source_amount == Decimal("40.00")
    assert preview.fx_rate is None
    assert preview.is_estimated is False
    assert preview.can_pay is True


@pytest.mark.asyncio
async def test_pay_preview_specific_period_overpayment(
    service, base_user_id, mock_repo, mock_account_repo
):
    acc = mock_account_repo.get_by_id.return_value
    acc.id = uuid.uuid4()
    acc.user_id = base_user_id
    acc.balance = Decimal("10000.00")
    acc.currency = "COP"

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = period
    mock_repo.session.execute.return_value = mock_res
    obligation = Obligation(id=period.obligation_id, user_id=base_user_id, currency="COP")
    mock_repo.get_by_id.return_value = obligation

    from app.obligations.schemas import ObligationPaymentPreviewCreate

    payload = ObligationPaymentPreviewCreate(account_id=acc.id, amount=Decimal("150.00"))

    service._calculate_fx_and_amounts.return_value = (
        Decimal("150.00"),
        Decimal("150.00"),
        Decimal("1.0"),
        None,
        None,
    )

    with pytest.raises(ObligationPaymentExceedsBalanceError):
        await service.pay_preview_specific_period(base_user_id, period.id, payload)
