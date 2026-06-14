from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import (
    LedgerEventDetail,
    LedgerEventsResponse,
    LedgerPaginationInfo,
    LedgerSummaryResponse,
    LedgerTimelineResponse,
)
from app.ledger.service import LedgerService
from app.users.dependencies import CurrentUserProfile

router = APIRouter(prefix="/ledger", tags=["ledger"])

def get_ledger_service(session: AsyncSession = Depends(get_db_session)) -> LedgerService:
    repo = LedgerRepository(session)
    return LedgerService(repo)

@router.get("/events", response_model=LedgerEventsResponse)
async def list_events(
    user: CurrentUserProfile,
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    account_id: UUID | None = Query(None),
    category_id: UUID | None = Query(None),
    event_type: str | None = Query(None),
    direction: str | None = Query(None),
    amount_min: float | None = Query(None),
    amount_max: float | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: LedgerService = Depends(get_ledger_service),
):
    """
    Lista el historial de eventos financieros del usuario.
    """
    # Para validar el filter de ownership: si el usuario intenta filtrar por account_id ajena,
    # en list_events devolverá 0 si la account_id no coincide (porque está ligada al mismo usuario 
    # si usamos un AND account_id = X AND user_id = Y). Pero el requerimiento dice: 
    # "usuario A no puede filtrar por account_id de B... Debe devolver 403 o 404".
    # Lo mismo para category. 
    # Por tanto, validamos en el router usando repo methods:
    if account_id:
        await service.repository.check_account_ownership(account_id, user.id)
    if category_id:
        await service.repository.check_category_ownership(category_id, user.id)

    items, total = await service.list_events(
        user_id=user.id,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        category_id=category_id,
        event_type=event_type,
        direction=direction,
        amount_min=amount_min,
        amount_max=amount_max,
        search=search,
        limit=limit,
        offset=offset,
    )

    return LedgerEventsResponse(
        items=[LedgerEventDetail.model_validate(item) for item in items],
        pagination=LedgerPaginationInfo(limit=limit, offset=offset, total=total)
    )

@router.get("/events/{event_id}", response_model=LedgerEventDetail)
async def get_event_detail(
    event_id: UUID,
    user: CurrentUserProfile,
    service: LedgerService = Depends(get_ledger_service),
):
    """
    Devuelve el detalle de un evento financiero.
    """
    return await service.get_event_detail(user.id, event_id)

@router.get("/summary", response_model=LedgerSummaryResponse)
async def get_summary(
    user: CurrentUserProfile,
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    service: LedgerService = Depends(get_ledger_service),
):
    """
    Resumen financiero agrupado para un periodo dado.
    """
    return await service.get_summary(user.id, date_from, date_to)

@router.get("/timeline", response_model=LedgerTimelineResponse)
async def get_timeline(
    user: CurrentUserProfile,
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    service: LedgerService = Depends(get_ledger_service),
):
    """
    Timeline de eventos financieros agrupados por día.
    """
    groups = await service.get_timeline(user.id, date_from, date_to)
    return {"groups": groups}
