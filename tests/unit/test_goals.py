import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError

from app.accounts.exceptions import AccountForbiddenError
from app.accounts.models import Account
from app.cash.exceptions import InsufficientFundsError
from app.core.errors import ConflictError, ForbiddenError
from app.core.errors import ValidationError as DomainValidationError
from app.core.uow import UnitOfWork
from app.goals.exceptions import (
    GoalAmountExceededError,
    GoalCompletedError,
    GoalDuplicateError,
    GoalForbiddenError,
    GoalNotActiveError,
    GoalTargetAmountError,
)
from app.goals.models import Goal, GoalTransaction
from app.goals.schemas import GoalContributionCreate, GoalCreate, GoalUpdate
from app.goals.service import GoalService


@pytest.fixture
def mock_goal_repo():
    repository = AsyncMock()
    repository.session = MagicMock()

    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=False)
    repository.session.begin_nested.return_value = nested
    repository.session.flush = AsyncMock()

    async def create_transaction(transaction):
        transaction.id = transaction.id or uuid.uuid4()
        transaction.created_at = transaction.created_at or datetime.now(UTC)
        return transaction

    repository.create_transaction.side_effect = create_transaction
    repository.get_transaction_by_command_id.return_value = None
    repository.calculate_reserved_by_account.return_value = Decimal("0")
    repository.calculate_progress_by_goal.return_value = Decimal("0")
    return repository


@pytest.fixture
def mock_account_repo():
    return AsyncMock()


@pytest.fixture
def mock_ledger_repo():
    repository = AsyncMock()
    repository.get_by_command_id.return_value = None
    return repository


@pytest.fixture
def goal_service(mock_goal_repo, mock_account_repo, mock_ledger_repo):
    return GoalService(mock_goal_repo, mock_account_repo, mock_ledger_repo)


def make_goal(
    *,
    goal_id=None,
    user_id=None,
    current=Decimal("100"),
    target=Decimal("500"),
    currency="COP",
    active=True,
    status="active",
):
    return Goal(
        id=goal_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        name="Meta",
        target_amount=target,
        current_amount=current,
        currency=currency,
        is_active=active,
        status=status,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(),
    )


def make_account(
    *,
    account_id=None,
    user_id=None,
    balance=Decimal("1000"),
    currency="COP",
    active=True,
):
    return Account(
        id=account_id or uuid.uuid4(),
        user_id=user_id,
        name="Cuenta",
        type="bank",
        balance=balance,
        currency=currency,
        is_active=active,
    )


def make_event(
    *,
    event_id=None,
    user_id,
    account_id,
    command_id,
    goal_id,
    amount=Decimal("100"),
    currency="COP",
):
    return SimpleNamespace(
        id=event_id or uuid.uuid4(),
        user_id=user_id,
        account_id=account_id,
        command_id=command_id,
        event_type="goal_contribution",
        direction="neutral",
        amount=amount,
        currency=currency,
        metadata_={
            "goal_id": str(goal_id),
            "transaction_type": "allocation",
        },
    )


def response_snapshot(
    *,
    current="200",
    remaining="300",
    status="active",
    balance="1000",
    reserved="200",
    available="800",
    progress="40",
):
    return {
        "goal_current_amount": current,
        "goal_remaining_amount": remaining,
        "goal_status": status,
        "account_balance": balance,
        "goal_reserved_amount": reserved,
        "available_balance": available,
        "progress_percentage": progress,
    }


def make_existing_transaction(
    service,
    *,
    user_id,
    goal_id,
    account_id,
    command_id,
    event_id,
    amount=Decimal("100"),
    source_currency="COP",
    goal_currency="COP",
    snapshot=None,
):
    fingerprint = service._generate_fingerprint(
        user_id,
        goal_id,
        account_id,
        "allocation",
        amount,
        source_currency,
        amount,
        goal_currency,
    )
    return GoalTransaction(
        id=uuid.uuid4(),
        goal_id=goal_id,
        account_id=account_id,
        user_id=user_id,
        event_id=event_id,
        transaction_type="allocation",
        source_amount=amount,
        source_currency=source_currency,
        applied_amount=amount,
        goal_currency=goal_currency,
        command_id=command_id,
        command_fingerprint=fingerprint,
        metadata_json=snapshot or response_snapshot(),
        created_at=datetime.now(UTC),
    )


