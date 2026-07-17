import inspect
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.cash.exceptions import InsufficientFundsError
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.uow import UnitOfWork
from app.ledger.enums import Direction, EventType
from app.ledger.repository import LedgerRepository
from app.main import app
from app.transfers.models import Transfer
from app.transfers.repository import TransfersRepository
from app.transfers.router import get_transfers_service
from app.transfers.schemas import TransferCreate, TransferRequest, TransferResult
from app.transfers.service import TransfersService
from app.users.dependencies import get_current_user_profile_dep
from app.users.schemas import UserRead

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class RecordingUow:
    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0

    @asynccontextmanager
    async def transaction(self):
        self.entered += 1
        try:
            yield self
        finally:
            self.exited += 1


def make_account(
    *,
    account_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    balance: Decimal | None = Decimal("500.00"),
    currency: str = "COP",
    is_active: bool = True,
    name: str = "Cuenta",
) -> Account:
    return Account(
        id=account_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        is_active=is_active,
        currency=currency,
        balance=balance,
        name=name,
        type="bank",
        created_at=NOW,
        updated_at=NOW,
    )


def make_transfer(
    *,
    user_id: uuid.UUID,
    source: Account,
    destination: Account,
    command_id: uuid.UUID,
    amount: Decimal = Decimal("100.00"),
    description: str | None = None,
    source_message_id: uuid.UUID | None = None,
    raw_message: str | None = None,
    created_at: datetime = NOW,
    status: str = "completed",
) -> Transfer:
    transfer = Transfer(
        id=uuid.uuid4(),
        user_id=user_id,
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount=amount,
        currency=source.currency,
        target_amount=amount,
        target_currency=destination.currency,
        fx_rate=None,
        rate_source=None,
        rate_timestamp=None,
        is_estimated=False,
        description=description,
        command_id=command_id,
        source_message_id=source_message_id,
        raw_message=raw_message,
        status=status,
        created_at=created_at,
        updated_at=created_at,
    )
    transfer.source_account = source
    transfer.destination_account = destination
    return transfer


def make_ledger_result() -> SimpleNamespace:
    return SimpleNamespace(event=SimpleNamespace(id=uuid.uuid4()), idempotent=False)


@pytest.fixture
def transfers_repo() -> AsyncMock:
    return AsyncMock(spec=TransfersRepository)


@pytest.fixture
def ledger_repo() -> AsyncMock:
    repo = AsyncMock(spec=LedgerRepository)
    repo.insert_event.side_effect = lambda _event: make_ledger_result()
    return repo


@pytest.fixture
def account_repo() -> AsyncMock:
    return AsyncMock(spec=AccountRepository)


@pytest.fixture
def uow() -> RecordingUow:
    return RecordingUow()


@pytest.fixture
def service(
    uow: RecordingUow,
    transfers_repo: AsyncMock,
    ledger_repo: AsyncMock,
    account_repo: AsyncMock,
) -> TransfersService:
    return TransfersService(uow, transfers_repo, ledger_repo, account_repo)  # type: ignore[arg-type]


def configure_new_transfer(
    transfers_repo: AsyncMock,
    account_repo: AsyncMock,
    source: Account,
    destination: Account,
) -> None:
    transfers_repo.get_by_command_id.return_value = None
    accounts = {source.id: source, destination.id: destination}

    async def get_locked(account_id: uuid.UUID, *, include_inactive: bool = False):
        assert include_inactive is True
        return accounts.get(account_id)

    async def create(transfer: Transfer) -> Transfer:
        transfer.id = uuid.uuid4()
        transfer.created_at = transfer.created_at or NOW
        transfer.updated_at = NOW
        transfer.source_account = source
        transfer.destination_account = destination
        return transfer

    account_repo.get_by_id_for_update.side_effect = get_locked
    transfers_repo.create_transfer.side_effect = create


