from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.categories.repository import CategoryRepository
from app.categories.schemas import CategoryCreate, CategoryRead, CategoryUpdate
from app.categories.service import CategoryService
from app.core.database import get_db_session
from app.core.uow import UnitOfWork
from app.users.dependencies import CurrentUserProfile

router = APIRouter(prefix="/categories", tags=["categories"])


def get_category_service(session: AsyncSession = Depends(get_db_session)) -> CategoryService:
    repo = CategoryRepository(session)
    return CategoryService(repo)


@router.get("", response_model=list[CategoryRead])
async def list_categories(
    current_profile: CurrentUserProfile,
    include_inactive: bool = False,
    type: str | None = None,
    service: CategoryService = Depends(get_category_service),
) -> list[CategoryRead]:
    user_id = current_profile.id
    return await service.list_categories(user_id, include_inactive, type)


@router.post("", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: CategoryCreate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> CategoryRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_category_service(session)
        user_id = current_profile.id
        result = await service.create_category(user_id, payload)
        return result


@router.patch("/{category_id}", response_model=CategoryRead)
async def update_category(
    category_id: UUID,
    payload: CategoryUpdate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> CategoryRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_category_service(session)
        user_id = current_profile.id
        result = await service.update_category(user_id, category_id, payload)
        return result


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT, deprecated=True)
async def delete_category(
    category_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_category_service(session)
        user_id = current_profile.id
        await service.delete_category(user_id, category_id)


@router.post("/{category_id}/archive", response_model=CategoryRead)
async def archive_category(
    category_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> CategoryRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_category_service(session)
        user_id = current_profile.id
        result = await service.archive_category(user_id, category_id)
        return result


@router.post("/{category_id}/restore", response_model=CategoryRead)
async def restore_category(
    category_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> CategoryRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_category_service(session)
        user_id = current_profile.id
        result = await service.restore_category(user_id, category_id)
        return result
