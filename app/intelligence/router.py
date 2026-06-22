from fastapi import APIRouter, Depends

from app.core.database import get_db_session
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
from app.users.dependencies import CurrentUserProfile

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


def get_intelligence_service(session=Depends(get_db_session)) -> IntelligenceService:
    from app.core.config import settings
    from app.fx.provider import DolarApiColombiaFxRateProvider, FxRateProvider, StaticFxRateProvider

    fx_provider: FxRateProvider
    if settings.FX_PROVIDER == "dolarapi_colombia":
        fx_provider = DolarApiColombiaFxRateProvider(
            base_url=settings.DOLAR_API_BASE_URL,
            timeout_seconds=settings.FX_TIMEOUT_SECONDS,
        )
    else:
        fx_provider = StaticFxRateProvider()

    return IntelligenceService(session, fx_provider=fx_provider)


@router.get("/snapshot", response_model=IntelligenceSnapshotRead)
async def get_snapshot(
    current_profile: CurrentUserProfile,
    service: IntelligenceService = Depends(get_intelligence_service),
):
    return await service.get_snapshot(current_profile.id)


@router.get("/balance", response_model=IntelligenceBalanceRead)
async def get_balance(
    current_profile: CurrentUserProfile,
    service: IntelligenceService = Depends(get_intelligence_service),
):
    return await service.get_balance(current_profile.id)


@router.get("/free-money", response_model=FreeMoneyRead)
async def get_free_money(
    current_profile: CurrentUserProfile,
    service: IntelligenceService = Depends(get_intelligence_service),
):
    return await service.get_free_money(current_profile.id)


@router.get("/cashflow", response_model=IntelligenceCashflowRead)
async def get_cashflow(
    current_profile: CurrentUserProfile,
    service: IntelligenceService = Depends(get_intelligence_service),
):
    return await service.get_cashflow_summary(current_profile.id)


@router.get("/debt", response_model=IntelligenceDebtRead)
async def get_debt(
    current_profile: CurrentUserProfile,
    service: IntelligenceService = Depends(get_intelligence_service),
):
    return await service.get_debt(current_profile.id)


@router.get("/goals", response_model=list[IntelligenceGoalRead])
async def get_goals(
    current_profile: CurrentUserProfile,
    service: IntelligenceService = Depends(get_intelligence_service),
):
    return await service.get_goals(current_profile.id)


@router.get("/obligations", response_model=list[IntelligenceObligationRead])
async def get_obligations(
    current_profile: CurrentUserProfile,
    service: IntelligenceService = Depends(get_intelligence_service),
):
    return await service.get_obligations(current_profile.id)


@router.get("/credit-cards", response_model=list[IntelligenceCreditCardRead])
async def get_credit_cards(
    current_profile: CurrentUserProfile,
    service: IntelligenceService = Depends(get_intelligence_service),
):
    return await service.get_credit_cards(current_profile.id)
