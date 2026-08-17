import calendar
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from app.accounts.exceptions import AccountForbiddenError
from app.accounts.repository import AccountRepository
from app.cash.exceptions import InsufficientFundsError
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.uow import UnitOfWork
from app.core.utils import clean_presentation_name, normalize_name
from app.goals.enums import GoalTransactionType
from app.goals.exceptions import (
    GoalAmountExceededError,
    GoalCompletedError,
    GoalDuplicateError,
    GoalForbiddenError,
    GoalNotActiveError,
    GoalTargetAmountError,
)
from app.goals.models import (
    Goal,
    GoalAutoContributionRun,
    GoalAutoContributionSchedule,
    GoalTransaction,
)
from app.goals.repository import GoalRepository
from app.goals.schemas import (
    GoalAccountReservationRead,
    GoalAutoContributionScheduleCreate,
    GoalAutoContributionScheduleRead,
    GoalAutoContributionScheduleUpdate,
    GoalContributionCreate,
    GoalContributionResult,
    GoalCreate,
    GoalDetailRead,
    GoalRead,
    GoalReleaseCreate,
    GoalReleaseResult,
    GoalTransactionRead,
    GoalTransactionsResponse,
    GoalUpdate,
)
from app.ledger.enums import Direction, EventType
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate

AUTO_CONTRIBUTION_EXECUTION_TIME = time(8, 0)
WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
MONTHLY_EXECUTION_DAYS = {str(value) for value in range(1, 32)}

NAMESPACE_AUTOCONTRIB = UUID("1481b2ab-9d8a-4c28-bb73-514dcd245b0d")
logger = get_logger(__name__)


@dataclass(frozen=True)
class AutoContributionExecutionResult:
    run_id: UUID
    status: str
    result_code: str
    configured_amount: Decimal
    executed_amount: Decimal | None
    goal_transaction_id: UUID | None
    idempotent: bool


@dataclass
class AutoContributionProcessorSummary:
    selected: int = 0
    succeeded: int = 0
    skipped: int = 0
    technical_failures: int = 0
    reconciled: int = 0
    missed_occurrences: int = 0
    completed_goals: int = 0
    paused_schedules: int = 0
    cancelled_schedules: int = 0


class _AutoContributionRunRace(Exception):
    """Otra transacción cerró la misma occurrence."""


