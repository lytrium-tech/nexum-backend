from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, create_autospec
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError

from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.goals.enums import GoalTransactionType
from app.goals.exceptions import GoalNotActiveError
from app.goals.models import Goal, GoalTransaction
from app.goals.repository import GoalRepository
from app.goals.schemas import GoalReleaseCreate
from app.goals.service import GoalService
from app.ledger.enums import Direction, EventType
from app.ledger.repository import LedgerRepository


def _account(
    *,
    account_id: UUID,
    user_id: UUID | None,
    active: bool = True,
    currency: str = "COP",
    balance: Decimal | None = Decimal("1000.00"),
) -> Account:
    return Account(
        id=account_id,
        user_id=user_id,
        name="Cuenta",
        type="savings",
        currency=currency,
        balance=balance,
        is_active=active,
    )


def _goal(
    *,
    goal_id: UUID,
    user_id: UUID,
    active: bool = True,
    status: str = "active",
    currency: str = "COP",
    target: Decimal = Decimal("500.00"),
    current: Decimal = Decimal("300.00"),
) -> Goal:
    return Goal(
        id=goal_id,
        user_id=user_id,
        name="Meta",
        target_amount=target,
        current_amount=current,
        currency=currency,
        status=status,
        is_active=active,
    )


def _event_for(transaction: GoalTransaction, *, description: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=transaction.event_id,
        user_id=transaction.user_id,
        account_id=transaction.account_id,
        event_type=EventType.GOAL_RELEASE.value,
        direction=Direction.NEUTRAL.value,
        amount=transaction.source_amount,
        currency=transaction.source_currency,
        description=description,
        metadata_={"goal_id": str(transaction.goal_id)},
    )


def _snapshot(
    *,
    current: str = "250.00",
    remaining: str = "250.00",
    status: str = "active",
    balance: str = "1000.00",
    goal_account: str = "250.00",
    goal_total: str = "250.00",
    account_total: str = "250.00",
    available: str = "750.00",
) -> dict[str, str]:
    return {
        "goal_current_amount": current,
        "goal_remaining_amount": remaining,
        "goal_status": status,
        "account_balance": balance,
        "goal_account_reserved_amount": goal_account,
        "goal_total_reserved_amount": goal_total,
        "account_total_reserved_amount": account_total,
        "available_balance": available,
    }


@pytest.fixture
def repos():
    goal_repo = create_autospec(GoalRepository, instance=True)
    account_repo = create_autospec(AccountRepository, instance=True)
    ledger_repo = create_autospec(LedgerRepository, instance=True)

    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested.return_value = nested
    goal_repo.session = session

    return goal_repo, account_repo, ledger_repo


@pytest.fixture
def service(repos):
    return GoalService(*repos)


def _configure_success(
    repos,
    *,
    user_id: UUID,
    goal_id: UUID,
    account_id: UUID,
    command_id: UUID | None = None,
    account_balance: Decimal = Decimal("1000.00"),
    goal_current: Decimal = Decimal("300.00"),
    goal_target: Decimal = Decimal("500.00"),
    goal_status: str = "active",
    specific_before: Decimal = Decimal("300.00"),
    account_before: Decimal = Decimal("400.00"),
    goal_before: Decimal | None = None,
    release_amount: Decimal = Decimal("50.00"),
):
    goal_repo, account_repo, ledger_repo = repos
    account = _account(
        account_id=account_id,
        user_id=user_id,
        balance=account_balance,
    )
    goal = _goal(
        goal_id=goal_id,
        user_id=user_id,
        current=goal_current,
        target=goal_target,
        status=goal_status,
    )
    account_repo.get_by_id_for_update.return_value = account
    goal_repo.get_by_id_for_update.return_value = goal
    if command_id is not None:
        goal_repo.get_transaction_by_command_id.side_effect = [None, None]

    total_before = goal_before if goal_before is not None else goal_current
    goal_repo.calculate_reserved_by_goal_and_account.side_effect = [
        specific_before,
        specific_before - release_amount,
    ]
    goal_repo.calculate_reserved_by_account.side_effect = [
        account_before,
        account_before - release_amount,
    ]
    goal_repo.calculate_total_reserved_by_goal.side_effect = [
        total_before,
        total_before - release_amount,
    ]
    goal_repo.calculate_progress_by_goal.side_effect = [
        goal_current,
        goal_current - release_amount,
    ]

    event_id = uuid4()
    ledger_repo.insert_event.return_value = SimpleNamespace(
        idempotent=False,
        event=SimpleNamespace(id=event_id),
    )

    captured: list[GoalTransaction] = []

    async def create_transaction(transaction: GoalTransaction):
        transaction.id = uuid4()
        transaction.created_at = datetime.now(UTC)
        captured.append(transaction)
        return transaction

    goal_repo.create_transaction.side_effect = create_transaction
    return account, goal, captured, event_id


