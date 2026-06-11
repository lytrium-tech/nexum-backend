from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.core.database import get_db_session
from app.core.uow import UnitOfWork
from app.goals.repository import GoalRepository
from app.goals.schemas import (
    GoalContributionCreate,
    GoalContributionResult,
    GoalCreate,
    GoalRead,
    GoalUpdate,
)
from app.goals.service import GoalService
from app.ledger.repository import LedgerRepository
from app.users.dependencies import CurrentUserProfile

router = APIRouter(prefix="/goals", tags=["goals"])


def get_goal_service(session: AsyncSession = Depends(get_db_session)) -> GoalService:
    repo = GoalRepository(session)
    account_repo = AccountRepository(session)
    ledger_repo = LedgerRepository(session)
    return GoalService(repo, account_repo, ledger_repo)





@router.post("", response_model=GoalRead, status_code=status.HTTP_201_CREATED)
async def create_goal(
    payload: GoalCreate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> GoalRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_goal_service(session)
        user_id = current_profile.id
        return await service.create_goal(user_id, payload)


@router.get("", response_model=list[GoalRead])
async def list_goals(
    current_profile: CurrentUserProfile, service: GoalService = Depends(get_goal_service)
) -> list[GoalRead]:
    user_id = current_profile.id
    return await service.list_goals(user_id)


@router.get("/{goal_id}", response_model=GoalRead)
async def get_goal(
    goal_id: UUID,
    current_profile: CurrentUserProfile,
    service: GoalService = Depends(get_goal_service),
) -> GoalRead:
    user_id = current_profile.id
    return await service.get_goal(user_id, goal_id)


@router.patch("/{goal_id}", response_model=GoalRead)
async def update_goal(
    goal_id: UUID,
    payload: GoalUpdate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> GoalRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_goal_service(session)
        user_id = current_profile.id
        return await service.update_goal(user_id, goal_id, payload)


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(
    goal_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_goal_service(session)
        user_id = current_profile.id
        await service.delete_goal(user_id, goal_id)


@router.post("/{goal_id}/contributions", response_model=GoalContributionResult)
async def create_contribution(
    goal_id: UUID,
    payload: GoalContributionCreate,
    current_profile: CurrentUserProfile,
    idempotency_key: str | None = Header(None),
    session: AsyncSession = Depends(get_db_session),
) -> GoalContributionResult:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_goal_service(session)
        user_id = current_profile.id
        return await service.create_contribution(user_id, goal_id, payload, idempotency_key)
