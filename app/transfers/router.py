"""
app/transfers/router.py
=======================
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.uow import UnitOfWork
from app.transfers.schemas import TransferCreate, TransferResult
from app.transfers.service import TransfersService
from app.transfers.repository import TransfersRepository
from app.ledger.repository import LedgerRepository
from app.accounts.repository import AccountRepository
from app.users.dependencies import CurrentUserProfile

from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session

router = APIRouter(prefix="/transfers", tags=["transfers"])

def get_transfers_service(session: AsyncSession = Depends(get_db_session)) -> TransfersService:
    uow = UnitOfWork(session)
    return TransfersService(
        uow=uow,
        transfers_repo=TransfersRepository(session),
        ledger_repo=LedgerRepository(session),
        account_repo=AccountRepository(session),
    )


@router.post("", response_model=TransferResult, status_code=status.HTTP_201_CREATED)
async def create_transfer(
    user: CurrentUserProfile,
    payload: TransferCreate,
    service: TransfersService = Depends(get_transfers_service),
):
    return await service.create_transfer(user.id, payload)


@router.get("", response_model=list[TransferResult])
async def list_transfers(
    user: CurrentUserProfile,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: TransfersService = Depends(get_transfers_service),
):
    items, _ = await service.list_transfers(user.id, limit, offset)
    return [TransferResult.model_validate(t) for t in items]


@router.get("/{transfer_id}", response_model=TransferResult)
async def get_transfer(
    transfer_id: UUID,
    user: CurrentUserProfile,
    service: TransfersService = Depends(get_transfers_service),
):
    return await service.get_transfer(transfer_id, user.id)