def _existing_release(
    service: GoalService,
    *,
    user_id: UUID,
    goal_id: UUID,
    account_id: UUID,
    command_id: UUID,
    amount: Decimal = Decimal("50.00"),
    description: str | None = None,
    snapshot: dict[str, str] | None = None,
    transaction_type: GoalTransactionType = GoalTransactionType.release,
) -> GoalTransaction:
    transaction = GoalTransaction(
        id=uuid4(),
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        event_id=uuid4(),
        transaction_type=transaction_type,
        source_amount=amount,
        source_currency="COP",
        applied_amount=amount,
        goal_currency="COP",
        command_id=command_id,
        metadata_json=snapshot if snapshot is not None else _snapshot(),
    )
    transaction.created_at = datetime.now(UTC)
    transaction.command_fingerprint = service._generate_fingerprint(
        user_id,
        goal_id,
        account_id,
        transaction_type.value,
        amount,
        "COP",
        amount,
        "COP",
        description=description,
        include_description=transaction_type == GoalTransactionType.release,
    )
    return transaction


@pytest.mark.asyncio
async def test_partial_release_persists_neutral_event_and_exact_postwrite_snapshot(service, repos):
    goal_repo, account_repo, ledger_repo = repos
    user_id, goal_id, account_id, command_id = uuid4(), uuid4(), uuid4(), uuid4()
    account, goal, captured, event_id = _configure_success(
        repos,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        command_id=command_id,
    )
    original_balance = account.balance
    payload = GoalReleaseCreate(
        account_id=account_id,
        amount=Decimal("50.00"),
        command_id=command_id,
        description="Liberación parcial",
    )

    result = await service.create_release(user_id, goal_id, payload, str(command_id))

    assert result.released_amount == result.applied_amount == Decimal("50.00")
    assert result.goal_current_amount == Decimal("250.00")
    assert result.goal_remaining_amount == Decimal("250.00")
    assert result.goal_account_reserved_amount == Decimal("250.00")
    assert result.goal_total_reserved_amount == Decimal("250.00")
    assert result.account_total_reserved_amount == Decimal("350.00")
    assert result.available_balance == Decimal("650.00")
    assert result.event_id == event_id
    assert result.idempotent is False
    assert goal.current_amount == Decimal("250.00")
    assert account.balance == original_balance

    event_create = ledger_repo.insert_event.call_args.args[0]
    assert event_create.event_type is EventType.GOAL_RELEASE
    assert event_create.direction is Direction.NEUTRAL
    assert event_create.amount == Decimal("50.00")
    assert event_create.currency == "COP"
    assert event_create.command_id == command_id
    assert event_create.metadata == {
        "goal_id": str(goal_id),
        "transaction_type": "release",
    }

    [transaction] = captured
    assert transaction.transaction_type is GoalTransactionType.release
    assert transaction.source_amount == transaction.applied_amount == Decimal("50.00")
    assert transaction.source_currency == transaction.goal_currency == "COP"
    assert transaction.metadata_json == _snapshot(
        current="250.00",
        remaining="250.00",
        goal_account="250.00",
        goal_total="250.00",
        account_total="350.00",
        available="650.00",
    )
    goal_repo.calculate_reserved_by_goal_and_account.assert_has_awaits(
        [call(user_id, goal_id, account_id), call(user_id, goal_id, account_id)]
    )
    goal_repo.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_total_release_reduces_progress_to_zero_without_touching_balance(service, repos):
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    account, goal, _, _ = _configure_success(
        repos,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        account_balance=Decimal("500.00"),
        goal_current=Decimal("100.00"),
        goal_target=Decimal("500.00"),
        specific_before=Decimal("100.00"),
        account_before=Decimal("100.00"),
        release_amount=Decimal("100.00"),
    )

    result = await service.create_release(
        user_id,
        goal_id,
        GoalReleaseCreate(account_id=account_id, amount=Decimal("100.00")),
        None,
    )

    assert result.goal_current_amount == Decimal("0.00")
    assert result.goal_remaining_amount == Decimal("500.00")
    assert result.goal_status == "active"
    assert result.goal_account_reserved_amount == Decimal("0.00")
    assert result.goal_total_reserved_amount == Decimal("0.00")
    assert result.account_total_reserved_amount == Decimal("0.00")
    assert result.available_balance == Decimal("500.00")
    assert goal.status == "active"
    assert account.balance == Decimal("500.00")


