import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.accounts.models import Account
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.uow import UnitOfWork
from app.ledger.enums import Direction
from app.obligations.models import ExchangeRate
from app.transfers.models import Transfer
from app.transfers.repository import TransfersRepository
from app.transfers.schemas import TransferCreate
from app.transfers.service import TransfersService


def make_account(user_id: uuid.UUID, currency: str = "COP", balance: str = "1000.00") -> Account:
    return Account(
        id=uuid.uuid4(),
        user_id=user_id,
        name="Test Account",
        type="bank",
        currency=currency,
        balance=Decimal(balance),
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def make_exchange_rate(
    base: str,
    quote: str,
    rate: str = "4000.0",
    expired: bool = False,
    *,
    is_stale: bool | None = False,
    provider: str = "test",
) -> ExchangeRate:
    return ExchangeRate(
        id=uuid.uuid4(),
        base_currency=base,
        quote_currency=quote,
        rate=Decimal(rate),
        provider=provider,
        fetched_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=-1 if expired else 1),
        is_stale=is_stale,
    )


@pytest.fixture
def uow_mock() -> AsyncMock:
    uow = AsyncMock(spec=UnitOfWork)
    uow.transaction.return_value.__aenter__.return_value = None
    uow.transaction.return_value.__aexit__.return_value = None
    uow.session = AsyncMock()
    return uow


@pytest.fixture
def transfers_repo_mock() -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_command_id.return_value = None

    def mock_create(t):
        if not getattr(t, "id", None):
            t.id = uuid.uuid4()
        if not getattr(t, "created_at", None):
            t.created_at = datetime.now(UTC)
        return t

    repo.create_transfer.side_effect = mock_create
    return repo


@pytest.fixture
def ledger_repo_mock() -> AsyncMock:
    repo = AsyncMock()
    repo.insert_event.side_effect = lambda e: MagicMock(event=MagicMock(id=uuid.uuid4()))
    return repo


@pytest.fixture
def account_repo_mock() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def service(uow_mock, transfers_repo_mock, ledger_repo_mock, account_repo_mock) -> TransfersService:
    return TransfersService(
        uow=uow_mock,
        transfers_repo=transfers_repo_mock,
        ledger_repo=ledger_repo_mock,
        account_repo=account_repo_mock,
    )


def configure_accounts(account_repo: AsyncMock, source: Account, destination: Account) -> None:
    async def get_by_id_for_update(account_id: uuid.UUID, **kwargs):
        if account_id == source.id:
            return source
        if account_id == destination.id:
            return destination
        return None

    account_repo.get_by_id_for_update.side_effect = get_by_id_for_update


def configure_fx(repo: AsyncMock, snapshot: ExchangeRate | None) -> None:
    repo.get_rate_snapshot_for_update.return_value = snapshot


@pytest.mark.asyncio
async def test_cross_currency_successful(
    service: TransfersService,
    uow_mock: AsyncMock,
    account_repo_mock: AsyncMock,
    transfers_repo_mock: AsyncMock,
    ledger_repo_mock: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id, currency="USD", balance="100.00")
    destination = make_account(user_id, currency="COP", balance="50000.00")
    configure_accounts(account_repo_mock, source, destination)

    snapshot = make_exchange_rate(base="USD", quote="COP", rate="4000.12345678")
    configure_fx(transfers_repo_mock, snapshot)

    command_id = uuid.uuid4()
    payload = TransferCreate(
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount=Decimal("10.00"),
        rate_snapshot_id=snapshot.id,
        command_id=command_id,
    )

    result = await service.create_transfer(user_id, payload)

    assert result.amount == Decimal("10.00")
    assert result.currency == "USD"
    assert result.target_amount == Decimal("40001.23")
    assert result.target_currency == "COP"
    assert result.fx_rate == Decimal("4000.12345678")
    assert result.rate_snapshot_id == snapshot.id

    # Verify ledger events
    assert ledger_repo_mock.insert_event.call_count == 2
    out_event = ledger_repo_mock.insert_event.call_args_list[0][0][0]
    in_event = ledger_repo_mock.insert_event.call_args_list[1][0][0]

    assert out_event.amount == Decimal("10.00")
    assert out_event.currency == "USD"
    assert out_event.direction == Direction.OUTFLOW

    assert in_event.amount == Decimal("40001.23")
    assert in_event.currency == "COP"
    assert in_event.direction == Direction.INFLOW

    persisted = transfers_repo_mock.create_transfer.call_args.args[0]
    assert persisted.fx_rate == Decimal("4000.12345678")
    assert persisted.rate_source == "test"
    assert persisted.rate_timestamp == snapshot.fetched_at
    assert persisted.rate_snapshot_id == snapshot.id
    assert persisted.is_estimated is False
    transfers_repo_mock.get_rate_snapshot_for_update.assert_awaited_once_with(snapshot.id)


