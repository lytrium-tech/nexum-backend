import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.uow import UnitOfWork
from app.credit.exceptions import (
    CreditCardInactiveError,
    CreditCardNotFoundError,
    CreditLimitExceededError,
    InvalidPaymentAmountError,
)
from app.credit.schemas import (
    CreditCardCreate,
    CreditCardEarlyPaymentCreate,
    CreditCardEarlyPaymentResult,
    CreditCardInstallmentRead,
    CreditCardPaymentCreate,
    CreditCardPaymentResult,
    CreditCardPurchaseCreate,
    CreditCardPurchaseResult,
    CreditCardRead,
    CreditCardStatusRead,
    CreditCardUpdate,
    CreditSummaryRead,
)
from app.credit.service import CreditCardService
from app.users.dependencies import CurrentUserProfile

router = APIRouter()


def get_credit_service(session: AsyncSession = Depends(get_db_session)) -> CreditCardService:
    return CreditCardService(session)


@router.post("/cards", response_model=CreditCardRead, status_code=status.HTTP_201_CREATED)
async def create_card(
    payload: CreditCardCreate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    uow = UnitOfWork(session)
    async with uow.transaction():
        user_id = current_profile.id
        service = get_credit_service(session)
        return await service.create_card(user_id, payload)


@router.get("/cards", response_model=list[CreditCardRead])
async def list_cards(
    current_profile: CurrentUserProfile, session: AsyncSession = Depends(get_db_session)
):
    user_id = current_profile.id
    service = get_credit_service(session)
    return await service.list_cards(user_id)


@router.get("/cards/{card_id}", response_model=CreditCardRead)
async def get_card(
    card_id: uuid.UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    user_id = current_profile.id
    service = get_credit_service(session)
    try:
        return await service.get_card(user_id, card_id)
    except CreditCardNotFoundError:
        raise HTTPException(status_code=404, detail="Credit card not found")


@router.patch("/cards/{card_id}", response_model=CreditCardRead)
async def update_card(
    card_id: uuid.UUID,
    payload: CreditCardUpdate,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    uow = UnitOfWork(session)
    async with uow.transaction():
        user_id = current_profile.id
        service = get_credit_service(session)
        try:
            return await service.update_card(user_id, card_id, payload)
        except CreditCardNotFoundError:
            raise HTTPException(status_code=404, detail="Credit card not found")


@router.delete("/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card(
    card_id: uuid.UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    uow = UnitOfWork(session)
    async with uow.transaction():
        user_id = current_profile.id
        service = get_credit_service(session)
        try:
            await service.delete_card(user_id, card_id)
        except CreditCardNotFoundError:
            raise HTTPException(status_code=404, detail="Credit card not found")


@router.post("/cards/{card_id}/purchases", response_model=CreditCardPurchaseResult)
async def create_purchase(
    card_id: uuid.UUID,
    payload: CreditCardPurchaseCreate,
    current_profile: CurrentUserProfile,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        command_id = uuid.UUID(idempotency_key)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Idempotency-Key format")

    uow = UnitOfWork(session)
    async with uow.transaction():
        user_id = current_profile.id
        service = get_credit_service(session)
        try:
            return await service.create_purchase(user_id, card_id, payload, command_id)
        except CreditCardNotFoundError:
            raise HTTPException(status_code=404, detail="Credit card not found")
        except CreditCardInactiveError:
            raise HTTPException(status_code=400, detail="Credit card is inactive")
        except CreditLimitExceededError:
            raise HTTPException(status_code=409, detail="Credit limit exceeded")


@router.post("/cards/{card_id}/payments", response_model=CreditCardPaymentResult)
async def create_payment(
    card_id: uuid.UUID,
    payload: CreditCardPaymentCreate,
    current_profile: CurrentUserProfile,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        command_id = uuid.UUID(idempotency_key)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Idempotency-Key format")

    uow = UnitOfWork(session)
    async with uow.transaction():
        user_id = current_profile.id
        service = get_credit_service(session)
        try:
            return await service.create_payment(user_id, card_id, payload, command_id)
        except CreditCardNotFoundError:
            raise HTTPException(status_code=404, detail="Credit card not found")
        except CreditCardInactiveError:
            raise HTTPException(status_code=400, detail="Credit card is inactive")
        except ValueError as e:
            if str(e) == "Account not found or inactive":
                raise HTTPException(status_code=404, detail=str(e))
            if str(e) == "Insufficient balance":
                raise HTTPException(status_code=400, detail=str(e))
            raise HTTPException(status_code=400, detail=str(e))
        except InvalidPaymentAmountError:
            raise HTTPException(status_code=409, detail="Payment amount exceeds current debt")


@router.post("/cards/{card_id}/purchases/{purchase_id}/pay_early", response_model=CreditCardEarlyPaymentResult)
async def create_early_payment(
    card_id: uuid.UUID,
    purchase_id: uuid.UUID,
    payload: CreditCardEarlyPaymentCreate,
    current_profile: CurrentUserProfile,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        command_id = uuid.UUID(idempotency_key)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Idempotency-Key format")

    uow = UnitOfWork(session)
    async with uow.transaction():
        user_id = current_profile.id
        service = get_credit_service(session)
        try:
            return await service.create_early_payment(user_id, card_id, purchase_id, payload, command_id)
        except CreditCardNotFoundError:
            raise HTTPException(status_code=404, detail="Credit card not found")
        except CreditCardInactiveError:
            raise HTTPException(status_code=400, detail="Credit card is inactive")
        except ValueError as e:
            if str(e) == "Account not found or inactive":
                raise HTTPException(status_code=404, detail=str(e))
            if str(e) == "Insufficient balance":
                raise HTTPException(status_code=400, detail=str(e))
            raise HTTPException(status_code=400, detail=str(e))
        except InvalidPaymentAmountError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            from app.credit.exceptions import CreditDomainError
            if isinstance(e, CreditDomainError) and "Purchase not found" in str(e):
                raise HTTPException(status_code=404, detail=str(e))
            if isinstance(e, CreditDomainError):
                raise HTTPException(status_code=400, detail=str(e))
            raise


@router.get("/summary", response_model=CreditSummaryRead)
async def get_credit_summary(
    current_profile: CurrentUserProfile, session: AsyncSession = Depends(get_db_session)
):
    user_id = current_profile.id
    service = get_credit_service(session)
    return await service.get_credit_summary(user_id)


@router.get("/cards/{card_id}/status", response_model=CreditCardStatusRead)
async def get_card_status(
    card_id: uuid.UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    user_id = current_profile.id
    service = get_credit_service(session)
    try:
        return await service.get_card_status(user_id, card_id)
    except CreditCardNotFoundError:
        raise HTTPException(status_code=404, detail="Credit card not found")


@router.get("/cards/{card_id}/installments", response_model=list[CreditCardInstallmentRead])
async def list_card_installments(
    card_id: uuid.UUID,
    current_profile: CurrentUserProfile,
    session: AsyncSession = Depends(get_db_session),
):
    user_id = current_profile.id
    service = get_credit_service(session)
    try:
        return await service.list_installments(user_id, card_id)
    except CreditCardNotFoundError:
        raise HTTPException(status_code=404, detail="Credit card not found")
