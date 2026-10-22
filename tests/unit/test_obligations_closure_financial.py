import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.ledger.models import FinancialEvent
from app.ledger.schemas import LedgerEventCreate
from app.obligations.enums_v17 import PeriodStatus
from app.obligations.exceptions import (
    ObligationInactiveError,
)
from app.obligations.models import (
    ExchangeRate,
    FXQuote,
    Obligation,
    ObligationPayment,
    ObligationPeriod,
)
from app.obligations.schemas import ObligationPaymentCreate
from app.obligations.schemas_v17 import (
    ObligationFIFOPaymentCreateRequest,
    ObligationPeriodPaymentCreateRequest,
)
from app.obligations.service import ObligationService
from app.obligations.service_v17 import ObligationV17Service
from app.obligations.settlement import (
    build_idempotency_fingerprint,
    reconcile_fifo_source_allocations,
    resolve_settlement,
    validate_idempotency_fingerprint,
)


def create_mock_v17_service(
    user_id: uuid.UUID, account: Account, obligation: Obligation, periods: list[ObligationPeriod]
):
    session = AsyncMock()

    def mock_execute(stmt, *args, **kwargs):
        stmt_str = str(stmt).lower()
        res = MagicMock()
        if "from accounts" in stmt_str or "join accounts" in stmt_str:
            res.scalars.return_value.first.return_value = account
            res.scalar_one_or_none.return_value = account
            res.fetchone.return_value = (user_id,)
            return res
        if "goal_transactions" in stmt_str:
            res.scalar_one_or_none.return_value = Decimal("0.00")
            return res
        if "from obligations" in stmt_str:
            res.scalars.return_value.first.return_value = obligation
            return res
        if "from obligation_periods" in stmt_str:
            if "status in" in stmt_str or "status.in_" in stmt_str:
                matching = [
                    p
                    for p in periods
                    if p.status
                    in (
                        PeriodStatus.pending_payment.value,
                        PeriodStatus.partially_paid.value,
                        PeriodStatus.overdue.value,
                    )
                ]
                res.scalars.return_value.all.return_value = matching
                res.scalars.return_value.first.return_value = matching[0] if matching else None
                return res
            res.scalars.return_value.first.return_value = periods[0] if periods else None
            res.scalars.return_value.all.return_value = periods
            return res
        if "from obligation_payments" in stmt_str:
            res.scalars.return_value.first.return_value = None
            res.scalars.return_value.all.return_value = []
            return res
        res.scalars.return_value.first.return_value = None
        res.scalars.return_value.all.return_value = []
        res.scalar_one_or_none.return_value = None
        return res

    session.execute.side_effect = mock_execute
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    service = ObligationV17Service(session)
    service.account_repo = AccountRepository(session)
    service.account_repo.get_by_id_for_update = AsyncMock(return_value=account)
    service.account_repo.update_balance = AsyncMock()

    mock_ledger_result = MagicMock(
        idempotent=False,
        event=MagicMock(
            id=uuid.uuid4(),
            user_id=user_id,
            account_id=account.id,
            metadata={},
        ),
    )
    service.ledger_service.record_event = AsyncMock(return_value=mock_ledger_result)
    return service, session


# ==============================================================================
# 1. ACCOUNT OWNERSHIP & STATE REGRESSION
# ==============================================================================


@pytest.mark.asyncio
async def test_account_ownership_foreign_account_rejected():
    user_id = uuid.uuid4()
    foreign_user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(),
        user_id=foreign_user_id,
        currency="COP",
        balance=Decimal("1000.00"),
        is_active=True,
    )
    obligation = Obligation(
        id=uuid.uuid4(), user_id=user_id, currency="COP", status="active", amount_type="fixed"
    )
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100"),
        paid_amount=Decimal("0"),
        status=PeriodStatus.pending_payment.value,
    )

    service, _ = create_mock_v17_service(user_id, account, obligation, [period])

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("100"),
        source_account_id=account.id,
        idempotency_key="key-foreign-acc",
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(user_id, obligation.id, period.id, req)
    assert exc.value.status_code == 404
    assert exc.value.detail == "account_not_found"