@pytest.mark.asyncio
async def test_cross_currency_invalid_snapshot(
    service: TransfersService,
    account_repo_mock: AsyncMock,
    transfers_repo_mock: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id, currency="USD", balance="100.00")
    destination = make_account(user_id, currency="COP", balance="50000.00")
    configure_accounts(account_repo_mock, source, destination)

    configure_fx(transfers_repo_mock, None)  # Not found

    with pytest.raises(NotFoundError, match="invalid_fx_rate_snapshot") as exc:
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount=Decimal("10.00"),
                rate_snapshot_id=uuid.uuid4(),
                command_id=uuid.uuid4(),
            ),
        )
    assert exc.value.error_code == "invalid_fx_rate_snapshot"


@pytest.mark.asyncio
async def test_cross_currency_expired_snapshot(
    service: TransfersService,
    account_repo_mock: AsyncMock,
    transfers_repo_mock: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id, currency="USD")
    destination = make_account(user_id, currency="COP")
    configure_accounts(account_repo_mock, source, destination)

    snapshot = make_exchange_rate(base="USD", quote="COP", expired=True)
    configure_fx(transfers_repo_mock, snapshot)

    with pytest.raises(ValidationError, match="fx_rate_snapshot_expired") as exc:
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount=Decimal("10.00"),
                rate_snapshot_id=snapshot.id,
                command_id=uuid.uuid4(),
            ),
        )
    assert exc.value.error_code == "fx_rate_snapshot_expired"


@pytest.mark.asyncio
async def test_cross_currency_pair_mismatch(
    service: TransfersService,
    account_repo_mock: AsyncMock,
    transfers_repo_mock: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id, currency="USD")
    destination = make_account(user_id, currency="COP")
    configure_accounts(account_repo_mock, source, destination)

    # Snapshot is COP -> USD (inverted)
    snapshot = make_exchange_rate(base="COP", quote="USD")
    configure_fx(transfers_repo_mock, snapshot)

    with pytest.raises(ValidationError, match="currency_pair_mismatch") as exc:
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount=Decimal("10.00"),
                rate_snapshot_id=snapshot.id,
                command_id=uuid.uuid4(),
            ),
        )
    assert exc.value.error_code == "currency_pair_mismatch"


@pytest.mark.asyncio
async def test_cross_currency_invalid_rate(
    service: TransfersService,
    account_repo_mock: AsyncMock,
    transfers_repo_mock: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id, currency="USD")
    destination = make_account(user_id, currency="COP")
    configure_accounts(account_repo_mock, source, destination)

    snapshot = make_exchange_rate(base="USD", quote="COP", rate="-100")
    configure_fx(transfers_repo_mock, snapshot)

    with pytest.raises(ValidationError, match="invalid_fx_rate") as exc:
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount=Decimal("10.00"),
                rate_snapshot_id=snapshot.id,
                command_id=uuid.uuid4(),
            ),
        )
    assert exc.value.error_code == "invalid_fx_rate"


