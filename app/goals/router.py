from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.core.database import get_db_session, mark_session_read_only
from app.core.uow import UnitOfWork
from app.goals.repository import GoalRepository
from app.goals.schemas import (
    GoalAutoContributionScheduleCreate,
    GoalAutoContributionScheduleRead,
    GoalAutoContributionScheduleUpdate,
    GoalContributionCreate,
    GoalContributionResult,
    GoalCreate,
    GoalDetailRead,
    GoalRead,
    GoalReleaseCreate,
    GoalReleaseResult,
    GoalTransactionsResponse,
    GoalUpdate,
)
from app.goals.service import GoalService
from app.ledger.repository import LedgerRepository
from app.users.dependencies import CurrentUserProfile

router = APIRouter(prefix="/goals", tags=["goals"])


def get_goal_service(session: AsyncSession = Depends(get_db_session)) -> GoalService:
    repo = GoalRepository(session)
    account_repo = AccountRepository(session)
    ledger_repo = LedgerRepository(session)
    return GoalService(repo, account_repo, ledger_repo)


@router.post("", response_model=GoalRead, status_code=status.HTTP_201_CREATED)
async def create_goal(
    payload: GoalCreate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> GoalRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_goal_service(session)
        user_id = current_profile.id
        return await service.create_goal(user_id, payload)


@router.get("", response_model=list[GoalRead])
async def list_goals(
    current_profile: CurrentUserProfile, service: GoalService = Depends(get_goal_service)
) -> list[GoalRead]:
    user_id = current_profile.id
    return await service.list_goals(user_id)


@router.get("/{goal_id}", response_model=GoalDetailRead)
async def get_goal(
    goal_id: UUID,
    current_profile: CurrentUserProfile,
    service: GoalService = Depends(get_goal_service),
) -> GoalDetailRead:
    user_id = current_profile.id
    return await service.get_goal(user_id, goal_id)


@router.patch("/{goal_id}", response_model=GoalRead)
async def update_goal(
    goal_id: UUID,
    payload: GoalUpdate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> GoalRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_goal_service(session)
        user_id = current_profile.id
        return await service.update_goal(user_id, goal_id, payload)


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(
    goal_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_goal_service(session)
        user_id = current_profile.id
        await service.delete_goal(user_id, goal_id)


@router.post(
    "/{goal_id}/contributions",
    response_model=GoalContributionResult,
    responses={
        401: {"description": "Autenticación requerida"},
        403: {"description": "Cuenta o meta ajena/inactiva"},
        404: {"description": "Cuenta o meta no encontrada"},
        409: {"description": "Conflicto de idempotencia, reserva o monto"},
        422: {"description": "Payload o moneda inválidos"},
    },
)
async def create_contribution(
    goal_id: UUID,
    payload: GoalContributionCreate,
    current_profile: CurrentUserProfile,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> GoalContributionResult:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_goal_service(session)
        user_id = current_profile.id
        return await service.create_contribution(user_id, goal_id, payload, idempotency_key)


@router.post(
    "/{goal_id}/releases",
    response_model=GoalReleaseResult,
    responses={
        401: {"description": "Autenticación requerida"},
        400: {"description": "Meta archivada, cancelada o con estado inválido"},
        403: {"description": "Cuenta inactiva o sin saldo configurado"},
        404: {"description": "Cuenta o meta no encontrada o inaccesible"},
        409: {"description": "Conflicto de idempotencia, reserva o monto"},
        422: {"description": "Payload o moneda inválidos"},
    },
)
async def create_release(
    goal_id: UUID,
    payload: GoalReleaseCreate,
    current_profile: CurrentUserProfile,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    service: GoalService = Depends(get_goal_service),
) -> GoalReleaseResult:
    user_id = current_profile.id
    return await service.create_release(user_id, goal_id, payload, idempotency_key)


@router.get(
    "/{goal_id}/transactions",
    response_model=GoalTransactionsResponse,
    responses={
        401: {"description": "Autenticación requerida"},
        403: {"description": "Meta ajena"},
        404: {"description": "Meta no encontrada"},
        422: {"description": "Paginación inválida"},
    },
)
async def list_goal_transactions(
    goal_id: UUID,
    current_profile: CurrentUserProfile,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: GoalService = Depends(get_goal_service),
) -> GoalTransactionsResponse:
    user_id = current_profile.id
    return await service.list_goal_transactions(user_id, goal_id, limit, offset)


@router.get(
    "/{goal_id}/auto-contribution",
    response_model=GoalAutoContributionScheduleRead,
    responses={
        401: {"description": "Autenticación requerida"},
        403: {"description": "Meta ajena"},
        404: {"description": "Meta o schedule no encontrado"},
    },
)
async def get_auto_contribution_schedule(
    goal_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
    service: GoalService = Depends(get_goal_service),
) -> GoalAutoContributionScheduleRead:
    mark_session_read_only(session)
    return await service.get_auto_contribution_schedule(current_profile.id, goal_id)


@router.put(
    "/{goal_id}/auto-contribution",
    response_model=GoalAutoContributionScheduleRead,
    responses={
        400: {"description": "Meta no operativa"},
        401: {"description": "Autenticación requerida"},
        403: {"description": "Meta o cuenta ajena/inactiva"},
        404: {"description": "Meta o cuenta no encontrada"},
        409: {"description": "Ya existe un schedule no cancelado"},
        422: {"description": "Configuración, timezone o moneda inválida"},
    },
)
async def put_auto_contribution_schedule(
    goal_id: UUID,
    payload: GoalAutoContributionScheduleCreate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> GoalAutoContributionScheduleRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_goal_service(session)
        return await service.put_auto_contribution_schedule(current_profile.id, goal_id, payload)


@router.patch(
    "/{goal_id}/auto-contribution",
    response_model=GoalAutoContributionScheduleRead,
    responses={
        401: {"description": "Autenticación requerida"},
        403: {"description": "Meta o cuenta ajena/inactiva"},
        404: {"description": "Meta, cuenta o schedule no encontrado"},
        422: {"description": "Configuración, timezone o moneda inválida"},
    },
)
async def patch_auto_contribution_schedule(
    goal_id: UUID,
    payload: GoalAutoContributionScheduleUpdate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> GoalAutoContributionScheduleRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_goal_service(session)
        return await service.patch_auto_contribution_schedule(current_profile.id, goal_id, payload)


@router.delete(
    "/{goal_id}/auto-contribution",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"description": "Autenticación requerida"},
        403: {"description": "Meta ajena"},
        404: {"description": "Meta o schedule no encontrado"},
    },
)
async def delete_auto_contribution_schedule(
    goal_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_goal_service(session)
        await service.delete_auto_contribution_schedule(current_profile.id, goal_id)


@router.post(
    "/{goal_id}/auto-contribution/pause",
    response_model=GoalAutoContributionScheduleRead,
    responses={
        401: {"description": "Autenticación requerida"},
        403: {"description": "Meta ajena"},
        404: {"description": "Meta o schedule no encontrado"},
    },
)
async def pause_auto_contribution_schedule(
    goal_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> GoalAutoContributionScheduleRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_goal_service(session)
        return await service.pause_auto_contribution_schedule(current_profile.id, goal_id)


@router.post(
    "/{goal_id}/auto-contribution/resume",
    response_model=GoalAutoContributionScheduleRead,
    responses={
        400: {"description": "Meta no operativa"},
        401: {"description": "Autenticación requerida"},
        403: {"description": "Meta o cuenta ajena/inactiva"},
        404: {"description": "Meta, cuenta o schedule no encontrado"},
        422: {"description": "Timezone o moneda inválida"},
    },
)
async def resume_auto_contribution_schedule(
    goal_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
) -> GoalAutoContributionScheduleRead:
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = get_goal_service(session)
        return await service.resume_auto_contribution_schedule(current_profile.id, goal_id)
