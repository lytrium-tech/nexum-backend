import uuid
from datetime import UTC
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.accounts.models import Account
from app.obligations.enums_v17 import AmountType, PeriodStatus
from app.obligations.models import Obligation, ObligationPayment, ObligationPeriod
from app.obligations.schemas_v17 import (
    ObligationFIFOPaymentCreateRequest,
    ObligationPeriodPaymentCreateRequest,
)
from app.obligations.service_v17 import ObligationV17Service


def create_mock_service(balance: Decimal, reserved: Decimal):
    session = AsyncMock()
    service = ObligationV17Service(session)
    service.ledger_service = AsyncMock()
    service.ledger_service.record_event.return_value = AsyncMock(
        idempotent=False, event=AsyncMock(id=uuid.uuid4())
    )
    service.account_repo = AsyncMock()
    acc = Account(
        id=uuid.uuid4(), user_id=uuid.uuid4(), currency="USD", balance=balance, is_active=True
    )
    acc.available_balance = balance - reserved
    service.account_repo.get_by_id_for_update.return_value = acc
    service.account_repo.get_by_id.return_value = acc
    return service, acc


def make_execute_side_effect(
    reserved: Decimal, payments: list, periods: list, user_id=None, obligation=None
):
    from datetime import datetime, timedelta

    def side_effect(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "from obligations" in sql:
            obl = obligation or Obligation(
                id=periods[0].obligation_id if periods else uuid.uuid4(),
                user_id=user_id or uuid.uuid4(),
                currency="USD",
                status="active",
                amount_type=AmountType.fixed.value,
            )
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=obl)))
            )
        if "goal_transactions" in sql:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=reserved))
        if "from obligation_payments" in sql:
            return MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(
                        first=MagicMock(return_value=payments[0] if payments else None),
                        all=MagicMock(return_value=payments),
                    )
                )
            )
        if "from obligation_periods" in sql:
            return MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(
                        first=MagicMock(return_value=periods[0] if periods else None),
                        all=MagicMock(return_value=periods),
                    )
                )
            )
        if "from fx_quotes" in sql:
            future = datetime.now(UTC) + timedelta(days=1)
            quote_mock = MagicMock(user_id=user_id, expires_at=future, status="active")
            quote_mock.from_currency = "COP"
            quote_mock.to_currency = "USD"
            quote_mock.rate = Decimal("0.00025")
            quote_mock.provider = "mock"
            quote_mock.target_amount = Decimal("10")
            quote_mock.source_amount = Decimal("40000")
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=quote_mock)))
            )
        return MagicMock(
            scalars=MagicMock(
                return_value=MagicMock(
                    first=MagicMock(return_value=None), all=MagicMock(return_value=[])
                )
            )
        )

    return side_effect


@pytest.mark.asyncio
async def test_case_a_allow_when_sufficient_balance():
    service, acc = create_mock_service(Decimal("1000"), Decimal("0"))
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("500"),
        paid_amount=Decimal("0"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    service.session.execute.side_effect = make_execute_side_effect(Decimal("0"), [], [period])
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=acc.id, idempotency_key=str(uuid.uuid4())
    )
    await service.pay_specific_period(acc.user_id, period.obligation_id, period.id, req)