@pytest.mark.asyncio
async def test_cross_currency_overflow(
    service: TransfersService,
    account_repo_mock: AsyncMock,
    transfers_repo_mock: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id, currency="USD", balance="90000000000.00")
    destination = make_account(user_id, currency="COP")
    configure_accounts(account_repo_mock, source, destination)

    snapshot = make_exchange_rate(base="USD", quote="COP", rate="4000")
    configure_fx(transfers_repo_mock, snapshot)

    with pytest.raises(ValidationError) as exc:
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount=Decimal("90000000000.00"),  # 90B * 4000 = 360 Trillion COP (> 14,2 bounds)
                rate_snapshot_id=snapshot.id,
                command_id=uuid.uuid4(),
            ),
        )
    assert exc.value.error_code == "cross_currency_overflow"


@pytest.mark.asyncio
async def test_same_currency_rejects_snapshot(
    service: TransfersService,
    uow_mock: AsyncMock,
    account_repo_mock: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id, currency="COP")
    destination = make_account(user_id, currency="COP")
    configure_accounts(account_repo_mock, source, destination)

    with pytest.raises(ValidationError, match="no acepta snapshot FX") as exc:
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount=Decimal("10.00"),
                rate_snapshot_id=uuid.uuid4(),
                command_id=uuid.uuid4(),
            ),
        )
    assert exc.value.error_code == "fx_rate_snapshot_not_allowed"


@pytest.mark.asyncio
async def test_cop_to_usd_uses_exact_snapshot_orientation(
    service: TransfersService,
    account_repo_mock: AsyncMock,
    transfers_repo_mock: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id, currency="COP", balance="4000.00")
    destination = make_account(user_id, currency="USD", balance="0.00")
    configure_accounts(account_repo_mock, source, destination)
    snapshot = make_exchange_rate(base="COP", quote="USD", rate="0.00025000")
    configure_fx(transfers_repo_mock, snapshot)

    result = await service.create_transfer(
        user_id,
        TransferCreate(
            source_account_id=source.id,
            destination_account_id=destination.id,
            amount=Decimal("4000.00"),
            rate_snapshot_id=snapshot.id,
            command_id=uuid.uuid4(),
        ),
    )

    assert result.target_amount == Decimal("1.00")
    assert result.target_currency == "USD"


@pytest.mark.parametrize(
    ("amount", "rate", "expected"),
    [
        ("1.00", "1.00500000", "1.01"),
        ("999999999999.99", "1.00000000", "999999999999.99"),
    ],
)
def test_target_amount_rounding_and_numeric_boundary(amount: str, rate: str, expected: str) -> None:
    assert TransfersService._calculate_target_amount(Decimal(amount), Decimal(rate)) == Decimal(
        expected
    )


@pytest.mark.parametrize(
    ("amount", "rate", "error_code"),
    [
        ("0.01", "0.01000000", "cross_currency_underflow"),
        ("999999999999.99", "1.00000001", "cross_currency_overflow"),
    ],
)
def test_target_amount_rejects_underflow_and_real_overflow(
    amount: str, rate: str, error_code: str
) -> None:
    with pytest.raises(ValidationError) as exc:
        TransfersService._calculate_target_amount(Decimal(amount), Decimal(rate))
    assert exc.value.error_code == error_code


@pytest.mark.parametrize("rate", ["0", "-0.01", "NaN", "Infinity"])
@pytest.mark.asyncio
async def test_cross_currency_rejects_non_positive_or_non_finite_rate(
    rate: str,
    service: TransfersService,
    account_repo_mock: AsyncMock,
    transfers_repo_mock: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id, currency="USD")
    destination = make_account(user_id, currency="COP")
    configure_accounts(account_repo_mock, source, destination)
    snapshot = make_exchange_rate(base="USD", quote="COP", rate=rate)
    configure_fx(transfers_repo_mock, snapshot)

    with pytest.raises(ValidationError) as exc:
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount=Decimal("10.00"),
                rate_snapshot_id=snapshot.id,
                command_id=uuid.uuid4(),
            ),
        )
    assert exc.value.error_code == "invalid_fx_rate"