@pytest.mark.asyncio
async def test_create_transfer_persists_exact_same_currency_effects(
    service: TransfersService,
    transfers_repo: AsyncMock,
    ledger_repo: AsyncMock,
    account_repo: AsyncMock,
    uow: RecordingUow,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id=user_id, balance=Decimal("500.00"), name="Origen")
    destination = make_account(user_id=user_id, balance=Decimal("25.00"), name="Destino")
    payload = TransferCreate(
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount="100.00",
        command_id=uuid.uuid4(),
        description="  Ahorro  ",
    )
    configure_new_transfer(transfers_repo, account_repo, source, destination)

    result = await service.create_transfer(user_id, payload)

    assert result.amount == result.target_amount == Decimal("100.00")
    assert result.currency == result.target_currency == "COP"
    assert result.fx_rate is None
    assert result.rate_source is None
    assert result.rate_timestamp is None
    assert result.is_estimated is False
    assert result.description == "Ahorro"
    assert result.status == "completed"
    assert result.is_idempotent is False
    assert result.ledger_events is not None
    assert uow.entered == uow.exited == 1

    saved = transfers_repo.create_transfer.await_args.args[0]
    assert saved.user_id == user_id
    assert saved.command_id == payload.command_id
    assert saved.target_amount == saved.amount
    assert saved.target_currency == saved.currency == "COP"
    assert saved.status == "completed"

    account_repo.update_balance.assert_has_awaits(
        [call(source, Decimal("-100.00")), call(destination, Decimal("100.00"))]
    )
    assert ledger_repo.insert_event.await_count == 2
    out_event, in_event = [item.args[0] for item in ledger_repo.insert_event.await_args_list]
    assert (out_event.event_type, out_event.direction) == (
        EventType.TRANSFER_OUT,
        Direction.OUTFLOW,
    )
    assert (in_event.event_type, in_event.direction) == (
        EventType.TRANSFER_IN,
        Direction.INFLOW,
    )
    assert out_event.account_id == source.id
    assert in_event.account_id == destination.id
    assert out_event.amount == in_event.amount == Decimal("100.00")
    assert out_event.currency == in_event.currency == "COP"
    assert out_event.transfer_id == in_event.transfer_id == saved.id
    assert out_event.category_id is in_event.category_id is None
    assert out_event.command_id is in_event.command_id is None


@pytest.mark.asyncio
async def test_accounts_are_locked_in_deterministic_uuid_order(
    service: TransfersService,
    transfers_repo: AsyncMock,
    account_repo: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    low_id = uuid.UUID(int=1)
    high_id = uuid.UUID(int=2)
    source = make_account(account_id=high_id, user_id=user_id)
    destination = make_account(account_id=low_id, user_id=user_id)
    configure_new_transfer(transfers_repo, account_repo, source, destination)

    await service.create_transfer(
        user_id,
        TransferCreate(
            source_account_id=high_id,
            destination_account_id=low_id,
            amount="1.00",
            command_id=uuid.uuid4(),
        ),
    )

    assert account_repo.get_by_id_for_update.await_args_list == [
        call(low_id, include_inactive=True),
        call(high_id, include_inactive=True),
    ]


@pytest.mark.parametrize(
    ("source_currency", "destination_currency"),
    [("USD", "COP"), ("COP", "USD")],
)
@pytest.mark.asyncio
async def test_cross_currency_is_rejected_without_fx(
    service: TransfersService,
    transfers_repo: AsyncMock,
    account_repo: AsyncMock,
    source_currency: str,
    destination_currency: str,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id=user_id, currency=source_currency)
    destination = make_account(user_id=user_id, currency=destination_currency)
    configure_new_transfer(transfers_repo, account_repo, source, destination)

    with pytest.raises(ValidationError, match="monedas diferentes") as exc:
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount="100.00",
                command_id=uuid.uuid4(),
            ),
        )

    assert exc.value.error_code == "cross_currency_transfer_not_supported"
    transfers_repo.create_transfer.assert_not_awaited()
    account_repo.update_balance.assert_not_awaited()


def test_service_has_no_fx_or_http_dependency() -> None:
    source = inspect.getsource(__import__("app.transfers.service", fromlist=["TransfersService"]))
    assert "app.core.currency" not in source
    assert "app.fx" not in source
    assert "httpx" not in source
    assert "get_fx_rate" not in source


