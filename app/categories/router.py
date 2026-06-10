from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.categories.repository import CategoryRepository
from app.categories.schemas import CategoryCreate, CategoryRead, CategoryUpdate
from app.categories.service import CategoryService
from app.core.database import get_db_session
from app.core.security import CurrentUser
from app.core.uow import UnitOfWork
from app.users.repository import UserRepository
from app.users.service import UserService

router = APIRouter(prefix="/categories", tags=["categories"])


def get_category_service(session: AsyncSession = Depends(get_db_session)) -> CategoryService:
    repo = CategoryRepository(session)
    user_repo = UserRepository(session)
    user_service = UserService(user_repo)
    return CategoryService(repo, user_service)


async def resolve_user_id(current_user: CurrentUser, user_service: UserService) -> UUID:
    user = await user_service.get_current_user_profile(current_user)
    return user.id


@router.get("", response_model=list[CategoryRead])
async def list_categories(
    current_user: CurrentUser, service: CategoryService = Depends(get_category_service)
) -> list[CategoryRead]:
    user_id = await resolve_user_id(current_user, service.user_service)
    return await service.list_categories(user_id)


@router.post("", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: CategoryCreate,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_db_session),
) -> CategoryRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_category_service(session)
        user_id = await resolve_user_id(current_user, service.user_service)
        result = await service.create_category(user_id, payload)
        return result


@router.patch("/{category_id}", response_model=CategoryRead)
async def update_category(
    category_id: UUID,
    payload: CategoryUpdate,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_db_session),
) -> CategoryRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_category_service(session)
        user_id = await resolve_user_id(current_user, service.user_service)
        result = await service.update_category(user_id, category_id, payload)
        return result


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: UUID, current_user: CurrentUser, session: AsyncSession = Depends(get_db_session)
) -> None:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_category_service(session)
        user_id = await resolve_user_id(current_user, service.user_service)
        await service.delete_category(user_id, category_id)