@pytest.mark.asyncio
async def test_account_ownership_inactive_account_rejected():
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        currency="COP",
        balance=Decimal("1000.00"),
        is_active=False,
    )
    obligation = Obligation(
        id=uuid.uuid4(), user_id=user_id, currency="COP", status="active", amount_type="fixed"
    )
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100"),
        paid_amount=Decimal("0"),
        status=PeriodStatus.pending_payment.value,
    )

    service, _ = create_mock_v17_service(user_id, account, obligation, [period])

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("100"),
        source_account_id=account.id,
        idempotency_key="key-inactive-acc",
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(user_id, obligation.id, period.id, req)
    assert exc.value.status_code == 400
    assert exc.value.detail == "account_inactive"


# ==============================================================================
# 2. OVERDUE PERIOD PAYMENT IN V1.7 SPECIFIC
# ==============================================================================


@pytest.mark.asyncio
async def test_specific_period_payment_allows_overdue_period():
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="COP", balance=Decimal("1000.00"), is_active=True
    )
    obligation = Obligation(
        id=uuid.uuid4(), user_id=user_id, currency="COP", status="active", amount_type="fixed"
    )
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100"),
        paid_amount=Decimal("0"),
        status=PeriodStatus.overdue.value,
    )

    service, _ = create_mock_v17_service(user_id, account, obligation, [period])

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("100"),
        source_account_id=account.id,
        idempotency_key="key-overdue-pay",
    )
    payment, updated_period = await service.pay_specific_period(
        user_id, obligation.id, period.id, req
    )
    assert payment.amount == Decimal("100")
    assert updated_period.status == PeriodStatus.paid.value
    assert updated_period.paid_amount == Decimal("100")


# ==============================================================================
# 3. INACTIVE / CANCELLED OBLIGATION REJECTION
# ==============================================================================


@pytest.mark.asyncio
async def test_inactive_obligation_rejected_in_v17():
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="COP", balance=Decimal("1000.00"), is_active=True
    )
    obligation = Obligation(
        id=uuid.uuid4(), user_id=user_id, currency="COP", status="cancelled", amount_type="fixed"
    )
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100"),
        paid_amount=Decimal("0"),
        status=PeriodStatus.pending_payment.value,
    )

    service, _ = create_mock_v17_service(user_id, account, obligation, [period])

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("100"),
        source_account_id=account.id,
        idempotency_key="key-cancelled-ob",
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(user_id, obligation.id, period.id, req)
    assert exc.value.status_code == 400
    assert exc.value.detail == "obligation_inactive"


@pytest.mark.asyncio
async def test_inactive_obligation_rejected_in_legacy():
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="COP", balance=Decimal("1000.00"), is_active=True
    )
    obligation = Obligation(id=uuid.uuid4(), user_id=user_id, currency="COP", status="inactive")
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100"),
        paid_amount=Decimal("0"),
        status="pending_payment",
    )

    mock_repo = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = period
    mock_repo.session.execute.return_value = mock_res
    mock_repo.get_by_id_for_update.return_value = obligation

    service = ObligationService(mock_repo)
    service.account_repo = AsyncMock()
    service.account_repo.get_by_id_for_update.return_value = account

    payload = ObligationPaymentCreate(
        account_id=account.id, amount=Decimal("100.00"), currency="COP"
    )
    with pytest.raises(ObligationInactiveError):
        await service.pay_specific_period(user_id, period.id, payload)


# ==============================================================================
# 4. UNDEFINED FIFO OLDEST STOPS FIFO BUT SPECIFIC CAN PAY LATER DEFINED
# ==============================================================================