def configure_new_contribution(
    *,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
    goal,
    account,
    command_id,
    reserved=Decimal("0"),
):
    mock_goal_repo.get_transaction_by_command_id.side_effect = [None, None]
    mock_account_repo.get_by_id_for_update.return_value = account
    mock_goal_repo.get_by_id_for_update.return_value = goal
    mock_goal_repo.calculate_reserved_by_account.return_value = reserved
    event = make_event(
        user_id=goal.user_id,
        account_id=account.id,
        command_id=command_id,
        goal_id=goal.id,
    )
    mock_ledger_repo.insert_event.return_value = SimpleNamespace(
        event=event,
        idempotent=False,
    )
    return event


@pytest.mark.asyncio
async def test_create_goal_success(goal_service, mock_goal_repo):
    user_id = uuid.uuid4()
    payload = GoalCreate(
        currency="COP",
        name="  Viaje a Japón  ",
        target_amount=Decimal("5000000"),
        target_date=date(2027, 1, 1),
    )
    mock_goal_repo.check_name_exists.return_value = False
    mock_goal_repo.create.return_value = make_goal(
        user_id=user_id,
        current=Decimal("0"),
        target=payload.target_amount,
    )
    mock_goal_repo.create.return_value.name = "Viaje A Japón"
    mock_goal_repo.create.return_value.target_date = payload.target_date

    result = await goal_service.create_goal(user_id, payload)

    assert result.name == "Viaje A Japón"
    assert result.current_amount == Decimal("0")
    mock_goal_repo.check_name_exists.assert_awaited_once_with(
        user_id,
        "viaje a japon",
    )


@pytest.mark.asyncio
async def test_create_goal_duplicate_name(goal_service, mock_goal_repo):
    user_id = uuid.uuid4()
    payload = GoalCreate(currency="COP", name="Coche", target_amount=Decimal("1000"))
    mock_goal_repo.check_name_exists.return_value = True

    with pytest.raises(GoalDuplicateError):
        await goal_service.create_goal(user_id, payload)


@pytest.mark.asyncio
async def test_update_goal_target_less_than_current(goal_service, mock_goal_repo):
    user_id = uuid.uuid4()
    goal = make_goal(user_id=user_id, current=Decimal("100"), target=Decimal("200"))
    mock_goal_repo.get_by_id.return_value = goal

    with pytest.raises(GoalTargetAmountError):
        await goal_service.update_goal(
            user_id,
            goal.id,
            GoalUpdate(target_amount=Decimal("50")),
        )


@pytest.mark.asyncio
async def test_delete_goal_is_soft_archive(goal_service, mock_goal_repo):
    user_id = uuid.uuid4()
    goal = make_goal(user_id=user_id)
    mock_goal_repo.get_by_id.return_value = goal
    mock_goal_repo.calculate_progress_by_goal.return_value = Decimal("0")

    await goal_service.delete_goal(user_id, goal.id)

    assert goal.is_active is False
    assert goal.status == "cancelled"


@pytest.mark.asyncio
async def test_delete_goal_rejects_active_reservation(goal_service, mock_goal_repo):
    user_id = uuid.uuid4()
    goal = make_goal(user_id=user_id)
    mock_goal_repo.get_by_id.return_value = goal
    mock_goal_repo.calculate_progress_by_goal.return_value = Decimal("1")

    with pytest.raises(ConflictError):
        await goal_service.delete_goal(user_id, goal.id)

    assert goal.is_active is True
    assert goal.status == "active"


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0"),
        Decimal("-0.01"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("1000000000000.00"),
        Decimal("1.001"),
    ],
)
def test_contribution_schema_rejects_invalid_amounts(amount):
    with pytest.raises(PydanticValidationError):
        GoalContributionCreate(account_id=uuid.uuid4(), amount=amount)


