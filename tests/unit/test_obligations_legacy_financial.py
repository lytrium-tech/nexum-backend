import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.accounts.models import Account
from app.core.errors import register_exception_handlers
from app.ledger.models import FinancialEvent
from app.obligations.exceptions import (
    ObligationAccountInactiveError,
    ObligationAccountUnavailableError,
    ObligationBelowMinimumAmountError,
    ObligationCurrencyMismatchError,
    ObligationIdempotencyConflictDomainError,
    ObligationInconsistentReservationError,
    ObligationInsufficientBalanceError,
)
from app.obligations.models import Obligation, ObligationPayment, ObligationPeriod
from app.obligations.schemas import ObligationPaymentCreate
from app.obligations.service import ObligationService


def create_service(account_balance: Decimal, reserved: Decimal = Decimal("0")):
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        currency="COP",
        balance=account_balance,
        is_active=True,
    )

    repo = AsyncMock()
    account_repo = AsyncMock()
    account_repo.get_by_id_for_update.return_value = account
    account_repo.get_by_id.return_value = account

    # Realistic balance mutation
    async def mock_update_balance(acc, delta):
        acc.balance += delta

    account_repo.update_balance = AsyncMock(side_effect=mock_update_balance)

    ledger_service = AsyncMock()

    service = ObligationService(repo)
    service.account_repo = account_repo
    service.ledger_service = ledger_service

    return service, account, user_id


@pytest.mark.asyncio
async def test_legacy_specific_successful_debit():
    service, account, user_id = create_service(Decimal("1000.00"), Decimal("0"))

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("300.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    obligation = Obligation(
        id=period.obligation_id,
        user_id=user_id,
        currency="COP",
        status="active",
    )
    service.repository.get_by_id_for_update.return_value = obligation

    def mock_exec(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "goal_transactions" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=Decimal("0")))
        if "from financial_events" in sql:
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
            )
        if "from obligation_periods" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=period))
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    service.repository.session.execute.side_effect = mock_exec

    event_id = uuid.uuid4()
    event = MagicMock(id=event_id, metadata_={})
    service.ledger_service.record_event.return_value = MagicMock(event=event, idempotent=False)

    payload = ObligationPaymentCreate(
        account_id=account.id,
        amount=Decimal("300.00"),
        source_message_id="msg_specific_1",
    )

    result = await service.pay_specific_period(user_id, period.id, payload)

    # 1. Assert balance was debited exactly once
    assert account.balance == Decimal("700.00")
    service.account_repo.update_balance.assert_called_once_with(account, -Decimal("300.00"))

    # 2. Assert period paid amount was updated
    assert period.paid_amount == Decimal("300.00")
    assert period.status == "paid"

    # 3. Assert payment record created with explicit user_id
    added_payment = service.repository.session.add.call_args[0][0]
    assert isinstance(added_payment, ObligationPayment)
    assert added_payment.user_id == user_id
    assert added_payment.source_amount == Decimal("300.00")
    assert added_payment.amount == Decimal("300.00")
    assert result.amount == Decimal("300.00")


@pytest.mark.asyncio
async def test_legacy_fifo_multi_period_exact_source_debit_and_sum():
    service, account, user_id = create_service(Decimal("1000.00"), Decimal("0"))

    obligation = Obligation(
        id=uuid.uuid4(),
        user_id=user_id,
        currency="COP",
        status="active",
    )
    service.repository.get_by_id_for_update.return_value = obligation

    p1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="overdue",
        sequence_number=1,
    )
    p2 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("200.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
        sequence_number=2,
    )

    with patch("app.obligations.service.PeriodEngine") as MockEngine:
        MockEngine.return_value.sync_periods = AsyncMock()

        def mock_exec(stmt, *args, **kwargs):
            sql = str(stmt).lower()
            if "goal_transactions" in sql:
                return MagicMock(scalar_one_or_none=MagicMock(return_value=Decimal("0")))
            if "from financial_events" in sql:
                return MagicMock(
                    scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
                )
            if "from obligation_periods" in sql:
                return MagicMock(
                    scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[p1, p2])))
                )
            return MagicMock(
                scalar_one_or_none=MagicMock(return_value=None),
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
            )

        service.repository.session.execute.side_effect = mock_exec

        def mock_record_event(evt):
            ev = MagicMock(id=uuid.uuid4(), metadata_=evt.metadata)
            return MagicMock(event=ev, idempotent=False)

        service.ledger_service.record_event.side_effect = mock_record_event

        payload = ObligationPaymentCreate(
            account_id=account.id,
            amount=Decimal("250.00"),
            source_message_id="msg_fifo_multi",
        )

        results = await service.pay_obligation_fifo(user_id, obligation.id, payload)

        # 1. Total debit EXACTLY ONCE
        assert account.balance == Decimal("750.00")
        service.account_repo.update_balance.assert_called_once_with(account, -Decimal("250.00"))

        # 2. Periods updated properly
        assert p1.paid_amount == Decimal("100.00")
        assert p1.status == "paid"
        assert p2.paid_amount == Decimal("150.00")
        assert p2.status == "partially_paid"

        # 3. Sum of payment source_amounts and applied amounts
        total_source = sum(r.source_amount for r in results)
        total_applied = sum(r.amount for r in results)
        assert total_source == Decimal("250.00")
        assert total_applied == Decimal("250.00")

        # 4. Payments have user_id
        for call_args in service.repository.session.add.call_args_list:
            pay = call_args[0][0]
            assert pay.user_id == user_id


