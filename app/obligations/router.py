from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.core.database import get_db_session
from app.users.dependencies import CurrentUserProfile
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

router = APIRouter(prefix="/obligations", tags=["obligations"])


def get_obligation_service(session: AsyncSession = Depends(get_db_session)) -> ObligationService:
    repo = ObligationRepository(session)
    account_repo = AccountRepository(session)
    ledger_repo = LedgerRepository(session)
    return ObligationService(repo, account_repo, ledger_repo)





@router.post("", response_model=ObligationRead, status_code=status.HTTP_201_CREATED)
async def create_obligation(
    payload: ObligationCreate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> ObligationRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_obligation_service(session)
        user_id = current_profile.id
        return await service.create_obligation(user_id, payload)


@router.get("", response_model=list[ObligationRead])
async def list_obligations(
    current_profile: CurrentUserProfile, session: AsyncSession = Depends(get_db_session)
) -> list[ObligationRead]:
    service = get_obligation_service(session)
    user_id = current_profile.id
    return await service.list_obligations(user_id)


@router.get("/{obligation_id}", response_model=ObligationRead)
async def get_obligation(
    obligation_id: UUID, current_profile: CurrentUserProfile, session: AsyncSession = Depends(get_db_session)
) -> ObligationRead:
    service = get_obligation_service(session)
    user_id = current_profile.id
    return await service.get_obligation(user_id, obligation_id)


@router.patch("/{obligation_id}", response_model=ObligationRead)
async def update_obligation(
    obligation_id: UUID,
    payload: ObligationUpdate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> ObligationRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_obligation_service(session)
        user_id = current_profile.id
        return await service.update_obligation(user_id, obligation_id, payload)


@router.delete("/{obligation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_obligation(
    obligation_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_obligation_service(session)
        user_id = current_profile.id
        await service.delete_obligation(user_id, obligation_id)


@router.post("/{obligation_id}/payments", response_model=ObligationPaymentResult)
async def create_payment(
    obligation_id: UUID,
    payload: ObligationPaymentCreate,
    current_profile: CurrentUserProfile,
    idempotency_key: str | None = Header(None),
    session: AsyncSession = Depends(get_db_session),
) -> ObligationPaymentResult:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_obligation_service(session)
        user_id = current_profile.id
        return await service.create_payment(user_id, obligation_id, payload, idempotency_key)
