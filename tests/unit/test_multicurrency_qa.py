import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun import freeze_time

from app.accounts.models import Account
from app.credit.exceptions import CreditDomainError
from app.credit.models import CreditCard, CreditCardInstallment, CreditCardTransaction
from app.credit.schemas import CreditCardEarlyPaymentCreate, CreditCardPaymentCreate
from app.credit.service import CreditCardService
from app.obligations.service import ObligationService


class ObligationPaymentCreate:
    def __init__(self, **kwargs):
        pass


# ── Fíxtures de Mocks ──────────────────────────────────────────────────────────


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
def mock_credit_repo():
    return AsyncMock()


@pytest.fixture
def mock_ledger_service():
    return AsyncMock()


@pytest.fixture
def obligation_service(mock_obligation_repo, mock_account_repo, mock_ledger_repo):
    return ObligationService(mock_obligation_repo)


@pytest.fixture
def credit_service(mock_credit_repo, mock_account_repo, mock_ledger_service):
    session = AsyncMock()
    svc = CreditCardService(session)
    svc.repo = mock_credit_repo
    svc.account_repo = mock_account_repo
    svc.ledger_service = mock_ledger_service
    return svc


# ── Tests de Obligaciones (1-4, 11-12, 13, 14) ─────────────────────────────────


# ── Tests Específicos para Bug 1 (Obligación Fija Multimoneda) ─────────────────


# ── Tests de Tarjetas de Crédito (5-8, 9-10) ───────────────────────────────────


@pytest.mark.asyncio
async def test_credit_card_payment_cop_to_cop(
    credit_service, mock_credit_repo, mock_account_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    acc_id = uuid.uuid4()

    mock_credit_repo.get_by_id_for_update.return_value = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("500000"),
        currency="COP",
        is_active=True,
        cutoff_day=15,
        due_day=5,
    )
    mock_account_repo.get_by_id_for_update.return_value = Account(
        id=acc_id, user_id=user_id, balance=Decimal("50000"), currency="COP", is_active=True
    )
    mock_credit_repo.get_card_debt.return_value = (Decimal("30000"), Decimal("30000"))

    # Mock pending installments for waterfall to prevent Overpayment errors
    mock_credit_repo.list_pending_installments_for_update.return_value = [
        CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=user_id,
            credit_card_id=card_id,
            installment_number=1,
            installments_total=12,
            principal_amount=Decimal("30000"),
            interest_amount=Decimal("0"),
            total_amount=Decimal("30000"),
            paid_amount=Decimal("0"),
            status="pending",
            scheduled_period="2026-06",
        )
    ]
    mock_credit_repo.list_unpaid_statement_charges_for_update.return_value = []

    event_mock = MagicMock()
    event_mock.id = uuid.uuid4()
    mock_ledger_service.record_event.return_value = MagicMock(event=event_mock, idempotent=False)

    payload = CreditCardPaymentCreate(account_id=acc_id, amount=Decimal("10000"))
    res = await credit_service.create_payment(user_id, card_id, payload, command_id=uuid.uuid4())

    assert res.status == "success"
    assert res.amount == Decimal("10000")
    mock_credit_repo.add_transaction.assert_called_once()
    tx_arg = mock_credit_repo.add_transaction.call_args[0][0]
    assert tx_arg.amount == Decimal("10000")


@pytest.mark.asyncio
async def test_credit_card_payment_usd_to_usd(
    credit_service, mock_credit_repo, mock_account_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    acc_id = uuid.uuid4()

    mock_credit_repo.get_by_id_for_update.return_value = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("200"),
        currency="USD",
        is_active=True,
        cutoff_day=15,
        due_day=5,
    )
    mock_account_repo.get_by_id_for_update.return_value = Account(
        id=acc_id, user_id=user_id, balance=Decimal("150"), currency="USD", is_active=True
    )
    mock_credit_repo.get_card_debt.return_value = (Decimal("100"), Decimal("100"))

    mock_credit_repo.list_pending_installments_for_update.return_value = [
        CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=user_id,
            credit_card_id=card_id,
            installment_number=1,
            installments_total=12,
            principal_amount=Decimal("100"),
            interest_amount=Decimal("0"),
            total_amount=Decimal("100"),
            paid_amount=Decimal("0"),
            status="pending",
            scheduled_period="2026-06",
        )
    ]
    mock_credit_repo.list_unpaid_statement_charges_for_update.return_value = []

    event_mock = MagicMock()
    event_mock.id = uuid.uuid4()
    mock_ledger_service.record_event.return_value = MagicMock(event=event_mock, idempotent=False)

    payload = CreditCardPaymentCreate(account_id=acc_id, amount=Decimal("50"))
    res = await credit_service.create_payment(user_id, card_id, payload, command_id=uuid.uuid4())

    assert res.status == "success"
    assert res.amount == Decimal("50")
    tx_arg = mock_credit_repo.add_transaction.call_args[0][0]
    assert tx_arg.amount == Decimal("50")