@pytest.mark.asyncio
async def test_legacy_exact_retry_after_changed_availability_zero_second_debit():
    service, account, user_id = create_service(Decimal("1000.00"), Decimal("0"))

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("300.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    obligation = Obligation(
        id=period.obligation_id,
        user_id=user_id,
        currency="COP",
        status="active",
    )
    service.repository.get_by_id_for_update.return_value = obligation

    existing_fe = None
    existing_pay = None

    def mock_exec(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "goal_transactions" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=Decimal("0")))
        if "from financial_events" in sql:
            evs = [existing_fe] if existing_fe else []
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=evs)))
            )
        if "from obligation_payments" in sql:
            pays = [existing_pay] if existing_pay else []
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=pays)))
            )
        if "from obligation_periods" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=period))
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    service.repository.session.execute.side_effect = mock_exec

    event_id = uuid.uuid4()
    fe_mock = FinancialEvent(
        id=event_id,
        user_id=user_id,
        account_id=account.id,
        event_type="obligation_payment",
        direction="outflow",
        amount=Decimal("300.00"),
        currency="COP",
        source_message_id="msg_retry_1",
        metadata_={
            "fingerprint": {
                "strategy": "specific_period",
                "obligation_id": str(obligation.id),
                "period_id": str(period.id),
                "account_id": str(account.id),
                "mode": "explicit",
                "requested_amount": "300.00",
                "currency": "COP",
            }
        },
    )
    service.ledger_service.record_event.return_value = MagicMock(event=fe_mock, idempotent=False)

    payload = ObligationPaymentCreate(
        account_id=account.id,
        amount=Decimal("300.00"),
        source_message_id="msg_retry_1",
    )

    # First attempt: succeeds and debits
    res1 = await service.pay_specific_period(user_id, period.id, payload)
    assert account.balance == Decimal("700.00")
    assert service.account_repo.update_balance.call_count == 1

    # Prepare stored state for second attempt
    existing_fe = fe_mock
    existing_pay = ObligationPayment(
        id=res1.id,
        user_id=user_id,
        obligation_id=obligation.id,
        obligation_period_id=period.id,
        account_id=account.id,
        financial_event_id=event_id,
        amount=Decimal("300.00"),
        currency="COP",
        source_amount=Decimal("300.00"),
        source_currency="COP",
        paid_at=datetime.now(),
        created_at=datetime.now(),
    )

    # Simulate availability drop (e.g. balance dropped to 50 COP)
    account.balance = Decimal("50.00")

    # Retry should return existing payment with NO debit
    res2 = await service.pay_specific_period(user_id, period.id, payload)
    assert res2.id == res1.id
    assert service.account_repo.update_balance.call_count == 1  # Still 1!
    assert account.balance == Decimal("50.00")  # Untouched


