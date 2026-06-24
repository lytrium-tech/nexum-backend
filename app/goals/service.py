from decimal import Decimal
from uuid import UUID

from app.accounts.exceptions import AccountForbiddenError
from app.accounts.repository import AccountRepository
from app.cash.exceptions import InsufficientFundsError
from app.core.currency import (
    FXProviderError,
    UnsupportedCurrencyError,
    get_fx_rate,
    round_to_minimum_unit,
)
from app.core.errors import ForbiddenError, NotFoundError
from app.core.utils import clean_presentation_name, normalize_name
from app.goals.exceptions import (
    GoalAmountExceededError,
    GoalCompletedError,
    GoalDuplicateError,
    GoalForbiddenError,
    GoalNotActiveError,
    GoalTargetAmountError,
)
from app.goals.models import Goal, GoalContribution
from app.goals.repository import GoalRepository
from app.goals.schemas import (
    GoalContributionCreate,
    GoalContributionResult,
    GoalCreate,
    GoalRead,
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

    def _current_period(self) -> str:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Bogota")
        now = datetime.now(tz)
        return f"{now.year}-{now.month:02d}"

    async def list_goals(self, auth_user_id: UUID) -> list[GoalRead]:
        goals = await self.repository.list_active(auth_user_id)
        period = self._current_period()
        contributions = await self.repository.get_period_contributions(auth_user_id, period)

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

        period = self._current_period()
        contributions = await self.repository.get_period_contributions(auth_user_id, period)

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

        period = self._current_period()
        contributions = await self.repository.get_period_contributions(auth_user_id, period)
        gr = GoalRead.model_validate(goal)
        gr.contributed_this_period = contributions.get(goal.id, Decimal("0.00"))
        return gr

    async def delete_goal(self, auth_user_id: UUID, goal_id: UUID) -> None:
        goal = await self._get_goal_or_404(goal_id)
        if goal.user_id != auth_user_id:
            raise GoalForbiddenError()

        goal.is_active = False
        goal.status = "cancelled"
        await self.repository.session.flush()

    async def create_contribution(
        self,
        auth_user_id: UUID,
        goal_id: UUID,
        payload: GoalContributionCreate,
        idempotency_key: str | None,
    ) -> GoalContributionResult:

        goal = await self.repository.get_by_id_for_update(goal_id)
        if not goal:
            raise NotFoundError(message="Meta no encontrada.")

        if goal.user_id != auth_user_id:
            raise GoalForbiddenError()

        if not goal.is_active:
            raise GoalNotActiveError()

        if goal.status == "completed":
            raise GoalCompletedError()

        account = await self.account_repo.get_by_id_for_update(payload.account_id)
        if not account:
            raise NotFoundError(message="Cuenta no encontrada.")

        if account.user_id is not None and account.user_id != auth_user_id:
            raise AccountForbiddenError()

        source_currency = payload.currency or account.currency
        goal_currency = goal.currency or "COP"

        if account.currency != source_currency:
            raise ForbiddenError(
                message="El currency no coincide con la moneda de la cuenta origen."
            )

        try:
            fx_info = await get_fx_rate(source_currency, goal_currency)
        except UnsupportedCurrencyError as e:
            raise ForbiddenError(message=str(e))
        except FXProviderError as e:
            raise ForbiddenError(message=f"No se pudo obtener la tasa de cambio: {e}")

        fx_rate = fx_info["fx_rate"]
        rate_source = fx_info["rate_source"]
        rate_timestamp = fx_info["rate_timestamp"]
        is_estimated = source_currency != goal_currency

        source_amount = payload.amount
        if source_currency == goal_currency:
            applied_amount = source_amount
        else:
            applied_amount = round_to_minimum_unit(source_amount * fx_rate, goal_currency)

        if applied_amount > (goal.target_amount - goal.current_amount):
            raise GoalAmountExceededError()

        if account.balance < source_amount:
            raise InsufficientFundsError()

        event_create = LedgerEventCreate(
            user_id=auth_user_id,
            account_id=account.id,
            event_type=EventType.GOAL_CONTRIBUTION,
            direction=Direction.OUTFLOW,
            amount=source_amount,
            currency=source_currency,
            source_message_id=payload.source_message_id,
            raw_message=payload.raw_message,
            metadata={"goal_id": str(goal.id)},
        )

        if idempotency_key:
            try:
                event_create.command_id = UUID(idempotency_key)
            except ValueError:
                pass

        result = await self.ledger_repo.insert_event(event_create)
        event = result.event

        if goal.target_amount > 0:
            progress = min(
                round((goal.current_amount / goal.target_amount) * Decimal("100"), 2),
                Decimal("100"),
            )
        else:
            progress = Decimal("0")

        if result.idempotent:
            # Idempotent retry
            return GoalContributionResult(
                contribution_id=None,
                event_id=event.id if event else None,
                amount=source_amount,
                currency=source_currency,
                applied_amount=applied_amount,
                goal_currency=goal_currency,
                fx_rate=fx_rate,
                rate_source=rate_source,
                rate_timestamp=rate_timestamp,
                is_estimated=is_estimated,
                balance_after=account.balance,
                goal_current_amount=goal.current_amount,
                progress_percentage=progress,
                status="idempotent_retry",
            )

        # New contribution
        await self.account_repo.update_balance(account, -source_amount)

        contribution = GoalContribution(
            user_id=auth_user_id,
            goal_id=goal.id,
            event_id=event.id,
            account_id=account.id,
            amount=source_amount,
            currency=source_currency,
            applied_amount=applied_amount,
            goal_currency=goal_currency,
            fx_rate=fx_rate,
            rate_source=rate_source,
            rate_timestamp=rate_timestamp,
            is_estimated=is_estimated,
            period=event.period,
        )
        await self.repository.create_contribution(contribution)

        goal.current_amount += applied_amount

        if goal.current_amount >= goal.target_amount:
            goal.status = "completed"
            goal.current_amount = goal.target_amount  # Ensure we don't exceed logically

        # Recalculate progress for new amount
        if goal.target_amount > 0:
            new_progress = min(
                round((goal.current_amount / goal.target_amount) * Decimal("100"), 2),
                Decimal("100"),
            )
        else:
            new_progress = Decimal("0")

        await self.repository.session.flush()
        await self.repository.session.refresh(goal)

        return GoalContributionResult(
            contribution_id=contribution.id,
            event_id=event.id,
            amount=payload.amount,
            balance_after=account.balance,
            goal_current_amount=goal.current_amount,
            progress_percentage=new_progress,
            status="success",
        )

    async def _get_goal_or_404(self, goal_id: UUID) -> Goal:
        goal = await self.repository.get_by_id(goal_id)
        if not goal:
            raise NotFoundError(message="Meta no encontrada.")
        return goal
