import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.accounts.schemas import BalanceAdjustmentCreate
from app.accounts.service import AccountService
from app.core.errors import ConflictError
from app.goals.repository import GoalRepository
from app.main import app
from app.transfers.schemas import TransferResult


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _RecordingSession:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _RowsResult(self.rows)


def _account(*, user_id, balance, name="Cuenta"):
    return Account(
        id=uuid4(),
        user_id=user_id,
        name=name,
        type="bank",
        balance=balance,
        currency="COP",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_reserved_amounts_for_user_is_one_source_semantics_query_with_ownership():
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    account_a = UUID("22222222-2222-2222-2222-222222222222")
    account_b = UUID("33333333-3333-3333-3333-333333333333")
    session = _RecordingSession(
        [
            SimpleNamespace(account_id=account_a, reserved=Decimal("175000.00")),
            SimpleNamespace(account_id=account_b, reserved=Decimal("25.50")),
        ]
    )
    repository = GoalRepository(session)  # type: ignore[arg-type]

    result = await repository.get_reserved_amounts_for_user(user_id)

    assert result == {
        account_a: Decimal("175000.00"),
        account_b: Decimal("25.50"),
    }
    assert len(session.statements) == 1
    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    lowered = sql.lower()
    assert "sum(sum" not in lowered
    assert "sum(goal_transactions.source_amount)" in lowered
    assert "applied_amount" not in lowered
    assert "join accounts on accounts.id = goal_transactions.account_id" in lowered
    assert f"goal_transactions.user_id = '{user_id}'" in lowered
    assert f"accounts.user_id = '{user_id}'" in lowered
    assert "in ('allocation', 'legacy_import', 'release')" in lowered
    assert "adjustment" not in lowered
    assert "goal_contributions" not in lowered
    assert "group by goal_transactions.account_id" in lowered
    assert "goal_transactions.goal_id" not in lowered
    assert " limit " not in lowered
    assert " offset " not in lowered


@pytest.mark.asyncio
async def test_account_list_uses_one_bulk_reservation_query_without_changing_gross_balance():
    user_id = uuid4()
    first = _account(user_id=user_id, balance=Decimal("1000000.00"), name="Primera")
    second = _account(user_id=user_id, balance=Decimal("500000.00"), name="Segunda")
    account_repository = AsyncMock(spec=AccountRepository)
    account_repository.list_by_user.return_value = [first, second]
    goal_repository = AsyncMock(spec=GoalRepository)
    goal_repository.get_reserved_amounts_for_user.return_value = {
        first.id: Decimal("175000.00"),
        second.id: Decimal("50000.00"),
    }
    service = AccountService(account_repository, goal_repository=goal_repository)

    result = await service.list_accounts(user_id)

    assert [item.balance for item in result] == [
        Decimal("1000000.00"),
        Decimal("500000.00"),
    ]
    assert [item.available_balance for item in result] == [
        Decimal("825000.00"),
        Decimal("450000.00"),
    ]
    account_repository.list_by_user.assert_awaited_once_with(user_id, include_archived=False)
    goal_repository.get_reserved_amounts_for_user.assert_awaited_once_with(user_id)
    goal_repository.calculate_reserved_by_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_account_detail_uses_source_reserve_and_preserves_gross_balance():
    user_id = uuid4()
    account = _account(user_id=user_id, balance=Decimal("1000000.00"))
    account_repository = AsyncMock(spec=AccountRepository)
    account_repository.get_by_id.return_value = account
    goal_repository = AsyncMock(spec=GoalRepository)
    goal_repository.calculate_reserved_by_account.return_value = Decimal("175000.00")
    service = AccountService(account_repository, goal_repository=goal_repository)

    result = await service.get_account(user_id, account.id)

    assert result.balance == Decimal("1000000.00")
    assert result.available_balance == Decimal("825000.00")
    assert account.balance == Decimal("1000000.00")
    goal_repository.calculate_reserved_by_account.assert_awaited_once_with(account.id, user_id)


@pytest.mark.asyncio
async def test_account_without_reservations_exposes_gross_balance_as_available():
    user_id = uuid4()
    account = _account(user_id=user_id, balance=Decimal("1000.00"))
    account_repository = AsyncMock(spec=AccountRepository)
    account_repository.list_by_user.return_value = [account]
    goal_repository = AsyncMock(spec=GoalRepository)
    goal_repository.get_reserved_amounts_for_user.return_value = {}
    service = AccountService(account_repository, goal_repository=goal_repository)

    [result] = await service.list_accounts(user_id)

    assert result.balance == result.available_balance == Decimal("1000.00")


@pytest.mark.parametrize("reserved", [Decimal("-0.01"), Decimal("1000.01")])
@pytest.mark.asyncio
async def test_account_list_rejects_inconsistent_reservations(reserved):
    user_id = uuid4()
    account = _account(user_id=user_id, balance=Decimal("1000.00"))
    account_repository = AsyncMock(spec=AccountRepository)
    account_repository.list_by_user.return_value = [account]
    goal_repository = AsyncMock(spec=GoalRepository)
    goal_repository.get_reserved_amounts_for_user.return_value = {account.id: reserved}
    service = AccountService(account_repository, goal_repository=goal_repository)

    with pytest.raises(ConflictError):
        await service.list_accounts(user_id)


def test_release_symmetry_restores_exact_available_balance_without_changing_gross():
    account = _account(user_id=uuid4(), balance=Decimal("1000000.00"))

    assert AccountService._set_available_balance(account, Decimal("175000.00")) == Decimal(
        "825000.00"
    )
    assert AccountService._set_available_balance(account, Decimal("125000.00")) == Decimal(
        "875000.00"
    )
    assert AccountService._set_available_balance(account, Decimal("0.00")) == Decimal("1000000.00")
    assert account.balance == Decimal("1000000.00")


@pytest.mark.asyncio
async def test_noop_balance_adjustment_still_populates_authoritative_availability():
    user_id = uuid4()
    account = _account(user_id=user_id, balance=Decimal("1000.00"))
    account_repository = AsyncMock(spec=AccountRepository)
    account_repository.get_by_id_for_update.return_value = account
    goal_repository = AsyncMock(spec=GoalRepository)
    goal_repository.calculate_reserved_by_account.return_value = Decimal("100.00")
    service = AccountService(account_repository, goal_repository=goal_repository)

    result = await service.create_balance_adjustment(
        user_id,
        account.id,
        BalanceAdjustmentCreate(
            target_balance=Decimal("1000.00"),
            reason="Conciliación sin cambio",
            idempotency_key=uuid4(),
        ),
    )

    assert result.balance == Decimal("1000.00")
    assert result.available_balance == Decimal("900.00")
    goal_repository.calculate_reserved_by_account.assert_awaited_once_with(account.id, user_id)


def test_available_balance_is_transient_and_transfer_contract_remains_gross_only():
    assert "available_balance" not in Account.__table__.columns
    account = _account(user_id=uuid4(), balance=Decimal("1000.00"))
    assert account.available_balance == Decimal("1000.00")
    account.available_balance = Decimal("900.00")
    assert account.available_balance == Decimal("900.00")
    assert account.balance == Decimal("1000.00")

    schema = TransferResult.model_json_schema()
    transfer_account = schema["$defs"]["TransferAccountRead"]
    assert "balance" in transfer_account["properties"]
    assert "available_balance" not in transfer_account["properties"]


def test_openapi_exposes_additive_account_availability_without_transfer_drift():
    runtime = app.openapi()
    persisted = json.loads(Path("openapi.json").read_text(encoding="utf-8"))

    assert persisted == runtime
    account = runtime["components"]["schemas"]["AccountRead"]
    account_detail = runtime["components"]["schemas"]["AccountDetailRead"]
    transfer_account = runtime["components"]["schemas"]["TransferAccountRead"]
    assert {"balance", "available_balance"} <= account["properties"].keys()
    assert {"balance", "available_balance"} <= account_detail["properties"].keys()
    assert "available_balance" not in transfer_account["properties"]
