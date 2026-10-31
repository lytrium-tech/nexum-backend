import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.errors import NotFoundError
from app.core.uow import UnitOfWork
from app.credit.exceptions import CreditCardInactiveError, CreditCardNotFoundError
from app.credit.schemas import (
    CreditCardAdjustmentCreate,
    CreditCardAdjustmentResult,
    CreditCardCreate,
    CreditCardEarlyPaymentCreate,
    CreditCardEarlyPaymentPreviewCreate,
    CreditCardEarlyPaymentResult,
    CreditCardInstallmentRead,
    CreditCardOpeningBalanceCreate,
    CreditCardOpeningBalanceResult,
    CreditCardPaymentCreate,
    CreditCardPaymentResult,
    CreditCardPurchaseCreate,
    CreditCardPurchaseResult,
    CreditCardRead,
    CreditCardStatementRead,
    CreditCardStatusRead,
    CreditCardUpdate,
    CreditCommandReconciliationRead,
    CreditSummaryRead,
    PaymentPreviewResult,
)
from app.credit.service import CreditCardService
from app.users.dependencies import CurrentUserProfile

router = APIRouter()


def get_credit_service(session: AsyncSession = Depends(get_db_session)) -> CreditCardService:
    return CreditCardService(session)


@router.post(
    "/cards/{card_id}/opening-balance",
    response_model=CreditCardOpeningBalanceResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_opening_balance(
    card_id: uuid.UUID,
    payload: CreditCardOpeningBalanceCreate,
    current_profile: CurrentUserProfile,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        command_id = uuid.UUID(idempotency_key)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Idempotency-Key format")
    async with UnitOfWork(session).transaction():
        service = get_credit_service(session)
        try:
            return await service.create_opening_balance(
                current_profile.id, card_id, payload, command_id
            )
        except CreditCardNotFoundError:
            raise HTTPException(status_code=404, detail="Credit card not found")
        except CreditCardInactiveError:
            raise HTTPException(status_code=400, detail="Credit card is inactive")


@router.post("/cards", response_model=CreditCardRead, status_code=status.HTTP_201_CREATED)
async def create_card(
    payload: CreditCardCreate,
    current_profile: CurrentUserProfile,
    service: CreditCardService = Depends(get_credit_service),
    session: AsyncSession = Depends(get_db_session),
):
    uow = UnitOfWork(session)
    async with uow.transaction():
        user_id = current_profile.id
        return await service.create_card(user_id, payload)


@router.get("/cards", response_model=list[CreditCardRead])
async def list_cards(
    current_profile: CurrentUserProfile,
    service: CreditCardService = Depends(get_credit_service),
):
    user_id = current_profile.id
    return await service.list_cards(user_id)


@router.get("/cards/{card_id}", response_model=CreditCardRead)
async def get_card(
    card_id: uuid.UUID,
    current_profile: CurrentUserProfile,
    service: CreditCardService = Depends(get_credit_service),
):
    user_id = current_profile.id
    return await service.get_card(user_id, card_id)


@router.patch("/cards/{card_id}", response_model=CreditCardRead)
async def update_card(
    card_id: uuid.UUID,
    payload: CreditCardUpdate,
    current_profile: CurrentUserProfile,
    service: CreditCardService = Depends(get_credit_service),
    session: AsyncSession = Depends(get_db_session),
):
    uow = UnitOfWork(session)
    async with uow.transaction():
        user_id = current_profile.id
        return await service.update_card(user_id, card_id, payload)


@router.delete("/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card(
    card_id: uuid.UUID,
    current_profile: CurrentUserProfile,
    service: CreditCardService = Depends(get_credit_service),
    session: AsyncSession = Depends(get_db_session),
):
    uow = UnitOfWork(session)
    async with uow.transaction():
        user_id = current_profile.id
        await service.delete_card(user_id, card_id)


@router.post("/cards/{card_id}/purchases", response_model=CreditCardPurchaseResult)
async def create_purchase(
    card_id: uuid.UUID,
    payload: CreditCardPurchaseCreate,
    current_profile: CurrentUserProfile,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    service: CreditCardService = Depends(get_credit_service),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        command_id = uuid.UUID(idempotency_key)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Idempotency-Key format")

    uow = UnitOfWork(session)
    async with uow.transaction():
        user_id = current_profile.id
        return await service.create_purchase(user_id, card_id, payload, command_id)


@router.post("/cards/{card_id}/payments", response_model=CreditCardPaymentResult)
async def create_payment(
    card_id: uuid.UUID,
    payload: CreditCardPaymentCreate,
    current_profile: CurrentUserProfile,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    service: CreditCardService = Depends(get_credit_service),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        command_id = uuid.UUID(idempotency_key)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Idempotency-Key format")

    uow = UnitOfWork(session)
    async with uow.transaction():
        user_id = current_profile.id
        return await service.create_payment(user_id, card_id, payload, command_id)


@router.post(
    "/cards/{card_id}/purchases/{purchase_id}/pay_early",
    response_model=CreditCardEarlyPaymentResult,
)
async def create_early_payment(
    card_id: uuid.UUID,
    purchase_id: uuid.UUID,
    payload: CreditCardEarlyPaymentCreate,
    current_profile: CurrentUserProfile,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    service: CreditCardService = Depends(get_credit_service),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        command_id = uuid.UUID(idempotency_key)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Idempotency-Key format")

    uow = UnitOfWork(session)
    async with uow.transaction():
        user_id = current_profile.id
        return await service.create_early_payment(
            user_id, card_id, purchase_id, payload, command_id
        )


@router.post(
    "/cards/{card_id}/purchases/{purchase_id}/pay_early/preview",
    response_model=PaymentPreviewResult,
)
async def preview_early_payment(
    card_id: uuid.UUID,
    purchase_id: uuid.UUID,
    payload: CreditCardEarlyPaymentPreviewCreate,
    current_profile: CurrentUserProfile,
    service: CreditCardService = Depends(get_credit_service),
):
    # Strictly read-only: no uow.transaction(), no row locks (F-17)
    user_id = current_profile.id
    return await service.preview_early_payment(user_id, card_id, purchase_id, payload)


@router.post("/cards/{card_id}/adjustments", response_model=CreditCardAdjustmentResult)
async def create_adjustment(
    card_id: uuid.UUID,
    payload: CreditCardAdjustmentCreate,
    current_profile: CurrentUserProfile,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    service: CreditCardService = Depends(get_credit_service),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        command_id = uuid.UUID(idempotency_key)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Idempotency-Key format")

    uow = UnitOfWork(session)
    async with uow.transaction():
        user_id = current_profile.id
        return await service.create_adjustment(user_id, card_id, payload, command_id)


@router.get("/summary", response_model=CreditSummaryRead)
async def get_credit_summary(
    current_profile: CurrentUserProfile,
    service: CreditCardService = Depends(get_credit_service),
):
    user_id = current_profile.id
    return await service.get_credit_summary(user_id)


@router.get("/cards/{card_id}/status", response_model=CreditCardStatusRead)
async def get_card_status(
    card_id: uuid.UUID,
    current_profile: CurrentUserProfile,
    service: CreditCardService = Depends(get_credit_service),
):
    user_id = current_profile.id
    return await service.get_card_status(user_id, card_id)


@router.get("/cards/{card_id}/installments", response_model=list[CreditCardInstallmentRead])
async def list_card_installments(
    card_id: uuid.UUID,
    current_profile: CurrentUserProfile,
    service: CreditCardService = Depends(get_credit_service),
):
    user_id = current_profile.id
    return await service.list_installments(user_id, card_id)


@router.get("/cards/{card_id}/statements", response_model=list[CreditCardStatementRead])
async def list_statements(
    card_id: uuid.UUID,
    current_profile: CurrentUserProfile,
    service: CreditCardService = Depends(get_credit_service),
):
    user_id = current_profile.id
    return await service.list_statements(user_id, card_id)


@router.get("/cards/{card_id}/statements/{period}", response_model=CreditCardStatementRead)
async def get_statement(
    card_id: uuid.UUID,
    period: str,
    current_profile: CurrentUserProfile,
    service: CreditCardService = Depends(get_credit_service),
):
    user_id = current_profile.id
    statement = await service.get_statement(user_id, card_id, period)
    if not statement:
        raise NotFoundError(message="Statement not found")
    return statement


@router.get("/commands/{command_id}", response_model=CreditCommandReconciliationRead)
async def reconcile_command(
    command_id: uuid.UUID,
    current_profile: CurrentUserProfile,
    service: CreditCardService = Depends(get_credit_service),
) -> CreditCommandReconciliationRead:
    user_id = current_profile.id
    return await service.reconcile_command(user_id=user_id, command_id=command_id)
