"""
app/transfers/service.py
========================
"""

from uuid import UUID

from app.accounts.repository import AccountRepository
from app.cash.exceptions import InsufficientFundsError
from app.core.errors import ConflictError, NotFoundError, ValidationError
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
            raise ValidationError(
                message="No puedes transferir a la misma cuenta.",
                error_code="same_account",
            )

        existing = await self.transfers_repo.get_by_command_id(payload.command_id)
        if existing:
            if not self._matches_command(existing, user_id, payload):
                raise ConflictError(
                    message="Llave de idempotencia reutilizada con datos diferentes.",
                    error_code="idempotency_key_reused",
                )

            res = TransferResult.model_validate(existing)
            res.is_idempotent = True
            return res

        async with self.uow.transaction():
            first_id, second_id = sorted(
                (payload.source_account_id, payload.destination_account_id),
                key=lambda value: value.int,
            )

            first_acc = await self.account_repo.get_by_id_for_update(
                first_id, include_inactive=True
            )
            second_acc = await self.account_repo.get_by_id_for_update(
                second_id, include_inactive=True
            )

            # Para evitar filtrado, validamos la existencia y luego asignamos roles
            source_acc = first_acc if first_id == payload.source_account_id else second_acc
            dest_acc = first_acc if first_id == payload.destination_account_id else second_acc

            if not source_acc or source_acc.user_id != user_id:
                raise NotFoundError(
                    message="Cuenta de origen no encontrada.",
                    error_code="source_account_not_found",
                )

            if not dest_acc or dest_acc.user_id != user_id:
                raise NotFoundError(
                    message="Cuenta destino no encontrada.",
                    error_code="destination_account_not_found",
                )

            if not source_acc.is_active:
                raise ValidationError(
                    message="Cuenta de origen inactiva.",
                    error_code="source_account_inactive",
                )
            if not dest_acc.is_active:
                raise ValidationError(
                    message="Cuenta destino inactiva.",
                    error_code="destination_account_inactive",
                )

            if source_acc.currency != dest_acc.currency:
                raise ValidationError(
                    message="Las transferencias entre monedas diferentes no están soportadas actualmente.",
                    error_code="cross_currency_transfer_not_supported",
                )

            source_amount = payload.amount
            if source_acc.balance is None or dest_acc.balance is None:
                raise ConflictError(
                    message="Una de las cuentas no tiene un saldo válido.",
                    error_code="account_balance_invalid",
                )
            if source_acc.balance < source_amount:
                raise InsufficientFundsError(message="Fondos insuficientes en la cuenta de origen.")

            # Misma moneda
            currency = source_acc.currency
            target_amount = source_amount

            transfer = Transfer(
                user_id=user_id,
                source_account_id=payload.source_account_id,
                destination_account_id=payload.destination_account_id,
                amount=source_amount,
                currency=currency,
                target_amount=target_amount,
                target_currency=currency,
                fx_rate=None,
                rate_source=None,
                rate_timestamp=None,
                is_estimated=False,
                description=payload.description,
                command_id=payload.command_id,
                source_message_id=payload.source_message_id,
                raw_message=payload.raw_message,
                status="completed",
            )

            if payload.occurred_at:
                transfer.created_at = payload.occurred_at

            saved_transfer = await self.transfers_repo.create_transfer(transfer)

            if saved_transfer is not transfer:
                if not self._matches_command(saved_transfer, user_id, payload):
                    raise ConflictError(
                        message="Llave de idempotencia reutilizada con datos diferentes concurrentemente.",
                        error_code="idempotency_key_reused",
                    )

                res = TransferResult.model_validate(saved_transfer)
                res.is_idempotent = True
                return res

            out_event_create = LedgerEventCreate(
                user_id=user_id,
                account_id=source_acc.id,
                category_id=None,
                event_type=EventType.TRANSFER_OUT,
                direction=Direction.OUTFLOW,
                amount=source_amount,
                currency=currency,
                description=payload.description,
                source="backend",
                command_id=None,
                transfer_id=saved_transfer.id,
                source_message_id=payload.source_message_id,
                occurred_at=payload.occurred_at,
            )
            out_result = await self.ledger_repo.insert_event(out_event_create)

            in_event_create = LedgerEventCreate(
                user_id=user_id,
                account_id=dest_acc.id,
                category_id=None,
                event_type=EventType.TRANSFER_IN,
                direction=Direction.INFLOW,
                amount=target_amount,
                currency=currency,
                description=payload.description,
                source="backend",
                command_id=None,
                transfer_id=saved_transfer.id,
                source_message_id=payload.source_message_id,
                occurred_at=payload.occurred_at,
            )
            in_result = await self.ledger_repo.insert_event(in_event_create)

            # Actualizar balances
            await self.account_repo.update_balance(source_acc, -source_amount)
            await self.account_repo.update_balance(dest_acc, target_amount)

            saved_transfer.source_account = source_acc
            saved_transfer.destination_account = dest_acc

            res = TransferResult.model_validate(saved_transfer)
            res.ledger_events = LedgerEventsRef(
                out_event_id=out_result.event.id, in_event_id=in_result.event.id
            )
            return res

    @staticmethod
    def _matches_command(existing: Transfer, user_id: UUID, payload: TransferCreate) -> bool:
        occurred_at_matches = (
            payload.occurred_at is None or existing.created_at == payload.occurred_at
        )
        return (
            existing.user_id == user_id
            and existing.source_account_id == payload.source_account_id
            and existing.destination_account_id == payload.destination_account_id
            and existing.amount == payload.amount
            and existing.description == payload.description
            and existing.source_message_id == payload.source_message_id
            and existing.raw_message == payload.raw_message
            and existing.status == "completed"
            and occurred_at_matches
        )

    async def list_transfers(
        self, user_id: UUID, limit: int = 50, offset: int = 0
    ) -> tuple[list[Transfer], int]:
        return await self.transfers_repo.list_transfers(user_id, limit, offset)

    async def get_transfer(self, transfer_id: UUID, user_id: UUID) -> TransferResult:
        t = await self.transfers_repo.get_by_id(transfer_id, user_id)
        return TransferResult.model_validate(t)