@pytest.mark.asyncio
@patch("app.obligations.service.get_fx_rate")
async def test_legacy_fx_target_amount_retry(mock_get_fx):
    service, account, user_id = create_service(Decimal("100000.00"), Decimal("0"))
    account.currency = "COP"

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("50.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    obligation = Obligation(
        id=period.obligation_id,
        user_id=user_id,
        currency="USD",
        status="active",
    )
    service.repository.get_by_id_for_update.return_value = obligation

    mock_get_fx.return_value = {
        "fx_rate": Decimal("0.00025"),  # 4000 COP / USD
        "rate_source": "mock",
        "rate_timestamp": datetime.now(),
    }

    existing_fe = None
    existing_pay = None

    def mock_exec(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "goal_transactions" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=Decimal("0")))
        if "from financial_events" in sql:
            evs = [existing_fe] if existing_fe else []
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=evs)))
            )
        if "from obligation_payments" in sql:
            pays = [existing_pay] if existing_pay else []
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=pays)))
            )
        if "from obligation_periods" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=period))
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    service.repository.session.execute.side_effect = mock_exec

    event_id = uuid.uuid4()
    fe_mock = FinancialEvent(
        id=event_id,
        user_id=user_id,
        account_id=account.id,
        event_type="obligation_payment",
        direction="outflow",
        amount=Decimal("40000.00"),
        currency="COP",
        source_message_id="msg_fx_1",
        metadata_={
            "fingerprint": {
                "strategy": "specific_period",
                "obligation_id": str(obligation.id),
                "period_id": str(period.id),
                "account_id": str(account.id),
                "mode": "explicit",
                "requested_amount": "10.00",
                "currency": "USD",
            }
        },
    )
    service.ledger_service.record_event.return_value = MagicMock(event=fe_mock, idempotent=False)

    # 10 USD target amount -> 40,000 COP source amount
    payload = ObligationPaymentCreate(
        account_id=account.id,
        amount=Decimal("10.00"),
        source_message_id="msg_fx_1",
    )

    res1 = await service.pay_specific_period(user_id, period.id, payload)
    assert account.balance == Decimal("60000.00")
    assert res1.source_amount == Decimal("40000.00")
    assert res1.amount == Decimal("10.00")

    existing_fe = fe_mock
    existing_pay = ObligationPayment(
        id=res1.id,
        user_id=user_id,
        obligation_id=obligation.id,
        obligation_period_id=period.id,
        account_id=account.id,
        financial_event_id=event_id,
        amount=Decimal("10.00"),
        currency="USD",
        source_amount=Decimal("40000.00"),
        source_currency="COP",
        paid_at=datetime.now(),
        created_at=datetime.now(),
    )

    # Same target amount retry succeeds
    res2 = await service.pay_specific_period(user_id, period.id, payload)
    assert res2.id == res1.id

    # Materially different target amount with same source_message_id raises conflict
    conflict_payload = ObligationPaymentCreate(
        account_id=account.id,
        amount=Decimal("15.00"),
        source_message_id="msg_fx_1",
    )
    with pytest.raises(ObligationIdempotencyConflictDomainError):
        await service.pay_specific_period(user_id, period.id, conflict_payload)


@pytest.mark.asyncio
async def test_legacy_replay_cross_obligation_and_account_conflicts():
    service, account, user_id = create_service(Decimal("1000.00"), Decimal("0"))

    obl1_id = uuid.uuid4()
    obl2_id = uuid.uuid4()
    period1_id = uuid.uuid4()

    fe_mock = FinancialEvent(
        id=uuid.uuid4(),
        user_id=user_id,
        account_id=account.id,
        event_type="obligation_payment",
        direction="outflow",
        amount=Decimal("100.00"),
        currency="COP",
        source_message_id="msg_conflict_test",
        metadata_={
            "fingerprint": {
                "strategy": "specific_period",
                "obligation_id": str(obl1_id),
                "period_id": str(period1_id),
                "account_id": str(account.id),
                "mode": "explicit",
                "requested_amount": "100.00",
                "currency": "COP",
            }
        },
    )

    pay_mock = ObligationPayment(
        id=uuid.uuid4(),
        user_id=user_id,
        obligation_id=obl1_id,
        obligation_period_id=period1_id,
        account_id=account.id,
        financial_event_id=fe_mock.id,
        amount=Decimal("100.00"),
        currency="COP",
        source_amount=Decimal("100.00"),
        source_currency="COP",
        paid_at=datetime.now(),
        created_at=datetime.now(),
    )

    def mock_exec(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "from financial_events" in sql:
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[fe_mock])))
            )
        if "from obligation_payments" in sql:
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[pay_mock])))
            )
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    service.repository.session.execute.side_effect = mock_exec

    # 1. Conflict on different obligation
    conflict_obl_payload = ObligationPaymentCreate(
        account_id=account.id,
        amount=Decimal("100.00"),
        source_message_id="msg_conflict_test",
    )
    with pytest.raises(ObligationIdempotencyConflictDomainError):
        await service._check_source_message_replay(
            auth_user_id=user_id,
            payload=conflict_obl_payload,
            expected_obligation_id=obl2_id,
            expected_period_id=period1_id,
            strategy="specific_period",
        )

    # 2. Conflict on different account
    diff_account_id = uuid.uuid4()
    conflict_acc_payload = ObligationPaymentCreate(
        account_id=diff_account_id,
        amount=Decimal("100.00"),
        source_message_id="msg_conflict_test",
    )
    with pytest.raises(ObligationIdempotencyConflictDomainError):
        await service._check_source_message_replay(
            auth_user_id=user_id,
            payload=conflict_acc_payload,
            expected_obligation_id=obl1_id,
            expected_period_id=period1_id,
            strategy="specific_period",
        )

    # 3. Conflict on strategy (FIFO called with specific message)
    with pytest.raises(ObligationIdempotencyConflictDomainError):
        await service._check_source_message_replay(
            auth_user_id=user_id,
            payload=conflict_obl_payload,
            expected_obligation_id=obl1_id,
            expected_period_id=None,
            strategy="fifo",
        )


