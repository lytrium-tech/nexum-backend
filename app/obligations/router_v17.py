from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_session
from app.core.financial_time import financial_date
from app.core.uow import UnitOfWork
from app.obligations.enums_v17 import PeriodStatus
from app.obligations.schemas_v17 import (
    ApiErrorResponse,
    ObligationAutoPaymentConfigRead,
    ObligationAutoPaymentConfigUpdateRequest,
    ObligationAutoPaymentRunsPage,
    ObligationFIFOPaymentCreateRequest,
    ObligationFIFOPaymentResultResponse,
    ObligationPaymentPreviewV17Request,
    ObligationPaymentPreviewV17Response,
    ObligationPaymentsV17ListResponse,
    ObligationPaymentV17Response,
    ObligationPeriodAmountDefineRequest,
    ObligationPeriodPaymentCreateRequest,
    ObligationPeriodPaymentResultResponse,
    ObligationPeriodRefreshOverdueResponse,
    ObligationPeriodV17Response,
    ObligationsV17IntelligenceContextResponse,
    ObligationsV17SummaryResponse,
    ObligationV17CreateRequest,
    ObligationV17OverviewResponse,
    ObligationV17Response,
    ObligationV17UpdateRequest,
)
from app.obligations.service_v17 import ObligationV17Service
from app.users.dependencies import CurrentUserProfile

router = APIRouter()


def check_v17_feature_flag():
    """Dependency to check if V1.7 is enabled."""
    if not settings.NEXUM_OBLIGATIONS_V17_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="feature_flag_disabled",
        )


def _map_obligation(obligation) -> ObligationV17Response:
    from datetime import UTC, datetime
    from decimal import Decimal

    ob_type = "one_time" if obligation.type == "one_time" else "recurring"
    amount_type = obligation.amount_type
    if not amount_type:
        amount_type = (
            "fixed"
            if obligation.payment_mode in ("fixed", "fixed_full_payment", "partial_allowed")
            else "variable"
        )

    return ObligationV17Response(
        id=obligation.id,
        user_id=obligation.user_id,
        name=obligation.name,
        description=obligation.description,
        currency=obligation.currency,
        amount=obligation.base_amount or Decimal("0"),
        status=obligation.status or "active",
        obligation_type=ob_type,
        frequency=obligation.frequency,
        amount_type=amount_type,
        start_date=obligation.start_date,
        first_due_date=obligation.first_due_date,
        due_day=obligation.due_day,
        due_month=obligation.due_month,
        interval_count=obligation.interval_count or 1,
        end_date=obligation.end_date,
        end_count=obligation.end_count,
        metadata_=getattr(obligation, "metadata_", getattr(obligation, "metadata", {})),
        created_at=obligation.created_at or datetime.now(UTC),
        updated_at=obligation.updated_at or datetime.now(UTC),
    )


def _map_obligation_period(period) -> ObligationPeriodV17Response:
    from datetime import UTC, datetime
    from decimal import Decimal

    today = financial_date()
    start_d = getattr(period, "start_date", None)
    end_d = getattr(period, "end_date", None)
    due_d = getattr(period, "due_date", None)
    seq = getattr(period, "sequence_number", None)

    # Derived temporal flags
    if start_d is not None and end_d is not None:
        is_curr = start_d <= today <= end_d
        is_fut = start_d > today
    else:
        is_curr = bool(getattr(period, "is_current", False))
        is_fut = False

    raw_status = period.status
    raw_amount = getattr(period, "amount", None)
    raw_paid = getattr(period, "paid_amount", None)
    paid = raw_paid if raw_paid is not None else Decimal("0")

    is_closed = raw_status in (
        PeriodStatus.paid.value,
        PeriodStatus.skipped.value,
        PeriodStatus.cancelled.value,
    )

    req_def = raw_status == PeriodStatus.pending_amount_definition.value or (
        raw_amount is None and not is_closed
    )

    if is_closed or is_fut:
        is_od = False
    elif due_d is not None:
        is_od = due_d < today
    else:
        is_od = False

    amt = raw_amount if raw_amount is not None else Decimal("0")
    amt_due = max(Decimal("0"), amt - paid) if raw_amount is not None else Decimal("0")

    return ObligationPeriodV17Response(
        id=period.id,
        obligation_id=period.obligation_id,
        due_date=due_d,
        status=raw_status,
        is_current=is_curr,
        is_future=is_fut,
        is_overdue=is_od,
        requires_amount_definition=req_def,
        amount=amt,
        amount_due=amt_due,
        amount_paid=paid,
        start_date=start_d,
        end_date=end_d,
        sequence_number=seq,
        period_sequence=seq,
        currency=getattr(period, "currency", None),
        created_at=getattr(period, "created_at", None) or datetime.now(UTC),
        updated_at=getattr(period, "updated_at", None) or datetime.now(UTC),
    )