@pytest.mark.asyncio
async def test_undefined_oldest_stops_fifo_but_specific_can_pay_defined_period():
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="COP", balance=Decimal("1000.00"), is_active=True
    )
    obligation = Obligation(
        id=uuid.uuid4(), user_id=user_id, currency="COP", status="active", amount_type="variable"
    )

    oldest_undefined = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=None,
        paid_amount=Decimal("0"),
        status=PeriodStatus.pending_amount_definition.value,
        sequence_number=1,
    )
    second_defined = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("200.00"),
        paid_amount=Decimal("0"),
        status=PeriodStatus.pending_payment.value,
        due_date=date.today() + timedelta(days=30),
        sequence_number=2,
    )

    service, _ = create_mock_v17_service(
        user_id, account, obligation, [oldest_undefined, second_defined]
    )

    # FIFO must stop because oldest period has no defined amount
    req_fifo = ObligationFIFOPaymentCreateRequest(
        amount=Decimal("100.00"),
        source_account_id=account.id,
        idempotency_key="key-fifo-undefined-stop",
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_obligation_fifo(user_id, obligation.id, req_fifo)
    assert exc.value.status_code == 422
    assert exc.value.detail == "oldest_period_requires_amount_definition"

    # Specific CAN pay the second period with defined amount
    req_specific = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("100.00"),
        source_account_id=account.id,
        idempotency_key="key-specific-defined-pay",
    )

    # Configure mock execute to return second_defined for specific period query
    def mock_side_effect(stmt, *a, **kw):
        stmt_str = str(stmt).lower()
        if "from obligation_payments" in stmt_str or "financial_events" in stmt_str:
            return MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(
                        first=MagicMock(return_value=None), all=MagicMock(return_value=[])
                    )
                )
            )
        if "from obligation_periods" in stmt_str:
            if "due_date <" in stmt_str or "due_date < :" in stmt_str:
                return MagicMock(
                    scalars=MagicMock(
                        return_value=MagicMock(
                            first=MagicMock(return_value=None), all=MagicMock(return_value=[])
                        )
                    )
                )
            return MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(
                        first=MagicMock(return_value=second_defined),
                        all=MagicMock(return_value=[second_defined]),
                    )
                )
            )
        if "goal_transactions" in stmt_str:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=Decimal("0.00")))
        if "from obligations" in stmt_str:
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=obligation)))
            )
        if "from accounts" in stmt_str:
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=account)))
            )
        return MagicMock(
            scalars=MagicMock(
                return_value=MagicMock(
                    first=MagicMock(return_value=None), all=MagicMock(return_value=[])
                )
            )
        )

    service.session.execute.side_effect = mock_side_effect
    payment, updated_p2 = await service.pay_specific_period(
        user_id, obligation.id, second_defined.id, req_specific
    )
    assert payment.amount == Decimal("100.00")
    assert updated_p2.paid_amount == Decimal("100.00")
    assert updated_p2.status == PeriodStatus.partially_paid.value


# ==============================================================================
# 5. SAME-CURRENCY SOURCE AMOUNT AUTHORITY & PRECISION REJECTION
# ==============================================================================