@pytest.mark.asyncio
async def test_credit_card_payment_cop_to_usd(
    credit_service, mock_credit_repo, mock_account_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    acc_id = uuid.uuid4()

    mock_credit_repo.get_by_id_for_update.return_value = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("200"),
        currency="USD",
        is_active=True,
        cutoff_day=15,
        due_day=5,
    )
    mock_account_repo.get_by_id_for_update.return_value = Account(
        id=acc_id, user_id=user_id, balance=Decimal("300000"), currency="COP", is_active=True
    )
    mock_credit_repo.get_card_debt.return_value = (Decimal("100"), Decimal("100"))

    mock_credit_repo.list_pending_installments_for_update.return_value = [
        CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=user_id,
            credit_card_id=card_id,
            installment_number=1,
            installments_total=12,
            principal_amount=Decimal("100"),
            interest_amount=Decimal("0"),
            total_amount=Decimal("100"),
            paid_amount=Decimal("0"),
            status="pending",
            scheduled_period="2026-06",
        )
    ]
    mock_credit_repo.list_unpaid_statement_charges_for_update.return_value = []

    event_mock = MagicMock()
    event_mock.id = uuid.uuid4()
    mock_ledger_service.record_event.return_value = MagicMock(event=event_mock, idempotent=False)

    payload = CreditCardPaymentCreate(account_id=acc_id, amount=Decimal("200000"))

    with patch("app.core.currency.get_fx_rate") as mock_fx:
        mock_fx.return_value = {
            "fx_rate": Decimal("0.00025"),
            "rate_source": "dolarapi_colombia",
            "rate_timestamp": datetime.now(),
        }
        res = await credit_service.create_payment(
            user_id, card_id, payload, command_id=uuid.uuid4()
        )

    assert res.status == "success"
    tx_arg = mock_credit_repo.add_transaction.call_args[0][0]
    assert tx_arg.amount == Decimal("50.00")  # Converted to card's USD currency


@pytest.mark.asyncio
async def test_credit_card_payment_usd_to_cop(
    credit_service, mock_credit_repo, mock_account_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    acc_id = uuid.uuid4()

    mock_credit_repo.get_by_id_for_update.return_value = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("500000"),
        currency="COP",
        is_active=True,
        cutoff_day=15,
        due_day=5,
    )
    mock_account_repo.get_by_id_for_update.return_value = Account(
        id=acc_id, user_id=user_id, balance=Decimal("100"), currency="USD", is_active=True
    )
    mock_credit_repo.get_card_debt.return_value = (Decimal("100000"), Decimal("100000"))

    mock_credit_repo.list_pending_installments_for_update.return_value = [
        CreditCardInstallment(
            id=uuid.uuid4(),
            user_id=user_id,
            credit_card_id=card_id,
            installment_number=1,
            installments_total=12,
            principal_amount=Decimal("100000"),
            interest_amount=Decimal("0"),
            total_amount=Decimal("100000"),
            paid_amount=Decimal("0"),
            status="pending",
            scheduled_period="2026-06",
        )
    ]
    mock_credit_repo.list_unpaid_statement_charges_for_update.return_value = []

    event_mock = MagicMock()
    event_mock.id = uuid.uuid4()
    mock_ledger_service.record_event.return_value = MagicMock(event=event_mock, idempotent=False)

    payload = CreditCardPaymentCreate(account_id=acc_id, amount=Decimal("20"))

    with patch("app.core.currency.get_fx_rate") as mock_fx:
        mock_fx.return_value = {
            "fx_rate": Decimal("4000"),
            "rate_source": "dolarapi_colombia",
            "rate_timestamp": datetime.now(),
        }
        res = await credit_service.create_payment(
            user_id, card_id, payload, command_id=uuid.uuid4()
        )

    assert res.status == "success"
    tx_arg = mock_credit_repo.add_transaction.call_args[0][0]
    assert tx_arg.amount == Decimal("80000")  # Converted to card's COP currency