@pytest.mark.asyncio
async def test_same_account_is_rejected_before_repository_access(
    service: TransfersService,
    transfers_repo: AsyncMock,
) -> None:
    account_id = uuid.uuid4()
    with pytest.raises(ValidationError) as exc:
        await service.create_transfer(
            uuid.uuid4(),
            TransferCreate(
                source_account_id=account_id,
                destination_account_id=account_id,
                amount="1.00",
                command_id=uuid.uuid4(),
            ),
        )

    assert exc.value.error_code == "same_account"
    transfers_repo.get_by_command_id.assert_not_awaited()


@pytest.mark.parametrize(
    ("role", "missing", "expected_code"),
    [
        ("source", True, "source_account_not_found"),
        ("source", False, "source_account_not_found"),
        ("destination", True, "destination_account_not_found"),
        ("destination", False, "destination_account_not_found"),
    ],
)
@pytest.mark.asyncio
async def test_missing_or_foreign_accounts_return_private_404(
    service: TransfersService,
    transfers_repo: AsyncMock,
    account_repo: AsyncMock,
    role: str,
    missing: bool,
    expected_code: str,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id=user_id)
    destination = make_account(user_id=user_id)
    if not missing:
        foreign = make_account(
            account_id=source.id if role == "source" else destination.id,
            user_id=uuid.uuid4(),
        )
        source = foreign if role == "source" else source
        destination = foreign if role == "destination" else destination
    accounts = {source.id: source, destination.id: destination}
    if missing:
        accounts.pop(source.id if role == "source" else destination.id)
    transfers_repo.get_by_command_id.return_value = None
    account_repo.get_by_id_for_update.side_effect = lambda account_id, **_kwargs: accounts.get(
        account_id
    )

    with pytest.raises(NotFoundError) as exc:
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount="1.00",
                command_id=uuid.uuid4(),
            ),
        )

    assert exc.value.status_code == 404
    assert exc.value.error_code == expected_code


@pytest.mark.parametrize(
    ("role", "expected_code"),
    [
        ("source", "source_account_inactive"),
        ("destination", "destination_account_inactive"),
    ],
)
@pytest.mark.asyncio
async def test_inactive_account_is_locked_then_rejected(
    service: TransfersService,
    transfers_repo: AsyncMock,
    account_repo: AsyncMock,
    role: str,
    expected_code: str,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id=user_id, is_active=role != "source")
    destination = make_account(user_id=user_id, is_active=role != "destination")
    configure_new_transfer(transfers_repo, account_repo, source, destination)

    with pytest.raises(ValidationError) as exc:
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount="1.00",
                command_id=uuid.uuid4(),
            ),
        )

    assert exc.value.error_code == expected_code
    assert account_repo.get_by_id_for_update.await_count == 2
    transfers_repo.create_transfer.assert_not_awaited()


@pytest.mark.asyncio
async def test_insufficient_funds_has_no_financial_writes(
    service: TransfersService,
    transfers_repo: AsyncMock,
    ledger_repo: AsyncMock,
    account_repo: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id=user_id, balance=Decimal("50.00"))
    destination = make_account(user_id=user_id, balance=Decimal("0.00"))
    configure_new_transfer(transfers_repo, account_repo, source, destination)

    with pytest.raises(InsufficientFundsError):
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount="50.01",
                command_id=uuid.uuid4(),
            ),
        )

    transfers_repo.create_transfer.assert_not_awaited()
    ledger_repo.insert_event.assert_not_awaited()
    account_repo.update_balance.assert_not_awaited()


@pytest.mark.asyncio
async def test_null_legacy_balance_is_rejected_without_writes(
    service: TransfersService,
    transfers_repo: AsyncMock,
    ledger_repo: AsyncMock,
    account_repo: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id=user_id, balance=None)
    destination = make_account(user_id=user_id)
    configure_new_transfer(transfers_repo, account_repo, source, destination)

    with pytest.raises(ConflictError) as exc:
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount="1.00",
                command_id=uuid.uuid4(),
            ),
        )

    assert exc.value.error_code == "account_balance_invalid"
    ledger_repo.insert_event.assert_not_awaited()
    account_repo.update_balance.assert_not_awaited()


