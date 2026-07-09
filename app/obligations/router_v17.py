from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_session
from app.core.security import AuthenticatedIdentity
from app.obligations.schemas_v17 import (
    ApiErrorResponse,
    EmptyStateResponse,
    ObligationFIFOPaymentCreateRequest,
    ObligationFIFOPaymentResultResponse,
    ObligationPaymentPreviewV17Request,
    ObligationPaymentPreviewV17Response,
    ObligationPaymentV17Response,
    ObligationPeriodAmountDefineRequest,
    ObligationPeriodPaymentCreateRequest,
    ObligationPeriodPaymentResultResponse,
    ObligationPeriodRefreshOverdueResponse,
    ObligationPeriodV17Response,
    ObligationsV17IntelligenceContextResponse,
    ObligationsV17SummaryResponse,
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
    "/summary",
    response_model=ObligationsV17SummaryResponse,
    summary="Get Obligations Summary (V1.7)",
    description="Gets a high-level summary of V1.7 obligations for a specific month.",
)
async def get_obligations_summary_v17(
    identity: AuthenticatedIdentity,
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$", description="Month in YYYY-MM format"),
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Returns a dashboard summary for the specified month (or current month).
    """
    service = ObligationV17Service(session)
    return await service.get_summary(identity.user_id, month)


@router.get(
    "/intelligence-context",
    response_model=ObligationsV17IntelligenceContextResponse,
    summary="Get Intelligence Context (V1.7)",
    description="Gets a read-only context of V1.7 obligations designed for AI features.",
)
async def get_obligations_intelligence_context_v17(
    identity: AuthenticatedIdentity,
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$", description="Month in YYYY-MM format"),
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Returns an AI-friendly context summary, reusing the summary endpoint logic.
    """
    service = ObligationV17Service(session)
    return await service.get_intelligence_context(identity.user_id, month)


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
    "/{obligation_id}/periods/{period_id}/payments/preview",
    response_model=ObligationPaymentPreviewV17Response,
    responses={404: {"model": ApiErrorResponse}, 422: {"model": ApiErrorResponse}},
    summary="Preview a specific period payment (V1.7)",
)
async def preview_pay_period_v17(
    obligation_id: UUID,
    period_id: UUID,
    data: ObligationPaymentPreviewV17Request,
    identity: AuthenticatedIdentity,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Simula o previsualiza un pago para un periodo específico, retornando el tipo de cambio y los montos exactos requeridos.
    """
    service = ObligationV17Service(session)
    return await service.pay_preview_specific_period(
        identity.user_id, obligation_id, period_id, data
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
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """
    Registra un pago específico a un periodo en V1.7.
    """
    if idempotency_key:
        data.idempotency_key = idempotency_key
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


@router.post(
    "/{obligation_id}/payments",
    response_model=ObligationFIFOPaymentResultResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        403: {"model": ApiErrorResponse},
        404: {"model": ApiErrorResponse},
        409: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
    },
    summary="Pay Obligation (FIFO)",
    description="Registers a payment using FIFO strategy across obligation periods.",
)
async def create_obligation_payment_fifo_v17(
    obligation_id: UUID,
    data: ObligationFIFOPaymentCreateRequest,
    identity: AuthenticatedIdentity,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """
    Registra un pago general a una obligacion en V1.7 usando estrategia FIFO.
    """
    if idempotency_key:
        data.idempotency_key = idempotency_key
    service = ObligationV17Service(session)
    payments, periods = await service.pay_obligation_fifo(identity.user_id, obligation_id, data)

    from datetime import datetime

    payment_responses = [
        ObligationPaymentV17Response(
            id=pay.id,
            obligation_id=pay.obligation_id,
            obligation_period_id=pay.obligation_period_id,
            user_id=pay.user_id,
            amount=pay.amount,
            quote_id=pay.quote_id,
            idempotency_key=pay.idempotency_key,
            created_at=pay.created_at or datetime.utcnow(),
            updated_at=pay.created_at or datetime.utcnow(),
        )
        for pay in payments
    ]

    period_responses = [
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
        for p in periods
    ]

    from decimal import Decimal

    return ObligationFIFOPaymentResultResponse(
        payments=payment_responses,
        periods=period_responses,
        total_applied=data.amount,
        remaining_unapplied=Decimal("0"),
        strategy="fifo",
    )


@router.post(
    "/{obligation_id}/periods/{period_id}/skip",
    response_model=ObligationPeriodV17Response,
    dependencies=[Depends(check_v17_feature_flag)],
)
async def skip_obligation_period(
    obligation_id: UUID,
    period_id: UUID,
    identity: AuthenticatedIdentity,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Skip a period. Only allowed for pending_amount_definition or pending_payment.
    """
    service = ObligationV17Service(session)
    period = await service.skip_period(
        user_id=str(identity.user_id),
        obligation_id=obligation_id,
        period_id=period_id,
    )
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
    "/{obligation_id}/periods/{period_id}/cancel",
    response_model=ObligationPeriodV17Response,
    dependencies=[Depends(check_v17_feature_flag)],
)
async def cancel_obligation_period(
    obligation_id: UUID,
    period_id: UUID,
    identity: AuthenticatedIdentity,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Cancel a period. Allowed for pending states, overdue, and skipped if no payments.
    """
    service = ObligationV17Service(session)
    period = await service.cancel_period(
        user_id=str(identity.user_id),
        obligation_id=obligation_id,
        period_id=period_id,
    )
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
    "/{obligation_id}/periods/refresh-overdue",
    response_model=ObligationPeriodRefreshOverdueResponse,
    dependencies=[Depends(check_v17_feature_flag)],
)
async def refresh_overdue_periods(
    obligation_id: UUID,
    identity: AuthenticatedIdentity,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Refresh overdue periods for an obligation.
    """
    service = ObligationV17Service(session)
    updated_periods, count = await service.refresh_overdue_periods(
        user_id=str(identity.user_id),
        obligation_id=obligation_id,
    )

    from datetime import datetime

    response_periods = [
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
        for p in updated_periods
    ]

    return ObligationPeriodRefreshOverdueResponse(
        updated_periods=response_periods, updated_count=count
    )


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
