from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_session
from app.core.errors import ValidationError
from app.core.security import AuthenticatedIdentity
from app.fx.provider import DolarApiColombiaFxRateProvider, StaticFxRateProvider
from app.fx.schemas import FXQuoteRequest, FXQuoteResponse, FXRatesLatestResponse
from app.fx.service import FXService

router = APIRouter(prefix="/fx", tags=["FX Engine"])


def get_fx_service(db: AsyncSession = Depends(get_db_session)) -> FXService:
    provider = (
        StaticFxRateProvider()
        if getattr(settings, "APP_ENV", "") == "development"
        or getattr(settings, "APP_ENV", "") == "test"
        else DolarApiColombiaFxRateProvider()
    )
    return FXService(provider=provider, session=db)


@router.get("/rates/latest", response_model=FXRatesLatestResponse)
async def get_latest_rate(
    base_currency: str = "USD",
    quote_currency: str = "COP",
    current_user: AuthenticatedIdentity = None,
    fx_service: FXService = Depends(get_fx_service),
):
    if not getattr(settings, "NEXUM_OBLIGATIONS_V17_ENABLED", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "feature_flag_disabled", "message": "FX features are disabled."},
        )
    try:
        return await fx_service.get_latest_rate(base_currency, quote_currency)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": e.error_code, "message": e.message},
        )


@router.post("/quote", response_model=FXQuoteResponse)
async def create_fx_quote(
    request: FXQuoteRequest,
    current_user: AuthenticatedIdentity = None,
    fx_service: FXService = Depends(get_fx_service),
):
    if not getattr(settings, "NEXUM_OBLIGATIONS_V17_ENABLED", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "feature_flag_disabled", "message": "FX features are disabled."},
        )

    try:
        return await fx_service.create_quote(
            user_id=current_user.user_id,
            source_currency=request.from_currency,
            target_currency=request.to_currency,
            source_amount=request.amount,
            idempotency_key=request.idempotency_key,
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": e.error_code, "message": e.message},
        )