def _map_payment(payment) -> ObligationPaymentV17Response:
    from datetime import UTC, datetime

    return ObligationPaymentV17Response(
        id=payment.id,
        obligation_id=payment.obligation_id,
        obligation_period_id=payment.obligation_period_id,
        user_id=payment.user_id,
        account_id=getattr(payment, "account_id", None),
        amount=payment.amount,
        currency=getattr(payment, "currency", None),
        source_amount=getattr(payment, "source_amount", None),
        source_currency=getattr(payment, "source_currency", None),
        fx_rate=getattr(payment, "fx_rate", None),
        rate_source=getattr(payment, "rate_source", None),
        rate_timestamp=getattr(payment, "rate_timestamp", None),
        paid_at=getattr(payment, "paid_at", None),
        quote_id=payment.quote_id,
        rate_snapshot_id=getattr(payment, "rate_snapshot_id", None),
        idempotency_key=payment.idempotency_key,
        financial_event_id=getattr(payment, "financial_event_id", None),
        channel="manual",
        partial_context=getattr(payment, "partial_context", None),
        created_at=payment.created_at or datetime.now(UTC),
        updated_at=getattr(payment, "updated_at", payment.created_at) or datetime.now(UTC),
    )


@router.get(
    "",
    response_model=list[ObligationV17Response],
    summary="List Obligations (V1.7)",
    description="List all obligations using the V1.7 model.",
)
async def list_obligations_v17(
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Returns a list of V1.7 obligations for the current user.
    """
    service = ObligationV17Service(session)
    obligations = await service.list_obligations(current_profile.id)
    return [_map_obligation(obl) for obl in obligations]


@router.get(
    "/overview",
    response_model=list[ObligationV17OverviewResponse],
    summary="List Obligations Overview (V1.7)",
    description="List all obligations with pre-computed action state and relevant periods.",
)
async def list_obligations_overview_v17(
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Returns obligations overview with relevant periods to avoid N+1.
    """
    service = ObligationV17Service(session)
    overview = await service.list_obligations_overview(current_profile.id)

    # overview contains dicts that match ObligationV17OverviewResponse fields natively
    # but the period needs _map_obligation_period.
    result = []
    for ob_dict in overview:
        rp = ob_dict.get("relevant_period")
        ob_dict["relevant_period"] = _map_obligation_period(rp) if rp else None
        result.append(ob_dict)

    return result


@router.post(
    "",
    response_model=ObligationV17Response,
    status_code=status.HTTP_201_CREATED,
    summary="Create Obligation (V1.7)",
    description="Create a new obligation using the V1.7 model.",
)
async def create_obligation_v17(
    data: ObligationV17CreateRequest,
    current_profile: CurrentUserProfile,
    db: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Creates an obligation using V1.7 contract.
    """
    uow = UnitOfWork(db)
    async with uow.transaction():
        service = ObligationV17Service(db)
        obligation, initial_period = await service.create_obligation(current_profile.id, data)

    # Map fields for response
    return _map_obligation(obligation)


@router.get(
    "/summary",
    response_model=ObligationsV17SummaryResponse,
    summary="Get Obligations Summary (V1.7)",
    description="Gets a high-level summary of V1.7 obligations for a specific month.",
)
async def get_obligations_summary_v17(
    current_profile: CurrentUserProfile,
    month: str | None = Query(
        None, pattern=r"^\d{4}-\d{2}$", description="Month in YYYY-MM format"
    ),
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Returns a dashboard summary for the specified month (or current month).
    """
    service = ObligationV17Service(session)
    return await service.get_summary(current_profile.id, month)


@router.get(
    "/intelligence-context",
    response_model=ObligationsV17IntelligenceContextResponse,
    summary="Get Intelligence Context (V1.7)",
    description="Gets a read-only context of V1.7 obligations designed for AI features.",
)
async def get_obligations_intelligence_context_v17(
    current_profile: CurrentUserProfile,
    month: str | None = Query(
        None, pattern=r"^\d{4}-\d{2}$", description="Month in YYYY-MM format"
    ),
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Returns an AI-friendly context summary, reusing the summary endpoint logic.
    """
    service = ObligationV17Service(session)
    return await service.get_intelligence_context(current_profile.id, month)


@router.get(
    "/{obligation_id}",
    response_model=ObligationV17Response,
    responses={403: {"model": ApiErrorResponse}, 404: {"model": ApiErrorResponse}},
    summary="Get V1.7 obligation by ID",
    description="Gets a specific obligation by ID using V1.7 read semantics.",
)
async def get_obligation_v17(
    obligation_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Obtiene una obligación específica V1.7.
    """
    service = ObligationV17Service(session)
    obligation = await service.get_obligation(current_profile.id, obligation_id)
    if not obligation:
        raise HTTPException(status_code=404, detail="Obligation not found")
    return _map_obligation(obligation)


@router.patch(
    "/{obligation_id}",
    response_model=ObligationV17Response,
    responses={
        400: {"model": ApiErrorResponse},
        403: {"model": ApiErrorResponse},
        404: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
    },
    summary="Update Obligation (V1.7)",
    dependencies=[Depends(check_v17_feature_flag)],
)
async def update_obligation_v17(
    obligation_id: UUID,
    data: ObligationV17UpdateRequest,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Actualiza campos mutables de una obligación (nombre, descripción, metadata, base_amount para fijas, estado).
    """
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = ObligationV17Service(session)
        obligation = await service.update_obligation(current_profile.id, obligation_id, data)
    return _map_obligation(obligation)


@router.get(
    "/{obligation_id}/payments",
    response_model=ObligationPaymentsV17ListResponse,
    responses={403: {"model": ApiErrorResponse}, 404: {"model": ApiErrorResponse}},
    summary="List payments for an obligation (V1.7)",
    dependencies=[Depends(check_v17_feature_flag)],
)
async def list_obligation_payments_v17(
    obligation_id: UUID,
    current_profile: CurrentUserProfile,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Historial paginado de pagos para una obligación específica.
    """
    service = ObligationV17Service(session)
    return await service.list_payments_for_obligation(
        current_profile.id, obligation_id, limit=limit, offset=offset
    )


@router.get(
    "/{obligation_id}/periods",
    response_model=list[ObligationPeriodV17Response],
    responses={403: {"model": ApiErrorResponse}, 404: {"model": ApiErrorResponse}},
    summary="List V1.7 obligation periods",
    description="Lists all periods for a specific obligation using V1.7 read semantics.",
    dependencies=[Depends(check_v17_feature_flag)],
)
async def get_obligation_periods_v17(
    obligation_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Lista de periodos de una obligación V1.7.
    """
    service = ObligationV17Service(session)
    periods = await service.list_periods_for_obligation(current_profile.id, obligation_id)

    if periods is None:
        raise HTTPException(status_code=404, detail="Obligation not found")

    if not periods:
        return []

    return [_map_obligation_period(p) for p in periods]


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
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
):
    """
    Define el monto para un periodo variable en V1.7.
    """
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = ObligationV17Service(session)
        period = await service.define_period_amount(
            current_profile.id, obligation_id, period_id, data
        )

    return _map_obligation_period(period)


@router.post(
    "/{obligation_id}/periods/{period_id}/payments/preview",
    response_model=ObligationPaymentPreviewV17Response,
    responses={
        400: {"model": ApiErrorResponse},
        404: {"model": ApiErrorResponse},
        409: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
    },
    summary="Preview a specific period payment (V1.7)",
    dependencies=[Depends(check_v17_feature_flag)],
)
async def preview_pay_period_v17(
    obligation_id: UUID,
    period_id: UUID,
    data: ObligationPaymentPreviewV17Request,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Simula o previsualiza un pago para un periodo específico, retornando el tipo de cambio y los montos exactos requeridos.
    """
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = ObligationV17Service(session)
        return await service.pay_preview_specific_period(
            current_profile.id, obligation_id, period_id, data
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
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """
    Registra un pago específico a un periodo en V1.7.
    """
    if idempotency_key:
        data.idempotency_key = idempotency_key
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = ObligationV17Service(session)
        payment, period = await service.pay_specific_period(
            current_profile.id, obligation_id, period_id, data
        )

    payment_response = _map_payment(payment)
    period_response = _map_obligation_period(period)
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
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(check_v17_feature_flag),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """
    Registra un pago general a una obligacion en V1.7 usando estrategia FIFO.
    """
    if idempotency_key:
        data.idempotency_key = idempotency_key
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = ObligationV17Service(session)
        payments, periods = await service.pay_obligation_fifo(
            current_profile.id, obligation_id, data
        )

    payment_responses = [_map_payment(pay) for pay in payments]
    period_responses = [_map_obligation_period(p) for p in periods]
    return ObligationFIFOPaymentResultResponse(
        payments=payment_responses,
        periods=period_responses,
        total_applied=data.amount,
        remaining_unapplied=Decimal("0"),
        strategy="fifo",
    )


@router.post(
    "/{obligation_id}/payments/smart",
    response_model=ObligationFIFOPaymentResultResponse,
    dependencies=[Depends(check_v17_feature_flag)],
)
async def pay_obligation_smart(
    obligation_id: UUID,
    data: ObligationFIFOPaymentCreateRequest,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """
    Unified smart payment endpoint.
    Automatically applies payment to the oldest payable periods.
    """
    if idempotency_key:
        data.idempotency_key = idempotency_key
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = ObligationV17Service(session)
        payments, periods = await service.pay_obligation_smart(
            current_profile.id, obligation_id, data
        )

    payment_responses = [_map_payment(pay) for pay in payments]
    period_responses = [_map_obligation_period(p) for p in periods]
    return ObligationFIFOPaymentResultResponse(
        payments=payment_responses,
        periods=period_responses,
        total_applied=data.amount,
        remaining_unapplied=Decimal("0"),
        strategy="smart",
    )


@router.post(
    "/{obligation_id}/periods/{period_id}/skip",
    response_model=ObligationPeriodV17Response,
    dependencies=[Depends(check_v17_feature_flag)],
)
async def skip_obligation_period(
    obligation_id: UUID,
    period_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Skip a period. Only allowed for pending_amount_definition or pending_payment.
    """
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = ObligationV17Service(session)
        period = await service.skip_period(
            user_id=str(current_profile.id),
            obligation_id=obligation_id,
            period_id=period_id,
            commit=False,
        )
    return _map_obligation_period(period)


@router.post(
    "/{obligation_id}/periods/{period_id}/cancel",
    response_model=ObligationPeriodV17Response,
    dependencies=[Depends(check_v17_feature_flag)],
)
async def cancel_obligation_period(
    obligation_id: UUID,
    period_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Cancel a period. Allowed for pending states, overdue, and skipped if no payments.
    """
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = ObligationV17Service(session)
        period = await service.cancel_period(
            user_id=str(current_profile.id),
            obligation_id=obligation_id,
            period_id=period_id,
            commit=False,
        )
    return _map_obligation_period(period)


@router.post(
    "/{obligation_id}/periods/refresh-overdue",
    response_model=ObligationPeriodRefreshOverdueResponse,
    dependencies=[Depends(check_v17_feature_flag)],
)
async def refresh_overdue_periods(
    obligation_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Refresh overdue periods for an obligation.
    """
    uow = UnitOfWork(session)
    async with uow.transaction():
        service = ObligationV17Service(session)
        updated_periods, count = await service.refresh_overdue_periods(
            user_id=str(current_profile.id),
            obligation_id=obligation_id,
            commit=False,
        )

    response_periods = [_map_obligation_period(p) for p in updated_periods]

    return ObligationPeriodRefreshOverdueResponse(
        updated_periods=response_periods, updated_count=count
    )


@router.get(
    "/payments/{payment_id}",
    response_model=ObligationPaymentV17Response,
    responses={403: {"model": ApiErrorResponse}, 404: {"model": ApiErrorResponse}},
    summary="Get V1.7 obligation payment by ID",
    description="Gets a specific obligation payment by ID using V1.7 read semantics.",
    dependencies=[Depends(check_v17_feature_flag)],
)
async def get_obligation_payment_v17(
    payment_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Obtiene un pago específico V1.7.
    """
    service = ObligationV17Service(session)
    return await service.get_payment(current_profile.id, payment_id)


def _get_auto_payment_service(session: AsyncSession):
    from app.accounts.repository import AccountRepository
    from app.fx.provider import get_default_fx_provider
    from app.fx.service import FXService
    from app.obligations.auto_service import ObligationAutoPaymentService

    fx_service = FXService(provider=get_default_fx_provider(), session=session)
    v17_service = ObligationV17Service(session)
    account_repo = AccountRepository(session)
    return ObligationAutoPaymentService(
        session=session,
        v17_service=v17_service,
        fx_service=fx_service,
        account_repo=account_repo,
    )


@router.get(
    "/{obligation_id}/auto-payment",
    response_model=ObligationAutoPaymentConfigRead,
    dependencies=[Depends(check_v17_feature_flag)],
)
async def get_obligation_auto_payment_config(
    obligation_id: UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    auto_service = _get_auto_payment_service(session)
    return await auto_service.get_config(current_profile.id, obligation_id)


@router.put(
    "/{obligation_id}/auto-payment",
    response_model=ObligationAutoPaymentConfigRead,
    dependencies=[Depends(check_v17_feature_flag)],
)
async def put_obligation_auto_payment_config(
    obligation_id: UUID,
    payload: ObligationAutoPaymentConfigUpdateRequest,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    auto_service = _get_auto_payment_service(session)
    return await auto_service.put_config(current_profile.id, obligation_id, payload)


@router.get(
    "/{obligation_id}/auto-payment/runs",
    response_model=ObligationAutoPaymentRunsPage,
    dependencies=[Depends(check_v17_feature_flag)],
)
async def list_obligation_auto_payment_runs(
    obligation_id: UUID,
    current_profile: CurrentUserProfile,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
):
    auto_service = _get_auto_payment_service(session)
    return await auto_service.list_runs(
        current_profile.id, obligation_id, limit=limit, offset=offset
    )