@pytest.mark.asyncio
async def test_release_reopens_completed_goal(service, repos):
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    _configure_success(
        repos,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        goal_current=Decimal("500.00"),
        goal_target=Decimal("500.00"),
        goal_status="completed",
        specific_before=Decimal("500.00"),
        account_before=Decimal("500.00"),
        release_amount=Decimal("10.00"),
    )

    result = await service.create_release(
        user_id,
        goal_id,
        GoalReleaseCreate(account_id=account_id, amount=Decimal("10.00")),
        None,
    )

    assert result.goal_current_amount == Decimal("490.00")
    assert result.goal_remaining_amount == Decimal("10.00")
    assert result.goal_status == "active"


@pytest.mark.asyncio
async def test_release_after_target_date_keeps_goal_active(service, repos):
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    _, goal, _, _ = _configure_success(
        repos,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
    )
    goal.target_date = date.today() - timedelta(days=1)

    result = await service.create_release(
        user_id,
        goal_id,
        GoalReleaseCreate(account_id=account_id, amount=Decimal("50.00")),
        None,
    )

    assert result.goal_status == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "current", "target"),
    [
        ("completed", Decimal("499.99"), Decimal("500.00")),
        ("active", Decimal("500.00"), Decimal("500.00")),
        ("active", Decimal("500.01"), Decimal("500.00")),
    ],
)
async def test_inconsistent_goal_lifecycle_is_rejected_before_reserve_queries(
    service, repos, status, current, target
):
    goal_repo, account_repo, _ = repos
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    account_repo.get_by_id_for_update.return_value = _account(
        account_id=account_id, user_id=user_id
    )
    goal_repo.get_by_id_for_update.return_value = _goal(
        goal_id=goal_id,
        user_id=user_id,
        status=status,
        current=current,
        target=target,
    )

    with pytest.raises(ConflictError, match="inconsistente"):
        await service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(account_id=account_id, amount=Decimal("1.00")),
            None,
        )

    goal_repo.calculate_reserved_by_goal_and_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_specific_and_global_reserves_remain_separate_for_multiple_accounts(service, repos):
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    _configure_success(
        repos,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        goal_current=Decimal("100.00"),
        specific_before=Decimal("40.00"),
        account_before=Decimal("200.00"),
        goal_before=Decimal("100.00"),
        release_amount=Decimal("20.00"),
    )

    result = await service.create_release(
        user_id,
        goal_id,
        GoalReleaseCreate(account_id=account_id, amount=Decimal("20.00")),
        None,
    )

    assert result.goal_account_reserved_amount == Decimal("20.00")
    assert result.goal_total_reserved_amount == Decimal("80.00")
    assert result.account_total_reserved_amount == Decimal("180.00")


@pytest.mark.asyncio
async def test_exact_idempotent_retry_returns_historical_snapshot_before_locks(service, repos):
    goal_repo, account_repo, ledger_repo = repos
    user_id, goal_id, account_id, command_id = uuid4(), uuid4(), uuid4(), uuid4()
    transaction = _existing_release(
        service,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        command_id=command_id,
        description="Original",
    )
    goal_repo.get_transaction_by_command_id.return_value = transaction
    ledger_repo.get_by_command_id.return_value = _event_for(
        transaction,
        description="Original",
    )

    result = await service.create_release(
        user_id,
        goal_id,
        GoalReleaseCreate(
            account_id=account_id,
            amount=Decimal("50.0"),
            description="Original",
        ),
        str(command_id),
    )

    assert result.idempotent is True
    assert result.goal_current_amount == Decimal("250.00")
    assert result.available_balance == Decimal("750.00")
    account_repo.get_by_id_for_update.assert_not_awaited()
    goal_repo.get_by_id_for_update.assert_not_awaited()
    ledger_repo.insert_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_field", ["amount", "account", "description"])
