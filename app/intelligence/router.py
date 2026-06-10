import uuid

from fastapi import APIRouter, Depends

from app.core.database import get_db_session
from app.core.security import AuthenticatedUser, get_current_user
from app.intelligence.schemas import (
    FreeMoneyRead,
    IntelligenceBalanceRead,
    IntelligenceCashflowRead,
    IntelligenceCreditCardRead,
    IntelligenceDebtRead,
    IntelligenceGoalRead,
    IntelligenceObligationRead,
    IntelligenceSnapshotRead,
)
from app.intelligence.service import IntelligenceService

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


def get_intelligence_service(session=Depends(get_db_session)) -> IntelligenceService:
    return IntelligenceService(session)


@router.get("/snapshot", response_model=IntelligenceSnapshotRead)
async def get_snapshot(
    service: IntelligenceService = Depends(get_intelligence_service),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    return await service.get_snapshot(uuid.UUID(current_user.user_id))


@router.get("/balance", response_model=IntelligenceBalanceRead)
async def get_balance(
    service: IntelligenceService = Depends(get_intelligence_service),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    return await service.get_balance(uuid.UUID(current_user.user_id))


@router.get("/free-money", response_model=FreeMoneyRead)
async def get_free_money(
    service: IntelligenceService = Depends(get_intelligence_service),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    return await service.get_free_money(uuid.UUID(current_user.user_id))


@router.get("/cashflow", response_model=IntelligenceCashflowRead)
async def get_cashflow(
    service: IntelligenceService = Depends(get_intelligence_service),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    return await service.get_cashflow_summary(uuid.UUID(current_user.user_id))


@router.get("/debt", response_model=IntelligenceDebtRead)
async def get_debt(
    service: IntelligenceService = Depends(get_intelligence_service),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    return await service.get_debt(uuid.UUID(current_user.user_id))


@router.get("/goals", response_model=list[IntelligenceGoalRead])
async def get_goals(
    service: IntelligenceService = Depends(get_intelligence_service),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    return await service.get_goals(uuid.UUID(current_user.user_id))


@router.get("/obligations", response_model=list[IntelligenceObligationRead])
async def get_obligations(
    service: IntelligenceService = Depends(get_intelligence_service),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    return await service.get_obligations(uuid.UUID(current_user.user_id))


@router.get("/credit-cards", response_model=list[IntelligenceCreditCardRead])
async def get_credit_cards(
    service: IntelligenceService = Depends(get_intelligence_service),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    return await service.get_credit_cards(uuid.UUID(current_user.user_id))