def test_contribution_schema_accepts_real_numeric_limit_and_legacy_fields():
    payload = GoalContributionCreate(
        account_id=uuid.uuid4(),
        amount=Decimal("999999999999.99"),
        currency="cop",
        source_message_id=uuid.uuid4(),
        raw_message="legacy",
    )

    assert payload.amount == Decimal("999999999999.99")
    assert payload.currency == "cop"


def test_contribution_schema_rejects_extra_financial_authority():
    with pytest.raises(PydanticValidationError):
        GoalContributionCreate(
            account_id=uuid.uuid4(),
            amount=Decimal("1"),
            target_amount=Decimal("2"),
        )


def test_fingerprint_normalizes_equivalent_decimals_and_currencies(goal_service):
    identifiers = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    fingerprints = {
        goal_service._generate_fingerprint(
            *identifiers,
            "allocation",
            amount,
            "cop",
            amount,
            "Cop",
        )
        for amount in (Decimal("100"), Decimal("100.0"), Decimal("100.00"))
    }

    assert len(fingerprints) == 1
    assert len(fingerprints.pop()) == 64


def test_command_id_resolution_matrix(goal_service):
    command_id = uuid.uuid4()

    assert goal_service._resolve_command_id(None, None) is None
    assert goal_service._resolve_command_id(command_id, None) == command_id
    assert goal_service._resolve_command_id(None, str(command_id)) == command_id
    assert goal_service._resolve_command_id(command_id, str(command_id).upper()) == command_id

    with pytest.raises(DomainValidationError):
        goal_service._resolve_command_id(command_id, str(uuid.uuid4()))
    with pytest.raises(DomainValidationError):
        goal_service._resolve_command_id(None, "not-a-uuid")


@pytest.mark.asyncio
async def test_contribution_preserves_balance_and_creates_neutral_allocation(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=user_id)
    account = make_account(user_id=user_id)
    event = configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
        reserved=Decimal("100"),
    )
    payload = GoalContributionCreate(
        account_id=account.id,
        amount=Decimal("100.00"),
        command_id=command_id,
        description="Aporte mensual",
    )
    balance_before = account.balance

    result = await goal_service.create_contribution(
        user_id,
        goal.id,
        payload,
        str(command_id),
    )

    assert account.balance == balance_before
    assert result.account_balance == balance_before
    assert result.goal_reserved_amount == Decimal("200")
    assert result.available_balance == Decimal("800")
    assert result.goal_current_amount == Decimal("200")
    assert result.goal_remaining_amount == Decimal("300")
    assert result.idempotent is False
    assert result.event_id == event.id

    transaction = mock_goal_repo.create_transaction.await_args.args[0]
    assert transaction.transaction_type == "allocation"
    assert transaction.source_amount == transaction.applied_amount == Decimal("100")
    assert transaction.metadata_json["available_balance"] == "800.00"
    assert transaction.metadata_json["channel"] == "manual"

    event_payload = mock_ledger_repo.insert_event.await_args.args[0]
    assert event_payload.direction == "neutral"
    assert event_payload.event_type == "goal_contribution"
    assert event_payload.amount == Decimal("100")
    assert event_payload.currency == "COP"
    assert event_payload.description == "Aporte mensual"
    mock_account_repo.update_balance.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_automatic_contribution_persists_history_channel(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=user_id)
    account = make_account(user_id=user_id)
    configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
    )

    await goal_service.create_contribution(
        user_id,
        goal.id,
        GoalContributionCreate(
            account_id=account.id,
            amount=Decimal("10.00"),
            command_id=command_id,
        ),
        str(command_id),
        channel="automatic",
    )

    transaction = mock_goal_repo.create_transaction.await_args.args[0]
    assert transaction.metadata_json["channel"] == "automatic"

    mock_goal_repo.get_by_id.return_value = goal
    mock_goal_repo.list_transactions_by_goal.return_value = ([(transaction, None)], 1)
    history = await goal_service.list_goal_transactions(user_id, goal.id)
    assert history.items[0].origin == "native"
    assert history.items[0].channel == "automatic"