@pytest.mark.asyncio
async def test_legacy_late_idempotency_collision_raises_conflict():
    service, account, user_id = create_service(Decimal("1000.00"), Decimal("0"))

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    obligation = Obligation(
        id=period.obligation_id,
        user_id=user_id,
        currency="COP",
        status="active",
    )
    service.repository.get_by_id_for_update.return_value = obligation

    def mock_exec(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "goal_transactions" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=Decimal("0")))
        if "from financial_events" in sql:
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
            )
        if "from obligation_periods" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=period))
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    service.repository.session.execute.side_effect = mock_exec

    # Simulate late idempotency collision in ledger
    service.ledger_service.record_event.return_value = MagicMock(event=None, idempotent=True)

    payload = ObligationPaymentCreate(
        account_id=account.id,
        amount=Decimal("100.00"),
        source_message_id="msg_late_collision",
    )

    with pytest.raises(ObligationIdempotencyConflictDomainError):
        await service.pay_specific_period(user_id, period.id, payload)


@pytest.mark.asyncio
async def test_legacy_same_currency_cop_cents_exact_precision():
    service, account, user_id = create_service(Decimal("10000.00"), Decimal("0"))

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("1000.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    obligation = Obligation(
        id=period.obligation_id,
        user_id=user_id,
        currency="COP",
        status="active",
    )
    service.repository.get_by_id_for_update.return_value = obligation

    def mock_exec(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "goal_transactions" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=Decimal("0")))
        if "from financial_events" in sql:
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
            )
        if "from obligation_periods" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=period))
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    service.repository.session.execute.side_effect = mock_exec

    event = MagicMock(id=uuid.uuid4(), metadata_={})
    service.ledger_service.record_event.return_value = MagicMock(event=event, idempotent=False)

    # Pass 501.25 COP
    payload = ObligationPaymentCreate(
        account_id=account.id,
        amount=Decimal("501.25"),
    )

    result = await service.pay_specific_period(user_id, period.id, payload)

    # Assert exact debit of 501.25, not 500 or 550
    assert result.source_amount == Decimal("501.25")
    assert result.amount == Decimal("501.25")
    assert account.balance == Decimal("9498.75")


@pytest.mark.asyncio
async def test_legacy_account_unavailable_and_inactive():
    service, account, user_id = create_service(Decimal("1000.00"), Decimal("0"))

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    obligation = Obligation(
        id=period.obligation_id,
        user_id=user_id,
        currency="COP",
        status="active",
    )
    service.repository.get_by_id_for_update.return_value = obligation

    def mock_exec(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "from obligation_periods" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=period))
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    service.repository.session.execute.side_effect = mock_exec

    payload = ObligationPaymentCreate(account_id=account.id, amount=Decimal("100.00"))

    # Inactive account
    account.is_active = False
    with pytest.raises(ObligationAccountInactiveError):
        await service.pay_specific_period(user_id, period.id, payload)

    # Inactive back to true, negative balance
    account.is_active = True
    account.balance = Decimal("-10.00")
    with pytest.raises(ObligationAccountUnavailableError):
        await service.pay_specific_period(user_id, period.id, payload)

    # None balance
    account.balance = None
    with pytest.raises(ObligationAccountUnavailableError):
        await service.pay_specific_period(user_id, period.id, payload)


