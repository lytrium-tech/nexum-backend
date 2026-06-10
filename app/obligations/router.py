from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.core.database import get_db_session
from app.core.security import CurrentUser
from app.core.uow import UnitOfWork
from app.ledger.repository import LedgerRepository
from app.obligations.repository import ObligationRepository
from app.obligations.schemas import (
    ObligationCreate,
    ObligationPaymentCreate,
    ObligationPaymentResult,
    ObligationRead,
    ObligationUpdate,
)
from app.obligations.service import ObligationService
from app.users.repository import UserRepository
from app.users.service import UserService

router = APIRouter(prefix="/obligations", tags=["obligations"])


def get_obligation_service(session: AsyncSession = Depends(get_db_session)) -> ObligationService:
    repo = ObligationRepository(session)
    account_repo = AccountRepository(session)
    ledger_repo = LedgerRepository(session)
    return ObligationService(repo, account_repo, ledger_repo)


async def resolve_user_id(current_user: CurrentUser, session: AsyncSession) -> UUID:
    user_repo = UserRepository(session)
    user_service = UserService(user_repo)
    user = await user_service.get_current_user_profile(current_user)
    return user.id


@router.post("", response_model=ObligationRead, status_code=status.HTTP_201_CREATED)
async def create_obligation(
    payload: ObligationCreate,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_db_session),
) -> ObligationRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_obligation_service(session)
        user_id = await resolve_user_id(current_user, session)
        return await service.create_obligation(user_id, payload)


@router.get("", response_model=list[ObligationRead])
async def list_obligations(
    current_user: CurrentUser, session: AsyncSession = Depends(get_db_session)
) -> list[ObligationRead]:
    service = get_obligation_service(session)
    user_id = await resolve_user_id(current_user, session)
    return await service.list_obligations(user_id)


@router.get("/{obligation_id}", response_model=ObligationRead)
async def get_obligation(
    obligation_id: UUID, current_user: CurrentUser, session: AsyncSession = Depends(get_db_session)
) -> ObligationRead:
    service = get_obligation_service(session)
    user_id = await resolve_user_id(current_user, session)
    return await service.get_obligation(user_id, obligation_id)


@router.patch("/{obligation_id}", response_model=ObligationRead)
async def update_obligation(
    obligation_id: UUID,
    payload: ObligationUpdate,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_db_session),
) -> ObligationRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_obligation_service(session)
        user_id = await resolve_user_id(current_user, session)
        return await service.update_obligation(user_id, obligation_id, payload)


@router.delete("/{obligation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_obligation(
    obligation_id: UUID,
    current_user: CurrentUser,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_obligation_service(session)
        user_id = await resolve_user_id(current_user, session)
        await service.delete_obligation(user_id, obligation_id)


@router.post("/{obligation_id}/payments", response_model=ObligationPaymentResult)
async def create_payment(
    obligation_id: UUID,
    payload: ObligationPaymentCreate,
    current_user: CurrentUser,
    idempotency_key: str | None = Header(None),
    session: AsyncSession = Depends(get_db_session),
) -> ObligationPaymentResult:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_obligation_service(session)
        user_id = await resolve_user_id(current_user, session)
        return await service.create_payment(user_id, obligation_id, payload, idempotency_key)