async def test_idempotent_retry_rejects_different_payload(service, repos, changed_field):
    goal_repo, _, ledger_repo = repos
    user_id, goal_id, account_id, command_id = uuid4(), uuid4(), uuid4(), uuid4()
    transaction = _existing_release(
        service,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        command_id=command_id,
        description="Original",
    )
    goal_repo.get_transaction_by_command_id.return_value = transaction
    ledger_repo.get_by_command_id.return_value = _event_for(transaction)
    payload = {
        "account_id": account_id,
        "amount": Decimal("50.00"),
        "description": "Original",
    }
    if changed_field == "amount":
        payload["amount"] = Decimal("51.00")
    elif changed_field == "account":
        payload["account_id"] = uuid4()
    else:
        payload["description"] = "Distinta"

    with pytest.raises(ConflictError):
        await service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(**payload),
            str(command_id),
        )


@pytest.mark.asyncio
async def test_allocation_command_cannot_be_reused_for_release(service, repos):
    goal_repo, account_repo, _ = repos
    user_id, goal_id, account_id, command_id = uuid4(), uuid4(), uuid4(), uuid4()
    allocation = _existing_release(
        service,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        command_id=command_id,
        transaction_type=GoalTransactionType.allocation,
    )
    goal_repo.get_transaction_by_command_id.return_value = allocation

    with pytest.raises(ConflictError):
        await service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(account_id=account_id, amount=Decimal("50.00")),
            str(command_id),
        )

    account_repo.get_by_id_for_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_idempotent_snapshot_must_be_complete(service, repos):
    goal_repo, _, ledger_repo = repos
    user_id, goal_id, account_id, command_id = uuid4(), uuid4(), uuid4(), uuid4()
    transaction = _existing_release(
        service,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        command_id=command_id,
        snapshot={"goal_current_amount": "1.00"},
    )
    goal_repo.get_transaction_by_command_id.return_value = transaction
    ledger_repo.get_by_command_id.return_value = _event_for(transaction)

    with pytest.raises(ConflictError, match="histórica completa"):
        await service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(account_id=account_id, amount=Decimal("50.00")),
            str(command_id),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["payload", "header", "both"])
async def test_command_id_sources_are_supported(service, repos, source):
    user_id, goal_id, account_id, command_id = uuid4(), uuid4(), uuid4(), uuid4()
    _, _, captured, _ = _configure_success(
        repos,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        command_id=command_id,
    )
    payload_command = command_id if source in {"payload", "both"} else None
    header_command = str(command_id) if source in {"header", "both"} else None

    await service.create_release(
        user_id,
        goal_id,
        GoalReleaseCreate(
            account_id=account_id,
            amount=Decimal("50.00"),
            command_id=payload_command,
        ),
        header_command,
    )

    assert captured[0].command_id == command_id
    assert captured[0].command_fingerprint is not None


@pytest.mark.asyncio
async def test_mismatched_payload_and_header_command_ids_fail_before_reads(service, repos):
    goal_repo, account_repo, _ = repos
    with pytest.raises(ValidationError):
        await service.create_release(
            uuid4(),
            uuid4(),
            GoalReleaseCreate(
                account_id=uuid4(),
                amount=Decimal("1.00"),
                command_id=uuid4(),
            ),
            str(uuid4()),
        )
    goal_repo.get_transaction_by_command_id.assert_not_awaited()
    account_repo.get_by_id_for_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_idempotency_header_fails_before_reads(service, repos):
    goal_repo, account_repo, _ = repos
    with pytest.raises(ValidationError):
        await service.create_release(
            uuid4(),
            uuid4(),
            GoalReleaseCreate(account_id=uuid4(), amount=Decimal("1.00")),
            "not-a-uuid",
        )
    goal_repo.get_transaction_by_command_id.assert_not_awaited()
    account_repo.get_by_id_for_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_lock_order_and_post_lock_idempotency_recheck(service, repos):
    goal_repo, account_repo, _ = repos
    user_id, goal_id, account_id, command_id = uuid4(), uuid4(), uuid4(), uuid4()
    order: list[str] = []

    async def command_lookup(_):
        order.append("idempotency")
        return None

    async def account_lock(*_, **__):
        order.append("account")
        return _account(account_id=account_id, user_id=user_id)

    async def goal_lock(_):
        order.append("goal")
        return _goal(goal_id=goal_id, user_id=user_id)

    goal_repo.get_transaction_by_command_id.side_effect = command_lookup
    account_repo.get_by_id_for_update.side_effect = account_lock
    goal_repo.get_by_id_for_update.side_effect = goal_lock
    goal_repo.calculate_reserved_by_goal_and_account.side_effect = [
        Decimal("300.00"),
        Decimal("250.00"),
    ]
    goal_repo.calculate_reserved_by_account.side_effect = [
        Decimal("300.00"),
        Decimal("250.00"),
    ]
    goal_repo.calculate_total_reserved_by_goal.side_effect = [
        Decimal("300.00"),
        Decimal("250.00"),
    ]
    goal_repo.calculate_progress_by_goal.side_effect = [
        Decimal("300.00"),
        Decimal("250.00"),
    ]
    event_id = uuid4()
    repos[2].insert_event.return_value = SimpleNamespace(
        idempotent=False, event=SimpleNamespace(id=event_id)
    )

    async def assign(transaction):
        transaction.id = uuid4()
        transaction.created_at = datetime.now(UTC)

    goal_repo.create_transaction.side_effect = assign

    await service.create_release(
        user_id,
        goal_id,
        GoalReleaseCreate(
            account_id=account_id,
            amount=Decimal("50.00"),
            command_id=command_id,
        ),
        None,
    )

    assert order[:4] == ["idempotency", "account", "goal", "idempotency"]


