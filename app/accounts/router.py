from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.accounts.schemas import AccountCreate, AccountRead, AccountUpdate
from app.accounts.service import AccountService
from app.core.database import get_db_session
from app.core.security import CurrentUser
from app.core.uow import UnitOfWork
from app.users.repository import UserRepository
from app.users.service import UserService

router = APIRouter(prefix="/accounts", tags=["accounts"])


def get_account_service(session: AsyncSession = Depends(get_db_session)) -> AccountService:
    repo = AccountRepository(session)
    user_repo = UserRepository(session)
    user_service = UserService(user_repo)
    return AccountService(repo, user_service)


async def resolve_user_id(current_user: CurrentUser, user_service: UserService) -> UUID:
    user = await user_service.get_current_user_profile(current_user)
    return user.id


@router.get("", response_model=list[AccountRead])
async def list_accounts(
    current_user: CurrentUser, service: AccountService = Depends(get_account_service)
) -> list[AccountRead]:
    user_id = await resolve_user_id(current_user, service.user_service)
    return await service.list_accounts(user_id)


@router.get("/{account_id}", response_model=AccountRead)
async def get_account(
    account_id: UUID,
    current_user: CurrentUser,
    service: AccountService = Depends(get_account_service),
) -> AccountRead:
    user_id = await resolve_user_id(current_user, service.user_service)
    return await service.get_account(user_id, account_id)


@router.post("", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreate,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_db_session),
) -> AccountRead:
    uow = UnitOfWork(session)
    async with uow:
        service = get_account_service(session)
        user_id = await resolve_user_id(current_user, service.user_service)
        result = await service.create_account(user_id, payload)
        await uow.commit()
        return result


@router.patch("/{account_id}", response_model=AccountRead)
async def update_account(
    account_id: UUID,
    payload: AccountUpdate,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_db_session),
) -> AccountRead:
    uow = UnitOfWork(session)
    async with uow:
        service = get_account_service(session)
        user_id = await resolve_user_id(current_user, service.user_service)
        result = await service.update_account(user_id, account_id, payload)
        await uow.commit()
        return result


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: UUID, current_user: CurrentUser, session: AsyncSession = Depends(get_db_session)
) -> None:
    uow = UnitOfWork(session)
    async with uow:
        service = get_account_service(session)
        user_id = await resolve_user_id(current_user, service.user_service)
        await service.delete_account(user_id, account_id)
        await uow.commit()
