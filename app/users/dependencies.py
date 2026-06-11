from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.security import AuthenticatedIdentity
from app.users.repository import UserRepository
from app.users.schemas import UserRead
from app.users.service import UserService


def get_user_service(session: AsyncSession = Depends(get_db_session)) -> UserService:
    repo = UserRepository(session)
    return UserService(repo)


async def get_current_user_profile_dep(
    identity: AuthenticatedIdentity, service: UserService = Depends(get_user_service)
) -> UserRead:
    return await service.get_current_user_profile(identity)


CurrentUserProfile = Annotated[UserRead, Depends(get_current_user_profile_dep)]
