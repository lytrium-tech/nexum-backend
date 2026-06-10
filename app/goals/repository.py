from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import normalize_name
from app.goals.models import Goal, GoalContribution


class GoalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, goal_id: UUID) -> Goal | None:
        result = await self.session.execute(select(Goal).where(Goal.id == goal_id))
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, goal_id: UUID) -> Goal | None:
        result = await self.session.execute(
            select(Goal).where(Goal.id == goal_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_active(self, user_id: UUID) -> list[Goal]:
        result = await self.session.execute(
            select(Goal)
            .where(Goal.user_id == user_id)
            .where(Goal.is_active)
            .order_by(Goal.created_at.desc())
        )
        return list(result.scalars().all())

    async def check_name_exists(self, user_id: UUID, norm_name: str) -> bool:
        result = await self.session.execute(
            select(Goal).where(Goal.user_id == user_id).where(Goal.is_active)
        )
        for goal in result.scalars().all():
            if normalize_name(goal.name) == norm_name:
                return True
        return False

    async def create(self, goal: Goal) -> Goal:
        self.session.add(goal)
        await self.session.flush()
        return goal

    async def create_contribution(self, contribution: GoalContribution) -> GoalContribution:
        self.session.add(contribution)
        await self.session.flush()
        return contribution
