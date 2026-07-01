from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.uow import UnitOfWork
from app.obligations.exceptions import ObligationForbiddenError
from app.obligations.repository import ObligationRepository
from app.obligations.schemas import ObligationCreate, ObligationRead, ObligationUpdate
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