@pytest.mark.asyncio
async def test_fast_path_exact_retry_has_no_locks_or_writes(
    service: TransfersService,
    transfers_repo: AsyncMock,
    ledger_repo: AsyncMock,
    account_repo: AsyncMock,
    uow: RecordingUow,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id=user_id)
    destination = make_account(user_id=user_id)
    command_id = uuid.uuid4()
    message_id = uuid.uuid4()
    payload = TransferCreate(
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount="100.00",
        description="retry",
        command_id=command_id,
        source_message_id=message_id,
        raw_message="transferir 100",
    )
    existing = make_transfer(
        user_id=user_id,
        source=source,
        destination=destination,
        command_id=command_id,
        description="retry",
        source_message_id=message_id,
        raw_message="transferir 100",
    )
    transfers_repo.get_by_command_id.return_value = existing

    result = await service.create_transfer(user_id, payload)

    assert result.id == existing.id
    assert result.is_idempotent is True
    assert uow.entered == 0
    account_repo.get_by_id_for_update.assert_not_awaited()
    transfers_repo.create_transfer.assert_not_awaited()
    ledger_repo.insert_event.assert_not_awaited()
    account_repo.update_balance.assert_not_awaited()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount", Decimal("101.00")),
        ("description", "different"),
        ("raw_message", "different"),
        ("source_message_id", uuid.UUID(int=42)),
        ("status", "pending"),
        ("created_at", NOW + timedelta(seconds=1)),
    ],
)
@pytest.mark.asyncio
async def test_fast_path_rejects_any_canonical_payload_mismatch(
    service: TransfersService,
    transfers_repo: AsyncMock,
    field: str,
    value: object,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id=user_id)
    destination = make_account(user_id=user_id)
    command_id = uuid.uuid4()
    payload = TransferCreate(
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount="100.00",
        description="same",
        command_id=command_id,
        occurred_at=NOW,
        source_message_id=uuid.UUID(int=41),
        raw_message="same",
    )
    existing = make_transfer(
        user_id=user_id,
        source=source,
        destination=destination,
        command_id=command_id,
        description="same",
        source_message_id=uuid.UUID(int=41),
        raw_message="same",
        created_at=NOW,
    )
    setattr(existing, field, value)
    transfers_repo.get_by_command_id.return_value = existing

    with pytest.raises(ConflictError) as exc:
        await service.create_transfer(user_id, payload)

    assert exc.value.error_code == "idempotency_key_reused"


@pytest.mark.asyncio
async def test_other_user_command_id_is_private_conflict_without_account_access(
    service: TransfersService,
    transfers_repo: AsyncMock,
    account_repo: AsyncMock,
) -> None:
    owner = uuid.uuid4()
    caller = uuid.uuid4()
    source = make_account(user_id=owner)
    destination = make_account(user_id=owner)
    command_id = uuid.uuid4()
    transfers_repo.get_by_command_id.return_value = make_transfer(
        user_id=owner,
        source=source,
        destination=destination,
        command_id=command_id,
    )

    with pytest.raises(ConflictError) as exc:
        await service.create_transfer(
            caller,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount="100.00",
                command_id=command_id,
            ),
        )

    assert exc.value.error_code == "idempotency_key_reused"
    account_repo.get_by_id_for_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_unique_race_returns_existing_without_duplicate_effects(
    service: TransfersService,
    transfers_repo: AsyncMock,
    ledger_repo: AsyncMock,
    account_repo: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id=user_id)
    destination = make_account(user_id=user_id)
    command_id = uuid.uuid4()
    payload = TransferCreate(
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount="100.00",
        command_id=command_id,
    )
    configure_new_transfer(transfers_repo, account_repo, source, destination)
    existing = make_transfer(
        user_id=user_id,
        source=source,
        destination=destination,
        command_id=command_id,
    )
    transfers_repo.create_transfer.side_effect = None
    transfers_repo.create_transfer.return_value = existing

    result = await service.create_transfer(user_id, payload)

    assert result.id == existing.id
    assert result.is_idempotent is True
    ledger_repo.insert_event.assert_not_awaited()
    account_repo.update_balance.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_command_cannot_double_spend_locked_balance(
    service: TransfersService,
    transfers_repo: AsyncMock,
    ledger_repo: AsyncMock,
    account_repo: AsyncMock,
) -> None:
    user_id = uuid.uuid4()
    source = make_account(user_id=user_id, balance=Decimal("100.00"))
    destination = make_account(user_id=user_id, balance=Decimal("0.00"))
    configure_new_transfer(transfers_repo, account_repo, source, destination)

    async def update_balance(account: Account, difference: Decimal) -> None:
        assert account.balance is not None
        account.balance += difference

    account_repo.update_balance.side_effect = update_balance

    base = {
        "source_account_id": source.id,
        "destination_account_id": destination.id,
        "amount": "60.00",
    }
    await service.create_transfer(user_id, TransferCreate(**base, command_id=uuid.uuid4()))
    with pytest.raises(InsufficientFundsError):
        await service.create_transfer(user_id, TransferCreate(**base, command_id=uuid.uuid4()))

    assert source.balance == Decimal("40.00")
    assert destination.balance == Decimal("60.00")
    assert transfers_repo.create_transfer.await_count == 1
    assert ledger_repo.insert_event.await_count == 2


