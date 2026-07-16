from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.categories.models import Category


class CategoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_available(
        self, user_id: UUID, include_inactive: bool = False, type_: str | None = None
    ) -> Sequence[Category]:
        conditions = [or_(Category.user_id == user_id, Category.user_id.is_(None))]
        if not include_inactive:
            conditions.append(Category.is_active.is_(True))
        if type_:
            conditions.append(Category.type == type_)

        stmt = select(Category).where(*conditions)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_id(self, category_id: UUID) -> Category | None:
        stmt = select(Category).where(Category.id == category_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_stable_key(self, stable_key: str) -> Category | None:
        stmt = select(Category).where(Category.stable_key == stable_key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def check_name_exists(
        self, user_id: UUID, normalized_name: str, type_: str | None = None
    ) -> bool:
        conditions = [
            or_(Category.user_id == user_id, Category.user_id.is_(None)),
            Category.normalized_name == normalized_name,
        ]
        if type_:
            conditions.append(Category.type == type_)

        stmt = select(Category.id).where(*conditions)
        res = await self.session.execute(stmt)
        return res.first() is not None

    async def create(self, category: Category) -> Category:
        self.session.add(category)
        await self.session.flush()
        return category