@pytest.mark.asyncio
async def test_internal_contribution_rejects_legacy_channel_before_io(goal_service):
    with pytest.raises(ValueError, match="canal interno"):
        await goal_service.create_contribution(
            uuid.uuid4(),
            uuid.uuid4(),
            GoalContributionCreate(account_id=uuid.uuid4(), amount=Decimal("10.00")),
            None,
            channel="legacy",  # type: ignore[arg-type]
        )

    goal_service.repository.get_transaction_by_command_id.assert_not_awaited()
    goal_service.account_repo.get_by_id_for_update.assert_not_awaited()
    goal_service.ledger_repo.insert_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_contribution_without_command_id_is_non_idempotent(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    goal = make_goal(user_id=user_id)
    account = make_account(user_id=user_id)
    configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=None,
    )

    result = await goal_service.create_contribution(
        user_id,
        goal.id,
        GoalContributionCreate(account_id=account.id, amount=Decimal("10")),
        None,
    )

    assert result.idempotent is False
    transaction = mock_goal_repo.create_transaction.await_args.args[0]
    assert transaction.command_id is None
    assert transaction.command_fingerprint is None


@pytest.mark.asyncio
async def test_exact_remaining_amount_completes_goal(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=user_id, current=Decimal("400"))
    account = make_account(user_id=user_id)
    configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
    )

    result = await goal_service.create_contribution(
        user_id,
        goal.id,
        GoalContributionCreate(
            account_id=account.id,
            amount=Decimal("100"),
            command_id=command_id,
        ),
        None,
    )

    assert goal.current_amount == Decimal("500")
    assert goal.status == "completed"
    assert result.goal_remaining_amount == Decimal("0")
    assert result.goal_status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("goal_attributes", "expected_error"),
    [
        ({"active": False}, GoalNotActiveError),
        ({"status": "completed"}, GoalCompletedError),
        ({"status": "cancelled"}, GoalNotActiveError),
    ],
)
async def test_contribution_rejects_invalid_goal_state(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
    goal_attributes,
    expected_error,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=user_id, **goal_attributes)
    account = make_account(user_id=user_id)
    configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
    )

    with pytest.raises(expected_error):
        await goal_service.create_contribution(
            user_id,
            goal.id,
            GoalContributionCreate(
                account_id=account.id,
                amount=Decimal("10"),
                command_id=command_id,
            ),
            None,
        )

    mock_ledger_repo.insert_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_contribution_rejects_foreign_goal(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=uuid.uuid4())
    account = make_account(user_id=user_id)
    configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
    )

    with pytest.raises(GoalForbiddenError):
        await goal_service.create_contribution(
            user_id,
            goal.id,
            GoalContributionCreate(
                account_id=account.id,
                amount=Decimal("10"),
                command_id=command_id,
            ),
            None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("account_user_id", [None, "foreign"])
async def test_contribution_rejects_unowned_or_global_account(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
    account_user_id,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=user_id)
    owner = uuid.uuid4() if account_user_id == "foreign" else None
    account = make_account(user_id=owner)
    configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
    )

    with pytest.raises(AccountForbiddenError):
        await goal_service.create_contribution(
            user_id,
            goal.id,
            GoalContributionCreate(
                account_id=account.id,
                amount=Decimal("10"),
                command_id=command_id,
            ),
            None,
        )