@pytest.mark.asyncio
async def test_same_currency_source_amount_authority_and_precision_validation():
    session = AsyncMock()
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="USD", balance=Decimal("1000.00"), is_active=True
    )
    obligation = Obligation(
        id=uuid.uuid4(), user_id=user_id, currency="USD", status="active", amount_type="fixed"
    )

    # Client passes malicious source_amount=1 when paying 500 USD: backend debits 500
    res = await resolve_settlement(
        session=session,
        user_id=user_id,
        obligation=obligation,
        account=account,
        requested_amount=Decimal("500"),
        requested_source_amount=Decimal("1"),
    )
    assert res.source_amount == Decimal("500")
    assert res.applied_amount == Decimal("500")

    # Sub-cent precision (1.001 USD) rejected with 422 invalid_amount_precision
    with pytest.raises(HTTPException) as exc:
        await resolve_settlement(
            session=session,
            user_id=user_id,
            obligation=obligation,
            account=account,
            requested_amount=Decimal("1.001"),
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "invalid_amount_precision"


# ==============================================================================
# 6. CONVERTED DEBIT ROUNDING TO ZERO NEVER INVENTS 1
# ==============================================================================


@pytest.mark.asyncio
async def test_converted_debit_rounding_to_zero_rejected_below_minimum_amount():
    session = AsyncMock()
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="USD", balance=Decimal("1000.00"), is_active=True
    )
    obligation = Obligation(
        id=uuid.uuid4(), user_id=user_id, currency="COP", status="active", amount_type="fixed"
    )

    snapshot = ExchangeRate(
        id=uuid.uuid4(),
        base_currency="USD",
        quote_currency="COP",
        rate=Decimal("4000.00"),
        provider="test",
        fetched_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=snapshot)))
    )

    # 1 COP / 4000 = 0.00025 USD, rounds to 0.00 USD. MUST NOT invent 1 USD. Must raise 422 below_minimum_amount
    with pytest.raises(HTTPException) as exc:
        await resolve_settlement(
            session=session,
            user_id=user_id,
            obligation=obligation,
            account=account,
            requested_amount=Decimal("1"),
            rate_snapshot_id=snapshot.id,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "below_minimum_amount"


# ==============================================================================
# 7. AUTHORITATIVE APPLIED AMOUNT FOR FIFO ALLOCATIONS & RECONCILED SLICES
# ==============================================================================


@pytest.mark.asyncio
async def test_fifo_authoritative_applied_amount_allocation_and_slice_sum():
    p1 = ObligationPeriod(id=uuid.uuid4(), amount=Decimal("100.00"), paid_amount=Decimal("0.00"))
    p2 = ObligationPeriod(id=uuid.uuid4(), amount=Decimal("100.00"), paid_amount=Decimal("0.00"))
    allocations = [(p1, Decimal("0.00"), Decimal("50.00")), (p2, Decimal("0.00"), Decimal("50.00"))]

    slices = reconcile_fifo_source_allocations(
        total_source_amount=Decimal("25.00"),
        allocations=allocations,
    )
    assert slices == [Decimal("12.50"), Decimal("12.50")]
    assert sum(slices) == Decimal("25.00")
    assert all(s > 0 for s in slices)
    assert all(s == s.quantize(Decimal("0.01")) for s in slices)

    # Rejection if total source amount cannot support positive slices
    alloc_tiny = [(p1, Decimal("0"), Decimal("0.01")), (p2, Decimal("0"), Decimal("0.01"))]
    with pytest.raises(HTTPException) as error:
        reconcile_fifo_source_allocations(
            total_source_amount=Decimal("0.01"),
            allocations=alloc_tiny,
        )
    assert error.value.status_code == 422
    assert error.value.detail == "cannot_support_positive_slices"
    thirds = [(p1, Decimal("0"), Decimal("0.01"))] * 3
    assert reconcile_fifo_source_allocations(Decimal("100"), thirds) == [
        Decimal("33.33"),
        Decimal("33.33"),
        Decimal("33.34"),
    ]


# ==============================================================================
# 8. PERSISTED QUOTE INTERNAL CONSISTENCY VALIDATION
# ==============================================================================


@pytest.mark.asyncio
async def test_persisted_quote_internal_consistency_rejected():
    session = AsyncMock()
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="USD", balance=Decimal("1000.00"), is_active=True
    )
    obligation = Obligation(
        id=uuid.uuid4(), user_id=user_id, currency="COP", status="active", amount_type="fixed"
    )

    # Inconsistent: 1 USD * 4000 rate != 500 COP target
    quote = FXQuote(
        id=uuid.uuid4(),
        user_id=user_id,
        from_currency="USD",
        to_currency="COP",
        rate=Decimal("4000.00"),
        source_amount=Decimal("1.00"),
        target_amount=Decimal("500.00"),
        provider="synthetic",
        rate_timestamp=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        status="active",
    )
    session.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=quote)))
    )

    with pytest.raises(HTTPException) as exc:
        await resolve_settlement(
            session=session,
            user_id=user_id,
            obligation=obligation,
            account=account,
            requested_amount=Decimal("500.00"),
            quote_id=quote.id,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "invalid_fx_quote"


# ==============================================================================
# 9. METADATA AND FINGERPRINT PERSISTED TO LEDGER EVENT
# ==============================================================================


@pytest.mark.asyncio
async def test_metadata_and_fingerprint_persisted_to_ledger():
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="COP", balance=Decimal("1000.00"), is_active=True
    )
    obligation = Obligation(
        id=uuid.uuid4(),
        user_id=user_id,
        currency="COP",
        status="active",
        amount_type="fixed",
        name="Office Rent",
    )
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100"),
        paid_amount=Decimal("0"),
        status=PeriodStatus.pending_payment.value,
    )

    service, _ = create_mock_v17_service(user_id, account, obligation, [period])

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("100"),
        source_account_id=account.id,
        idempotency_key="key-traceability-1",
    )
    await service.pay_specific_period(user_id, obligation.id, period.id, req)

    service.ledger_service.record_event.assert_called_once()
    event_arg: LedgerEventCreate = service.ledger_service.record_event.call_args[0][0]
    assert event_arg.metadata is not None
    assert "fingerprint" in event_arg.metadata
    assert event_arg.metadata["fingerprint"]["strategy"] == "specific_period"
    assert event_arg.metadata["fingerprint"]["source_account_id"] == str(account.id)
    assert event_arg.metadata["fingerprint"]["amount"] == "100"


