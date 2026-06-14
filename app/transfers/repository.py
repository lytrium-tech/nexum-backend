"""
app/transfers/repository.py
===========================
"""

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.transfers.models import Transfer


class TransfersRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_transfer(self, transfer: Transfer) -> Transfer:
        try:
            async with self.session.begin_nested():
                self.session.add(transfer)
                await self.session.flush()
        except IntegrityError as exc:
            error_msg = str(exc.orig)
            constraint_name = getattr(exc.orig, "constraint_name", None)

            is_idempotency = (
                constraint_name in ("transfers_command_id_key", "transfers_command_id_idx")
                or "transfers_command_id_key" in error_msg
            )
            
            if is_idempotency:
                assert transfer.command_id is not None
                existing = await self.get_by_command_id(transfer.command_id)
                if existing:
                    return existing

            raise ConflictError(message="Error de integridad al registrar transferencia", details=error_msg)

        await self.session.refresh(transfer, ["source_account", "destination_account"])
        return transfer

    async def get_by_command_id(self, command_id: UUID) -> Transfer | None:
        stmt = (
            select(Transfer)
            .options(selectinload(Transfer.source_account), selectinload(Transfer.destination_account))
            .where(Transfer.command_id == command_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, transfer_id: UUID, user_id: UUID) -> Transfer:
        stmt = (
            select(Transfer)
            .options(selectinload(Transfer.source_account), selectinload(Transfer.destination_account))
            .where(Transfer.id == transfer_id, Transfer.user_id == user_id)
        )
        result = await self.session.execute(stmt)
        transfer = result.scalar_one_or_none()
        if not transfer:
            raise NotFoundError(message="Transferencia no encontrada.")
        return transfer

    async def list_transfers(self, user_id: UUID, limit: int = 50, offset: int = 0) -> tuple[list[Transfer], int]:
        # Ocurred_at doesn't exist on transfers, we use created_at for ordering.
        stmt = (
            select(Transfer)
            .options(selectinload(Transfer.source_account), selectinload(Transfer.destination_account))
            .where(Transfer.user_id == user_id)
            .order_by(Transfer.created_at.desc())
        )
        
        # Paginate
        page_stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(page_stmt)
        items = list(result.scalars().all())

        # Count
        from sqlalchemy import func
        count_stmt = select(func.count()).select_from(Transfer).where(Transfer.user_id == user_id)
        count_result = await self.session.execute(count_stmt)
        total = cast(int, count_result.scalar_one())

        return items, total
