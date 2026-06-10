from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.cash.schemas import CashExpenseCreate, CashIncomeCreate, CashOperationResult
from app.cash.service import CashService
from app.categories.repository import CategoryRepository
from app.core.database import get_db_session
from app.core.security import AuthenticatedUser, get_current_user
from app.core.uow import UnitOfWork
from app.ledger.repository import LedgerRepository

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
    idempotency_key: UUID = Header(
        ..., description="UUID único para identificar la transacción de negocio"
    ),
    auth_user: AuthenticatedUser = Depends(get_current_user),
    service: CashService = Depends(get_cash_service),
) -> CashOperationResult:
    return await service.create_income(
        auth_user=auth_user,
        payload=payload,
        command_id=idempotency_key,
    )


@router.post("/expense", response_model=CashOperationResult)
async def create_expense(
    payload: CashExpenseCreate,
    idempotency_key: UUID = Header(
        ..., description="UUID único para identificar la transacción de negocio"
    ),
    auth_user: AuthenticatedUser = Depends(get_current_user),
    service: CashService = Depends(get_cash_service),
) -> CashOperationResult:
    return await service.create_expense(
        auth_user=auth_user,
        payload=payload,
        command_id=idempotency_key,
    )
