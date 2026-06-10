from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.categories.models import Category


class CategoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_available(self, user_id: UUID) -> Sequence[Category]:
        stmt = select(Category).where(
            or_(Category.user_id == user_id, Category.user_id.is_(None)),
            Category.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_id(self, category_id: UUID) -> Category | None:
        stmt = select(Category).where(Category.id == category_id, Category.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def check_name_exists(self, user_id: UUID, normalized_name: str) -> bool:
        stmt = select(Category.id).where(
            or_(Category.user_id == user_id, Category.user_id.is_(None)),
            Category.is_active.is_(True),
            func.lower(Category.name) == normalized_name,
        )
        res = await self.session.execute(stmt)
        return res.first() is not None

    async def create(self, category: Category) -> Category:
        self.session.add(category)
        await self.session.flush()
        return category