@pytest.mark.asyncio
async def test_early_future_payment_succeeds(
    credit_service, mock_credit_repo, mock_account_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    purchase_id = uuid.uuid4()
    acc_id = uuid.uuid4()

    mock_credit_repo.get_by_id_for_update.return_value = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("500"),
        currency="USD",
        is_active=True,
        cutoff_day=15,
        due_day=5,
    )
    mock_account_repo.get_by_id_for_update.return_value = Account(
        id=acc_id, user_id=user_id, balance=Decimal("150"), currency="USD", is_active=True
    )
    mock_credit_repo.get_transaction_by_id.return_value = CreditCardTransaction(
        id=purchase_id,
        credit_card_id=card_id,
        user_id=user_id,
        amount=Decimal("240"),
        type="purchase",
    )

    mock_inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=user_id,
        credit_card_id=card_id,
        purchase_transaction_id=purchase_id,
        installment_number=1,
        installments_total=12,
        principal_amount=Decimal("20"),
        interest_amount=Decimal("0"),
        total_amount=Decimal("20"),
        status="pending",
        paid_amount=None,  # Tests NoneType safety!
        scheduled_period="2027-07",
    )
    mock_credit_repo.list_installments_for_transaction_for_update.return_value = [mock_inst]
    mock_credit_repo.get_card_debt.return_value = (Decimal("240"), Decimal("240"))

    event_mock = MagicMock()
    event_mock.id = uuid.uuid4()
    mock_ledger_service.record_event.return_value = MagicMock(event=event_mock, idempotent=False)

    payload = CreditCardEarlyPaymentCreate(
        account_id=acc_id, amount=Decimal("10"), allocation_mode="reduce_installment_amount"
    )
    res = await credit_service.create_early_payment(
        user_id, card_id, purchase_id, payload, command_id=uuid.uuid4()
    )

    assert res.status == "success"
    assert res.amount == Decimal("10")
    assert mock_inst.principal_amount == Decimal("10.00")  # Principal reduced safely from 20 to 10


@pytest.mark.asyncio
async def test_early_future_payment_cross_currency(
    credit_service, mock_credit_repo, mock_account_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    purchase_id = uuid.uuid4()
    acc_id = uuid.uuid4()

    mock_credit_repo.get_by_id_for_update.return_value = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("500"),
        currency="USD",
        is_active=True,
        cutoff_day=15,
        due_day=5,
    )
    mock_account_repo.get_by_id_for_update.return_value = Account(
        id=acc_id, user_id=user_id, balance=Decimal("100000"), currency="COP", is_active=True
    )
    mock_credit_repo.get_transaction_by_id.return_value = CreditCardTransaction(
        id=purchase_id,
        credit_card_id=card_id,
        user_id=user_id,
        amount=Decimal("240"),
        type="purchase",
    )

    mock_inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=user_id,
        credit_card_id=card_id,
        purchase_transaction_id=purchase_id,
        installment_number=1,
        installments_total=12,
        principal_amount=Decimal("20"),
        interest_amount=Decimal("0"),
        total_amount=Decimal("20"),
        status="pending",
        paid_amount=Decimal("0"),
        scheduled_period="2027-07",
    )
    mock_credit_repo.list_installments_for_transaction_for_update.return_value = [mock_inst]
    mock_credit_repo.get_card_debt.return_value = (Decimal("240"), Decimal("240"))

    event_mock = MagicMock()
    event_mock.id = uuid.uuid4()
    mock_ledger_service.record_event.return_value = MagicMock(event=event_mock, idempotent=False)

    payload = CreditCardEarlyPaymentCreate(
        account_id=acc_id, amount=Decimal("40000"), allocation_mode="reduce_installment_amount"
    )

    with patch("app.core.currency.get_fx_rate") as mock_fx:
        mock_fx.return_value = {
            "fx_rate": Decimal("0.00025"),
            "rate_source": "dolarapi_colombia",
            "rate_timestamp": datetime.now(),
        }
        res = await credit_service.create_early_payment(
            user_id, card_id, purchase_id, payload, command_id=uuid.uuid4()
        )

    assert res.status == "success"
    assert res.amount == Decimal("40000")
    assert mock_inst.principal_amount == Decimal(
        "10.00"
    )  # Converted 40000 COP -> 10 USD early payment


# ── Tests Específicos para Bug 2 y Bug 3 (Abonos Anticipados y Elegibilidad) ───