class GoalService:
    def __init__(
        self,
        repository: GoalRepository,
        account_repo: AccountRepository,
        ledger_repo: LedgerRepository,
    ):
        self.repository = repository
        self.account_repo = account_repo
        self.ledger_repo = ledger_repo

    def _current_period_dates(self) -> tuple[datetime, datetime]:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Bogota")
        now = datetime.now(tz)
        start_date = datetime(now.year, now.month, 1, tzinfo=tz)

        # Calculate start of next month for exclusive boundary
        if now.month == 12:
            end_date = datetime(now.year + 1, 1, 1, tzinfo=tz)
        else:
            end_date = datetime(now.year, now.month + 1, 1, tzinfo=tz)

        return start_date, end_date

    async def list_goals(self, auth_user_id: UUID) -> list[GoalRead]:
        goals = await self.repository.list_active(auth_user_id)
        start_date, end_date = self._current_period_dates()
        contributions = await self.repository.get_period_contributions(
            auth_user_id, start_date, end_date
        )

        results = []
        for g in goals:
            gr = GoalRead.model_validate(g)
            gr.contributed_this_period = contributions.get(g.id, Decimal("0.00"))
            results.append(gr)
        return results

    async def get_goal(self, auth_user_id: UUID, goal_id: UUID) -> GoalDetailRead:
        goal = await self._get_goal_or_404(goal_id)
        if goal.user_id != auth_user_id:
            raise GoalForbiddenError()

        start_date, end_date = self._current_period_dates()
        contributions = await self.repository.get_period_contributions(
            auth_user_id, start_date, end_date
        )
        reservations_raw = await self.repository.get_reservations_by_account(auth_user_id, goal_id)
        if any(
            amount < 0
            for reservation in reservations_raw
            for amount in (
                reservation.contributed_amount,
                reservation.released_amount,
                reservation.reserved_amount,
                reservation.applied_contributed_amount,
                reservation.applied_released_amount,
                reservation.applied_reserved_amount,
            )
        ):
            raise ConflictError(message="El desglose de reservas por cuenta es inconsistente.")

        reservations_total = sum(
            (reservation.applied_reserved_amount for reservation in reservations_raw),
            Decimal("0.00"),
        )
        if reservations_total != goal.current_amount:
            raise ConflictError(
                message="El total reservado por cuenta no coincide con el progreso de la meta."
            )

        gr = GoalDetailRead.model_validate(goal)
        gr.contributed_this_period = contributions.get(goal.id, Decimal("0.00"))

        mapped_reservations = []
        for r in reservations_raw:
            is_releasable = False
            release_block_reason = None

            if r.account_currency.upper() != r.goal_currency.upper():
                release_block_reason = "currency_mismatch_legacy"
            elif not r.account_is_active:
                release_block_reason = "account_inactive"
            else:
                is_releasable = True

            read_model = GoalAccountReservationRead(
                account_id=r.account_id,
                account_name=r.account_name,
                account_currency=r.account_currency,
                contributed_amount=r.contributed_amount,
                released_amount=r.released_amount,
                reserved_amount=r.reserved_amount,
                goal_currency=r.goal_currency,
                applied_contributed_amount=r.applied_contributed_amount,
                applied_released_amount=r.applied_released_amount,
                applied_reserved_amount=r.applied_reserved_amount,
                account_is_active=r.account_is_active,
                is_releasable=is_releasable,
                release_block_reason=release_block_reason,
            )
            mapped_reservations.append(read_model)

        gr.reservations_by_account = mapped_reservations
        return gr

    async def create_goal(self, auth_user_id: UUID, payload: GoalCreate) -> GoalRead:
        norm_name = normalize_name(payload.name)
        if not norm_name:
            raise ValueError("El nombre no puede estar vacío.")

        exists = await self.repository.check_name_exists(auth_user_id, norm_name)
        if exists:
            raise GoalDuplicateError()

        db_goal = Goal(
            user_id=auth_user_id,
            name=clean_presentation_name(payload.name),
            target_amount=payload.target_amount,
            currency=payload.currency,
            target_date=payload.target_date,
            is_active=True,
            status="active",
        )
        created = await self.repository.create(db_goal)
        gr = GoalRead.model_validate(created)
        gr.contributed_this_period = Decimal("0.00")
        return gr

    async def update_goal(self, auth_user_id: UUID, goal_id: UUID, payload: GoalUpdate) -> GoalRead:
        goal = await self._get_goal_or_404(goal_id)
        if goal.user_id != auth_user_id:
            raise GoalForbiddenError()
        if not goal.is_active:
            raise GoalNotActiveError()
        if goal.status == "completed":
            raise GoalCompletedError()

        if payload.name is not None:
            norm_name = normalize_name(payload.name)
            if not norm_name:
                raise ValueError("El nombre no puede estar vacío.")
            if normalize_name(goal.name) != norm_name:
                exists = await self.repository.check_name_exists(auth_user_id, norm_name)
                if exists:
                    raise GoalDuplicateError()
            goal.name = clean_presentation_name(payload.name)

        if payload.target_amount is not None:
            if payload.target_amount < goal.current_amount:
                raise GoalTargetAmountError()
            goal.target_amount = payload.target_amount
            if goal.current_amount >= goal.target_amount:
                goal.status = "completed"

        if payload.target_date is not None:
            goal.target_date = payload.target_date

        await self.repository.session.flush()

        start_date, end_date = self._current_period_dates()
        contributions = await self.repository.get_period_contributions(
            auth_user_id, start_date, end_date
        )
        gr = GoalRead.model_validate(goal)
        gr.contributed_this_period = contributions.get(goal.id, Decimal("0.00"))
        return gr

    async def delete_goal(self, auth_user_id: UUID, goal_id: UUID) -> None:
        goal = await self._get_goal_or_404(goal_id)
        if goal.user_id != auth_user_id:
            raise GoalForbiddenError()

        progress = await self.repository.calculate_progress_by_goal(
            goal_id,
            auth_user_id,
        )
        if progress > 0:
            raise ConflictError(message="No se puede archivar una meta con fondos reservados.")

        goal.is_active = False
        goal.status = "cancelled"
        await self.repository.session.flush()

    @staticmethod
    def _canonical_decimal(value: Decimal) -> str:
        return format(value.normalize(), "f")

    def _generate_fingerprint(
        self,
        user_id: UUID,
        goal_id: UUID,
        account_id: UUID,
        transaction_type: str,
        source_amount: Decimal,
        source_currency: str,
        applied_amount: Decimal,
        goal_currency: str,
        *,
        description: str | None = None,
        include_description: bool = False,
    ) -> str:
        payload = {
            "account_id": str(account_id),
            "applied_amount": self._canonical_decimal(applied_amount),
            "goal_currency": goal_currency.upper(),
            "goal_id": str(goal_id),
            "source_amount": self._canonical_decimal(source_amount),
            "source_currency": source_currency.upper(),
            "transaction_type": transaction_type,
            "user_id": str(user_id),
        }
        if include_description:
            payload["description"] = description
        canon_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon_json.encode("utf-8")).hexdigest()

    @staticmethod
    def _resolve_command_id(
        payload_command_id: UUID | None,
        idempotency_key: str | None,
    ) -> UUID | None:
        header_command_id: UUID | None = None
        if idempotency_key is not None:
            try:
                header_command_id = UUID(idempotency_key)
            except (ValueError, AttributeError) as exc:
                raise ValidationError(
                    message="El Idempotency-Key debe ser un UUID válido."
                ) from exc

        if (
            payload_command_id is not None
            and header_command_id is not None
            and payload_command_id != header_command_id
        ):
            raise ValidationError(
                message=("El command_id del payload no coincide con el Idempotency-Key header.")
            )

        return payload_command_id or header_command_id

    @staticmethod
    def _is_command_unique_violation(exc: IntegrityError) -> bool:
        current: object | None = exc.orig
        visited: set[int] = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            constraint_name = getattr(current, "constraint_name", None)
            diag = getattr(current, "diag", None)
            if (
                constraint_name == "uq_gtx_command_id"
                or getattr(diag, "constraint_name", None) == "uq_gtx_command_id"
            ):
                return True
            current = getattr(current, "__cause__", None) or getattr(
                current,
                "__context__",
                None,
            )
        return "uq_gtx_command_id" in str(exc.orig)

    async def _replay_contribution(
        self,
        *,
        auth_user_id: UUID,
        goal_id: UUID,
        payload: GoalContributionCreate,
        command_id: UUID,
        transaction: GoalTransaction,
        channel: Literal["manual", "automatic"],
    ) -> GoalContributionResult:
        if (
            transaction.user_id != auth_user_id
            or transaction.goal_id != goal_id
            or transaction.account_id != payload.account_id
        ):
            raise ConflictError(message="El Idempotency-Key ya fue utilizado por otra operación.")

        if (
            payload.currency is not None
            and payload.currency.upper() != transaction.source_currency.upper()
        ):
            raise ConflictError(
                message="Conflicto de idempotencia: el payload difiere del original."
            )

        fingerprint = self._generate_fingerprint(
            auth_user_id,
            goal_id,
            payload.account_id,
            GoalTransactionType.allocation.value,
            payload.amount,
            transaction.source_currency,
            payload.amount,
            transaction.goal_currency,
        )
        if transaction.command_fingerprint != fingerprint:
            raise ConflictError(
                message="Conflicto de idempotencia: el payload difiere del original."
            )

        event = await self.ledger_repo.get_by_command_id(command_id)
        event_metadata = (event.metadata_ or {}) if event is not None else {}
        if (
            event is None
            or event.id != transaction.event_id
            or event.user_id != auth_user_id
            or event.account_id != transaction.account_id
            or event.event_type != EventType.GOAL_CONTRIBUTION.value
            or event.direction != Direction.NEUTRAL.value
            or event.amount != transaction.source_amount
            or event.currency.upper() != transaction.source_currency.upper()
            or event_metadata.get("goal_id") != str(goal_id)
        ):
            raise ConflictError(
                message="El registro idempotente no coincide con su evento financiero."
            )

        snapshot = transaction.metadata_json or {}
        required_snapshot_fields = {
            "goal_current_amount",
            "goal_remaining_amount",
            "goal_status",
            "account_balance",
            "goal_reserved_amount",
            "available_balance",
            "progress_percentage",
        }
        if not required_snapshot_fields.issubset(snapshot):
            raise ConflictError(
                message="El registro idempotente no contiene una respuesta histórica completa."
            )
        persisted_channel = snapshot.get("channel", "manual")
        if persisted_channel != channel:
            raise ConflictError(
                message="Conflicto de idempotencia: el canal difiere de la operación original."
            )

        return GoalContributionResult(
            transaction_id=transaction.id,
            event_id=transaction.event_id,
            goal_id=transaction.goal_id,
            account_id=transaction.account_id,
            source_amount=transaction.source_amount,
            source_currency=transaction.source_currency,
            applied_amount=transaction.applied_amount,
            goal_currency=transaction.goal_currency,
            goal_current_amount=Decimal(snapshot["goal_current_amount"]),
            goal_remaining_amount=Decimal(snapshot["goal_remaining_amount"]),
            goal_status=snapshot["goal_status"],
            account_balance=Decimal(snapshot["account_balance"]),
            goal_reserved_amount=Decimal(snapshot["goal_reserved_amount"]),
            available_balance=Decimal(snapshot["available_balance"]),
            progress_percentage=Decimal(snapshot["progress_percentage"]),
            idempotent=True,
            created_at=transaction.created_at,
        )

    async def _find_idempotent_contribution(
        self,
        *,
        auth_user_id: UUID,
        goal_id: UUID,
        payload: GoalContributionCreate,
        command_id: UUID | None,
        channel: Literal["manual", "automatic"],
    ) -> GoalContributionResult | None:
        if command_id is None:
            return None
        transaction = await self.repository.get_transaction_by_command_id(command_id)
        if transaction is None:
            return None
        return await self._replay_contribution(
            auth_user_id=auth_user_id,
            goal_id=goal_id,
            payload=payload,
            command_id=command_id,
            transaction=transaction,
            channel=channel,
        )

    async def create_contribution(
        self,
        auth_user_id: UUID,
        goal_id: UUID,
        payload: GoalContributionCreate,
        idempotency_key: str | None,
        *,
        channel: Literal["manual", "automatic"] = "manual",
    ) -> GoalContributionResult:
        if channel not in {"manual", "automatic"}:
            raise ValueError("El canal interno del aporte no es v\u00e1lido.")

        command_id = self._resolve_command_id(
            payload.command_id,
            idempotency_key,
        )

        replay = await self._find_idempotent_contribution(
            auth_user_id=auth_user_id,
            goal_id=goal_id,
            payload=payload,
            command_id=command_id,
            channel=channel,
        )
        if replay is not None:
            return replay

        account = await self.account_repo.get_by_id_for_update(
            payload.account_id,
            include_inactive=True,
        )
        if not account:
            raise NotFoundError(message="Cuenta no encontrada.")

        goal = await self.repository.get_by_id_for_update(goal_id)
        if not goal:
            raise NotFoundError(message="Meta no encontrada.")

        # A concurrent winner may have committed while either lock was awaited.
        replay = await self._find_idempotent_contribution(
            auth_user_id=auth_user_id,
            goal_id=goal_id,
            payload=payload,
            command_id=command_id,
            channel=channel,
        )
        if replay is not None:
            return replay

        if account.user_id != auth_user_id:
            raise AccountForbiddenError()
        if goal.user_id != auth_user_id:
            raise GoalForbiddenError()

        if not account.is_active:
            raise ForbiddenError(message="Cuenta inactiva.")
        if not goal.is_active:
            raise GoalNotActiveError()
        if goal.status == "completed":
            raise GoalCompletedError()
        if goal.status != "active":
            raise GoalNotActiveError()

        source_currency = account.currency.upper()
        goal_currency = goal.currency.upper()
        if source_currency != goal_currency:
            raise ValidationError(
                message="La moneda de la cuenta no coincide con la moneda de la meta."
            )
        if payload.currency is not None and payload.currency.upper() != source_currency:
            raise ValidationError(message="La moneda legacy del payload no coincide con la cuenta.")

        source_amount = payload.amount
        applied_amount = source_amount

        remaining = goal.target_amount - goal.current_amount
        if applied_amount > remaining:
            raise GoalAmountExceededError()

        if account.balance is None:
            raise ForbiddenError(message="La cuenta no tiene saldo configurado.")
        goal_reserved = await self.repository.calculate_reserved_by_account(
            account.id,
            auth_user_id,
        )
        if goal_reserved < 0:
            raise ConflictError(message="La reserva acumulada de la cuenta es inconsistente.")
        available_balance = account.balance - goal_reserved
        if available_balance < 0:
            raise InsufficientFundsError()
        if source_amount > available_balance:
            raise InsufficientFundsError()

        new_current_amount = goal.current_amount + applied_amount
        new_remaining = goal.target_amount - new_current_amount
        new_status = "completed" if new_remaining == 0 else "active"
        new_reserved = goal_reserved + source_amount
        new_available = account.balance - new_reserved
        progress = min(
            round(
                (new_current_amount / goal.target_amount) * Decimal("100"),
                2,
            ),
            Decimal("100"),
        )
        response_snapshot = {
            "goal_current_amount": str(new_current_amount),
            "goal_remaining_amount": str(new_remaining),
            "goal_status": new_status,
            "account_balance": str(account.balance),
            "goal_reserved_amount": str(new_reserved),
            "available_balance": str(new_available),
            "progress_percentage": str(progress),
            "channel": channel,
        }

        event_create = LedgerEventCreate(
            user_id=auth_user_id,
            account_id=account.id,
            event_type=EventType.GOAL_CONTRIBUTION,
            direction=Direction.NEUTRAL,
            amount=source_amount,
            currency=source_currency,
            description=payload.description,
            source_message_id=payload.source_message_id,
            raw_message=payload.raw_message,
            metadata={
                "goal_id": str(goal.id),
                "transaction_type": GoalTransactionType.allocation.value,
            },
            command_id=command_id,
        )
        fingerprint = self._generate_fingerprint(
            auth_user_id,
            goal_id,
            payload.account_id,
            GoalTransactionType.allocation.value,
            source_amount,
            source_currency,
            applied_amount,
            goal_currency,
        )

        try:
            async with self.repository.session.begin_nested():
                result = await self.ledger_repo.insert_event(event_create)
                if result.idempotent:
                    replay = await self._find_idempotent_contribution(
                        auth_user_id=auth_user_id,
                        goal_id=goal_id,
                        payload=payload,
                        command_id=command_id,
                        channel=channel,
                    )
                    if replay is not None:
                        return replay
                    raise ConflictError(
                        message=("El Idempotency-Key ya pertenece a otra operación financiera.")
                    )

                event = result.event
                transaction = GoalTransaction(
                    user_id=auth_user_id,
                    goal_id=goal.id,
                    account_id=account.id,
                    event_id=event.id,
                    transaction_type=GoalTransactionType.allocation,
                    source_amount=source_amount,
                    source_currency=source_currency,
                    applied_amount=applied_amount,
                    goal_currency=goal_currency,
                    command_id=command_id,
                    command_fingerprint=fingerprint if command_id else None,
                    metadata_json=response_snapshot,
                )
                await self.repository.create_transaction(transaction)

                goal.current_amount = new_current_amount
                goal.status = new_status
                await self.repository.session.flush()

        except IntegrityError as e:
            if command_id is None or not self._is_command_unique_violation(e):
                raise
            replay = await self._find_idempotent_contribution(
                auth_user_id=auth_user_id,
                goal_id=goal_id,
                payload=payload,
                command_id=command_id,
                channel=channel,
            )
            if replay is None:
                raise
            return replay

        return GoalContributionResult(
            transaction_id=transaction.id,
            event_id=event.id,
            goal_id=goal.id,
            account_id=account.id,
            source_amount=source_amount,
            source_currency=source_currency,
            applied_amount=applied_amount,
            goal_currency=goal_currency,
            goal_current_amount=goal.current_amount,
            goal_remaining_amount=new_remaining,
            goal_status=goal.status,
            account_balance=account.balance,
            goal_reserved_amount=new_reserved,
            available_balance=new_available,
            progress_percentage=progress,
            idempotent=False,
            created_at=transaction.created_at,
        )

    @staticmethod
    def _resolve_history_channel(
        transaction_type: GoalTransactionType,
        legacy_contribution_id: UUID | None,
        metadata_json: object,
    ) -> Literal["manual", "automatic", "legacy"]:
        if (
            legacy_contribution_id is not None
            or transaction_type == GoalTransactionType.legacy_import
        ):
            return "legacy"
        if transaction_type != GoalTransactionType.allocation:
            return "manual"
        if isinstance(metadata_json, dict):
            channel = metadata_json.get("channel")
            if channel in {"manual", "automatic"}:
                return channel
        return "manual"

    async def list_goal_transactions(
        self, auth_user_id: UUID, goal_id: UUID, limit: int = 50, offset: int = 0
    ) -> "GoalTransactionsResponse":
        goal = await self._get_goal_or_404(goal_id)
        if goal.user_id != auth_user_id:
            raise GoalForbiddenError()

        rows, total = await self.repository.list_transactions_by_goal(
            goal_id, auth_user_id, limit, offset
        )

        read_items = []
        for transaction, event_description in rows:
            transaction_type = GoalTransactionType(transaction.transaction_type)
            t = transaction
            legacy_contribution_id = getattr(t, "legacy_contribution_id", None)
            is_legacy = legacy_contribution_id is not None
            origin = "legacy" if is_legacy else "native"
            channel = self._resolve_history_channel(
                transaction_type,
                legacy_contribution_id,
                t.metadata_json,
            )

            description = None
            if origin == "native" and isinstance(event_description, str):
                sanitized = " ".join(event_description.split())
                description = sanitized[:255] or None
            read_items.append(
                GoalTransactionRead(
                    id=t.id,
                    transaction_type=transaction_type,
                    account_id=t.account_id,
                    source_amount=t.source_amount,
                    source_currency=t.source_currency,
                    applied_amount=t.applied_amount,
                    goal_currency=t.goal_currency,
                    event_id=t.event_id,
                    description=description,
                    created_at=t.created_at,
                    origin=origin,
                    channel=channel,
                )
            )

        return GoalTransactionsResponse(
            items=read_items,
            total=total,
            limit=limit,
            offset=offset,
        )

    async def _get_goal_or_404(self, goal_id: UUID) -> Goal:
        goal = await self.repository.get_by_id(goal_id)
        if not goal:
            raise NotFoundError(message="Meta no encontrada.")
        return goal

    async def _replay_release(
        self,
        *,
        auth_user_id: UUID,
        goal_id: UUID,
        payload: GoalReleaseCreate,
        command_id: UUID,
        transaction: GoalTransaction,
    ) -> GoalReleaseResult:
        if (
            transaction.user_id != auth_user_id
            or transaction.goal_id != goal_id
            or transaction.account_id != payload.account_id
            or transaction.command_id != command_id
            or transaction.transaction_type != GoalTransactionType.release.value
            or transaction.source_amount != payload.amount
            or transaction.applied_amount != payload.amount
            or transaction.source_currency.upper() != transaction.goal_currency.upper()
        ):
            raise ConflictError(message="El Idempotency-Key ya fue utilizado por otra operación.")

        fingerprint = self._generate_fingerprint(
            auth_user_id,
            goal_id,
            payload.account_id,
            GoalTransactionType.release.value,
            payload.amount,
            transaction.source_currency,
            payload.amount,
            transaction.goal_currency,
            description=payload.description,
            include_description=True,
        )
        if transaction.command_fingerprint != fingerprint:
            raise ConflictError(
                message="Conflicto de idempotencia: el payload difiere del original."
            )

        event = await self.ledger_repo.get_by_command_id(command_id)
        event_metadata = (event.metadata_ or {}) if event is not None else {}
        if (
            event is None
            or event.id != transaction.event_id
            or event.user_id != auth_user_id
            or event.account_id != transaction.account_id
            or event.event_type != EventType.GOAL_RELEASE.value
            or event.direction != Direction.NEUTRAL.value
            or event.amount != transaction.source_amount
            or event.currency.upper() != transaction.source_currency.upper()
            or event_metadata.get("goal_id") != str(goal_id)
        ):
            raise ConflictError(
                message="El registro idempotente no coincide con su evento financiero."
            )

        snapshot = transaction.metadata_json or {}
        required_snapshot_fields = {
            "goal_current_amount",
            "goal_remaining_amount",
            "goal_status",
            "account_balance",
            "goal_account_reserved_amount",
            "goal_total_reserved_amount",
            "account_total_reserved_amount",
            "available_balance",
        }
        # Older snapshots might not have all the new detailed reserves, but releases are completely new.
        if not required_snapshot_fields.issubset(snapshot):
            raise ConflictError(
                message="El registro idempotente no contiene una respuesta histórica completa."
            )

        return GoalReleaseResult(
            transaction_id=transaction.id,
            event_id=transaction.event_id,
            goal_id=transaction.goal_id,
            account_id=transaction.account_id,
            released_amount=transaction.source_amount,
            source_currency=transaction.source_currency,
            applied_amount=transaction.applied_amount,
            goal_currency=transaction.goal_currency,
            goal_current_amount=Decimal(snapshot["goal_current_amount"]),
            goal_remaining_amount=Decimal(snapshot.get("goal_remaining_amount", "0")),
            goal_status=snapshot["goal_status"],
            account_balance=Decimal(snapshot["account_balance"]),
            goal_account_reserved_amount=Decimal(
                snapshot.get(
                    "goal_account_reserved_amount", snapshot.get("goal_reserved_amount", "0")
                )
            ),
            goal_total_reserved_amount=Decimal(snapshot.get("goal_total_reserved_amount", "0")),
            account_total_reserved_amount=Decimal(
                snapshot.get("account_total_reserved_amount", "0")
            ),
            available_balance=Decimal(snapshot["available_balance"]),
            idempotent=True,
            created_at=transaction.created_at,
        )

    async def _find_idempotent_release(
        self,
        *,
        auth_user_id: UUID,
        goal_id: UUID,
        payload: GoalReleaseCreate,
        command_id: UUID | None,
    ) -> GoalReleaseResult | None:
        if command_id is None:
            return None
        transaction = await self.repository.get_transaction_by_command_id(command_id)
        if transaction is None:
            return None
        return await self._replay_release(
            auth_user_id=auth_user_id,
            goal_id=goal_id,
            payload=payload,
            command_id=command_id,
            transaction=transaction,
        )

    async def create_release(
        self,
        auth_user_id: UUID,
        goal_id: UUID,
        payload: GoalReleaseCreate,
        idempotency_key: str | None,
    ) -> GoalReleaseResult:
        command_id = self._resolve_command_id(
            payload.command_id,
            idempotency_key,
        )

        replay = await self._find_idempotent_release(
            auth_user_id=auth_user_id,
            goal_id=goal_id,
            payload=payload,
            command_id=command_id,
        )
        if replay is not None:
            return replay

        account = await self.account_repo.get_by_id_for_update(
            payload.account_id,
            include_inactive=True,
        )
        if not account:
            raise NotFoundError(message="Cuenta no encontrada.")

        goal = await self.repository.get_by_id_for_update(goal_id)
        if not goal:
            raise NotFoundError(message="Meta no encontrada.")

        replay = await self._find_idempotent_release(
            auth_user_id=auth_user_id,
            goal_id=goal_id,
            payload=payload,
            command_id=command_id,
        )
        if replay is not None:
            return replay

        if account.user_id != auth_user_id:
            raise NotFoundError(message="Cuenta no encontrada.")
        if goal.user_id != auth_user_id:
            raise NotFoundError(message="Meta no encontrada.")

        if not account.is_active:
            raise ForbiddenError(message="Cuenta inactiva.")
        if not goal.is_active:
            raise GoalNotActiveError()
        if goal.status not in ("active", "completed"):
            raise GoalNotActiveError()

        source_currency = account.currency.upper()
        goal_currency = goal.currency.upper()
        if source_currency != goal_currency:
            raise ValidationError(
                message="La moneda de la cuenta no coincide con la moneda de la meta."
            )

        if account.balance is None:
            raise ForbiddenError(message="La cuenta no tiene saldo configurado.")

        source_amount = payload.amount
        applied_amount = source_amount

        if goal.current_amount < 0 or goal.current_amount > goal.target_amount:
            raise ConflictError(message="El progreso actual de la meta es inconsistente.")
        if (goal.status == "completed" and goal.current_amount != goal.target_amount) or (
            goal.status == "active" and goal.current_amount == goal.target_amount
        ):
            raise ConflictError(message="El estado y el progreso de la meta son inconsistentes.")

        reserved_goal_account_before = await self.repository.calculate_reserved_by_goal_and_account(
            auth_user_id,
            goal.id,
            account.id,
        )
        account_total_reserved_before = await self.repository.calculate_reserved_by_account(
            account.id,
            auth_user_id,
        )
        goal_total_reserved_before = await self.repository.calculate_total_reserved_by_goal(
            auth_user_id,
            goal.id,
        )
        goal_progress_before = await self.repository.calculate_progress_by_goal(
            goal.id,
            auth_user_id,
        )

        if (
            reserved_goal_account_before < 0
            or account_total_reserved_before < 0
            or goal_total_reserved_before < 0
            or goal_progress_before < 0
        ):
            raise ConflictError(message="Las reservas de la meta presentan valores negativos.")
        if (
            goal_progress_before != goal.current_amount
            or goal_total_reserved_before != goal_progress_before
        ):
            raise ConflictError(message="El progreso y las reservas de la meta son inconsistentes.")
        if (
            reserved_goal_account_before > account_total_reserved_before
            or reserved_goal_account_before > goal_total_reserved_before
        ):
            raise ConflictError(
                message="La reserva específica de la meta y la cuenta es inconsistente."
            )
        if account.balance - account_total_reserved_before < 0:
            raise ConflictError(message="Las reservas de la cuenta exceden su saldo bruto.")
        if reserved_goal_account_before <= 0:
            raise ConflictError(
                message="La reserva de esta meta en la cuenta es insuficiente o cero."
            )

        if source_amount > reserved_goal_account_before:
            raise ConflictError(
                message="El monto a liberar excede la reserva de la meta en esta cuenta."
            )

        if source_amount > goal.current_amount:
            raise GoalAmountExceededError()

        new_current_amount = goal.current_amount - applied_amount
        if new_current_amount < 0:
            raise ConflictError(message="El progreso de la meta no puede ser negativo.")

        new_remaining = goal.target_amount - new_current_amount
        new_status = "active" if new_remaining > 0 else "completed"
        expected_goal_account_reserved = reserved_goal_account_before - source_amount
        expected_goal_total_reserved = goal_total_reserved_before - source_amount
        expected_account_total_reserved = account_total_reserved_before - source_amount

        event_create = LedgerEventCreate(
            user_id=auth_user_id,
            account_id=account.id,
            event_type=EventType.GOAL_RELEASE,
            direction=Direction.NEUTRAL,
            amount=source_amount,
            currency=source_currency,
            description=payload.description,
            metadata={
                "goal_id": str(goal.id),
                "transaction_type": GoalTransactionType.release.value,
            },
            command_id=command_id,
        )
        fingerprint = self._generate_fingerprint(
            auth_user_id,
            goal_id,
            payload.account_id,
            GoalTransactionType.release.value,
            source_amount,
            source_currency,
            applied_amount,
            goal_currency,
            description=payload.description,
            include_description=True,
        )

        try:
            async with self.repository.session.begin_nested():
                result = await self.ledger_repo.insert_event(event_create)
                if result.idempotent:
                    replay = await self._find_idempotent_release(
                        auth_user_id=auth_user_id,
                        goal_id=goal_id,
                        payload=payload,
                        command_id=command_id,
                    )
                    if replay is not None:
                        return replay
                    raise ConflictError(
                        message=("El Idempotency-Key ya pertenece a otra operación financiera.")
                    )

                event = result.event
                transaction = GoalTransaction(
                    user_id=auth_user_id,
                    goal_id=goal.id,
                    account_id=account.id,
                    event_id=event.id,
                    transaction_type=GoalTransactionType.release,
                    source_amount=source_amount,
                    source_currency=source_currency,
                    applied_amount=applied_amount,
                    goal_currency=goal_currency,
                    command_id=command_id,
                    command_fingerprint=fingerprint if command_id else None,
                    metadata_json={},
                )
                await self.repository.create_transaction(transaction)

                goal.current_amount = new_current_amount
                goal.status = new_status
                await self.repository.session.flush()

                goal_account_reserved = (
                    await self.repository.calculate_reserved_by_goal_and_account(
                        auth_user_id,
                        goal.id,
                        account.id,
                    )
                )
                goal_total_reserved = await self.repository.calculate_total_reserved_by_goal(
                    auth_user_id,
                    goal.id,
                )
                account_total_reserved = await self.repository.calculate_reserved_by_account(
                    account.id,
                    auth_user_id,
                )
                goal_progress = await self.repository.calculate_progress_by_goal(
                    goal.id,
                    auth_user_id,
                )
                if (
                    goal_account_reserved != expected_goal_account_reserved
                    or goal_total_reserved != expected_goal_total_reserved
                    or account_total_reserved != expected_account_total_reserved
                    or goal_progress != new_current_amount
                    or goal_total_reserved != goal_progress
                ):
                    raise ConflictError(
                        message="El estado persistido del release es inconsistente."
                    )

                available_balance = account.balance - account_total_reserved
                response_snapshot = {
                    "goal_current_amount": str(new_current_amount),
                    "goal_remaining_amount": str(new_remaining),
                    "goal_status": new_status,
                    "account_balance": str(account.balance),
                    "goal_account_reserved_amount": str(goal_account_reserved),
                    "goal_total_reserved_amount": str(goal_total_reserved),
                    "account_total_reserved_amount": str(account_total_reserved),
                    "available_balance": str(available_balance),
                }
                transaction.metadata_json = response_snapshot
                await self.repository.session.flush()

        except IntegrityError as e:
            if command_id is None or not self._is_command_unique_violation(e):
                raise
            replay = await self._find_idempotent_release(
                auth_user_id=auth_user_id,
                goal_id=goal_id,
                payload=payload,
                command_id=command_id,
            )
            if replay is None:
                raise
            return replay

        return GoalReleaseResult(
            transaction_id=transaction.id,
            event_id=event.id,
            goal_id=goal.id,
            account_id=account.id,
            released_amount=source_amount,
            source_currency=source_currency,
            applied_amount=applied_amount,
            goal_currency=goal_currency,
            goal_current_amount=goal.current_amount,
            goal_remaining_amount=new_remaining,
            goal_status=goal.status,
            account_balance=account.balance,
            goal_account_reserved_amount=goal_account_reserved,
            goal_total_reserved_amount=goal_total_reserved,
            account_total_reserved_amount=account_total_reserved,
            available_balance=available_balance,
            idempotent=False,
            created_at=transaction.created_at,
        )

    @staticmethod
    def _calculate_next_occurrence(
        frequency: str,
        execution_day: str,
        timezone_str: str,
        start_date_val: date | None,
        now: datetime,
    ) -> datetime:
        if now.utcoffset() is None:
            raise ValueError("now debe incluir zona horaria")

        tz = ZoneInfo(timezone_str)
        now_utc = now.astimezone(UTC)
        now_local = now.astimezone(tz)
        effective_start_date = start_date_val if start_date_val is not None else now_local.date()
        start_checking_date = max(effective_start_date, now_local.date())

        if frequency == "weekly":
            try:
                target_weekday = WEEKDAY_INDEX[execution_day.lower()]
            except KeyError as exc:
                raise ValueError("execution_day inválido para frecuencia weekly") from exc
            current_weekday = start_checking_date.weekday()
            days_ahead = target_weekday - current_weekday
            if days_ahead < 0:
                days_ahead += 7
            candidate_date = start_checking_date + timedelta(days=days_ahead)
            candidate = datetime.combine(
                candidate_date,
                AUTO_CONTRIBUTION_EXECUTION_TIME,
                tzinfo=tz,
            ).astimezone(UTC)
            if candidate <= now_utc:
                candidate_date += timedelta(days=7)
                candidate = datetime.combine(
                    candidate_date,
                    AUTO_CONTRIBUTION_EXECUTION_TIME,
                    tzinfo=tz,
                ).astimezone(UTC)
            return candidate

        if frequency == "monthly":
            if execution_day not in MONTHLY_EXECUTION_DAYS:
                raise ValueError("execution_day inválido para frecuencia monthly")
            target_day = int(execution_day)
            year = start_checking_date.year
            month = start_checking_date.month
            for _ in range(12):
                last_day_of_month = calendar.monthrange(year, month)[1]
                actual_day = min(target_day, last_day_of_month)
                if (
                    start_checking_date.year == year
                    and start_checking_date.month == month
                    and start_checking_date.day > actual_day
                ):
                    if month == 12:
                        year += 1
                        month = 1
                    else:
                        month += 1
                    continue
                candidate_date = date(year, month, actual_day)
                candidate = datetime.combine(
                    candidate_date,
                    AUTO_CONTRIBUTION_EXECUTION_TIME,
                    tzinfo=tz,
                ).astimezone(UTC)
                if candidate > now_utc:
                    return candidate
                if month == 12:
                    year += 1
                    month = 1
                else:
                    month += 1
            raise ValueError("No se pudo calcular la próxima fecha")

        raise ValueError("Frecuencia de schedule inválida")

    async def _get_owned_goal(self, auth_user_id: UUID, goal_id: UUID) -> Goal:
        goal = await self._get_goal_or_404(goal_id)
        if goal.user_id != auth_user_id:
            raise GoalForbiddenError()
        return goal

    @staticmethod
    def _ensure_goal_operational(goal: Goal) -> None:
        if not goal.is_active or goal.status != "active":
            raise GoalNotActiveError()

    async def _validate_schedule_account(
        self,
        auth_user_id: UUID,
        goal: Goal,
        account_id: UUID,
    ):
        account = await self.account_repo.get_by_id(account_id, include_inactive=True)
        if not account:
            raise NotFoundError(message="Cuenta no encontrada.")
        if account.user_id != auth_user_id or account.user_id != goal.user_id:
            raise AccountForbiddenError()
        if not account.is_active:
            raise ForbiddenError(message="Cuenta inactiva.")
        if account.currency.upper() != goal.currency.upper():
            raise ValidationError(
                message="La moneda de la cuenta no coincide con la moneda de la meta."
            )
        return account

    async def _validate_schedule_dependencies(
        self, auth_user_id: UUID, goal_id: UUID, account_id: UUID
    ):
        goal = await self._get_owned_goal(auth_user_id, goal_id)
        account = await self._validate_schedule_account(auth_user_id, goal, account_id)
        return goal, account

    async def get_auto_contribution_schedule(
        self,
        auth_user_id: UUID,
        goal_id: UUID,
    ) -> GoalAutoContributionScheduleRead:
        await self._get_owned_goal(auth_user_id, goal_id)
        schedule = await self.repository.get_current_auto_contribution_schedule(
            auth_user_id, goal_id
        )
        if not schedule:
            raise NotFoundError(message="Schedule no encontrado.")
        return GoalAutoContributionScheduleRead.model_validate(schedule)

    @staticmethod
    def _is_auto_schedule_unique_violation(exc: IntegrityError) -> bool:
        current: object | None = exc.orig
        visited: set[int] = set()
        expected = "ix_goal_auto_contrib_goal_id_active"
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            constraint_name = getattr(current, "constraint_name", None)
            diag = getattr(current, "diag", None)
            if constraint_name == expected or getattr(diag, "constraint_name", None) == expected:
                return True
            current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        return expected in str(exc.orig)

    async def put_auto_contribution_schedule(
        self,
        auth_user_id: UUID,
        goal_id: UUID,
        payload: GoalAutoContributionScheduleCreate,
    ) -> GoalAutoContributionScheduleRead:
        goal, account = await self._validate_schedule_dependencies(
            auth_user_id, goal_id, payload.account_id
        )
        self._ensure_goal_operational(goal)

        try:
            async with self.repository.session.begin_nested():
                schedule = await self.repository.get_auto_contribution_schedule_for_update(
                    auth_user_id, goal_id
                )
                now = datetime.now(UTC)

                if schedule:
                    schedule.account_id = payload.account_id
                    schedule.amount = payload.amount
                    schedule.frequency = payload.frequency
                    schedule.execution_day = payload.execution_day
                    schedule.timezone = payload.timezone
                    schedule.start_date = payload.start_date
                    schedule.updated_at = now

                    if schedule.status == "active":
                        schedule.next_run_at = self._calculate_next_occurrence(
                            schedule.frequency,
                            schedule.execution_day,
                            schedule.timezone,
                            schedule.start_date,
                            now,
                        )
                    else:
                        schedule.next_run_at = None
                else:
                    next_run_at = self._calculate_next_occurrence(
                        payload.frequency,
                        payload.execution_day,
                        payload.timezone,
                        payload.start_date,
                        now,
                    )
                    schedule = GoalAutoContributionSchedule(
                        user_id=auth_user_id,
                        goal_id=goal_id,
                        account_id=payload.account_id,
                        amount=payload.amount,
                        frequency=payload.frequency,
                        execution_day=payload.execution_day,
                        timezone=payload.timezone,
                        start_date=payload.start_date,
                        status="active",
                        next_run_at=next_run_at,
                    )
                    await self.repository.create_auto_contribution_schedule(schedule)

                await self.repository.session.flush()
                return GoalAutoContributionScheduleRead.model_validate(schedule)
        except IntegrityError as exc:
            if not self._is_auto_schedule_unique_violation(exc):
                raise
            raise ConflictError(message="Ya existe un schedule activo o pausado.") from exc

    async def patch_auto_contribution_schedule(
        self,
        auth_user_id: UUID,
        goal_id: UUID,
        payload: GoalAutoContributionScheduleUpdate,
    ) -> GoalAutoContributionScheduleRead:
        goal = await self._get_owned_goal(auth_user_id, goal_id)
        schedule = await self.repository.get_auto_contribution_schedule_for_update(
            auth_user_id, goal_id
        )
        if not schedule:
            raise NotFoundError(message="Schedule no encontrado.")

        if payload.account_id is not None and payload.account_id != schedule.account_id:
            await self._validate_schedule_account(auth_user_id, goal, payload.account_id)
            schedule.account_id = payload.account_id

        if payload.amount is not None:
            schedule.amount = payload.amount

        freq = payload.frequency if payload.frequency is not None else schedule.frequency
        day = payload.execution_day if payload.execution_day is not None else schedule.execution_day
        tz = payload.timezone if payload.timezone is not None else schedule.timezone
        start_date_changed = "start_date" in payload.model_fields_set
        start_date = payload.start_date if start_date_changed else schedule.start_date

        if freq == "weekly":
            valid_weekly = {
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            }
            if day.lower() not in valid_weekly:
                raise ValidationError(message="execution_day inválido para frecuencia weekly")
        elif freq == "monthly":
            if day not in MONTHLY_EXECUTION_DAYS:
                raise ValidationError(message="execution_day inválido para frecuencia monthly")

        calendar_changed = (
            payload.frequency is not None
            or payload.execution_day is not None
            or payload.timezone is not None
            or start_date_changed
        )

        schedule.frequency = freq
        schedule.execution_day = day.lower()
        schedule.timezone = tz
        schedule.start_date = start_date

        now = datetime.now(UTC)
        schedule.updated_at = now

        if schedule.status == "active" and calendar_changed:
            schedule.next_run_at = self._calculate_next_occurrence(
                schedule.frequency,
                schedule.execution_day,
                schedule.timezone,
                schedule.start_date,
                now,
            )

        await self.repository.session.flush()
        return GoalAutoContributionScheduleRead.model_validate(schedule)

    async def pause_auto_contribution_schedule(
        self,
        auth_user_id: UUID,
        goal_id: UUID,
    ) -> GoalAutoContributionScheduleRead:
        await self._get_owned_goal(auth_user_id, goal_id)
        schedule = await self.repository.get_auto_contribution_schedule_for_update(
            auth_user_id, goal_id
        )
        if not schedule:
            raise NotFoundError(message="Schedule no encontrado.")

        if schedule.status == "active":
            schedule.status = "paused"
            schedule.pause_reason = "user_paused"
            schedule.next_run_at = None
            schedule.updated_at = datetime.now(UTC)
            await self.repository.session.flush()

        return GoalAutoContributionScheduleRead.model_validate(schedule)

    async def resume_auto_contribution_schedule(
        self,
        auth_user_id: UUID,
        goal_id: UUID,
    ) -> GoalAutoContributionScheduleRead:
        goal = await self._get_owned_goal(auth_user_id, goal_id)
        schedule = await self.repository.get_auto_contribution_schedule_for_update(
            auth_user_id, goal_id
        )
        if not schedule:
            raise NotFoundError(message="Schedule no encontrado.")

        await self._validate_schedule_account(auth_user_id, goal, schedule.account_id)
        self._ensure_goal_operational(goal)

        now = datetime.now(UTC)
        schedule.status = "active"
        schedule.pause_reason = None
        schedule.next_run_at = self._calculate_next_occurrence(
            schedule.frequency, schedule.execution_day, schedule.timezone, schedule.start_date, now
        )
        schedule.updated_at = now
        await self.repository.session.flush()

        return GoalAutoContributionScheduleRead.model_validate(schedule)

    async def delete_auto_contribution_schedule(
        self,
        auth_user_id: UUID,
        goal_id: UUID,
    ) -> None:
        await self._get_owned_goal(auth_user_id, goal_id)
        schedule = await self.repository.get_auto_contribution_schedule_for_update(
            auth_user_id, goal_id
        )
        if not schedule:
            raise NotFoundError(message="Schedule no encontrado.")

        schedule.status = "cancelled"
        schedule.pause_reason = None
        schedule.next_run_at = None
        schedule.updated_at = datetime.now(UTC)
        await self.repository.session.flush()

    @staticmethod
    def _canonical_auto_contribution_instant(value: datetime, field_name: str) -> datetime:
        if value.utcoffset() is None:
            raise ValueError(f"{field_name} debe incluir zona horaria.")
        return value.astimezone(UTC)

    @classmethod
    def _auto_contribution_command_id(cls, schedule_id: UUID, scheduled_for: datetime) -> UUID:
        scheduled_for_utc = cls._canonical_auto_contribution_instant(scheduled_for, "scheduled_for")
        canonical_occurrence = scheduled_for_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        return uuid5(NAMESPACE_AUTOCONTRIB, f"{schedule_id}|{canonical_occurrence}")

    @staticmethod
    def _validate_auto_contribution_calendar(
        schedule: GoalAutoContributionSchedule,
        scheduled_for_utc: datetime,
    ) -> None:
        local_occurrence = scheduled_for_utc.astimezone(ZoneInfo(schedule.timezone))
        if local_occurrence.timetz().replace(tzinfo=None) != AUTO_CONTRIBUTION_EXECUTION_TIME:
            raise ValueError("scheduled_for no coincide con la hora local del schedule.")
        if schedule.start_date is not None and local_occurrence.date() < schedule.start_date:
            raise ValueError("scheduled_for es anterior al start_date del schedule.")

        if schedule.frequency == "weekly":
            expected_weekday = WEEKDAY_INDEX.get(schedule.execution_day.lower())
            if expected_weekday is None or local_occurrence.weekday() != expected_weekday:
                raise ValueError("scheduled_for no pertenece al calendario weekly del schedule.")
            return

        if schedule.frequency == "monthly":
            if schedule.execution_day not in MONTHLY_EXECUTION_DAYS:
                raise ValueError("El schedule monthly tiene un execution_day inválido.")
            desired_day = int(schedule.execution_day)
            actual_day = min(
                desired_day,
                calendar.monthrange(local_occurrence.year, local_occurrence.month)[1],
            )
            if local_occurrence.day != actual_day:
                raise ValueError("scheduled_for no pertenece al calendario monthly del schedule.")
            return

        raise ValueError("El schedule tiene una frecuencia inválida.")

    @staticmethod
    def _execution_result_from_run(
        run: GoalAutoContributionRun, *, idempotent: bool
    ) -> AutoContributionExecutionResult:
        return AutoContributionExecutionResult(
            run_id=run.id,
            status=run.status,
            result_code=run.result_code,
            configured_amount=run.configured_amount,
            executed_amount=run.executed_amount,
            goal_transaction_id=run.goal_transaction_id,
            idempotent=idempotent,
        )

    @staticmethod
    def _is_auto_contribution_run_unique_violation(exc: IntegrityError) -> bool:
        current: object | None = exc.orig
        visited: set[int] = set()
        expected = "uq_gacr_schedule_scheduled_for"
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            constraint_name = getattr(current, "constraint_name", None)
            diag = getattr(current, "diag", None)
            if constraint_name == expected or getattr(diag, "constraint_name", None) == expected:
                return True
            current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        return expected in str(exc.orig)

    async def _persist_auto_contribution_run(
        self, run: GoalAutoContributionRun
    ) -> AutoContributionExecutionResult:
        try:
            async with self.repository.session.begin_nested():
                await self.repository.create_auto_contribution_run(run)
        except IntegrityError as exc:
            if self._is_auto_contribution_run_unique_violation(exc):
                raise _AutoContributionRunRace() from exc
            raise
        return self._execution_result_from_run(run, idempotent=False)

    def _advance_auto_contribution_schedule(
        self,
        schedule: GoalAutoContributionSchedule,
        now_utc: datetime,
    ) -> None:
        schedule.status = "active"
        schedule.pause_reason = None
        schedule.next_run_at = self._calculate_next_occurrence(
            schedule.frequency,
            schedule.execution_day,
            schedule.timezone,
            schedule.start_date,
            now_utc,
        )
        schedule.updated_at = now_utc

    async def execute_auto_contribution_occurrence(
        self,
        schedule_id: UUID,
        scheduled_for: datetime,
        now: datetime,
        missed_occurrences_count: int = 0,
    ) -> AutoContributionExecutionResult:
        """Ejecuta y confirma una occurrence en una sola transacción."""
        if (
            not isinstance(missed_occurrences_count, int)
            or isinstance(missed_occurrences_count, bool)
            or missed_occurrences_count < 0
        ):
            raise ValueError("missed_occurrences_count debe ser un entero mayor o igual a cero.")

        scheduled_for_utc = self._canonical_auto_contribution_instant(
            scheduled_for, "scheduled_for"
        )
        now_utc = self._canonical_auto_contribution_instant(now, "now")
        if scheduled_for_utc > now_utc:
            raise ValueError("scheduled_for no puede estar en el futuro.")

        try:
            async with UnitOfWork(self.repository.session).transaction():
                return await self._execute_auto_contribution_occurrence(
                    schedule_id=schedule_id,
                    scheduled_for_utc=scheduled_for_utc,
                    now_utc=now_utc,
                    missed_occurrences_count=missed_occurrences_count,
                )
        except _AutoContributionRunRace:
            async with UnitOfWork(self.repository.session).transaction():
                existing_run = await self.repository.get_auto_contribution_run(
                    schedule_id, scheduled_for_utc
                )
                if existing_run is None:
                    raise ConflictError(
                        message="La occurrence colisionó pero el Run ganador no está disponible."
                    )
                return self._execution_result_from_run(existing_run, idempotent=True)

    async def _execute_auto_contribution_occurrence(
        self,
        *,
        schedule_id: UUID,
        scheduled_for_utc: datetime,
        now_utc: datetime,
        missed_occurrences_count: int,
    ) -> AutoContributionExecutionResult:
        # Replay if Run already exists before locks
        existing_run = await self.repository.get_auto_contribution_run(
            schedule_id, scheduled_for_utc
        )
        if existing_run:
            return self._execution_result_from_run(existing_run, idempotent=True)

        schedule = await self.repository.get_auto_contribution_schedule_by_id_for_update(
            schedule_id
        )
        if not schedule:
            raise NotFoundError(message="Schedule no encontrado.")
        return await self._execute_auto_contribution_occurrence_in_uow(
            schedule=schedule,
            scheduled_for_utc=scheduled_for_utc,
            now_utc=now_utc,
            missed_occurrences_count=missed_occurrences_count,
        )

    async def _execute_auto_contribution_occurrence_in_uow(
        self,
        *,
        schedule: GoalAutoContributionSchedule,
        scheduled_for_utc: datetime,
        now_utc: datetime,
        missed_occurrences_count: int,
    ) -> AutoContributionExecutionResult:
        idempotency_key = str(self._auto_contribution_command_id(schedule.id, scheduled_for_utc))

        # Replay if Run already exists before locks
        existing_run = await self.repository.get_auto_contribution_run(
            schedule.id, scheduled_for_utc
        )
        if existing_run:
            return self._execution_result_from_run(existing_run, idempotent=True)

        if schedule.status != "active":
            raise ConflictError(message="El schedule no está activo.")

        self._validate_auto_contribution_calendar(schedule, scheduled_for_utc)

        # Mismo orden de locks que create_contribution: Account -> Goal.
        account = await self.account_repo.get_by_id_for_update(
            schedule.account_id, include_inactive=True
        )
        goal = await self.repository.get_by_id_for_update(schedule.goal_id)

        if not goal or not account:
            raise ConflictError(message="Goal o Account no encontrados.")

        if schedule.user_id != goal.user_id or schedule.user_id != account.user_id:
            raise ConflictError(message="Ownership invariant failure.")

        business_skip_reason = None
        if goal.status == "completed":
            business_skip_reason = "goal_completed"
        elif goal.status == "cancelled":
            business_skip_reason = "goal_cancelled"
        elif not goal.is_active:
            business_skip_reason = "goal_archived"
        elif goal.status != "active":
            raise ConflictError(message="El lifecycle de la meta es inconsistente.")
        elif not account.is_active:
            business_skip_reason = "account_inactive"
        elif account.currency.upper() != goal.currency.upper():
            business_skip_reason = "currency_mismatch"

        if business_skip_reason:
            if business_skip_reason in ("goal_completed", "account_inactive", "goal_archived"):
                schedule.status = "paused"
                schedule.pause_reason = business_skip_reason
                schedule.next_run_at = None
            elif business_skip_reason == "goal_cancelled":
                schedule.status = "cancelled"
                schedule.pause_reason = None
                schedule.next_run_at = None
            elif business_skip_reason == "currency_mismatch":
                self._advance_auto_contribution_schedule(schedule, now_utc)
            if business_skip_reason != "currency_mismatch":
                schedule.updated_at = now_utc

            run = GoalAutoContributionRun(
                schedule_id=schedule.id,
                scheduled_for=scheduled_for_utc,
                status="skipped",
                result_code=business_skip_reason,
                configured_amount=schedule.amount,
                executed_amount=None,
                missed_occurrences_count=missed_occurrences_count,
            )
            return await self._persist_auto_contribution_run(run)

        remaining = goal.target_amount - goal.current_amount
        if remaining <= 0:
            schedule.status = "paused"
            schedule.pause_reason = "goal_completed"
            schedule.next_run_at = None
            schedule.updated_at = now_utc
            run = GoalAutoContributionRun(
                schedule_id=schedule.id,
                scheduled_for=scheduled_for_utc,
                status="skipped",
                result_code="goal_completed",
                configured_amount=schedule.amount,
                executed_amount=None,
                missed_occurrences_count=missed_occurrences_count,
            )
            return await self._persist_auto_contribution_run(run)

        candidate = min(schedule.amount, remaining)
        payload = GoalContributionCreate(
            account_id=schedule.account_id,
            amount=candidate,
            currency=goal.currency,
            command_id=None,
        )

        contrib_result = None
        try:
            async with self.repository.session.begin_nested():
                contrib_result = await self.create_contribution(
                    auth_user_id=schedule.user_id,
                    goal_id=schedule.goal_id,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    channel="automatic",
                )
        except InsufficientFundsError:
            self._advance_auto_contribution_schedule(schedule, now_utc)
            run = GoalAutoContributionRun(
                schedule_id=schedule.id,
                scheduled_for=scheduled_for_utc,
                status="skipped",
                result_code="insufficient_available_balance",
                configured_amount=schedule.amount,
                executed_amount=None,
                missed_occurrences_count=missed_occurrences_count,
            )
            return await self._persist_auto_contribution_run(run)

        if goal.status == "completed":
            schedule.status = "paused"
            schedule.pause_reason = "goal_completed"
            schedule.next_run_at = None
            schedule.updated_at = now_utc
        else:
            self._advance_auto_contribution_schedule(schedule, now_utc)

        run = GoalAutoContributionRun(
            schedule_id=schedule.id,
            scheduled_for=scheduled_for_utc,
            executed_at=now_utc if not contrib_result.idempotent else contrib_result.created_at,
            status="succeeded",
            result_code="success",
            configured_amount=schedule.amount,
            executed_amount=contrib_result.applied_amount,
            missed_occurrences_count=missed_occurrences_count,
            goal_transaction_id=contrib_result.transaction_id,
        )
        result = await self._persist_auto_contribution_run(run)
        return AutoContributionExecutionResult(
            run_id=result.run_id,
            status=result.status,
            result_code=result.result_code,
            configured_amount=result.configured_amount,
            executed_amount=result.executed_amount,
            goal_transaction_id=result.goal_transaction_id,
            idempotent=contrib_result.idempotent,
        )

    @staticmethod
    def calculate_latest_due_occurrence(
        schedule: GoalAutoContributionSchedule,
        now: datetime,
    ) -> tuple[datetime, int]:
        if schedule.next_run_at is None:
            raise ValueError("Schedule no tiene next_run_at")
        if now.utcoffset() is None:
            raise ValueError("now debe incluir zona horaria")
        if schedule.next_run_at.utcoffset() is None:
            raise ValueError("next_run_at debe incluir zona horaria")

        now = now.astimezone(UTC)
        tz = ZoneInfo(schedule.timezone)
        now_local = now.astimezone(tz)
        first_due_local = schedule.next_run_at.astimezone(tz)

        if now_local < first_due_local:
            return schedule.next_run_at, 0

        if schedule.frequency == "weekly":
            diff_days = (now_local.date() - first_due_local.date()).days
            missed_count = diff_days // 7
            latest_due_date = first_due_local.date() + timedelta(days=missed_count * 7)
            latest_due_utc = datetime.combine(
                latest_due_date,
                AUTO_CONTRIBUTION_EXECUTION_TIME,
                tzinfo=tz,
            ).astimezone(UTC)

            if latest_due_utc > now:
                missed_count -= 1
                if missed_count < 0:
                    return schedule.next_run_at, 0
                latest_due_date = first_due_local.date() + timedelta(days=missed_count * 7)
                latest_due_utc = datetime.combine(
                    latest_due_date,
                    AUTO_CONTRIBUTION_EXECUTION_TIME,
                    tzinfo=tz,
                ).astimezone(UTC)

            return latest_due_utc, missed_count

        if schedule.frequency == "monthly":
            first_year = first_due_local.year
            first_month = first_due_local.month
            now_year = now_local.year
            now_month = now_local.month

            desired_day = int(schedule.execution_day)

            candidate_day = min(desired_day, calendar.monthrange(now_year, now_month)[1])
            candidate_date = date(now_year, now_month, candidate_day)
            candidate_utc = datetime.combine(
                candidate_date,
                AUTO_CONTRIBUTION_EXECUTION_TIME,
                tzinfo=tz,
            ).astimezone(UTC)

            if candidate_utc > now:
                if now_month == 1:
                    now_month = 12
                    now_year -= 1
                else:
                    now_month -= 1
                candidate_day = min(desired_day, calendar.monthrange(now_year, now_month)[1])
                candidate_date = date(now_year, now_month, candidate_day)
                candidate_utc = datetime.combine(
                    candidate_date,
                    AUTO_CONTRIBUTION_EXECUTION_TIME,
                    tzinfo=tz,
                ).astimezone(UTC)

            if candidate_utc < schedule.next_run_at:
                candidate_utc = schedule.next_run_at
                now_year = first_year
                now_month = first_month

            missed_count = (now_year * 12 + now_month) - (first_year * 12 + first_month)
            if missed_count < 0:
                missed_count = 0

            return candidate_utc, missed_count

        raise ValueError("Frecuencia de schedule inválida")

    async def _reconcile_existing_auto_contribution_run(
        self,
        *,
        schedule: GoalAutoContributionSchedule,
        run: GoalAutoContributionRun,
        now_utc: datetime,
    ) -> tuple[bool, bool]:
        """Consume un Run final existente sin repetir escrituras financieras."""
        previous_status = schedule.status

        if run.status == "succeeded":
            if run.result_code != "success":
                raise ConflictError(message="Run succeeded con result_code inconsistente.")
            goal = await self.repository.get_by_id(schedule.goal_id)
            if goal is None:
                raise ConflictError(message="Goal del schedule no encontrado.")
            if goal.status == "completed":
                schedule.status = "paused"
                schedule.pause_reason = "goal_completed"
                schedule.next_run_at = None
                schedule.updated_at = now_utc
            elif goal.status == "cancelled":
                schedule.status = "cancelled"
                schedule.pause_reason = None
                schedule.next_run_at = None
                schedule.updated_at = now_utc
            elif not goal.is_active:
                schedule.status = "paused"
                schedule.pause_reason = "goal_archived"
                schedule.next_run_at = None
                schedule.updated_at = now_utc
            elif goal.status == "active":
                self._advance_auto_contribution_schedule(schedule, now_utc)
            else:
                raise ConflictError(message="El lifecycle de la meta es inconsistente.")
        elif run.status == "skipped":
            if run.result_code in {
                "goal_completed",
                "account_inactive",
                "goal_archived",
            }:
                schedule.status = "paused"
                schedule.pause_reason = run.result_code
                schedule.next_run_at = None
                schedule.updated_at = now_utc
            elif run.result_code == "goal_cancelled":
                schedule.status = "cancelled"
                schedule.pause_reason = None
                schedule.next_run_at = None
                schedule.updated_at = now_utc
            elif run.result_code in {
                "insufficient_available_balance",
                "currency_mismatch",
            }:
                self._advance_auto_contribution_schedule(schedule, now_utc)
            else:
                raise ConflictError(message="Run skipped con result_code inconsistente.")
        else:
            raise ConflictError(message="Run existente no es un outcome final reconciliable.")

        paused = previous_status != "paused" and schedule.status == "paused"
        cancelled = previous_status != "cancelled" and schedule.status == "cancelled"
        return paused, cancelled

    async def process_due_auto_contributions(
        self, now: datetime, batch_size: int = 50
    ) -> AutoContributionProcessorSummary:
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or batch_size < 1
            or batch_size > 50
        ):
            raise ValueError("batch_size debe ser un entero entre 1 y 50.")

        now_utc = self._canonical_auto_contribution_instant(now, "now")
        summary = AutoContributionProcessorSummary()
        attempted_schedule_ids: set[UUID] = set()
        logger.info(
            "auto_contribution_processor_started",
            now=now_utc.isoformat(),
            batch_size=batch_size,
        )

        try:
            for _ in range(batch_size):
                schedule: GoalAutoContributionSchedule | None = None
                selected_schedule_id: UUID | None = None
                latest_due_utc: datetime | None = None
                try:
                    async with UnitOfWork(self.repository.session).transaction():
                        schedule = await self.repository.get_next_due_auto_contribution_schedule_for_update(
                            now=now_utc,
                            excluded_schedule_ids=(
                                list(attempted_schedule_ids) if attempted_schedule_ids else None
                            ),
                        )
                        if schedule is None:
                            break

                        selected_schedule_id = schedule.id
                        attempted_schedule_ids.add(selected_schedule_id)
                        summary.selected += 1
                        latest_due_utc, missed_count = self.calculate_latest_due_occurrence(
                            schedule, now_utc
                        )
                        logger.info(
                            "auto_contribution_schedule_selected",
                            schedule_id=str(schedule.id),
                            scheduled_for=latest_due_utc.isoformat(),
                            missed_occurrences_count=missed_count,
                        )

                        existing_run = await self.repository.get_auto_contribution_run(
                            schedule.id, latest_due_utc
                        )
                        if existing_run is not None:
                            (
                                paused,
                                cancelled,
                            ) = await self._reconcile_existing_auto_contribution_run(
                                schedule=schedule,
                                run=existing_run,
                                now_utc=now_utc,
                            )
                            summary.reconciled += 1
                            summary.paused_schedules += int(paused)
                            summary.cancelled_schedules += int(cancelled)
                            logger.info(
                                "auto_contribution_occurrence_reconciled",
                                schedule_id=str(schedule.id),
                                scheduled_for=latest_due_utc.isoformat(),
                                run_status=existing_run.status,
                                result_code=existing_run.result_code,
                            )
                            continue

                        result = await self._execute_auto_contribution_occurrence_in_uow(
                            schedule=schedule,
                            scheduled_for_utc=latest_due_utc,
                            now_utc=now_utc,
                            missed_occurrences_count=missed_count,
                        )

                        if result.idempotent:
                            summary.reconciled += 1
                        elif result.status == "succeeded":
                            summary.succeeded += 1
                        elif result.status == "skipped":
                            summary.skipped += 1

                        summary.missed_occurrences += missed_count

                        if (
                            not result.idempotent
                            and result.status == "succeeded"
                            and schedule.pause_reason == "goal_completed"
                        ):
                            summary.completed_goals += 1

                        if schedule.status == "paused":
                            summary.paused_schedules += 1
                        elif schedule.status == "cancelled":
                            summary.cancelled_schedules += 1

                        logger.info(
                            "auto_contribution_occurrence_succeeded"
                            if result.status == "succeeded"
                            else "auto_contribution_occurrence_skipped",
                            schedule_id=str(schedule.id),
                            scheduled_for=latest_due_utc.isoformat(),
                            result_code=result.result_code,
                            missed_occurrences_count=missed_count,
                            idempotent=result.idempotent,
                        )
                except Exception:
                    if selected_schedule_id is None:
                        logger.exception(
                            "auto_contribution_processor_selection_failed",
                            now=now_utc.isoformat(),
                        )
                        raise
                    summary.technical_failures += 1
                    logger.exception(
                        "auto_contribution_occurrence_failed",
                        schedule_id=str(selected_schedule_id),
                        scheduled_for=(
                            latest_due_utc.isoformat() if latest_due_utc is not None else None
                        ),
                    )
        finally:
            logger.info(
                "auto_contribution_processor_finished",
                selected=summary.selected,
                succeeded=summary.succeeded,
                skipped=summary.skipped,
                technical_failures=summary.technical_failures,
                reconciled=summary.reconciled,
                missed_occurrences=summary.missed_occurrences,
                completed_goals=summary.completed_goals,
                paused_schedules=summary.paused_schedules,
                cancelled_schedules=summary.cancelled_schedules,
            )

        return summary
