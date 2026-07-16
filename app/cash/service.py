from uuid import UUID

from app.accounts.repository import AccountRepository
from app.cash.exceptions import InsufficientFundsError
from app.cash.schemas import CashExpenseCreate, CashIncomeCreate, CashOperationResult
from app.categories.repository import CategoryRepository
from app.core.errors import ForbiddenError, InfrastructureError, NotFoundError, ValidationError
from app.core.uow import UnitOfWork
from app.ledger.enums import Direction, EventType
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate


class CashService:
    def __init__(
        self,
        uow: UnitOfWork,
        ledger_repo: LedgerRepository,
        account_repo: AccountRepository,
        category_repo: CategoryRepository,
    ):
        self.uow = uow
        self.ledger_repo = ledger_repo
        self.account_repo = account_repo
        self.category_repo = category_repo

    async def _resolve_category_id(
        self, category_id: UUID | None, user_id: UUID, fallback_stable_key: str, expected_type: str
    ) -> UUID:
        if category_id:
            category = await self.category_repo.get_by_id(category_id)
            if not category:
                raise NotFoundError(message="Categoría no encontrada.")
            if category.user_id is not None and category.user_id != user_id:
                raise ForbiddenError(message="No tienes permisos sobre esta categoría privada.")
            if not category.is_active:
                raise ValidationError(message="No se puede usar una categoría archivada.")
            if category.type != expected_type:
                raise ValidationError(message=f"La categoría debe ser de tipo {expected_type}.")
            return category_id

        fallback = await self.category_repo.get_by_stable_key(fallback_stable_key)
        if not fallback:
            raise InfrastructureError(
                message=f"Falta categoría global requerida: {fallback_stable_key}"
            )
        if fallback.user_id is not None:
            raise InfrastructureError(message=f"Fallback {fallback_stable_key} no es global.")
        if not fallback.is_active:
            raise InfrastructureError(message=f"Fallback {fallback_stable_key} está inactiva.")
        if fallback.type != expected_type:
            raise InfrastructureError(
                message=f"Fallback {fallback_stable_key} tiene tipo incorrecto."
            )
        return fallback.id

    async def create_income(
        self, user_id: UUID, payload: CashIncomeCreate, command_id: UUID
    ) -> CashOperationResult:

        async with self.uow.transaction():
            # 1. Bloquear cuenta
            account = await self.account_repo.get_by_id_for_update(payload.account_id)
            if not account:
                raise NotFoundError(message="Cuenta no encontrada.")
            if account.user_id != user_id:
                raise ForbiddenError(message="No tienes permisos sobre esta cuenta.")

            category_id = await self._resolve_category_id(
                payload.category_id, user_id, "income_uncategorized", "income"
            )

            # 2. Crear evento en Ledger
            event_create = LedgerEventCreate(
                user_id=user_id,
                account_id=payload.account_id,
                category_id=category_id,
                event_type=EventType.INCOME,
                direction=Direction.INFLOW,
                amount=payload.amount,
                currency=account.currency,
                description=payload.description,
                source=payload.source.value,
                command_id=command_id,
                source_message_id=payload.source_message_id,
                raw_message=payload.raw_message,
                metadata={},
            )

            # Insert lanza exception dentro de un savepoint si es idempotente
            ledger_result = await self.ledger_repo.insert_event(event_create)

            if ledger_result.idempotent:
                return CashOperationResult(
                    event_id=ledger_result.event.id,
                    account_id=payload.account_id,
                    balance_after=account.balance,
                    occurred_at=ledger_result.event.occurred_at,
                    status="idempotent_retry",
                )

            # 3. Actualizar balance
            await self.account_repo.update_balance(account, payload.amount)

            return CashOperationResult(
                event_id=ledger_result.event.id,
                account_id=payload.account_id,
                balance_after=account.balance,
                occurred_at=ledger_result.event.occurred_at,
                status="created",
            )

    async def create_expense(
        self, user_id: UUID, payload: CashExpenseCreate, command_id: UUID
    ) -> CashOperationResult:

        async with self.uow.transaction():
            # 1. Bloquear cuenta
            account = await self.account_repo.get_by_id_for_update(payload.account_id)
            if not account:
                raise NotFoundError(message="Cuenta no encontrada.")
            if account.user_id != user_id:
                raise ForbiddenError(message="No tienes permisos sobre esta cuenta.")

            category_id = await self._resolve_category_id(
                payload.category_id, user_id, "expense_uncategorized", "expense"
            )

            # 2. Crear evento en Ledger
            event_create = LedgerEventCreate(
                user_id=user_id,
                account_id=payload.account_id,
                category_id=category_id,
                event_type=EventType.EXPENSE,
                direction=Direction.OUTFLOW,
                amount=payload.amount,
                currency=account.currency,
                description=payload.description,
                source=payload.source.value,
                command_id=command_id,
                source_message_id=payload.source_message_id,
                raw_message=payload.raw_message,
                metadata={},
            )

            ledger_result = await self.ledger_repo.insert_event(event_create)

            if ledger_result.idempotent:
                return CashOperationResult(
                    event_id=ledger_result.event.id,
                    account_id=payload.account_id,
                    balance_after=account.balance,
                    occurred_at=ledger_result.event.occurred_at,
                    status="idempotent_retry",
                )

            # 3. Validar fondos suficientes
            if account.balance - payload.amount < 0:
                raise InsufficientFundsError()

            # 4. Actualizar balance
            await self.account_repo.update_balance(account, -payload.amount)

            return CashOperationResult(
                event_id=ledger_result.event.id,
                account_id=payload.account_id,
                balance_after=account.balance,
                occurred_at=ledger_result.event.occurred_at,
                status="created",
            )
