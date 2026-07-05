from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_session
from app.core.security import AuthenticatedIdentity
from app.obligations.schemas_v17 import (
    ObligationPeriodV17Response,
    ObligationV17Response,
    ObligationV17CreateRequest,
    EmptyStateResponse,
    ApiErrorResponse,
    ObligationPaymentV17Response,
)
from app.obligations.service_v17 import ObligationV17Service

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
    response_model=list[ObligationV17Response],
    summary="List Obligations (V1.7)",
    description="List all obligations using the V1.7 model.",
)
async def list_obligations_v17(
    identity: AuthenticatedIdentity,
    _: None = Depends(check_v17_feature_flag),
):
    """
    Returns an empty list for now. Placeholder for V1.7 listing.
    """
    # This is a read-only endpoint that is guarded by check_v17_feature_flag
    # Eventually it will call the service to fetch V1.7 obligations.
    return []


@router.post(
    "",
    response_model=ObligationV17Response,
    status_code=status.HTTP_201_CREATED,
    summary="Create Obligation (V1.7)",
    description="Create a new obligation using the V1.7 model.",
)
async def create_obligation_v17(
    data: ObligationV17CreateRequest,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Creates an obligation using V1.7 contract.
    """
    service = ObligationV17Service(db)
    obligation, initial_period = await service.create_obligation(identity.user_id, data)

    from datetime import datetime

    # Map fields for response
    return ObligationV17Response(
        id=obligation.id,
        user_id=obligation.user_id,
        name=obligation.name,
        currency=obligation.currency,
        amount=obligation.base_amount or Decimal("0"),
        status=obligation.status,
        obligation_type="recurring" if obligation.type == "indefinite" else "one_time",
        frequency=obligation.frequency,
        amount_type=obligation.amount_type or "variable",
        created_at=obligation.created_at or datetime.utcnow(),
        updated_at=obligation.updated_at or datetime.utcnow(),
    )


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
