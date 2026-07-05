from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_session
from app.core.security import AuthenticatedIdentity
from app.obligations.schemas_v17 import (
    ApiErrorResponse,
    EmptyStateResponse,
    ObligationPaymentV17Response,
    ObligationPeriodAmountDefineRequest,
    ObligationPeriodPaymentCreateRequest,
    ObligationPeriodPaymentResultResponse,
    ObligationPeriodV17Response,
    ObligationV17CreateRequest,
    ObligationV17Response,
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


def _map_obligation(obligation) -> ObligationV17Response:
    from datetime import datetime

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
    "",
    response_model=list[ObligationV17Response],
    summary="List Obligations (V1.7)",
    description="List all obligations using the V1.7 model.",
)
async def list_obligations_v17(
    identity: AuthenticatedIdentity,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Returns a list of V1.7 obligations for the current user.
    """
    service = ObligationV17Service(session)
    obligations = await service.list_obligations(identity.user_id)
    return [_map_obligation(obl) for obl in obligations]


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

    # Map fields for response
    return _map_obligation(obligation)


@router.get(
    "/{obligation_id}",
    response_model=ObligationV17Response,
    responses={403: {"model": ApiErrorResponse}, 404: {"model": ApiErrorResponse}},
    summary="Get V1.7 obligation by ID",
    description="Gets a specific obligation by ID using V1.7 read semantics.",
)
async def get_obligation_v17(
    obligation_id: UUID,
    identity: AuthenticatedIdentity,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Obtiene una obligación específica V1.7.
    """
    service = ObligationV17Service(session)
    obligation = await service.get_obligation(identity.user_id, obligation_id)
    if not obligation:
        raise HTTPException(status_code=404, detail="Obligation not found")
    return _map_obligation(obligation)


@router.get(
    "/{obligation_id}/periods",
    response_model=list[ObligationPeriodV17Response] | EmptyStateResponse,
    responses={403: {"model": ApiErrorResponse}, 404: {"model": ApiErrorResponse}},
    summary="List V1.7 obligation periods",
    description="Lists all periods for a specific obligation using V1.7 read semantics.",
)
async def get_obligation_periods_v17(
    obligation_id: UUID,
    identity: AuthenticatedIdentity,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Lista de periodos de una obligación V1.7.
    """
    service = ObligationV17Service(session)
    periods = await service.list_periods_for_obligation(identity.user_id, obligation_id)

    if periods is None:
        raise HTTPException(status_code=404, detail="Obligation not found")

    if not periods:
        return EmptyStateResponse(message="No periods found", items=[])

    mapped_periods = []
    for p in periods:
        from datetime import datetime

        mapped_periods.append(
            ObligationPeriodV17Response(
                id=p.id,
                obligation_id=p.obligation_id,
                due_date=p.due_date,
                status=p.status,
                is_current=p.is_current,
                amount_due=p.amount or Decimal("0"),
                amount_paid=p.paid_amount or Decimal("0"),
                created_at=p.created_at or datetime.utcnow(),
                updated_at=p.updated_at or datetime.utcnow(),
            )
        )
    return mapped_periods


@router.patch(
    "/{obligation_id}/periods/{period_id}/amount",
    response_model=ObligationPeriodV17Response,
    responses={
        403: {"model": ApiErrorResponse},
        404: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
    },
    summary="Define Variable Period Amount (V1.7)",
    description="Defines the amount for a variable period currently pending amount definition.",
)
async def define_period_amount_v17(
    obligation_id: UUID,
    period_id: UUID,
    data: ObligationPeriodAmountDefineRequest,
    identity: AuthenticatedIdentity,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Define el monto para un periodo variable en V1.7.
    """
    service = ObligationV17Service(session)
    period = await service.define_period_amount(identity.user_id, obligation_id, period_id, data)

    from datetime import datetime

    return ObligationPeriodV17Response(
        id=period.id,
        obligation_id=period.obligation_id,
        due_date=period.due_date,
        status=period.status,
        is_current=period.is_current,
        amount_due=period.amount or Decimal("0"),
        amount_paid=period.paid_amount or Decimal("0"),
        created_at=period.created_at or datetime.utcnow(),
        updated_at=period.updated_at or datetime.utcnow(),
    )


@router.post(
    "/{obligation_id}/periods/{period_id}/payments",
    response_model=ObligationPeriodPaymentResultResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        403: {"model": ApiErrorResponse},
        404: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
    },
    summary="Pay Specific Period (V1.7)",
    description="Registers a payment specifically targeting a given obligation period.",
)
async def create_period_payment_v17(
    obligation_id: UUID,
    period_id: UUID,
    data: ObligationPeriodPaymentCreateRequest,
    identity: AuthenticatedIdentity,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Registra un pago específico a un periodo en V1.7.
    """
    service = ObligationV17Service(session)
    payment, period = await service.pay_specific_period(
        identity.user_id, obligation_id, period_id, data
    )

    from datetime import datetime

    payment_response = ObligationPaymentV17Response(
        id=payment.id,
        obligation_id=payment.obligation_id,
        obligation_period_id=payment.obligation_period_id,
        user_id=payment.user_id,
        amount=payment.amount,
        quote_id=payment.quote_id,
        idempotency_key=payment.idempotency_key,
        created_at=payment.created_at or datetime.utcnow(),
        updated_at=payment.created_at or datetime.utcnow(),
    )

    period_response = ObligationPeriodV17Response(
        id=period.id,
        obligation_id=period.obligation_id,
        due_date=period.due_date,
        status=period.status,
        is_current=period.is_current,
        amount_due=period.amount or Decimal("0"),
        amount_paid=period.paid_amount or Decimal("0"),
        created_at=period.created_at or datetime.utcnow(),
        updated_at=period.updated_at or datetime.utcnow(),
    )

    return ObligationPeriodPaymentResultResponse(payment=payment_response, period=period_response)


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