@pytest.mark.asyncio
async def test_case_b_reject_when_insufficient_available_balance():
    service, acc = create_mock_service(Decimal("1000"), Decimal("700"))
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("500"),
        paid_amount=Decimal("0"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    service.session.execute.side_effect = make_execute_side_effect(Decimal("700"), [], [period])
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=acc.id, idempotency_key=str(uuid.uuid4())
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(acc.user_id, period.obligation_id, period.id, req)
    assert exc.value.status_code == 400
    assert exc.value.detail == "insufficient_balance"


@pytest.mark.asyncio
async def test_case_c_allow_when_sufficient_available_balance():
    service, acc = create_mock_service(Decimal("1000"), Decimal("700"))
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("300"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    service.session.execute.side_effect = make_execute_side_effect(Decimal("700"), [], [period])
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("300"), source_account_id=acc.id, idempotency_key=str(uuid.uuid4())
    )
    await service.pay_specific_period(acc.user_id, period.obligation_id, period.id, req)


@pytest.mark.asyncio
async def test_case_d_reject_when_no_available_balance():
    service, acc = create_mock_service(Decimal("1000"), Decimal("1000"))
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("100"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    service.session.execute.side_effect = make_execute_side_effect(Decimal("1000"), [], [period])
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("100"), source_account_id=acc.id, idempotency_key=str(uuid.uuid4())
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(acc.user_id, period.obligation_id, period.id, req)
    assert exc.value.status_code == 400
    assert exc.value.detail == "insufficient_balance"


@pytest.mark.asyncio
async def test_case_e_replay_when_insufficient_available():
    service, acc = create_mock_service(Decimal("1000"), Decimal("900"))
    p_obs_id = uuid.uuid4()
    p_per_id = uuid.uuid4()
    payment = ObligationPayment(
        id=uuid.uuid4(),
        obligation_id=p_obs_id,
        user_id=acc.user_id,
        account_id=acc.id,
        currency="USD",
        obligation_period_id=p_per_id,
        amount=Decimal("500"),
    )
    period = ObligationPeriod(
        id=payment.obligation_period_id,
        obligation_id=payment.obligation_id,
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("500"),
        paid_amount=Decimal("0"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    service.session.execute.side_effect = make_execute_side_effect(
        Decimal("900"), [payment], [period]
    )
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=acc.id, idempotency_key=str(uuid.uuid4())
    )
    res = await service.pay_specific_period(acc.user_id, period.obligation_id, period.id, req)
    assert res[0].id == payment.id


@pytest.mark.asyncio
async def test_case_f_fifo_smart_payment():
    service, acc = create_mock_service(Decimal("1000"), Decimal("0"))
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("500"),
        paid_amount=Decimal("0"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    service.session.execute.side_effect = make_execute_side_effect(Decimal("0"), [], [period])
    req = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=acc.id, idempotency_key=str(uuid.uuid4())
    )
    await service.pay_obligation_fifo(acc.user_id, period.obligation_id, req)


@pytest.mark.asyncio
async def test_case_g_fifo_smart_payment_insufficient_available():
    service, acc = create_mock_service(Decimal("1000"), Decimal("600"))
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("500"),
        paid_amount=Decimal("0"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    service.session.execute.side_effect = make_execute_side_effect(Decimal("600"), [], [period])
    req = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=acc.id, idempotency_key=str(uuid.uuid4())
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_obligation_fifo(acc.user_id, period.obligation_id, req)
    assert exc.value.status_code == 400
    assert exc.value.detail == "insufficient_balance"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_case_h_replay_fifo_when_insufficient_available():
    service, acc = create_mock_service(Decimal("1000"), Decimal("900"))
    p_obs_id = uuid.uuid4()
    p_per_id = uuid.uuid4()
    payment = ObligationPayment(
        id=uuid.uuid4(),
        obligation_id=p_obs_id,
        user_id=acc.user_id,
        account_id=acc.id,
        currency="USD",
        obligation_period_id=p_per_id,
        amount=Decimal("500"),
    )
    period = ObligationPeriod(
        id=payment.obligation_period_id,
        obligation_id=payment.obligation_id,
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("500"),
        paid_amount=Decimal("0"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    service.session.execute.side_effect = make_execute_side_effect(
        Decimal("900"), [payment], [period]
    )
    req = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=acc.id, idempotency_key=str(uuid.uuid4())
    )
    res = await service.pay_obligation_fifo(acc.user_id, period.obligation_id, req)
    assert res[0][0].id == payment.id


@pytest.mark.asyncio
async def test_case_i_fx_sufficient_available():
    service, acc = create_mock_service(Decimal("100000"), Decimal("0"))
    acc.currency = "COP"
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("10"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    service._calculate_fx_and_amounts = AsyncMock(
        return_value=(Decimal("10"), Decimal("40000"), Decimal("0.00025"), "mock", "mock")
    )
    service.session.execute.side_effect = make_execute_side_effect(
        Decimal("0"), [], [period], user_id=acc.user_id
    )
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("10"),
        source_account_id=acc.id,
        quote_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
    )
    await service.pay_specific_period(acc.user_id, period.obligation_id, period.id, req)


@pytest.mark.asyncio
async def test_case_j_fx_insufficient_available():
    service, acc = create_mock_service(Decimal("100000"), Decimal("70000"))
    acc.currency = "COP"
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("10"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    service._calculate_fx_and_amounts = AsyncMock(
        return_value=(Decimal("10"), Decimal("40000"), Decimal("0.00025"), "mock", "mock")
    )
    service.session.execute.side_effect = make_execute_side_effect(
        Decimal("70000"), [], [period], user_id=acc.user_id
    )
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("10"),
        source_account_id=acc.id,
        quote_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(acc.user_id, period.obligation_id, period.id, req)
    assert exc.value.status_code == 400
    assert exc.value.detail == "insufficient_balance"


@pytest.mark.asyncio
async def test_case_k_fifo_multiple_periods():
    service, acc = create_mock_service(Decimal("1000"), Decimal("0"))
    p1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.overdue.value,
        amount=Decimal("500"),
        sequence_number=1,
    )
    p2 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=p1.obligation_id,
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("500"),
        sequence_number=2,
    )
    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=p1.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )
    service.session.execute.side_effect = make_execute_side_effect(Decimal("0"), [], [p1, p2])
    req = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("1000"), source_account_id=acc.id, idempotency_key=str(uuid.uuid4())
    )
    await service.pay_obligation_fifo(acc.user_id, p1.obligation_id, req)


@pytest.mark.asyncio
async def test_case_l_fifo_multiple_periods_insufficient():
    service, acc = create_mock_service(Decimal("1000"), Decimal("200"))
    p1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.overdue.value,
        amount=Decimal("500"),
        sequence_number=1,
    )
    p2 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=p1.obligation_id,
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("500"),
        sequence_number=2,
    )
    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=p1.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )
    service.session.execute.side_effect = make_execute_side_effect(Decimal("200"), [], [p1, p2])
    req = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("1000"), source_account_id=acc.id, idempotency_key=str(uuid.uuid4())
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_obligation_fifo(acc.user_id, p1.obligation_id, req)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_case_m_fifo_smart_payment_overpayment():
    service, acc = create_mock_service(Decimal("1000"), Decimal("0"))
    p1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("500"),
        sequence_number=1,
    )
    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=p1.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )
    service.session.execute.side_effect = make_execute_side_effect(Decimal("0"), [], [p1])
    req = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("1000"), source_account_id=acc.id, idempotency_key=str(uuid.uuid4())
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_obligation_fifo(acc.user_id, p1.obligation_id, req)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_case_n_concurrency_double_spend():
    service, acc = create_mock_service(Decimal("1000"), Decimal("0"))
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("500"),
        paid_amount=Decimal("0"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    service.session.execute.side_effect = make_execute_side_effect(Decimal("0"), [], [period])
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=acc.id, idempotency_key=str(uuid.uuid4())
    )
    await service.pay_specific_period(acc.user_id, period.obligation_id, period.id, req)
    service.account_repo.get_by_id_for_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_case_o_fifo_malicious_source_amount_same_currency():
    service, acc = create_mock_service(Decimal("1000"), Decimal("700"))
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("500"),
        paid_amount=Decimal("0"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    service.session.execute.side_effect = make_execute_side_effect(Decimal("700"), [], [period])
    req = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("500"),
        source_amount=Decimal("1"),
        source_account_id=acc.id,
        idempotency_key=str(uuid.uuid4()),
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_obligation_fifo(acc.user_id, period.obligation_id, req)
    assert exc.value.status_code == 400
    assert exc.value.detail == "insufficient_balance"
    # Ensure ledger and balance are not touched
    service.ledger_service.record_event.assert_not_called()
    service.account_repo.update_balance.assert_not_called()


@pytest.mark.asyncio
async def test_case_p_fx_malicious_source_amount():
    service, acc = create_mock_service(Decimal("100000"), Decimal("0"))
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("10"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    acc.currency = "COP"
    service.session.execute.side_effect = make_execute_side_effect(
        Decimal("0"), [], [period], user_id=acc.user_id
    )
    req = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("10"),
        source_amount=Decimal("1"),
        source_account_id=acc.id,
        idempotency_key=str(uuid.uuid4()),
        quote_id=uuid.uuid4(),
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_obligation_fifo(acc.user_id, period.obligation_id, req)
    assert exc.value.status_code == 422
    assert exc.value.detail == "invalid_fx_quote"


@pytest.mark.asyncio
async def test_case_q_zero_write_semantics():
    service, acc = create_mock_service(Decimal("200"), Decimal("150"))
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("100"),
        paid_amount=Decimal("0"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    service.session.execute.side_effect = make_execute_side_effect(Decimal("150"), [], [period])
    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("100"), source_account_id=acc.id, idempotency_key=str(uuid.uuid4())
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(acc.user_id, period.obligation_id, period.id, req)
    assert exc.value.status_code == 400
    assert exc.value.detail == "insufficient_balance"
    # Absolutely no financial side effects
    service.ledger_service.record_event.assert_not_called()
    service.account_repo.update_balance.assert_not_called()
    service.session.add.assert_not_called()
    assert period.paid_amount == Decimal("0")
    assert period.status == PeriodStatus.pending_payment.value


@pytest.mark.asyncio
async def test_case_r_v1_5_positive_reservation():
    from app.accounts.models import Account
    from app.obligations.schemas import ObligationPaymentCreate
    from app.obligations.service import ObligationService

    db_mock = AsyncMock()
    service = ObligationService(db_mock)
    service.account_repo = MagicMock()

    acc = Account(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        currency="USD",
        balance=Decimal("1000"),
        is_active=True,
    )
    service.account_repo.get_by_id_for_update = AsyncMock(return_value=acc)
    service.account_repo.update_balance = AsyncMock()
    service.ledger_service = MagicMock()
    service.ledger_service.record_event = AsyncMock()

    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("500"),
        paid_amount=Decimal("0"),
    )

    service.repository = MagicMock()
    service.repository.session = db_mock
    obl_r = Obligation(
        id=period.obligation_id,
        user_id=acc.user_id,
        currency="USD",
        amount_type=AmountType.fixed.value,
        status="active",
    )
    service.repository.get_by_id = AsyncMock(return_value=obl_r)
    service.repository.get_by_id_for_update = AsyncMock(return_value=obl_r)

    from app.goals.repository import GoalRepository

    original_calc = GoalRepository.calculate_reserved_by_account

    async def mock_calc(*args, **kwargs):
        return Decimal("700")

    GoalRepository.calculate_reserved_by_account = mock_calc

    try:

        def mock_execute(*args, **kwargs):
            class Result:
                def scalar_one_or_none(self):
                    return period

                def scalars(self):
                    class Scalars:
                        def first(self):
                            return None

                    return Scalars()

            return Result()

        db_mock.execute = AsyncMock(side_effect=mock_execute)

        req = ObligationPaymentCreate(amount=Decimal("500"), account_id=acc.id)

        with pytest.raises(ValueError) as exc:
            await service.pay_specific_period(acc.user_id, period.id, req)

        assert str(exc.value) == "Insufficient balance"

        service.account_repo.update_balance.assert_not_called()
        service.ledger_service.record_event.assert_not_called()
    finally:
        GoalRepository.calculate_reserved_by_account = original_calc


@pytest.mark.asyncio
async def test_case_s_rate_snapshot_malicious_source_amount():
    service, acc = create_mock_service(Decimal("10"), Decimal("0"))
    # In create_mock_service acc.currency = "USD". So source_currency = USD.
    # Let's make obligation EUR, so FX needed.
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("10"),
    )
    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="EUR",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    from datetime import UTC, datetime, timedelta

    from app.obligations.models import ExchangeRate

    # base_currency must match source_currency (USD), quote_currency must match obligation (EUR)
    snapshot = ExchangeRate(
        id=uuid.uuid4(),
        base_currency="USD",
        quote_currency="EUR",
        rate=Decimal("0.9090"),
        provider="test",
        fetched_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    def mock_execute(*args, **kwargs):
        class Result:
            def scalars(self):
                class Scalars:
                    def first(self):
                        stmt_str = str(args[0]).lower()
                        if (
                            "exchangerates" in stmt_str
                            or "exchange_rates" in stmt_str
                            or "exchangerate" in stmt_str
                            or "exchange_rate" in stmt_str
                        ):
                            return snapshot
                        if "from obligations" in stmt_str:
                            return Obligation(
                                id=period.obligation_id,
                                user_id=acc.user_id,
                                currency="EUR",
                                status="active",
                            )
                        if "from obligation_payments" in stmt_str:
                            return None
                        if "from obligation_periods" in stmt_str:
                            return period
                        return None

                    def all(self):
                        return [period]

                return Scalars()

            def scalar_one_or_none(self):
                return Decimal("0")

        return Result()

    service.session.execute = AsyncMock(side_effect=mock_execute)
    service.ledger_service.record_event = AsyncMock(
        return_value=MagicMock(idempotent=False, event=MagicMock(id=uuid.uuid4()))
    )

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("10"),
        source_account_id=acc.id,
        source_amount=Decimal("1"),  # malicious
        rate_snapshot_id=snapshot.id,
        idempotency_key=str(uuid.uuid4()),
    )

    # balance = 10. reserved = 0.
    # amount = 10 EUR. rate = 0.9090 EUR/USD. source_amount = 10 / 0.9090 = 11.00 USD.
    # available (10) < source_amount (11).
    # So it MUST raise insufficient_balance. This proves the backend used 11.00 instead of 1.
    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(acc.user_id, period.obligation_id, period.id, req)

    assert exc.value.status_code == 400
    assert exc.value.detail == "insufficient_balance"


@pytest.mark.asyncio
async def test_case_t_ledger_failure_after_debit():
    service, acc = create_mock_service(Decimal("1000"), Decimal("0"))
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("500"),
        paid_amount=Decimal("0"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    def mock_execute(*args, **kwargs):
        class Result:
            def scalars(self):
                class Scalars:
                    def first(self):
                        stmt_str = str(args[0]).lower()
                        if "from obligations" in stmt_str:
                            return Obligation(
                                id=period.obligation_id,
                                user_id=acc.user_id,
                                currency="USD",
                                status="active",
                            )
                        if "from obligation_payments" in stmt_str:
                            return None
                        if "from obligation_periods" in stmt_str:
                            return period
                        return None

                    def all(self):
                        return [period]

                return Scalars()

            def scalar_one_or_none(self):
                return Decimal("0")

        return Result()

    service.session.execute = AsyncMock(side_effect=mock_execute)

    class LedgerException(Exception):
        pass

    service.ledger_service.record_event = AsyncMock(
        side_effect=LedgerException("Technical failure")
    )

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=acc.id, idempotency_key=str(uuid.uuid4())
    )

    with pytest.raises(LedgerException):
        await service.pay_specific_period(acc.user_id, period.obligation_id, period.id, req)

    service.account_repo.update_balance.assert_called_once()
    service.session.add.assert_not_called()


@pytest.mark.asyncio
async def test_case_u_duplicate_after_debit_rollback():
    service, acc = create_mock_service(Decimal("1000"), Decimal("0"))
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        status=PeriodStatus.pending_payment.value,
        amount=Decimal("500"),
        paid_amount=Decimal("0"),
    )

    service.get_obligation = AsyncMock(
        return_value=Obligation(
            id=period.obligation_id,
            user_id=acc.user_id,
            currency="USD",
            amount_type=AmountType.fixed.value,
            status="active",
        )
    )

    from app.obligations.models import ObligationPayment

    winner_event_id = uuid.uuid4()
    req_key = str(uuid.uuid4())
    existing_payment = ObligationPayment(
        id=uuid.uuid4(),
        obligation_id=period.obligation_id,
        obligation_period_id=period.id,
        user_id=acc.user_id,
        account_id=acc.id,
        financial_event_id=winner_event_id,
        amount=Decimal("500"),
        currency="USD",
        source_amount=Decimal("500"),
        source_currency="USD",
        idempotency_key=req_key,
    )

    call_count = 0

    def mock_execute(*args, **kwargs):
        nonlocal call_count
        call_count += 1

        class Result:
            def scalars(self):
                class Scalars:
                    def first(self):
                        stmt_str = str(args[0]).lower()
                        if "from obligations" in stmt_str:
                            return Obligation(
                                id=period.obligation_id,
                                user_id=acc.user_id,
                                currency="USD",
                                status="active",
                            )
                        if "from obligation_periods" in stmt_str:
                            return period
                        if "from obligation_payments" in stmt_str:
                            # Only return winner payment after ledger collision
                            if service.ledger_service.record_event.called:
                                return existing_payment
                            return None
                        return None

                    def all(self):
                        stmt_str = str(args[0]).lower()
                        if "from obligation_payments" in stmt_str:
                            if service.ledger_service.record_event.called:
                                return [existing_payment]
                            return []
                        return [period]

                return Scalars()

            def scalar_one_or_none(self):
                return Decimal("0")

        return Result()

    service.session.execute = AsyncMock(side_effect=mock_execute)

    winner_event = MagicMock(id=winner_event_id, metadata={})
    winner_event.metadata_ = None
    service.ledger_service.record_event = AsyncMock(
        return_value=MagicMock(idempotent=True, event=winner_event)
    )

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("500"), source_account_id=acc.id, idempotency_key=req_key
    )

    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(acc.user_id, period.obligation_id, period.id, req)
    assert exc.value.status_code == 409
    assert exc.value.detail == "idempotency_conflict"
    service.account_repo.update_balance.assert_awaited_once_with(acc, Decimal("-500"))
    service.session.add.assert_not_called()
