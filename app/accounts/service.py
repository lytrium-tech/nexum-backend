from uuid import UUID

from app.accounts.exceptions import AccountDuplicateError, AccountForbiddenError
from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.accounts.schemas import (
    AccountCreate,
    AccountRead,
    AccountSummary,
    AccountUpdate,
    BalanceAdjustmentCreate,
)
from app.core.errors import NotFoundError
from app.core.utils import clean_presentation_name, normalize_name
from app.ledger.enums import Direction, EventType
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate


class AccountService:
    def __init__(self, repository: AccountRepository, ledger_repo: LedgerRepository | None = None):
        self.repository = repository
        self.ledger_repo = ledger_repo

    async def list_accounts(self, auth_user_id: UUID) -> list[AccountRead]:
        accounts = await self.repository.list_by_user(auth_user_id)
        return [AccountRead.model_validate(a) for a in accounts]

    async def get_account(self, auth_user_id: UUID, account_id: UUID) -> AccountRead:
        account = await self._get_account_or_404(account_id)
        if account.user_id != auth_user_id:
            raise AccountForbiddenError()
        return AccountRead.model_validate(account)

    async def create_account(self, auth_user_id: UUID, payload: AccountCreate) -> AccountRead:
        import uuid

        norm_name = normalize_name(payload.name)
        if not norm_name:
            raise ValueError("El nombre no puede estar vacío.")

        exists = await self.repository.check_name_exists(auth_user_id, norm_name)
        if exists:
            raise AccountDuplicateError()

        db_account = Account(
            user_id=auth_user_id,
            name=clean_presentation_name(payload.name),
            type=payload.type.value,
            currency=payload.currency,
            balance=payload.initial_balance,
            is_active=True,
        )
        created = await self.repository.create(db_account)

        if payload.initial_balance > 0 and self.ledger_repo:
            event_create = LedgerEventCreate(
                user_id=auth_user_id,
                account_id=created.id,
                category_id=None,
                event_type=EventType.OPENING_BALANCE,
                direction=Direction.INFLOW,
                amount=payload.initial_balance,
                description="Saldo inicial",
                source="api",
                command_id=uuid.uuid4(),
                source_message_id=None,
                raw_message=None,
                metadata={},
            )
            await self.ledger_repo.insert_event(event_create)

        return AccountRead.model_validate(created)

    async def update_account(
        self, auth_user_id: UUID, account_id: UUID, payload: AccountUpdate
    ) -> AccountRead:
        account = await self._get_account_or_404(account_id)
        if account.user_id != auth_user_id:
            raise AccountForbiddenError()

        if payload.name is not None:
            norm_name = normalize_name(payload.name)
            if not norm_name:
                raise ValueError("El nombre no puede estar vacío.")
            if normalize_name(account.name) != norm_name:
                exists = await self.repository.check_name_exists(auth_user_id, norm_name)
                if exists:
                    raise AccountDuplicateError()
            account.name = clean_presentation_name(payload.name)

        if payload.is_active is not None:
            account.is_active = payload.is_active

        await self.repository.session.flush()
        return AccountRead.model_validate(account)

    async def delete_account(self, auth_user_id: UUID, account_id: UUID) -> None:
        account = await self._get_account_or_404(account_id)
        if account.user_id != auth_user_id:
            raise AccountForbiddenError()
        account.is_active = False
        await self.repository.session.flush()

    async def create_balance_adjustment(
        self, auth_user_id: UUID, account_id: UUID, payload: "BalanceAdjustmentCreate"
    ) -> AccountRead:
        import uuid

        account = await self.repository.get_by_id_for_update(account_id)
        if not account:
            raise NotFoundError(message="Cuenta no encontrada.")
        if account.user_id != auth_user_id:
            raise AccountForbiddenError()

        if payload.type not in (EventType.OPENING_BALANCE, EventType.BALANCE_ADJUSTMENT):
            raise ValueError("El tipo debe ser opening_balance o balance_adjustment.")

        ledger_dir = Direction.INFLOW if payload.direction == "increase" else Direction.OUTFLOW

        if payload.direction == "increase":
            account.balance += payload.amount
        else:
            account.balance -= payload.amount

        if self.ledger_repo:
            event_create = LedgerEventCreate(
                user_id=auth_user_id,
                account_id=account.id,
                category_id=None,
                event_type=payload.type,
                direction=ledger_dir,
                amount=payload.amount,
                description=payload.description or "Ajuste de balance",
                source="api",
                command_id=uuid.uuid4(),
                source_message_id=None,
                raw_message=None,
                metadata={},
            )
            await self.ledger_repo.insert_event(event_create)

        await self.repository.session.flush()
        return AccountRead.model_validate(account)

    async def _get_account_or_404(self, account_id: UUID) -> Account:
        account = await self.repository.get_by_id(account_id)
        if not account:
            raise NotFoundError()
        return account

    async def get_summary(self, auth_user_id: UUID) -> AccountSummary:
        accounts = await self.repository.list_by_user_all(auth_user_id)
        total = sum(a.balance for a in accounts if a.is_active)
        return AccountSummary(
            total_balance=total,
            accounts_count=len(accounts),
            active_accounts_count=sum(1 for a in accounts if a.is_active),
            currency="COP",
        )
