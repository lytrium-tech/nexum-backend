"""
app/transfers/router.py
=======================
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.core.database import get_db_session
from app.core.uow import UnitOfWork
from app.ledger.repository import LedgerRepository
from app.transfers.repository import TransfersRepository
from app.transfers.schemas import TransferCreate, TransferRequest, TransferResult
from app.transfers.service import TransfersService
from app.users.dependencies import get_current_user_profile_dep
from app.users.schemas import UserRead

router = APIRouter(prefix="/transfers", tags=["transfers"])


def get_transfers_service(session: AsyncSession = Depends(get_db_session)) -> TransfersService:
    uow = UnitOfWork(session)
    return TransfersService(
        uow=uow,
        transfers_repo=TransfersRepository(session),
        ledger_repo=LedgerRepository(session),
        account_repo=AccountRepository(session),
    )


@router.post(
    "",
    response_model=TransferResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Autenticación requerida"},
        status.HTTP_404_NOT_FOUND: {"description": "Cuenta no encontrada o inaccesible"},
        status.HTTP_409_CONFLICT: {"description": "Conflicto financiero o idempotente"},
    },
)
async def create_transfer(
    payload: TransferRequest,
    user: UserRead = Depends(get_current_user_profile_dep),
    service: TransfersService = Depends(get_transfers_service),
) -> TransferResult:
    return await service.create_transfer(user.id, TransferCreate(**payload.model_dump()))


@router.get(
    "",
    response_model=list[TransferResult],
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Autenticación requerida"}},
)
async def list_transfers(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: UserRead = Depends(get_current_user_profile_dep),
    service: TransfersService = Depends(get_transfers_service),
) -> list[TransferResult]:
    items, _ = await service.list_transfers(user.id, limit, offset)
    return [TransferResult.model_validate(t) for t in items]


@router.get(
    "/{transfer_id}",
    response_model=TransferResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Autenticación requerida"},
        status.HTTP_404_NOT_FOUND: {"description": "Transferencia no encontrada"},
    },
)
async def get_transfer(
    transfer_id: UUID,
    user: UserRead = Depends(get_current_user_profile_dep),
    service: TransfersService = Depends(get_transfers_service),
) -> TransferResult:
    return await service.get_transfer(transfer_id, user.id)
