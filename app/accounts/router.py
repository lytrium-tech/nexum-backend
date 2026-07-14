from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.accounts.schemas import (
    AccountCreate,
    AccountRead,
    AccountDetailRead,
    AccountSummary,
    AccountPeriodSummary,
    AccountUpdate,
    BalanceAdjustmentCreate,
)
from app.accounts.service import AccountService
from app.core.database import get_db_session
from app.core.uow import UnitOfWork
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventsResponse
from app.users.dependencies import CurrentUserProfile

router = APIRouter(prefix="/accounts", tags=["accounts"])


def get_account_service(session: AsyncSession = Depends(get_db_session)) -> AccountService:
    repo = AccountRepository(session)
    ledger_repo = LedgerRepository(session)
    return AccountService(repo, ledger_repo)


@router.get("", response_model=list[AccountRead])
async def list_accounts(
    current_profile: CurrentUserProfile,
    include_archived: bool = Query(False),
    service: AccountService = Depends(get_account_service),
) -> list[AccountRead]:
    user_id = current_profile.id
    return await service.list_accounts(user_id, include_archived=include_archived)


@router.get("/summary", response_model=AccountSummary)
async def get_summary(
    current_profile: CurrentUserProfile, service: AccountService = Depends(get_account_service)
) -> AccountSummary:
    user_id = current_profile.id
    return await service.get_summary(user_id)


@router.get("/{account_id}", response_model=AccountDetailRead)
async def get_account(
    account_id: UUID,
    current_profile: CurrentUserProfile,
    service: AccountService = Depends(get_account_service),
) -> AccountDetailRead:
    user_id = current_profile.id
    return await service.get_account(user_id, account_id)


@router.get("/{account_id}/movements", response_model=LedgerEventsResponse)
async def list_account_movements(
    account_id: UUID,
    current_profile: CurrentUserProfile,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    event_type: str | None = None,
    direction: str | None = None,
    service: AccountService = Depends(get_account_service),
) -> LedgerEventsResponse:
    user_id = current_profile.id
    return await service.list_account_movements(
        user_id, account_id, limit, offset, date_from, date_to, event_type, direction
    )


@router.get("/{account_id}/summary", response_model=AccountPeriodSummary)
async def get_account_period_summary(
    account_id: UUID,
    month: str,
    current_profile: CurrentUserProfile,
    service: AccountService = Depends(get_account_service),
) -> AccountPeriodSummary:
    user_id = current_profile.id
    return await service.get_account_period_summary(user_id, account_id, month)


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


@router.post("/{account_id}/adjustments", response_model=AccountRead)
async def create_balance_adjustment(
    account_id: UUID,
    payload: BalanceAdjustmentCreate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> AccountRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_account_service(session)
        user_id = current_profile.id
        result = await service.create_balance_adjustment(user_id, account_id, payload)
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


@router.post("/{account_id}/archive", response_model=AccountRead)
async def archive_account(
    account_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> AccountRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_account_service(session)
        user_id = current_profile.id
        return await service.archive_account(user_id, account_id)


@router.post("/{account_id}/restore", response_model=AccountRead)
async def restore_account(
    account_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> AccountRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_account_service(session)
        user_id = current_profile.id
        return await service.restore_account(user_id, account_id)


@router.delete("/{account_id}", status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, deprecated=True)
async def delete_account(
    account_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    from fastapi import HTTPException
    raise HTTPException(
        status_code=422,
        detail="account_deletion_not_supported_use_archive"
    )