@pytest.mark.asyncio
async def test_legacy_insufficient_and_reserved_balance():
    service, account, user_id = create_service(Decimal("1000.00"), Decimal("800.00"))

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("500.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    obligation = Obligation(
        id=period.obligation_id,
        user_id=user_id,
        currency="COP",
        status="active",
    )
    service.repository.get_by_id_for_update.return_value = obligation

    reserved_val = Decimal("800.00")

    def mock_exec(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "goal_transactions" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=reserved_val))
        if "from obligation_periods" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=period))
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    service.repository.session.execute.side_effect = mock_exec

    payload = ObligationPaymentCreate(account_id=account.id, amount=Decimal("300.00"))

    # Balance 1000, reserved 800 -> available 200 < 300
    with pytest.raises(ObligationInsufficientBalanceError):
        await service.pay_specific_period(user_id, period.id, payload)

    # Corrupt negative reservation
    reserved_val = Decimal("-50.00")
    with pytest.raises(ObligationInconsistentReservationError):
        await service.pay_specific_period(user_id, period.id, payload)


@pytest.mark.asyncio
async def test_legacy_currency_mismatch_hint():
    service, account, user_id = create_service(Decimal("1000.00"), Decimal("0"))
    account.currency = "USD"

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    obligation = Obligation(
        id=period.obligation_id,
        user_id=user_id,
        currency="USD",
        status="active",
    )
    service.repository.get_by_id_for_update.return_value = obligation

    def mock_exec(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "from obligation_periods" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=period))
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    service.repository.session.execute.side_effect = mock_exec

    payload = ObligationPaymentCreate(
        account_id=account.id,
        amount=Decimal("100.00"),
        currency="EUR",  # Account is USD -> mismatch
    )

    with pytest.raises(ObligationCurrencyMismatchError):
        await service.pay_specific_period(user_id, period.id, payload)


@pytest.mark.asyncio
@patch("app.obligations.service.get_fx_rate")
async def test_legacy_below_minimum_amount_rejected(mock_get_fx):
    service, account, user_id = create_service(Decimal("100000.00"), Decimal("0"))
    account.currency = "USD"

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("10.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    obligation = Obligation(
        id=period.obligation_id,
        user_id=user_id,
        currency="COP",
        status="active",
    )
    service.repository.get_by_id_for_update.return_value = obligation

    # FX rate where 1 USD = 40,000 COP, but requested target is 0.0001 COP -> 0.0001 / 40000 = 0.00 USD
    mock_get_fx.return_value = {
        "fx_rate": Decimal("40000.00"),
        "rate_source": "mock",
        "rate_timestamp": datetime.now(),
    }

    def mock_exec(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "from obligation_periods" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=period))
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    service.repository.session.execute.side_effect = mock_exec

    payload = ObligationPaymentCreate(
        account_id=account.id,
        amount=Decimal("0.01"),
    )

    with pytest.raises(ObligationBelowMinimumAmountError):
        await service.pay_specific_period(user_id, period.id, payload)


@pytest.mark.asyncio
async def test_legacy_ledger_failure_propagates_for_outer_rollback():
    service, account, user_id = create_service(Decimal("1000.00"), Decimal("0"))

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="pending_payment",
    )
    obligation = Obligation(
        id=period.obligation_id,
        user_id=user_id,
        currency="COP",
        status="active",
    )
    service.repository.get_by_id_for_update.return_value = obligation

    def mock_exec(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "goal_transactions" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=Decimal("0")))
        if "from financial_events" in sql:
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
            )
        if "from obligation_periods" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=period))
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    service.repository.session.execute.side_effect = mock_exec

    class LedgerTechnicalFailure(RuntimeError):
        pass

    service.ledger_service.record_event.side_effect = LedgerTechnicalFailure(
        "Ledger DB write failed"
    )

    payload = ObligationPaymentCreate(
        account_id=account.id,
        amount=Decimal("100.00"),
        source_message_id=str(uuid.uuid4()),
    )

    with pytest.raises(LedgerTechnicalFailure):
        await service.pay_specific_period(user_id, period.id, payload)

    assert period.paid_amount == Decimal("0.00")
    assert period.status == "pending_payment"
    service.repository.session.add.assert_not_called()


def test_legacy_payment_business_error_has_stable_http_contract():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/payment")
    def reject_payment():
        raise ObligationInsufficientBalanceError()

    response = TestClient(app).get("/payment")
    assert response.status_code == 422
    assert response.json()["error_code"] == "insufficient_balance"
