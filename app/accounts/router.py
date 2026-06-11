from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.accounts.schemas import AccountCreate, AccountRead, AccountUpdate
from app.accounts.service import AccountService
from app.core.database import get_db_session
from app.core.uow import UnitOfWork
from app.users.dependencies import CurrentUserProfile

router = APIRouter(prefix="/accounts", tags=["accounts"])


def get_account_service(session: AsyncSession = Depends(get_db_session)) -> AccountService:
    repo = AccountRepository(session)
    return AccountService(repo)





@router.get("", response_model=list[AccountRead])
async def list_accounts(
    current_profile: CurrentUserProfile, service: AccountService = Depends(get_account_service)
) -> list[AccountRead]:
    user_id = current_profile.id
    return await service.list_accounts(user_id)


@router.get("/{account_id}", response_model=AccountRead)
async def get_account(
    account_id: UUID,
    current_profile: CurrentUserProfile,
    service: AccountService = Depends(get_account_service),
) -> AccountRead:
    user_id = current_profile.id
    return await service.get_account(user_id, account_id)


@router.post("", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> AccountRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_account_service(session)
        user_id = current_profile.id
        result = await service.create_account(user_id, payload)
        return result


@router.patch("/{account_id}", response_model=AccountRead)
async def update_account(
    account_id: UUID,
    payload: AccountUpdate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> AccountRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_account_service(session)
        user_id = current_profile.id
        result = await service.update_account(user_id, account_id, payload)
        return result


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: UUID, current_profile: CurrentUserProfile, session: AsyncSession = Depends(get_db_session)
) -> None:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_account_service(session)
        user_id = current_profile.id
        await service.delete_account(user_id, account_id)