@pytest.mark.asyncio
async def test_early_payment_amount_none_succeeds(
    credit_service, mock_credit_repo, mock_account_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    purchase_id = uuid.uuid4()
    acc_id = uuid.uuid4()

    mock_credit_repo.get_by_id_for_update.return_value = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("500"),
        currency="USD",
        is_active=True,
        cutoff_day=15,
        due_day=5,
    )
    mock_account_repo.get_by_id_for_update.return_value = Account(
        id=acc_id, user_id=user_id, balance=Decimal("150"), currency="USD", is_active=True
    )
    mock_credit_repo.get_transaction_by_id.return_value = CreditCardTransaction(
        id=purchase_id,
        credit_card_id=card_id,
        user_id=user_id,
        amount=Decimal("240"),
        type="purchase",
    )

    mock_inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=user_id,
        credit_card_id=card_id,
        purchase_transaction_id=purchase_id,
        installment_number=1,
        installments_total=12,
        principal_amount=Decimal("20"),
        interest_amount=Decimal("0"),
        total_amount=Decimal("20"),
        status="pending",
        paid_amount=Decimal("0"),
        scheduled_period="2027-07",
    )
    mock_credit_repo.list_installments_for_transaction_for_update.return_value = [mock_inst]
    mock_credit_repo.get_card_debt.return_value = (Decimal("240"), Decimal("240"))

    event_mock = MagicMock()
    event_mock.id = uuid.uuid4()
    mock_ledger_service.record_event.return_value = MagicMock(event=event_mock, idempotent=False)

    # amount is None -> should dynamically calculate total_remaining_principal (20)
    payload = CreditCardEarlyPaymentCreate(
        account_id=acc_id, amount=None, allocation_mode="reduce_installment_amount"
    )
    res = await credit_service.create_early_payment(
        user_id, card_id, purchase_id, payload, command_id=uuid.uuid4()
    )

    assert res.status == "success"
    assert res.amount == Decimal("20")
    assert mock_inst.paid_amount == mock_inst.principal_amount


@pytest.mark.asyncio
async def test_early_payment_single_installment_rejected(
    credit_service, mock_credit_repo, mock_account_repo
):
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    purchase_id = uuid.uuid4()
    acc_id = uuid.uuid4()

    mock_credit_repo.get_by_id_for_update.return_value = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("500"),
        currency="USD",
        is_active=True,
        cutoff_day=15,
        due_day=5,
    )
    mock_credit_repo.get_transaction_by_id.return_value = CreditCardTransaction(
        id=purchase_id,
        credit_card_id=card_id,
        user_id=user_id,
        amount=Decimal("240"),
        type="purchase",
    )

    mock_inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=user_id,
        credit_card_id=card_id,
        purchase_transaction_id=purchase_id,
        installment_number=1,
        installments_total=1,  # 1 installment total!
        principal_amount=Decimal("240"),
        interest_amount=Decimal("0"),
        total_amount=Decimal("240"),
        status="pending",
        paid_amount=Decimal("0"),
        scheduled_period="2026-07",
    )
    mock_credit_repo.list_installments_for_transaction_for_update.return_value = [mock_inst]

    payload = CreditCardEarlyPaymentCreate(
        account_id=acc_id, amount=Decimal("50"), allocation_mode="reduce_installment_amount"
    )
    with pytest.raises(CreditDomainError, match="PAY_EARLY_NOT_ELIGIBLE"):
        await credit_service.create_early_payment(
            user_id, card_id, purchase_id, payload, command_id=uuid.uuid4()
        )


@pytest.mark.asyncio
async def test_early_payment_no_future_installments_rejected(
    credit_service, mock_credit_repo, mock_account_repo
):
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    purchase_id = uuid.uuid4()
    acc_id = uuid.uuid4()

    mock_credit_repo.get_by_id_for_update.return_value = CreditCard(
        id=card_id,
        user_id=user_id,
        credit_limit=Decimal("500"),
        currency="USD",
        is_active=True,
        cutoff_day=15,
        due_day=5,
    )
    mock_credit_repo.get_transaction_by_id.return_value = CreditCardTransaction(
        id=purchase_id,
        credit_card_id=card_id,
        user_id=user_id,
        amount=Decimal("240"),
        type="purchase",
    )

    # Installment is for current period (2026-06) or past, not in future (scheduled_period <= current_period)
    mock_inst = CreditCardInstallment(
        id=uuid.uuid4(),
        user_id=user_id,
        credit_card_id=card_id,
        purchase_transaction_id=purchase_id,
        installment_number=1,
        installments_total=12,
        principal_amount=Decimal("20"),
        interest_amount=Decimal("0"),
        total_amount=Decimal("20"),
        status="pending",
        paid_amount=Decimal("0"),
        scheduled_period="2026-06",
    )
    mock_credit_repo.list_installments_for_transaction_for_update.return_value = [mock_inst]

    payload = CreditCardEarlyPaymentCreate(
        account_id=acc_id, amount=Decimal("10"), allocation_mode="reduce_installment_amount"
    )

    with freeze_time("2026-06-27"):
        with pytest.raises(CreditDomainError, match="PAY_EARLY_NOT_ELIGIBLE"):
            await credit_service.create_early_payment(
                user_id, card_id, purchase_id, payload, command_id=uuid.uuid4()
            )


# ── Tests para fixed_full_payment con amount=None (Bug 1 - Full Payment Intent) ────
