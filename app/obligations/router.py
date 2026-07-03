from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.uow import UnitOfWork
from app.obligations.repository import ObligationRepository
from app.obligations.schemas import (
    ObligationCreate,
    ObligationPaymentCreate,
    ObligationPaymentPreviewCreate,
    ObligationPaymentPreviewRead,
    ObligationPaymentRead,
    ObligationPeriodAmountUpdate,
    ObligationPeriodRead,
    ObligationRead,
)
from app.obligations.service import ObligationService
from app.users.dependencies import CurrentUserProfile

router = APIRouter(prefix="/obligations", tags=["obligations"])


def get_obligation_service(session: AsyncSession = Depends(get_db_session)) -> ObligationService:
    repo = ObligationRepository(session)
    return ObligationService(repo)


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
    current_profile: CurrentUserProfile,
    include_archived: bool = Query(False),
    session: AsyncSession = Depends(get_db_session),
) -> list[ObligationRead]:
    service = get_obligation_service(session)
    user_id = current_profile.id
    return await service.list_obligations(user_id, include_archived=include_archived)


@router.get("/{obligation_id}", response_model=ObligationRead)
async def get_obligation(
    obligation_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> ObligationRead:
    service = get_obligation_service(session)
    user_id = current_profile.id
    return await service.get_obligation(user_id, obligation_id)


@router.get("/{obligation_id}/periods", response_model=list[ObligationPeriodRead])
async def list_periods(
    obligation_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> list[ObligationPeriodRead]:
    service = get_obligation_service(session)
    return await service.list_periods(current_profile.id, obligation_id)


@router.post("/{obligation_id}/sync-periods", response_model=list[ObligationPeriodRead])
async def sync_periods(
    obligation_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> list[ObligationPeriodRead]:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_obligation_service(session)
        return await service.sync_periods(current_profile.id, obligation_id)


@router.post("/periods/{period_id}/skip", response_model=ObligationPeriodRead)
async def skip_period(
    period_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> ObligationPeriodRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_obligation_service(session)
        return await service.skip_period(current_profile.id, period_id)


@router.post("/{obligation_id}/pay", response_model=list[ObligationPaymentRead])
async def pay_obligation_fifo(
    obligation_id: UUID,
    payload: ObligationPaymentCreate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> list[ObligationPaymentRead]:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_obligation_service(session)
        return await service.pay_obligation_fifo(current_profile.id, obligation_id, payload)


@router.post("/periods/{period_id}/pay", response_model=ObligationPaymentRead)
async def pay_specific_period(
    period_id: UUID,
    payload: ObligationPaymentCreate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> ObligationPaymentRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_obligation_service(session)
        return await service.pay_specific_period(current_profile.id, period_id, payload)


@router.post(
    "/periods/{period_id}/pay/preview",
    response_model=ObligationPaymentPreviewRead,
    summary="Preview obligation period payment",
)
async def preview_pay_period(
    period_id: UUID,
    payload: ObligationPaymentPreviewCreate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> ObligationPaymentPreviewRead:
    """
    Preview the FX rate and amounts for a payment against a specific obligation period.
    """
    service = get_obligation_service(session)
    return await service.pay_preview_specific_period(current_profile.id, period_id, payload)


@router.patch("/periods/{period_id}/amount", response_model=ObligationPeriodRead)
async def define_period_amount(
    period_id: UUID,
    payload: ObligationPeriodAmountUpdate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> ObligationPeriodRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_obligation_service(session)
        return await service.define_amount(current_profile.id, period_id, payload)
