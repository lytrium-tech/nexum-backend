from collections.abc import Sequence
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
