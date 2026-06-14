"""
app/transfers/service.py
========================
"""

from uuid import UUID

from app.accounts.repository import AccountRepository
from app.cash.exceptions import InsufficientFundsError
from app.core.errors import ForbiddenError, NotFoundError
from app.core.uow import UnitOfWork
from app.ledger.enums import Direction, EventType
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate
from app.transfers.models import Transfer
from app.transfers.repository import TransfersRepository
from app.transfers.schemas import LedgerEventsRef, TransferCreate, TransferResult


class TransfersService:
    def __init__(
        self,
        uow: UnitOfWork,
        transfers_repo: TransfersRepository,
        ledger_repo: LedgerRepository,
        account_repo: AccountRepository,
    ):
        self.uow = uow
        self.transfers_repo = transfers_repo
        self.ledger_repo = ledger_repo
        self.account_repo = account_repo

    async def create_transfer(self, user_id: UUID, payload: TransferCreate) -> TransferResult:
        if payload.source_account_id == payload.destination_account_id:
            raise ForbiddenError(message="No puedes transferir a la misma cuenta.")

        async with self.uow.transaction():
            # Obtener y bloquear cuentas
            # Ordenamos los IDs para evitar deadlocks en la BD si hay 2 transacciones simultaneas cruzadas
            if payload.source_account_id < payload.destination_account_id:
                first_id, second_id = payload.source_account_id, payload.destination_account_id
            else:
                first_id, second_id = payload.destination_account_id, payload.source_account_id

            first_acc = await self.account_repo.get_by_id_for_update(first_id)
            second_acc = await self.account_repo.get_by_id_for_update(second_id)

            if not first_acc or not second_acc:
                raise NotFoundError(message="Una de las cuentas no fue encontrada.")

            if first_acc.user_id != user_id or second_acc.user_id != user_id:
                raise ForbiddenError(message="No tienes permisos sobre las cuentas.")

            if not first_acc.is_active or not second_acc.is_active:
                raise ForbiddenError(message="Ambas cuentas deben estar activas.")

            # Identificar cual es cual
            source_acc = first_acc if first_acc.id == payload.source_account_id else second_acc
            dest_acc = first_acc if first_acc.id == payload.destination_account_id else second_acc

            if source_acc.balance < payload.amount:
                raise InsufficientFundsError(message="Fondos insuficientes en la cuenta de origen.")

            # Preparar transferencia
            transfer = Transfer(
                user_id=user_id,
                source_account_id=payload.source_account_id,
                destination_account_id=payload.destination_account_id,
                amount=payload.amount,
                currency=payload.currency,
                description=payload.description,
                command_id=payload.command_id,
                source_message_id=payload.source_message_id,
                raw_message=payload.raw_message,
            )

            if payload.occurred_at:
                transfer.created_at = payload.occurred_at

            if payload.command_id:
                existing = await self.transfers_repo.get_by_command_id(payload.command_id)
                if existing:
                    return TransferResult.model_validate(existing)
            
            self.transfers_repo.session.add(transfer)
            await self.transfers_repo.session.flush()

            # Evento salida (transfer_out)
            out_event_create = LedgerEventCreate(
                user_id=user_id,
                account_id=source_acc.id,
                category_id=None,
                event_type=EventType.TRANSFER_OUT,
                direction=Direction.OUTFLOW,
                amount=payload.amount,
                description=payload.description,
                source="backend",
                command_id=None, # command_id está en transfer, si lo ponemos aquí chocaría. Podríamos usar UUID5 pero lo evitamos
                transfer_id=transfer.id,
                source_message_id=payload.source_message_id,
                occurred_at=payload.occurred_at
            )
            out_result = await self.ledger_repo.insert_event(out_event_create)

            # Evento entrada (transfer_in)
            in_event_create = LedgerEventCreate(
                user_id=user_id,
                account_id=dest_acc.id,
                category_id=None,
                event_type=EventType.TRANSFER_IN,
                direction=Direction.INFLOW,
                amount=payload.amount,
                description=payload.description,
                source="backend",
                command_id=None,
                transfer_id=transfer.id,
                source_message_id=payload.source_message_id,
                occurred_at=payload.occurred_at
            )
            in_result = await self.ledger_repo.insert_event(in_event_create)

            # Actualizar balances
            await self.account_repo.update_balance(source_acc, -payload.amount)
            await self.account_repo.update_balance(dest_acc, payload.amount)

            transfer.source_account = source_acc
            transfer.destination_account = dest_acc

            res = TransferResult.model_validate(transfer)
            res.ledger_events = LedgerEventsRef(
                out_event_id=out_result.event.id,
                in_event_id=in_result.event.id
            )
            return res

    async def list_transfers(self, user_id: UUID, limit: int = 50, offset: int = 0) -> tuple[list[Transfer], int]:
        return await self.transfers_repo.list_transfers(user_id, limit, offset)

    async def get_transfer(self, transfer_id: UUID, user_id: UUID) -> TransferResult:
        t = await self.transfers_repo.get_by_id(transfer_id, user_id)
        return TransferResult.model_validate(t)