@pytest.mark.asyncio
async def test_missing_account_stops_before_goal_lock(service, repos):
    goal_repo, account_repo, _ = repos
    account_repo.get_by_id_for_update.return_value = None
    with pytest.raises(NotFoundError):
        await service.create_release(
            uuid4(),
            uuid4(),
            GoalReleaseCreate(account_id=uuid4(), amount=Decimal("1.00")),
            None,
        )
    goal_repo.get_by_id_for_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_goal_is_rejected_after_account_lock(service, repos):
    goal_repo, account_repo, _ = repos
    user_id, account_id = uuid4(), uuid4()
    account_repo.get_by_id_for_update.return_value = _account(
        account_id=account_id, user_id=user_id
    )
    goal_repo.get_by_id_for_update.return_value = None
    with pytest.raises(NotFoundError):
        await service.create_release(
            user_id,
            uuid4(),
            GoalReleaseCreate(account_id=account_id, amount=Decimal("1.00")),
            None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("foreign_account", NotFoundError),
        ("foreign_goal", NotFoundError),
        ("inactive_account", ForbiddenError),
        ("archived_goal", GoalNotActiveError),
        ("cancelled_goal", GoalNotActiveError),
        ("currency_mismatch", ValidationError),
        ("missing_balance", ForbiddenError),
    ],
)
async def test_ownership_state_and_currency_validation(service, repos, case, expected_error):
    goal_repo, account_repo, _ = repos
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    account = _account(account_id=account_id, user_id=user_id)
    goal = _goal(goal_id=goal_id, user_id=user_id)
    if case == "foreign_account":
        account.user_id = uuid4()
    elif case == "foreign_goal":
        goal.user_id = uuid4()
    elif case == "inactive_account":
        account.is_active = False
    elif case == "archived_goal":
        goal.is_active = False
    elif case == "cancelled_goal":
        goal.status = "cancelled"
    elif case == "currency_mismatch":
        account.currency = "USD"
    elif case == "missing_balance":
        account.balance = None
    account_repo.get_by_id_for_update.return_value = account
    goal_repo.get_by_id_for_update.return_value = goal

    with pytest.raises(expected_error):
        await service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(account_id=account_id, amount=Decimal("1.00")),
            None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("specific", "account_total", "goal_total", "progress", "message"),
    [
        (Decimal("0.00"), Decimal("300.00"), Decimal("300.00"), Decimal("300.00"), "cero"),
        (Decimal("-1.00"), Decimal("300.00"), Decimal("300.00"), Decimal("300.00"), "negativos"),
        (Decimal("301.00"), Decimal("300.00"), Decimal("300.00"), Decimal("300.00"), "específica"),
        (
            Decimal("300.00"),
            Decimal("300.00"),
            Decimal("299.00"),
            Decimal("300.00"),
            "inconsistentes",
        ),
        (
            Decimal("300.00"),
            Decimal("300.00"),
            Decimal("300.00"),
            Decimal("299.00"),
            "inconsistentes",
        ),
    ],
)
async def test_prewrite_reservation_drift_is_rejected(
    service, repos, specific, account_total, goal_total, progress, message
):
    goal_repo, account_repo, ledger_repo = repos
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    account_repo.get_by_id_for_update.return_value = _account(
        account_id=account_id, user_id=user_id
    )
    goal_repo.get_by_id_for_update.return_value = _goal(goal_id=goal_id, user_id=user_id)
    goal_repo.calculate_reserved_by_goal_and_account.return_value = specific
    goal_repo.calculate_reserved_by_account.return_value = account_total
    goal_repo.calculate_total_reserved_by_goal.return_value = goal_total
    goal_repo.calculate_progress_by_goal.return_value = progress

    with pytest.raises(ConflictError, match=message):
        await service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(account_id=account_id, amount=Decimal("1.00")),
            None,
        )
    ledger_repo.insert_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_above_specific_reserve_is_rejected(service, repos):
    goal_repo, account_repo, _ = repos
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    account_repo.get_by_id_for_update.return_value = _account(
        account_id=account_id, user_id=user_id
    )
    goal_repo.get_by_id_for_update.return_value = _goal(
        goal_id=goal_id, user_id=user_id, current=Decimal("300.00")
    )
    goal_repo.calculate_reserved_by_goal_and_account.return_value = Decimal("10.00")
    goal_repo.calculate_reserved_by_account.return_value = Decimal("300.00")
    goal_repo.calculate_total_reserved_by_goal.return_value = Decimal("300.00")
    goal_repo.calculate_progress_by_goal.return_value = Decimal("300.00")

    with pytest.raises(ConflictError, match="excede la reserva"):
        await service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(account_id=account_id, amount=Decimal("11.00")),
            None,
        )


