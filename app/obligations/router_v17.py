from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_session
from app.obligations.schemas_v17 import (
    ObligationPeriodV17Response,
    ObligationV17Response,
    EmptyStateResponse,
    ApiErrorResponse,
    ObligationPaymentV17Response,
)

router = APIRouter()


def check_v17_feature_flag():
    """Dependency to check if V1.7 is enabled."""
    if not settings.NEXUM_OBLIGATIONS_V17_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="feature_flag_disabled",
        )


@router.get(
    "",
    response_model=List[ObligationV17Response] | EmptyStateResponse,
    responses={403: {"model": ApiErrorResponse}},
    summary="List all V1.7 obligations",
    description="Lists all obligations for the current user using V1.7 read semantics.",
)
async def get_obligations_v17(
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Lista de obligaciones V1.7.
    Si no hay obligaciones, retorna EmptyStateResponse.
    Actualmente stub read-only para foundations.
    """
    return EmptyStateResponse(message="No obligations found", items=[])


@router.get(
    "/{obligation_id}",
    response_model=ObligationV17Response,
    responses={403: {"model": ApiErrorResponse}, 404: {"model": ApiErrorResponse}},
    summary="Get V1.7 obligation by ID",
    description="Gets a specific obligation by ID using V1.7 read semantics.",
)
async def get_obligation_v17(
    obligation_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Obtiene una obligación específica V1.7.
    """
    raise HTTPException(status_code=404, detail="Obligation not found")


@router.get(
    "/{obligation_id}/periods",
    response_model=List[ObligationPeriodV17Response] | EmptyStateResponse,
    responses={403: {"model": ApiErrorResponse}, 404: {"model": ApiErrorResponse}},
    summary="List V1.7 obligation periods",
    description="Lists all periods for a specific obligation using V1.7 read semantics.",
)
async def get_obligation_periods_v17(
    obligation_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Lista de periodos de una obligación V1.7.
    """
    return EmptyStateResponse(message="No periods found", items=[])


@router.get(
    "/payments/{payment_id}",
    response_model=ObligationPaymentV17Response,
    responses={403: {"model": ApiErrorResponse}, 404: {"model": ApiErrorResponse}},
    summary="Get V1.7 obligation payment by ID",
    description="Gets a specific obligation payment by ID using V1.7 read semantics.",
)
async def get_obligation_payment_v17(
    payment_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Obtiene un pago específico V1.7.
    """
    raise HTTPException(status_code=404, detail="Payment not found")