@pytest.mark.asyncio
async def test_ledger_failure_triggers_uow_rollback() -> None:
    session = AsyncMock(spec=AsyncSession)
    uow = UnitOfWork(session)
    transfers_repo = AsyncMock(spec=TransfersRepository)
    ledger_repo = AsyncMock(spec=LedgerRepository)
    account_repo = AsyncMock(spec=AccountRepository)
    service = TransfersService(uow, transfers_repo, ledger_repo, account_repo)
    user_id = uuid.uuid4()
    source = make_account(user_id=user_id)
    destination = make_account(user_id=user_id)
    configure_new_transfer(transfers_repo, account_repo, source, destination)
    ledger_repo.insert_event.side_effect = [make_ledger_result(), RuntimeError("ledger failed")]

    with pytest.raises(RuntimeError, match="ledger failed"):
        await service.create_transfer(
            user_id,
            TransferCreate(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount="1.00",
                command_id=uuid.uuid4(),
            ),
        )

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    account_repo.update_balance.assert_not_awaited()


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "0.001", "NaN", "Infinity", "-Infinity", "1000000000000.00"],
)
def test_amount_rejects_invalid_or_unrepresentable_values(value: str) -> None:
    with pytest.raises(PydanticValidationError):
        TransferRequest(
            source_account_id=uuid.uuid4(),
            destination_account_id=uuid.uuid4(),
            amount=value,
            command_id=uuid.uuid4(),
        )


def test_amount_accepts_numeric_14_2_boundary() -> None:
    request = TransferRequest(
        source_account_id=uuid.uuid4(),
        destination_account_id=uuid.uuid4(),
        amount="999999999999.99",
        command_id=uuid.uuid4(),
    )
    assert request.amount == Decimal("999999999999.99")


def test_public_request_forbids_internal_and_unknown_fields() -> None:
    base = {
        "source_account_id": uuid.uuid4(),
        "destination_account_id": uuid.uuid4(),
        "amount": "1.00",
        "command_id": uuid.uuid4(),
    }
    for field in (
        "user_id",
        "currency",
        "target_currency",
        "target_amount",
        "fx_rate",
        "rate_source",
        "rate_timestamp",
        "is_estimated",
        "status",
        "occurred_at",
        "source_message_id",
        "raw_message",
        "unexpected",
    ):
        with pytest.raises(PydanticValidationError):
            TransferRequest(**base, **{field: "forbidden"})


def test_internal_command_preserves_backend_trace_fields() -> None:
    message_id = uuid.uuid4()
    command = TransferCreate(
        source_account_id=uuid.uuid4(),
        destination_account_id=uuid.uuid4(),
        amount="1.00",
        command_id=uuid.uuid4(),
        occurred_at=NOW,
        source_message_id=message_id,
        raw_message="mensaje interno",
    )
    assert command.occurred_at == NOW
    assert command.source_message_id == message_id
    assert command.raw_message == "mensaje interno"


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [("  ahorro  ", "ahorro"), ("\t\n", None), ("", None), (None, None)],
)
def test_description_is_canonically_normalized(raw: str | None, normalized: str | None) -> None:
    request = TransferRequest(
        source_account_id=uuid.uuid4(),
        destination_account_id=uuid.uuid4(),
        amount="1.00",
        command_id=uuid.uuid4(),
        description=raw,
    )
    assert request.description == normalized


