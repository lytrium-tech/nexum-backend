from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account


class AccountRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_by_user(self, user_id: UUID) -> Sequence[Account]:
        stmt = select(Account).where(Account.user_id == user_id, Account.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_id(self, account_id: UUID) -> Account | None:
        stmt = select(Account).where(Account.id == account_id, Account.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def check_name_exists(self, user_id: UUID, normalized_name: str) -> bool:
        stmt = select(Account.id).where(
            Account.user_id == user_id,
            Account.is_active.is_(True),
            func.lower(Account.name) == normalized_name,
        )
        res = await self.session.execute(stmt)
        return res.first() is not None

    async def create(self, account: Account) -> Account:
        self.session.add(account)
        await self.session.flush()
        return account

    async def get_by_id_for_update(self, account_id: UUID) -> Account | None:
        """
        Bloquea la cuenta con SELECT FOR UPDATE para evitar condiciones de carrera.
        Debe ejecutarse dentro de un UnitOfWork (transacción activa).
        """
        stmt = (
            select(Account)
            .where(Account.id == account_id, Account.is_active.is_(True))
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_balance(self, account: Account, amount_diff: Decimal) -> None:
        """
        Actualiza matemáticamente el balance de una cuenta en memoria.
        Requiere que la cuenta haya sido bloqueada previamente con get_by_id_for_update.
        """
        account.balance += amount_diff
        await self.session.flush()

    async def list_by_user_all(self, user_id: UUID) -> Sequence[Account]:
        stmt = select(Account).where(Account.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()