# ==============================================================================
# 10. REPLAY AFTER CHANGED AVAILABILITY AND EXPIRED FX RETURNS WINNER
# ==============================================================================


@pytest.mark.asyncio
async def test_replay_after_changed_availability_and_expired_fx_returns_winner_without_debit():
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="COP", balance=Decimal("0.00"), is_active=True
    )  # balance is now 0!
    obligation = Obligation(
        id=uuid.uuid4(), user_id=user_id, currency="COP", status="completed", amount_type="fixed"
    )  # now completed!
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100"),
        paid_amount=Decimal("100"),
        status=PeriodStatus.paid.value,
    )

    winner_payment = ObligationPayment(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        obligation_period_id=period.id,
        user_id=user_id,
        account_id=account.id,
        idempotency_key="key-winner-replay",
        amount=Decimal("100"),
        currency="COP",
        source_amount=Decimal("100"),
        source_currency="COP",
    )
    winner_event = FinancialEvent(
        id=uuid.uuid4(),
        user_id=user_id,
        metadata_={
            "fingerprint": build_idempotency_fingerprint(
                obligation_id=obligation.id,
                period_id=period.id,
                strategy="specific_period",
                source_account_id=account.id,
                amount=Decimal("100"),
                currency="COP",
                source_currency="COP",
            )
        },
    )

    service, _ = create_mock_v17_service(user_id, account, obligation, [period])

    # Replay queries return winner payment and event
    service.session.execute.side_effect = lambda stmt, *a, **kw: (
        MagicMock(
            scalars=MagicMock(
                return_value=MagicMock(
                    first=MagicMock(return_value=winner_payment),
                    all=MagicMock(return_value=[winner_payment]),
                )
            )
        )
        if "from obligation_payments" in str(stmt).lower()
        else (
            MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(first=MagicMock(return_value=winner_event))
                )
            )
            if "from financial_events" in str(stmt).lower()
            else (
                MagicMock(
                    scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=period)))
                )
                if "from obligation_periods" in str(stmt).lower()
                else MagicMock(
                    scalars=MagicMock(
                        return_value=MagicMock(first=MagicMock(return_value=obligation))
                    )
                )
            )
        )
    )

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("100"),
        source_account_id=account.id,
        idempotency_key="key-winner-replay",
    )
    # Replay succeeds even though balance is 0 and obligation is completed!
    payment, rep_period = await service.pay_specific_period(user_id, obligation.id, period.id, req)
    assert payment.id == winner_payment.id
    service.account_repo.update_balance.assert_not_called()
    service.ledger_service.record_event.assert_not_called()


# ==============================================================================
# 11. RECHECK REPLAY AFTER ROOT WAIT BEFORE TERMINAL LIFECYCLE CHECK
# ==============================================================================