def test_transfer_model_declares_unique_command_id() -> None:
    command_column = Transfer.__table__.c.command_id
    assert command_column.unique is True


class NestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_repository_recovers_unique_race_using_driver_diag() -> None:
    session = MagicMock(spec=AsyncSession)
    session.begin_nested.return_value = NestedTransaction()
    original = RuntimeError("duplicate")
    original.diag = SimpleNamespace(constraint_name="transfers_command_id_key")  # type: ignore[attr-defined]
    session.flush = AsyncMock(side_effect=IntegrityError("insert", {}, original))
    session.add = MagicMock()
    repo = TransfersRepository(session)
    existing = MagicMock(spec=Transfer)
    repo.get_by_command_id = AsyncMock(return_value=existing)  # type: ignore[method-assign]
    transfer = Transfer(command_id=uuid.uuid4())

    result = await repo.create_transfer(transfer)

    assert result is existing
    repo.get_by_command_id.assert_awaited_once_with(transfer.command_id)


@pytest.mark.asyncio
async def test_repository_does_not_expose_database_error_details() -> None:
    session = MagicMock(spec=AsyncSession)
    session.begin_nested.return_value = NestedTransaction()
    session.flush = AsyncMock(
        side_effect=IntegrityError("insert", {}, RuntimeError("secret database detail"))
    )
    session.add = MagicMock()
    repo = TransfersRepository(session)

    with pytest.raises(ConflictError) as exc:
        await repo.create_transfer(Transfer(command_id=uuid.uuid4()))

    assert exc.value.error_code == "transfer_integrity_conflict"
    assert exc.value.detail == {}
    assert "secret" not in exc.value.message


@pytest.mark.asyncio
async def test_repository_history_is_owned_paginated_and_deterministically_ordered() -> None:
    session = MagicMock(spec=AsyncSession)
    page_result = MagicMock()
    page_result.scalars.return_value.all.return_value = []
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    session.execute = AsyncMock(side_effect=[page_result, count_result])
    repo = TransfersRepository(session)
    user_id = uuid.uuid4()

    items, total = await repo.list_transfers(user_id, limit=25, offset=50)

    assert items == []
    assert total == 0
    page_stmt = session.execute.await_args_list[0].args[0]
    sql = str(page_stmt)
    assert "transfers.user_id" in sql
    assert "ORDER BY transfers.created_at DESC, transfers.id DESC" in sql
    assert page_stmt._limit_clause.value == 25
    assert page_stmt._offset_clause.value == 50


@pytest.mark.asyncio
async def test_repository_detail_masks_missing_or_foreign_transfer_as_404() -> None:
    session = MagicMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    repo = TransfersRepository(session)
    user_id = uuid.uuid4()
    transfer_id = uuid.uuid4()

    with pytest.raises(NotFoundError):
        await repo.get_by_id(transfer_id, user_id)

    sql = str(session.execute.await_args.args[0])
    assert "transfers.id" in sql
    assert "transfers.user_id" in sql


@pytest.fixture
def api_user() -> UserRead:
    return UserRead(
        id=uuid.uuid4(),
        email="transfer@test.local",
        is_active=True,
        created_at=None,
        updated_at=None,
        name="Transfer User",
        timezone="UTC",
        currency="COP",
        status="active",
    )


@pytest.fixture
def api_service(api_user: UserRead) -> AsyncMock:
    service = AsyncMock(spec=TransfersService)
    source = make_account(user_id=api_user.id, name="Origen")
    destination = make_account(user_id=api_user.id, name="Destino")
    transfer = make_transfer(
        user_id=api_user.id,
        source=source,
        destination=destination,
        command_id=uuid.uuid4(),
        description="HTTP",
    )
    service.create_transfer.return_value = TransferResult.model_validate(transfer)
    service.list_transfers.return_value = ([transfer], 1)
    service.get_transfer.return_value = TransferResult.model_validate(transfer)
    return service


@pytest.fixture
async def http_client(api_user: UserRead, api_service: AsyncMock):
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user_profile_dep] = lambda: api_user
    app.dependency_overrides[get_transfers_service] = lambda: api_service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


