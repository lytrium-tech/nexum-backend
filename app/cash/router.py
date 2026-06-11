from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.cash.schemas import CashExpenseCreate, CashIncomeCreate, CashOperationResult
from app.cash.service import CashService
from app.categories.repository import CategoryRepository
from app.core.database import get_db_session
from app.core.uow import UnitOfWork
from app.ledger.repository import LedgerRepository
from app.users.dependencies import CurrentUserProfile

router = APIRouter(prefix="/cash", tags=["cash"])


def get_cash_service(session: AsyncSession = Depends(get_db_session)) -> CashService:
    uow = UnitOfWork(session)
    ledger_repo = LedgerRepository(session)
    account_repo = AccountRepository(session)
    category_repo = CategoryRepository(session)
    return CashService(
        uow=uow,
        ledger_repo=ledger_repo,
        account_repo=account_repo,
        category_repo=category_repo,
    )


@router.post("/income", response_model=CashOperationResult)
async def create_income(
    payload: CashIncomeCreate,
    current_profile: CurrentUserProfile,
    idempotency_key: UUID = Header(
        ..., description="UUID único para identificar la transacción de negocio"
    ),
    service: CashService = Depends(get_cash_service),
) -> CashOperationResult:
    return await service.create_income(
        user_id=current_profile.id,
        payload=payload,
        command_id=idempotency_key,
    )


@router.post("/expense", response_model=CashOperationResult)
async def create_expense(
    payload: CashExpenseCreate,
    current_profile: CurrentUserProfile,
    idempotency_key: UUID = Header(
        ..., description="UUID único para identificar la transacción de negocio"
    ),
    service: CashService = Depends(get_cash_service),
) -> CashOperationResult:
    return await service.create_expense(
        user_id=current_profile.id,
        payload=payload,
        command_id=idempotency_key,
    )