@pytest.mark.asyncio
async def test_release_above_goal_current_amount_is_rejected(service, repos):
    goal_repo, account_repo, _ = repos
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    account_repo.get_by_id_for_update.return_value = _account(
        account_id=account_id, user_id=user_id
    )
    goal_repo.get_by_id_for_update.return_value = _goal(
        goal_id=goal_id, user_id=user_id, current=Decimal("10.00")
    )
    goal_repo.calculate_reserved_by_goal_and_account.return_value = Decimal("20.00")
    goal_repo.calculate_reserved_by_account.return_value = Decimal("20.00")
    goal_repo.calculate_total_reserved_by_goal.return_value = Decimal("10.00")
    goal_repo.calculate_progress_by_goal.return_value = Decimal("10.00")

    with pytest.raises(ConflictError, match="específica"):
        await service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(account_id=account_id, amount=Decimal("11.00")),
            None,
        )


@pytest.mark.asyncio
async def test_postwrite_drift_rolls_back_service_savepoint(service, repos):
    goal_repo, _, _ = repos
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    _configure_success(
        repos,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
    )
    goal_repo.calculate_total_reserved_by_goal.side_effect = [
        Decimal("300.00"),
        Decimal("249.99"),
    ]

    with pytest.raises(ConflictError, match="persistido"):
        await service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(account_id=account_id, amount=Decimal("50.00")),
            None,
        )

    nested = goal_repo.session.begin_nested.return_value
    assert nested.__aexit__.await_args.args[0] is ConflictError


@pytest.mark.asyncio
async def test_event_insert_failure_aborts_before_transaction_and_goal_mutation(service, repos):
    goal_repo, _, ledger_repo = repos
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    _, goal, _, _ = _configure_success(
        repos,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
    )
    ledger_repo.insert_event.side_effect = RuntimeError("ledger unavailable")

    with pytest.raises(RuntimeError, match="ledger unavailable"):
        await service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(account_id=account_id, amount=Decimal("50.00")),
            None,
        )

    goal_repo.create_transaction.assert_not_awaited()
    assert goal.current_amount == Decimal("300.00")


@pytest.mark.asyncio
async def test_transaction_insert_failure_aborts_before_goal_mutation(service, repos):
    goal_repo, _, _ = repos
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    _, goal, _, _ = _configure_success(
        repos,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
    )
    goal_repo.create_transaction.side_effect = RuntimeError("transaction insert failed")

    with pytest.raises(RuntimeError, match="transaction insert failed"):
        await service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(account_id=account_id, amount=Decimal("50.00")),
            None,
        )

    assert goal.current_amount == Decimal("300.00")


