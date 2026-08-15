import calendar
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from app.accounts.exceptions import AccountDuplicateError, AccountForbiddenError
from app.accounts.models import Account
from app.accounts.repository import AccountRepository
from app.accounts.schemas import (
    AccountAvailabilityRead,
    AccountCreate,
    AccountDetailRead,
    AccountPeriodSummary,
    AccountRead,
    AccountSummary,
    AccountUpdate,
    BalanceAdjustmentCreate,
)
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.utils import clean_presentation_name, normalize_name
from app.ledger.enums import Direction, EventType
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import (
    LedgerEventCreate,
    LedgerEventDetail,
    LedgerEventsResponse,
    LedgerPaginationInfo,
)

if TYPE_CHECKING:
    from app.goals.repository import GoalRepository


class AccountService:
    def __init__(
        self,
        repository: AccountRepository,
        ledger_repo: LedgerRepository | None = None,
        goal_repository: "GoalRepository | None" = None,
    ):
        self.repository = repository
        self.ledger_repo = ledger_repo
        self.goal_repository = goal_repository

    @staticmethod
    def _set_available_balance(account: Account, reserved: Decimal) -> Decimal:
        if account.balance is None:
            raise ValidationError(message="La cuenta no tiene un balance válido.")
        if reserved < 0:
            raise ConflictError(message="La reserva acumulada de la cuenta es inconsistente.")

        available_balance = account.balance - reserved
        if available_balance < 0:
            raise ConflictError(
                message="Las reservas de metas exceden el balance bruto de la cuenta."
            )

        account.available_balance = available_balance
        return available_balance

    async def _populate_available_balance(self, account, auth_user_id: UUID) -> None:
        if self.goal_repository:
            reserved = await self.goal_repository.calculate_reserved_by_account(
                account.id, auth_user_id
            )
            self._set_available_balance(account, reserved)

    async def list_accounts(
        self, auth_user_id: UUID, include_archived: bool = False
    ) -> list[AccountRead]:
        accounts = await self.repository.list_by_user(
            auth_user_id, include_archived=include_archived
        )
        if self.goal_repository:
            reserved_by_account = await self.goal_repository.get_reserved_amounts_for_user(
                auth_user_id
            )
            for account in accounts:
                reserved = reserved_by_account.get(account.id, Decimal("0.00"))
                self._set_available_balance(account, reserved)

        return [AccountRead.model_validate(a) for a in accounts]

    async def get_account(self, auth_user_id: UUID, account_id: UUID) -> AccountDetailRead:
        account = await self._get_account_or_404(account_id, include_inactive=True)
        if account.user_id != auth_user_id:
            raise AccountForbiddenError()

        await self._populate_available_balance(account, auth_user_id)

        has_movements = False
        movement_count = 0
        last_movement_at = None

        if self.ledger_repo:
            events, total = await self.ledger_repo.list_events(
                user_id=auth_user_id, account_id=account_id, limit=1
            )
            movement_count = total
            if total > 0:
                has_movements = True
                last_movement_at = events[0].occurred_at

        read_dict = AccountRead.model_validate(account).model_dump()
        return AccountDetailRead(
            **read_dict,
            has_movements=has_movements,
            movement_count=movement_count,
            last_movement_at=last_movement_at,
        )

    async def create_account(self, auth_user_id: UUID, payload: AccountCreate) -> AccountRead:
        import uuid

        norm_name = normalize_name(payload.name)
        if not norm_name:
            raise ValueError("El nombre no puede estar vacío.")

        exists = await self.repository.check_name_exists(auth_user_id, norm_name)
        if exists:
            raise AccountDuplicateError()

        db_account = Account(
            user_id=auth_user_id,
            name=clean_presentation_name(payload.name),
            type=payload.type.value,
            currency=payload.currency,
            balance=payload.initial_balance,
            is_active=True,
        )
        created = await self.repository.create(db_account)

        if payload.initial_balance > 0 and self.ledger_repo:
            event_create = LedgerEventCreate(
                user_id=auth_user_id,
                account_id=created.id,
                category_id=None,
                event_type=EventType.OPENING_BALANCE,
                direction=Direction.INFLOW,
                amount=payload.initial_balance,
                currency=payload.currency,
                description="Saldo inicial",
                source="api",
                command_id=uuid.uuid4(),
                source_message_id=None,
                raw_message=None,
                metadata={},
            )
            await self.ledger_repo.insert_event(event_create)

        await self._populate_available_balance(created, auth_user_id)
        return AccountRead.model_validate(created)

    async def update_account(
        self, auth_user_id: UUID, account_id: UUID, payload: AccountUpdate
    ) -> AccountRead:
        account = await self._get_account_or_404(account_id, include_inactive=True)
        if account.user_id != auth_user_id:
            raise AccountForbiddenError()

        if payload.name is not None:
            norm_name = normalize_name(payload.name)
            if not norm_name:
                raise ValueError("El nombre no puede estar vacío.")
            if normalize_name(account.name) != norm_name:
                exists = await self.repository.check_name_exists(auth_user_id, norm_name)
                if exists:
                    raise AccountDuplicateError()
            account.name = clean_presentation_name(payload.name)

        if payload.type is not None:
            account.type = payload.type.value

        await self.repository.session.flush()
        await self._populate_available_balance(account, auth_user_id)
        return AccountRead.model_validate(account)

    async def archive_account(self, auth_user_id: UUID, account_id: UUID) -> AccountRead:
        account = await self._get_account_or_404(account_id, include_inactive=True)
        if account.user_id != auth_user_id:
            raise AccountForbiddenError()
        account.is_active = False
        await self.repository.session.flush()
        await self._populate_available_balance(account, auth_user_id)
        return AccountRead.model_validate(account)

    async def restore_account(self, auth_user_id: UUID, account_id: UUID) -> AccountRead:
        account = await self._get_account_or_404(account_id, include_inactive=True)
        if account.user_id != auth_user_id:
            raise AccountForbiddenError()
        account.is_active = True
        await self.repository.session.flush()
        await self._populate_available_balance(account, auth_user_id)
        return AccountRead.model_validate(account)

    async def delete_account(self, auth_user_id: UUID, account_id: UUID) -> None:
        raise ValueError(
            "La eliminación de cuentas no está soportada. Por favor archive la cuenta."
        )

    async def create_balance_adjustment(
        self, auth_user_id: UUID, account_id: UUID, payload: "BalanceAdjustmentCreate"
    ) -> AccountRead:
        account = await self.repository.get_by_id_for_update(account_id)
        if not account:
            raise NotFoundError(message="Cuenta no encontrada.")
        if account.user_id != auth_user_id:
            raise AccountForbiddenError()

        diff = payload.target_balance - account.balance
        if diff == 0:
            await self._populate_available_balance(account, auth_user_id)
            return AccountRead.model_validate(account)

        ledger_dir = Direction.INFLOW if diff > 0 else Direction.OUTFLOW
        amount = abs(diff)

        account.balance = payload.target_balance

        if self.ledger_repo:
            event_create = LedgerEventCreate(
                user_id=auth_user_id,
                account_id=account.id,
                category_id=None,
                event_type=EventType.MANUAL_ADJUSTMENT,
                direction=ledger_dir,
                amount=amount,
                currency=account.currency,
                description=payload.reason or "Ajuste de saldo manual",
                source="api",
                command_id=payload.idempotency_key,
                source_message_id=None,
                raw_message=None,
                metadata={},
            )
            await self.ledger_repo.insert_event(event_create)

        await self.repository.session.flush()
        await self._populate_available_balance(account, auth_user_id)
        return AccountRead.model_validate(account)

    async def _get_account_or_404(
        self, account_id: UUID, include_inactive: bool = False
    ) -> Account:
        account = await self.repository.get_by_id(account_id, include_inactive=include_inactive)
        if not account:
            raise NotFoundError(message="Cuenta no encontrada.")
        return account

    async def get_summary(self, auth_user_id: UUID) -> AccountSummary:
        from collections import defaultdict

        accounts = await self.repository.list_by_user_all(auth_user_id)
        totals = defaultdict(Decimal)
        for a in accounts:
            if a.is_active:
                totals[a.currency] += a.balance

        return AccountSummary(
            totals_by_currency=dict(totals),
            accounts_count=len(accounts),
            active_accounts_count=sum(1 for a in accounts if a.is_active),
        )

    async def get_account_period_summary(
        self, auth_user_id: UUID, account_id: UUID, month: str
    ) -> AccountPeriodSummary:
        account = await self._get_account_or_404(account_id, include_inactive=True)
        if account.user_id != auth_user_id:
            raise AccountForbiddenError()

        # Parse month YYYY-MM
        try:
            year, month_num = map(int, month.split("-"))
            _, last_day = calendar.monthrange(year, month_num)
            date_from = datetime(year, month_num, 1)
            date_to = datetime(year, month_num, last_day, 23, 59, 59, 999999)
        except ValueError:
            raise ValueError("Formato de mes inválido. Use YYYY-MM")

        total_inflows = Decimal("0")
        total_outflows = Decimal("0")
        transfer_inflows = Decimal("0")
        transfer_outflows = Decimal("0")
        net_flow = Decimal("0")

        if not self.ledger_repo:
            return AccountPeriodSummary(
                total_inflows=total_inflows,
                total_outflows=total_outflows,
                net_flow=net_flow,
                transfer_inflows=transfer_inflows,
                transfer_outflows=transfer_outflows,
                movement_count=0,
                period_start=date_from,
                period_end=date_to,
                currency=account.currency,
            )

        events, total_count = await self.ledger_repo.list_events(
            user_id=auth_user_id,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            limit=1000000,
        )

        for e in events:
            if e.event_type == EventType.TRANSFER_IN.value:
                transfer_inflows += e.amount
            elif e.event_type == EventType.TRANSFER_OUT.value:
                transfer_outflows += e.amount
            else:
                if e.direction == Direction.INFLOW.value:
                    total_inflows += e.amount
                    net_flow += e.amount
                elif e.direction == Direction.OUTFLOW.value:
                    total_outflows += e.amount
                    net_flow -= e.amount

        return AccountPeriodSummary(
            total_inflows=total_inflows,
            total_outflows=total_outflows,
            net_flow=net_flow,
            transfer_inflows=transfer_inflows,
            transfer_outflows=transfer_outflows,
            movement_count=total_count,
            period_start=date_from,
            period_end=date_to,
            currency=account.currency,
        )

    async def list_account_movements(
        self,
        auth_user_id: UUID,
        account_id: UUID,
        limit: int = 50,
        offset: int = 0,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        event_type: str | None = None,
        direction: str | None = None,
    ) -> LedgerEventsResponse:
        account = await self._get_account_or_404(account_id, include_inactive=True)
        if account.user_id != auth_user_id:
            raise AccountForbiddenError()

        if not self.ledger_repo:
            return LedgerEventsResponse(
                items=[], pagination=LedgerPaginationInfo(limit=limit, offset=offset, total=0)
            )

        events, total = await self.ledger_repo.list_events(
            user_id=auth_user_id,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            event_type=event_type,
            direction=direction,
            limit=limit,
            offset=offset,
        )

        details = [LedgerEventDetail.model_validate(e) for e in events]
        return LedgerEventsResponse(
            items=details, pagination=LedgerPaginationInfo(limit=limit, offset=offset, total=total)
        )

    async def get_account_availability(
        self,
        auth_user_id: UUID,
        account_id: UUID,
    ) -> AccountAvailabilityRead:
        account = await self.repository.get_by_id(account_id, include_inactive=True)
        if not account:
            raise NotFoundError(message="Cuenta no encontrada.")
        if account.user_id != auth_user_id:
            raise NotFoundError(message="Cuenta no encontrada.")
        if not account.is_active:
            raise ValidationError(message="La cuenta no est\u00e1 activa.")
        if account.balance is None:
            raise ValidationError(message="La cuenta no tiene un balance v\u00e1lido.")

        goal_repository = self.goal_repository
        if goal_repository is None:
            # Preserve existing constructor consumers while reusing the same
            # session and transaction boundary.
            from app.goals.repository import GoalRepository

            goal_repository = GoalRepository(self.repository.session)

        reserved_amount = await goal_repository.calculate_reserved_by_account(
            account_id,
            auth_user_id,
        )
        available_balance = self._set_available_balance(account, reserved_amount)

        return AccountAvailabilityRead(
            account_id=account.id,
            currency=account.currency,
            balance=account.balance,
            goal_reserved_amount=reserved_amount,
            available_balance=available_balance,
        )