@pytest.mark.asyncio
async def test_http_post_contract_and_status(
    http_client: AsyncClient,
    api_service: AsyncMock,
    api_user: UserRead,
) -> None:
    payload = {
        "source_account_id": str(uuid.uuid4()),
        "destination_account_id": str(uuid.uuid4()),
        "amount": "100.00",
        "command_id": str(uuid.uuid4()),
        "description": "  HTTP  ",
    }

    response = await http_client.post("/api/v1/transfers", json=payload)

    assert response.status_code == 201
    command = api_service.create_transfer.await_args.args[1]
    assert isinstance(command, TransferCreate)
    assert command.description == "HTTP"
    assert command.occurred_at is None
    assert command.source_message_id is None
    assert command.raw_message is None
    api_service.create_transfer.assert_awaited_once_with(api_user.id, command)


@pytest.mark.parametrize(
    "field",
    [
        "currency",
        "target_currency",
        "occurred_at",
        "source_message_id",
        "raw_message",
        "status",
        "fx_rate",
        "user_id",
    ],
)
@pytest.mark.asyncio
async def test_http_post_rejects_client_controlled_fields(
    http_client: AsyncClient,
    api_service: AsyncMock,
    field: str,
) -> None:
    payload = {
        "source_account_id": str(uuid.uuid4()),
        "destination_account_id": str(uuid.uuid4()),
        "amount": "1.00",
        "command_id": str(uuid.uuid4()),
        field: "forbidden",
    }
    response = await http_client.post("/api/v1/transfers", json=payload)
    assert response.status_code == 422
    api_service.create_transfer.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_post_requires_command_id(
    http_client: AsyncClient,
    api_service: AsyncMock,
) -> None:
    response = await http_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": str(uuid.uuid4()),
            "destination_account_id": str(uuid.uuid4()),
            "amount": "1.00",
        },
    )
    assert response.status_code == 422
    api_service.create_transfer.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_list_and_detail_contracts(
    http_client: AsyncClient,
    api_service: AsyncMock,
    api_user: UserRead,
) -> None:
    listing = await http_client.get("/api/v1/transfers?limit=25&offset=5")
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert listing.json()[0]["source_account"]["name"] == "Origen"
    api_service.list_transfers.assert_awaited_once_with(api_user.id, 25, 5)

    transfer_id = uuid.uuid4()
    detail = await http_client.get(f"/api/v1/transfers/{transfer_id}")
    assert detail.status_code == 200
    assert detail.json()["destination_account"]["name"] == "Destino"
    api_service.get_transfer.assert_awaited_once_with(transfer_id, api_user.id)


@pytest.mark.asyncio
async def test_http_domain_404_and_409_use_nexum_error_shape(
    http_client: AsyncClient,
    api_service: AsyncMock,
) -> None:
    api_service.get_transfer.side_effect = NotFoundError(message="Transferencia no encontrada.")
    detail = await http_client.get(f"/api/v1/transfers/{uuid.uuid4()}")
    assert detail.status_code == 404
    assert detail.json()["error_code"] == "not_found"

    api_service.create_transfer.side_effect = ConflictError(
        message="Llave reutilizada.", error_code="idempotency_key_reused"
    )
    create = await http_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": str(uuid.uuid4()),
            "destination_account_id": str(uuid.uuid4()),
            "amount": "1.00",
            "command_id": str(uuid.uuid4()),
        },
    )
    assert create.status_code == 409
    assert create.json()["error_code"] == "idempotency_key_reused"


def test_runtime_openapi_transfer_contract() -> None:
    schema = app.openapi()
    request_schema = schema["components"]["schemas"]["TransferRequest"]
    assert set(request_schema["properties"]) == {
        "source_account_id",
        "destination_account_id",
        "amount",
        "description",
        "command_id",
    }
    assert set(request_schema["required"]) == {
        "source_account_id",
        "destination_account_id",
        "amount",
        "command_id",
    }
    assert request_schema["additionalProperties"] is False

    paths = schema["paths"]
    assert set(paths["/api/v1/transfers"]) == {"post", "get"}
    assert set(paths["/api/v1/transfers/{transfer_id}"]) == {"get"}
    assert set(paths["/api/v1/transfers"]["post"]["responses"]) == {
        "201",
        "401",
        "404",
        "409",
        "422",
    }
