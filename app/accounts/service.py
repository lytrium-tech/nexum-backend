from uuid import UUID

from app.accounts.exceptions import AccountDuplicateError, AccountForbiddenError
from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.accounts.schemas import AccountCreate, AccountRead, AccountUpdate
from app.core.errors import NotFoundError
from app.core.utils import clean_presentation_name, normalize_name


class AccountService:
    def __init__(self, repository: AccountRepository):
        self.repository = repository
    async def list_accounts(self, auth_user_id: UUID) -> list[AccountRead]:
        accounts = await self.repository.list_by_user(auth_user_id)
        return [AccountRead.model_validate(a) for a in accounts]

    async def get_account(self, auth_user_id: UUID, account_id: UUID) -> AccountRead:
        account = await self._get_account_or_404(account_id)
        if account.user_id != auth_user_id:
            raise AccountForbiddenError()
        return AccountRead.model_validate(account)

    async def create_account(self, auth_user_id: UUID, payload: AccountCreate) -> AccountRead:
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
            balance=0,
            is_active=True,
        )
        created = await self.repository.create(db_account)
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

        await self.repository.session.flush()
        return AccountRead.model_validate(account)

    async def delete_account(self, auth_user_id: UUID, account_id: UUID) -> None:
        account = await self._get_account_or_404(account_id)
        if account.user_id != auth_user_id:
            raise AccountForbiddenError()

        account.is_active = False
        await self.repository.session.flush()

    async def _get_account_or_404(self, account_id: UUID) -> Account:
        account = await self.repository.get_by_id(account_id)
        if not account:
            raise NotFoundError(message="Cuenta no encontrada.")
        return account