@pytest.mark.asyncio
async def test_recheck_replay_after_root_wait_before_terminal_lifecycle_rejection():
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="COP", balance=Decimal("1000.00"), is_active=True
    )
    # Obligation became completed during lock wait
    obligation = Obligation(
        id=uuid.uuid4(), user_id=user_id, currency="COP", status="completed", amount_type="fixed"
    )
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100"),
        paid_amount=Decimal("100"),
        status=PeriodStatus.paid.value,
    )

    winner_payment = ObligationPayment(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        obligation_period_id=period.id,
        user_id=user_id,
        account_id=account.id,
        idempotency_key="key-concurrent-complete",
        amount=Decimal("100"),
        currency="COP",
        source_amount=Decimal("100"),
        source_currency="COP",
    )

    service, _ = create_mock_v17_service(user_id, account, obligation, [period])

    call_count = 0

    def mock_exec(stmt, *a, **kw):
        nonlocal call_count
        stmt_str = str(stmt).lower()
        if "from obligation_payments" in stmt_str:
            call_count += 1
            if call_count == 1:
                # Early check misses winner (concurrent attempt still running)
                return MagicMock(
                    scalars=MagicMock(
                        return_value=MagicMock(
                            first=MagicMock(return_value=None), all=MagicMock(return_value=[])
                        )
                    )
                )
            # Under root lock, winner is found
            return MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(
                        first=MagicMock(return_value=winner_payment),
                        all=MagicMock(return_value=[winner_payment]),
                    )
                )
            )
        if "from obligations" in stmt_str:
            return MagicMock(
                scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=obligation)))
            )
        if "from obligation_periods" in stmt_str:
            return MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(
                        first=MagicMock(return_value=period), all=MagicMock(return_value=[period])
                    )
                )
            )
        return MagicMock(
            scalars=MagicMock(
                return_value=MagicMock(
                    first=MagicMock(return_value=None), all=MagicMock(return_value=[])
                )
            )
        )

    service.session.execute.side_effect = mock_exec

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("100"),
        source_account_id=account.id,
        idempotency_key="key-concurrent-complete",
    )
    # Must succeed via root replay re-check BEFORE throwing 400 obligation_completed!
    payment, _ = await service.pay_specific_period(user_id, obligation.id, period.id, req)
    assert payment.id == winner_payment.id


# ==============================================================================
# 12. LITERAL WILDCARD KEYS (% AND _) AND MATERIAL MISMATCH REJECTION
# ==============================================================================


@pytest.mark.asyncio
async def test_literal_wildcard_keys_do_not_bleed_and_material_mismatch_conflicts():
    user_id = uuid.uuid4()
    account_a = Account(
        id=uuid.uuid4(), user_id=user_id, currency="COP", balance=Decimal("1000.00"), is_active=True
    )
    account_b = Account(
        id=uuid.uuid4(), user_id=user_id, currency="COP", balance=Decimal("1000.00"), is_active=True
    )
    obligation = Obligation(
        id=uuid.uuid4(), user_id=user_id, currency="COP", status="active", amount_type="fixed"
    )
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100"),
        paid_amount=Decimal("0"),
        status=PeriodStatus.pending_payment.value,
    )

    winner_payment = ObligationPayment(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        obligation_period_id=period.id,
        user_id=user_id,
        account_id=account_a.id,
        idempotency_key="key%test_1",
        amount=Decimal("100"),
        currency="COP",
        source_amount=Decimal("100"),
        source_currency="COP",
    )

    # Validate that fingerprint comparison with different account raises 409
    fp = build_idempotency_fingerprint(
        obligation_id=obligation.id,
        period_id=period.id,
        strategy="specific_period",
        source_account_id=account_a.id,
        amount=Decimal("100"),
        currency="COP",
        source_currency="COP",
    )

    # Same key "key%test_1" with different account MUST 409
    with pytest.raises(HTTPException) as exc:
        validate_idempotency_fingerprint(
            fingerprint=fp,
            obligation_id=obligation.id,
            period_id=period.id,
            strategy="specific_period",
            source_account_id=account_b.id,  # different account!
            amount=Decimal("100"),
            currency="COP",
            quote_id=None,
            rate_snapshot_id=None,
            payment_date=None,
            existing_payments=[winner_payment],
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "idempotency_conflict"

    # Same key with different amount (Decimal 100 vs 200) MUST 409
    with pytest.raises(HTTPException) as exc:
        validate_idempotency_fingerprint(
            fingerprint=fp,
            obligation_id=obligation.id,
            period_id=period.id,
            strategy="specific_period",
            source_account_id=account_a.id,
            amount=Decimal("200"),  # different amount!
            currency="COP",
            quote_id=None,
            rate_snapshot_id=None,
            payment_date=None,
            existing_payments=[winner_payment],
        )
    assert exc.value.status_code == 409

    # Numeric equivalence (100 == 100.00) MUST pass
    validate_idempotency_fingerprint(
        fingerprint=fp,
        obligation_id=obligation.id,
        period_id=period.id,
        strategy="specific_period",
        source_account_id=account_a.id,
        amount=Decimal("100.00"),  # 100.00 == 100 numerically
        currency="COP",
        quote_id=None,
        rate_snapshot_id=None,
        payment_date=None,
        existing_payments=[winner_payment],
    )


# ==============================================================================
# 13. INCOMPLETE RECEIPT ON LEDGER UNIQUENESS COLLISION CONFLICTS (NO FALLTHROUGH)
# ==============================================================================


@pytest.mark.asyncio
async def test_incomplete_receipt_on_ledger_collision_raises_409_without_fallthrough():
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="COP", balance=Decimal("1000.00"), is_active=True
    )
    obligation = Obligation(
        id=uuid.uuid4(), user_id=user_id, currency="COP", status="active", amount_type="fixed"
    )
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100"),
        paid_amount=Decimal("0"),
        status=PeriodStatus.pending_payment.value,
    )

    service, _ = create_mock_v17_service(user_id, account, obligation, [period])

    # Ledger reports collision (idempotent=True), but winner payments are absent from database
    collision_result = MagicMock(
        idempotent=True,
        event=MagicMock(
            id=uuid.uuid4(),
            user_id=user_id,
            account_id=account.id,
            metadata={},
        ),
    )
    service.ledger_service.record_event = AsyncMock(return_value=collision_result)

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("100"),
        source_account_id=account.id,
        idempotency_key="key-incomplete-receipt",
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(user_id, obligation.id, period.id, req)
    assert exc.value.status_code == 409
    assert exc.value.detail == "idempotency_conflict"

    # Propagate conflict for outer rollback without creating another financial mutation.
    service.account_repo.update_balance.assert_awaited_once_with(account, Decimal("-100"))
    service.session.add.assert_not_called()


