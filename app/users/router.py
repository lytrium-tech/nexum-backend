from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.security import CurrentUser
from app.users.repository import UserRepository
from app.users.schemas import UserRead
from app.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def get_user_service(session: AsyncSession = Depends(get_db_session)) -> UserService:
    repo = UserRepository(session)
    return UserService(repo)


@router.get("/me", response_model=UserRead)
async def get_me(
    current_user: CurrentUser, service: UserService = Depends(get_user_service)
) -> UserRead:
    return await service.get_current_user_profile(current_user)