@pytest.mark.asyncio
async def test_contribution_rejects_inactive_account(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=user_id)
    account = make_account(user_id=user_id, active=False)
    configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
    )

    with pytest.raises(ForbiddenError):
        await goal_service.create_contribution(
            user_id,
            goal.id,
            GoalContributionCreate(
                account_id=account.id,
                amount=Decimal("10"),
                command_id=command_id,
            ),
            None,
        )

    mock_account_repo.get_by_id_for_update.assert_awaited_once_with(
        account.id,
        include_inactive=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("balance", "reserved", "amount", "expected_error"),
    [
        (None, Decimal("0"), Decimal("10"), ForbiddenError),
        (Decimal("100"), Decimal("110"), Decimal("1"), InsufficientFundsError),
        (Decimal("100"), Decimal("90"), Decimal("11"), InsufficientFundsError),
    ],
)
async def test_contribution_rejects_invalid_availability(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
    balance,
    reserved,
    amount,
    expected_error,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=user_id)
    account = make_account(user_id=user_id, balance=balance)
    configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
        reserved=reserved,
    )

    with pytest.raises(expected_error):
        await goal_service.create_contribution(
            user_id,
            goal.id,
            GoalContributionCreate(
                account_id=account.id,
                amount=amount,
                command_id=command_id,
            ),
            None,
        )