@pytest.mark.asyncio
async def test_goal_flush_failure_propagates_through_service_savepoint(service, repos):
    goal_repo, _, _ = repos
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    _configure_success(
        repos,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
    )
    goal_repo.session.flush.side_effect = RuntimeError("goal flush failed")

    with pytest.raises(RuntimeError, match="goal flush failed"):
        await service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(account_id=account_id, amount=Decimal("50.00")),
            None,
        )

    nested = goal_repo.session.begin_nested.return_value
    assert nested.__aexit__.await_args.args[0] is RuntimeError


class _ConstraintError(Exception):
    def __init__(self, constraint_name: str):
        super().__init__(constraint_name)
        self.constraint_name = constraint_name


@pytest.mark.asyncio
async def test_goal_transaction_unique_race_recovers_committed_winner(service, repos):
    goal_repo, _, ledger_repo = repos
    user_id, goal_id, account_id, command_id = uuid4(), uuid4(), uuid4(), uuid4()
    _configure_success(
        repos,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        command_id=command_id,
    )
    winner = _existing_release(
        service,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        command_id=command_id,
    )
    goal_repo.get_transaction_by_command_id.side_effect = [None, None, winner]
    goal_repo.create_transaction.side_effect = IntegrityError(
        "insert",
        {},
        _ConstraintError("uq_gtx_command_id"),
    )
    ledger_repo.get_by_command_id.return_value = _event_for(winner)

    result = await service.create_release(
        user_id,
        goal_id,
        GoalReleaseCreate(account_id=account_id, amount=Decimal("50.00")),
        str(command_id),
    )

    assert result.idempotent is True
    assert result.transaction_id == winner.id


@pytest.mark.asyncio
async def test_unrelated_integrity_error_is_not_treated_as_idempotency(service, repos):
    goal_repo, _, _ = repos
    user_id, goal_id, account_id = uuid4(), uuid4(), uuid4()
    _configure_success(
        repos,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
    )
    error = IntegrityError("insert", {}, _ConstraintError("ck_gtx_source_amount_positive"))
    goal_repo.create_transaction.side_effect = error

    with pytest.raises(IntegrityError) as raised:
        await service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(account_id=account_id, amount=Decimal("50.00")),
            None,
        )
    assert raised.value is error


@pytest.mark.asyncio
async def test_ledger_idempotent_result_without_goal_transaction_is_conflict(service, repos):
    _, _, ledger_repo = repos
    user_id, goal_id, account_id, command_id = uuid4(), uuid4(), uuid4(), uuid4()
    _configure_success(
        repos,
        user_id=user_id,
        goal_id=goal_id,
        account_id=account_id,
        command_id=command_id,
    )
    ledger_repo.insert_event.return_value = SimpleNamespace(
        idempotent=True,
        event=SimpleNamespace(id=uuid4()),
    )
    repos[0].get_transaction_by_command_id.side_effect = [None, None, None]

    with pytest.raises(ConflictError, match="otra operación financiera"):
        await service.create_release(
            user_id,
            goal_id,
            GoalReleaseCreate(account_id=account_id, amount=Decimal("50.00")),
            str(command_id),
        )


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0"),
        Decimal("-0.01"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1000000000000.00"),
        Decimal("1.001"),
    ],
)
def test_release_schema_rejects_invalid_amounts(amount):
    with pytest.raises(PydanticValidationError):
        GoalReleaseCreate(account_id=uuid4(), amount=amount)


def test_release_schema_accepts_numeric_14_2_maximum_and_optional_command():
    command_id = uuid4()
    payload = GoalReleaseCreate(
        account_id=uuid4(),
        amount=Decimal("999999999999.99"),
        command_id=command_id,
        description="x" * 255,
    )
    assert payload.amount == Decimal("999999999999.99")
    assert payload.command_id == command_id


def test_release_schema_forbids_authoritative_currency_and_unknown_fields():
    with pytest.raises(PydanticValidationError):
        GoalReleaseCreate(
            account_id=uuid4(),
            amount=Decimal("1.00"),
            currency="COP",
            user_id=uuid4(),
        )


def test_release_schema_rejects_description_over_255():
    with pytest.raises(PydanticValidationError):
        GoalReleaseCreate(
            account_id=uuid4(),
            amount=Decimal("1.00"),
            description="x" * 256,
        )
