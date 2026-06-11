from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.security import AuthenticatedIdentity
from app.users.repository import UserRepository
from app.users.schemas import UserRead, UserOnboardingRequest, UserOnboardingResponse
from app.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def get_user_service(session: AsyncSession = Depends(get_db_session)) -> UserService:
    repo = UserRepository(session)
    return UserService(repo)


@router.get("/me", response_model=UserRead)
async def get_me(
    identity: AuthenticatedIdentity, service: UserService = Depends(get_user_service)
) -> UserRead:
    return await service.get_current_user_profile(identity)

@router.post("/me/bootstrap", response_model=UserOnboardingResponse)
async def bootstrap_user(
    payload: UserOnboardingRequest,
    identity: AuthenticatedIdentity,
    service: UserService = Depends(get_user_service)
) -> UserOnboardingResponse:
    return await service.onboard_user(identity, payload)