@pytest.mark.asyncio
async def test_contribution_rejects_overshoot_before_writes(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=user_id, current=Decimal("450"))
    account = make_account(user_id=user_id)
    configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
    )

    with pytest.raises(GoalAmountExceededError):
        await goal_service.create_contribution(
            user_id,
            goal.id,
            GoalContributionCreate(
                account_id=account.id,
                amount=Decimal("51"),
                command_id=command_id,
            ),
            None,
        )

    mock_ledger_repo.insert_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("account_currency", "goal_currency", "payload_currency"),
    [
        ("USD", "COP", None),
        ("COP", "COP", "USD"),
    ],
)
async def test_contribution_rejects_currency_mismatch_without_fx(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
    account_currency,
    goal_currency,
    payload_currency,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=user_id, currency=goal_currency)
    account = make_account(user_id=user_id, currency=account_currency)
    configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
    )

    with pytest.raises(DomainValidationError):
        await goal_service.create_contribution(
            user_id,
            goal.id,
            GoalContributionCreate(
                account_id=account.id,
                amount=Decimal("10"),
                command_id=command_id,
                currency=payload_currency,
            ),
            None,
        )

    mock_ledger_repo.insert_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_idempotent_retry_uses_historical_snapshot_after_completion(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    account_id = uuid.uuid4()
    command_id = uuid.uuid4()
    event = make_event(
        user_id=user_id,
        account_id=account_id,
        command_id=command_id,
        goal_id=goal_id,
    )
    transaction = make_existing_transaction(
        goal_service,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        command_id=command_id,
        event_id=event.id,
        snapshot=response_snapshot(
            current="500",
            remaining="0",
            status="completed",
            balance="1000",
            reserved="500",
            available="500",
            progress="100",
        ),
    )
    mock_goal_repo.get_transaction_by_command_id.return_value = transaction
    mock_ledger_repo.get_by_command_id.return_value = event

    result = await goal_service.create_contribution(
        user_id,
        goal_id,
        GoalContributionCreate(
            account_id=account_id,
            amount=Decimal("100.00"),
            command_id=command_id,
        ),
        str(command_id),
    )

    assert result.idempotent is True
    assert result.goal_status == "completed"
    assert result.goal_remaining_amount == 0
    assert result.available_balance == Decimal("500")
    mock_account_repo.get_by_id_for_update.assert_not_awaited()
    mock_goal_repo.get_by_id_for_update.assert_not_awaited()
    mock_ledger_repo.insert_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_idempotent_retry_accepts_equivalent_decimal(
    goal_service,
    mock_goal_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    account_id = uuid.uuid4()
    command_id = uuid.uuid4()
    event = make_event(
        user_id=user_id,
        account_id=account_id,
        command_id=command_id,
        goal_id=goal_id,
    )
    transaction = make_existing_transaction(
        goal_service,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        command_id=command_id,
        event_id=event.id,
        amount=Decimal("100.00"),
    )
    mock_goal_repo.get_transaction_by_command_id.return_value = transaction
    mock_ledger_repo.get_by_command_id.return_value = event

    result = await goal_service.create_contribution(
        user_id,
        goal_id,
        GoalContributionCreate(
            account_id=account_id,
            amount=Decimal("100.0"),
            command_id=command_id,
        ),
        None,
    )

    assert result.idempotent is True


@pytest.mark.asyncio
@pytest.mark.parametrize("collision", ["amount", "goal", "account", "user"])
async def test_idempotent_collision_is_controlled_conflict(
    goal_service,
    mock_goal_repo,
    mock_ledger_repo,
    collision,
):
    user_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    account_id = uuid.uuid4()
    command_id = uuid.uuid4()
    event = make_event(
        user_id=user_id,
        account_id=account_id,
        command_id=command_id,
        goal_id=goal_id,
    )
    transaction = make_existing_transaction(
        goal_service,
        user_id=uuid.uuid4() if collision == "user" else user_id,
        goal_id=uuid.uuid4() if collision == "goal" else goal_id,
        account_id=uuid.uuid4() if collision == "account" else account_id,
        command_id=command_id,
        event_id=event.id,
    )
    mock_goal_repo.get_transaction_by_command_id.return_value = transaction
    mock_ledger_repo.get_by_command_id.return_value = event
    amount = Decimal("101") if collision == "amount" else Decimal("100")

    with pytest.raises(ConflictError):
        await goal_service.create_contribution(
            user_id,
            goal_id,
            GoalContributionCreate(
                account_id=account_id,
                amount=amount,
                command_id=command_id,
            ),
            None,
        )

    mock_ledger_repo.insert_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_idempotent_retry_rejects_foreign_event(
    goal_service,
    mock_goal_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    account_id = uuid.uuid4()
    command_id = uuid.uuid4()
    event = make_event(
        user_id=uuid.uuid4(),
        account_id=account_id,
        command_id=command_id,
        goal_id=goal_id,
    )
    transaction = make_existing_transaction(
        goal_service,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        command_id=command_id,
        event_id=event.id,
    )
    mock_goal_repo.get_transaction_by_command_id.return_value = transaction
    mock_ledger_repo.get_by_command_id.return_value = event

    with pytest.raises(ConflictError):
        await goal_service.create_contribution(
            user_id,
            goal_id,
            GoalContributionCreate(
                account_id=account_id,
                amount=Decimal("100"),
                command_id=command_id,
            ),
            None,
        )


@pytest.mark.asyncio
async def test_global_ledger_command_collision_cannot_create_goal_transaction(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=user_id)
    account = make_account(user_id=user_id)
    existing_event = make_event(
        user_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        command_id=command_id,
        goal_id=uuid.uuid4(),
    )
    configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
    )
    mock_goal_repo.get_transaction_by_command_id.side_effect = [None, None, None]
    mock_ledger_repo.insert_event.return_value = SimpleNamespace(
        event=existing_event,
        idempotent=True,
    )

    with pytest.raises(ConflictError):
        await goal_service.create_contribution(
            user_id,
            goal.id,
            GoalContributionCreate(
                account_id=account.id,
                amount=Decimal("100"),
                command_id=command_id,
            ),
            None,
        )

    mock_goal_repo.create_transaction.assert_not_awaited()
    assert goal.current_amount == Decimal("100")


class _ConstraintError(Exception):
    def __init__(self, constraint_name):
        super().__init__(constraint_name)
        self.constraint_name = constraint_name


@pytest.mark.asyncio
async def test_unique_command_race_replays_winner(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=user_id)
    account = make_account(user_id=user_id)
    attempted_event = configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
    )
    winner_event = make_event(
        user_id=user_id,
        account_id=account.id,
        command_id=command_id,
        goal_id=goal.id,
    )
    winner = make_existing_transaction(
        goal_service,
        user_id=user_id,
        goal_id=goal.id,
        account_id=account.id,
        command_id=command_id,
        event_id=winner_event.id,
    )
    mock_goal_repo.get_transaction_by_command_id.side_effect = [None, None, winner]
    mock_goal_repo.create_transaction.side_effect = IntegrityError(
        "INSERT",
        {},
        _ConstraintError("uq_gtx_command_id"),
    )
    mock_ledger_repo.get_by_command_id.return_value = winner_event

    result = await goal_service.create_contribution(
        user_id,
        goal.id,
        GoalContributionCreate(
            account_id=account.id,
            amount=Decimal("100"),
            command_id=command_id,
        ),
        None,
    )

    assert result.idempotent is True
    assert result.event_id == winner_event.id
    assert result.event_id != attempted_event.id
    assert goal.current_amount == Decimal("100")


@pytest.mark.asyncio
async def test_non_command_integrity_error_propagates(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=user_id)
    account = make_account(user_id=user_id)
    configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
    )
    expected = IntegrityError(
        "INSERT",
        {},
        _ConstraintError("chk_gtx_event_id"),
    )
    mock_goal_repo.create_transaction.side_effect = expected

    with pytest.raises(IntegrityError) as caught:
        await goal_service.create_contribution(
            user_id,
            goal.id,
            GoalContributionCreate(
                account_id=account.id,
                amount=Decimal("100"),
                command_id=command_id,
            ),
            None,
        )

    assert caught.value is expected
    assert goal.current_amount == Decimal("100")


