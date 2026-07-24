import hashlib
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.accounts.exceptions import AccountForbiddenError
from app.accounts.repository import AccountRepository
from app.cash.exceptions import InsufficientFundsError
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
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
from app.goals.models import Goal, GoalTransaction
from app.goals.repository import GoalRepository
from app.goals.schemas import (
    GoalContributionCreate,
    GoalContributionResult,
    GoalCreate,
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

    async def get_goal(self, auth_user_id: UUID, goal_id: UUID) -> GoalRead:
        goal = await self._get_goal_or_404(goal_id)
        if goal.user_id != auth_user_id:
            raise GoalForbiddenError()

        start_date, end_date = self._current_period_dates()
        contributions = await self.repository.get_period_contributions(
            auth_user_id, start_date, end_date
        )

        gr = GoalRead.model_validate(goal)
        gr.contributed_this_period = contributions.get(goal.id, Decimal("0.00"))
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
        )

    async def create_contribution(
        self,
        auth_user_id: UUID,
        goal_id: UUID,
        payload: GoalContributionCreate,
        idempotency_key: str | None,
    ) -> GoalContributionResult:
        command_id = self._resolve_command_id(
            payload.command_id,
            idempotency_key,
        )

        replay = await self._find_idempotent_contribution(
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

        # A concurrent winner may have committed while either lock was awaited.
        replay = await self._find_idempotent_contribution(
            auth_user_id=auth_user_id,
            goal_id=goal_id,
            payload=payload,
            command_id=command_id,
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
            origin = "legacy" if getattr(t, "legacy_contribution_id", None) else "native"
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