@pytest.mark.parametrize(
    ("is_stale", "provider", "error_code"),
    [
        (True, "test", "fx_rate_snapshot_stale"),
        (None, "test", "fx_rate_snapshot_stale"),
        (False, "   ", "invalid_fx_rate_snapshot"),
    ],
)
@pytest.mark.asyncio
async def test_cross_currency_rejects_invalid_snapshot_state_or_source(
    is_stale: bool | None,
    provider: str,
    error_code: str,
    service: TransfersService,
    account_repo_mock: AsyncMock,
    transfers_repo_mock: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id, currency="USD")
    destination = make_account(user_id, currency="COP")
    configure_accounts(account_repo_mock, source, destination)
    snapshot = make_exchange_rate(base="USD", quote="COP", is_stale=is_stale, provider=provider)
    configure_fx(transfers_repo_mock, snapshot)

    with pytest.raises(ValidationError) as exc:
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount=Decimal("10.00"),
                rate_snapshot_id=snapshot.id,
                command_id=uuid.uuid4(),
            ),
        )
    assert exc.value.error_code == error_code


@pytest.mark.asyncio
async def test_snapshot_repository_uses_row_lock() -> None:
    session = AsyncMock()
    snapshot = make_exchange_rate(base="USD", quote="COP")
    result = MagicMock()
    result.scalar_one_or_none.return_value = snapshot
    session.execute.return_value = result

    repository = TransfersRepository(session)
    resolved = await repository.get_rate_snapshot_for_update(snapshot.id)

    assert resolved is snapshot
    statement = session.execute.await_args.args[0]
    assert statement._for_update_arg is not None


@pytest.mark.asyncio
async def test_cross_currency_retry_returns_persisted_snapshot_semantics(
    service: TransfersService,
    transfers_repo_mock: AsyncMock,
    account_repo_mock: AsyncMock,
    ledger_repo_mock: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id, currency="USD")
    destination = make_account(user_id, currency="COP")
    snapshot_id = uuid.uuid4()
    command_id = uuid.uuid4()
    transfer = Transfer(
        id=uuid.uuid4(),
        user_id=user_id,
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount=Decimal("10.00"),
        currency="USD",
        target_amount=Decimal("40001.23"),
        target_currency="COP",
        fx_rate=Decimal("4000.12345678"),
        rate_source="test",
        rate_timestamp=datetime.now(UTC),
        rate_snapshot_id=snapshot_id,
        is_estimated=False,
        command_id=command_id,
        status="completed",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    transfer.source_account = source
    transfer.destination_account = destination
    transfers_repo_mock.get_by_command_id.return_value = transfer

    result = await service.create_transfer(
        user_id,
        TransferCreate(
            source_account_id=source.id,
            destination_account_id=destination.id,
            amount=Decimal("10.00"),
            rate_snapshot_id=snapshot_id,
            command_id=command_id,
        ),
    )

    assert result.is_idempotent is True
    assert result.target_amount == Decimal("40001.23")
    assert result.fx_rate == Decimal("4000.12345678")
    transfers_repo_mock.get_rate_snapshot_for_update.assert_not_awaited()
    account_repo_mock.get_by_id_for_update.assert_not_awaited()
    ledger_repo_mock.insert_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_currency_retry_rejects_different_snapshot(
    service: TransfersService,
    transfers_repo_mock: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id, currency="USD")
    destination = make_account(user_id, currency="COP")
    command_id = uuid.uuid4()
    transfer = Transfer(
        id=uuid.uuid4(),
        user_id=user_id,
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount=Decimal("10.00"),
        currency="USD",
        target_amount=Decimal("40000.00"),
        target_currency="COP",
        fx_rate=Decimal("4000.00000000"),
        rate_source="test",
        rate_timestamp=datetime.now(UTC),
        rate_snapshot_id=uuid.uuid4(),
        is_estimated=False,
        command_id=command_id,
        status="completed",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    transfer.source_account = source
    transfer.destination_account = destination
    transfers_repo_mock.get_by_command_id.return_value = transfer

    with pytest.raises(ConflictError) as exc:
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount=Decimal("10.00"),
                rate_snapshot_id=uuid.uuid4(),
                command_id=command_id,
            ),
        )
    assert exc.value.error_code == "idempotency_key_reused"