@pytest.mark.asyncio
async def test_ledger_failure_leaves_domain_state_untouched(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=user_id)
    account = make_account(user_id=user_id)
    configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
    )
    mock_ledger_repo.insert_event.side_effect = RuntimeError("ledger failed")

    with pytest.raises(RuntimeError, match="ledger failed"):
        await goal_service.create_contribution(
            user_id,
            goal.id,
            GoalContributionCreate(
                account_id=account.id,
                amount=Decimal("100"),
                command_id=command_id,
            ),
            None,
        )

    assert goal.current_amount == Decimal("100")
    assert account.balance == Decimal("1000")
    mock_goal_repo.create_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_lock_order_is_account_then_goal(
    goal_service,
    mock_goal_repo,
    mock_account_repo,
    mock_ledger_repo,
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    goal = make_goal(user_id=user_id)
    account = make_account(user_id=user_id)
    calls = []

    async def lock_account(*args, **kwargs):
        calls.append("account")
        return account

    async def lock_goal(*args, **kwargs):
        calls.append("goal")
        return goal

    configure_new_contribution(
        mock_goal_repo=mock_goal_repo,
        mock_account_repo=mock_account_repo,
        mock_ledger_repo=mock_ledger_repo,
        goal=goal,
        account=account,
        command_id=command_id,
    )
    mock_account_repo.get_by_id_for_update.side_effect = lock_account
    mock_goal_repo.get_by_id_for_update.side_effect = lock_goal

    await goal_service.create_contribution(
        user_id,
        goal.id,
        GoalContributionCreate(
            account_id=account.id,
            amount=Decimal("10"),
            command_id=command_id,
        ),
        None,
    )

    assert calls == ["account", "goal"]


@pytest.mark.asyncio
async def test_uow_rolls_back_when_commit_fails():
    session = AsyncMock()
    session.commit.side_effect = RuntimeError("commit failed")
    uow = UnitOfWork(session)

    with pytest.raises(RuntimeError, match="commit failed"):
        async with uow.transaction():
            pass

    session.rollback.assert_awaited_once()