# ==============================================================================
# 14. ROUTER UOW TRANSACTION OWNERSHIP: SERVICE FLUSHES BUT NEVER COMMITS
# ==============================================================================


@pytest.mark.asyncio
async def test_service_payment_primitive_flushes_never_commits():
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="COP", balance=Decimal("1000.00"), is_active=True
    )
    obligation = Obligation(
        id=uuid.uuid4(), user_id=user_id, currency="COP", status="active", amount_type="fixed"
    )
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100"),
        paid_amount=Decimal("0"),
        status=PeriodStatus.pending_payment.value,
    )

    service, session = create_mock_v17_service(user_id, account, obligation, [period])

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("100"),
        source_account_id=account.id,
        idempotency_key="key-flush-only",
    )
    await service.pay_specific_period(user_id, obligation.id, period.id, req)

    # Critical invariant: service payment primitive must FLUSH, never COMMIT
    session.flush.assert_called()
    session.commit.assert_not_called()


# ==============================================================================
# 15. GOAL RESERVATION INCONSISTENCY REJECTION (NO SPENDABLE INFLATION)
# ==============================================================================


@pytest.mark.asyncio
async def test_negative_goal_reservation_rejected_to_prevent_spendable_inflation():
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="COP", balance=Decimal("1000.00"), is_active=True
    )
    obligation = Obligation(
        id=uuid.uuid4(), user_id=user_id, currency="COP", status="active", amount_type="fixed"
    )
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100"),
        paid_amount=Decimal("0"),
        status=PeriodStatus.pending_payment.value,
    )

    service, _ = create_mock_v17_service(user_id, account, obligation, [period])

    # Goal transactions aggregate returns negative reservation: -500.00
    service.session.execute.side_effect = lambda stmt, *a, **kw: (
        MagicMock(scalar_one_or_none=MagicMock(return_value=Decimal("-500.00")))
        if "goal_transactions" in str(stmt).lower()
        else (
            MagicMock(
                scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=obligation)))
            )
            if "from obligations" in str(stmt).lower()
            else (
                MagicMock(
                    scalars=MagicMock(
                        return_value=MagicMock(
                            first=MagicMock(return_value=period),
                            all=MagicMock(return_value=[period]),
                        )
                    )
                )
                if "from obligation_periods" in str(stmt).lower()
                else (
                    MagicMock(
                        scalars=MagicMock(
                            return_value=MagicMock(first=MagicMock(return_value=account))
                        )
                    )
                    if "from accounts" in str(stmt).lower()
                    else MagicMock(
                        scalars=MagicMock(
                            return_value=MagicMock(
                                first=MagicMock(return_value=None), all=MagicMock(return_value=[])
                            )
                        )
                    )
                )
            )
        )
    )

    req = ObligationPeriodPaymentCreateRequest(
        amount=Decimal("100"),
        source_account_id=account.id,
        idempotency_key="key-neg-reservation",
    )
    with pytest.raises(HTTPException) as exc:
        await service.pay_specific_period(user_id, obligation.id, period.id, req)
    assert exc.value.status_code == 409
    assert exc.value.detail == "inconsistent_reservation_state"
    # Account debit must not happen
    service.account_repo.update_balance.assert_not_called()


@pytest.mark.asyncio
async def test_fifo_keeps_unique_allocation_keys_under_one_ledger_event():
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="USD", balance=Decimal("1000"), is_active=True
    )
    obligation = Obligation(id=uuid.uuid4(), user_id=user_id, currency="USD", status="active")
    periods = [
        ObligationPeriod(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            amount=Decimal("100"),
            paid_amount=Decimal("0"),
            status="pending_payment",
        )
        for _ in range(2)
    ]
    service, _ = create_mock_v17_service(user_id, account, obligation, periods)
    payments, _ = await service.pay_obligation_fifo(
        user_id,
        obligation.id,
        ObligationFIFOPaymentCreateRequest(
            amount=Decimal("200"), source_account_id=account.id, idempotency_key="literal%_group"
        ),
    )
    assert len(payments) == len({payment.idempotency_key for payment in payments}) == 2
    assert len({payment.financial_event_id for payment in payments}) == 1
    assert sum(payment.source_amount for payment in payments) == Decimal("200")
    service.ledger_service.record_event.assert_awaited_once()


@pytest.mark.parametrize(
    "source_currency,target_currency,source,target,rate,accepted",
    [
        ("USD", "COP", "0.13", "501", "4000", True),
        ("COP", "USD", "39", "0.01", "0.00025", True),
        ("USD", "COP", "0.01", "1", "4000", False),
    ],
)
@pytest.mark.asyncio
async def test_quote_consistency_respects_currency_rounding(
    source_currency, target_currency, source, target, rate, accepted
):
    user_id = uuid.uuid4()
    quote = FXQuote(
        id=uuid.uuid4(),
        user_id=user_id,
        from_currency=source_currency,
        to_currency=target_currency,
        source_amount=Decimal(source),
        target_amount=Decimal(target),
        rate=Decimal(rate),
        provider="synthetic",
        rate_timestamp=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
        status="active",
    )
    session = AsyncMock()
    session.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=quote)))
    )
    call = resolve_settlement(
        session,
        user_id,
        Obligation(currency=target_currency),
        Account(currency=source_currency),
        requested_amount=Decimal(target),
        quote_id=quote.id,
    )
    if accepted:
        result = await call
        assert result.source_amount == Decimal(source)
        assert result.applied_amount == Decimal(target)
    else:
        with pytest.raises(HTTPException) as error:
            await call
        assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_same_currency_cop_keeps_exact_cents_instead_of_fx_cash_rounding():
    result = await resolve_settlement(
        AsyncMock(),
        uuid.uuid4(),
        Obligation(currency="COP"),
        Account(currency="COP"),
        requested_amount=Decimal("501.25"),
        requested_source_amount=Decimal("1"),
    )
    assert result.source_amount == result.applied_amount == Decimal("501.25")


@pytest.mark.parametrize("balance", [None, Decimal("NaN"), Decimal("-1")])
@pytest.mark.asyncio
async def test_unavailable_account_balance_never_creates_financial_effects(balance):
    user_id = uuid.uuid4()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, currency="USD", balance=balance, is_active=True
    )
    obligation = Obligation(id=uuid.uuid4(), user_id=user_id, currency="USD", status="active")
    period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        amount=Decimal("100"),
        paid_amount=Decimal("0"),
        status="pending_payment",
    )
    service, _ = create_mock_v17_service(user_id, account, obligation, [period])
    with pytest.raises(HTTPException) as error:
        await service.pay_specific_period(
            user_id,
            obligation.id,
            period.id,
            ObligationPeriodPaymentCreateRequest(
                amount=Decimal("100"), source_account_id=account.id, idempotency_key="unavailable"
            ),
        )
    assert error.value.detail == "account_unavailable"
    service.account_repo.update_balance.assert_not_awaited()
    service.ledger_service.record_event.assert_not_awaited()
