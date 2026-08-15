from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account
from app.core.utils import normalize_name
from app.goals.enums import GoalTransactionType
from app.goals.models import Goal, GoalContribution, GoalTransaction
from app.ledger.models import FinancialEvent


@dataclass(frozen=True, slots=True)
class GoalAccountReservation:
    account_id: UUID
    account_name: str
    account_currency: str
    contributed_amount: Decimal
    released_amount: Decimal
    reserved_amount: Decimal
    goal_currency: str
    applied_contributed_amount: Decimal
    applied_released_amount: Decimal
    applied_reserved_amount: Decimal
    account_is_active: bool


def _source_reservation_amounts():
    allocations_sum = func.coalesce(
        func.sum(GoalTransaction.source_amount).filter(
            GoalTransaction.transaction_type.in_(
                [GoalTransactionType.allocation, GoalTransactionType.legacy_import]
            )
        ),
        Decimal("0"),
    )
    releases_sum = func.coalesce(
        func.sum(GoalTransaction.source_amount).filter(
            GoalTransaction.transaction_type == GoalTransactionType.release
        ),
        Decimal("0"),
    )
    return allocations_sum, releases_sum, allocations_sum - releases_sum


def _applied_reservation_amounts():
    allocations_sum = func.coalesce(
        func.sum(GoalTransaction.applied_amount).filter(
            GoalTransaction.transaction_type.in_(
                [GoalTransactionType.allocation, GoalTransactionType.legacy_import]
            )
        ),
        Decimal("0"),
    )
    releases_sum = func.coalesce(
        func.sum(GoalTransaction.applied_amount).filter(
            GoalTransaction.transaction_type == GoalTransactionType.release
        ),
        Decimal("0"),
    )
    return allocations_sum, releases_sum, allocations_sum - releases_sum


class GoalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, goal_id: UUID) -> Goal | None:
        result = await self.session.execute(select(Goal).where(Goal.id == goal_id))
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, goal_id: UUID) -> Goal | None:
        result = await self.session.execute(
            select(Goal).where(Goal.id == goal_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_active(self, user_id: UUID) -> list[Goal]:
        result = await self.session.execute(
            select(Goal)
            .where(Goal.user_id == user_id)
            .where(Goal.is_active)
            .order_by(Goal.created_at.desc())
        )
        return list(result.scalars().all())

    async def check_name_exists(self, user_id: UUID, norm_name: str) -> bool:
        result = await self.session.execute(
            select(Goal).where(Goal.user_id == user_id).where(Goal.is_active)
        )
        for goal in result.scalars().all():
            if normalize_name(goal.name) == norm_name:
                return True
        return False

    async def get_period_contributions(
        self,
        user_id: UUID,
        start_datetime: datetime,
        end_datetime_exclusive: datetime,
    ) -> dict[UUID, Decimal]:
        if start_datetime.utcoffset() is None or end_datetime_exclusive.utcoffset() is None:
            raise ValueError("Los límites temporales deben incluir zona horaria.")
        if start_datetime >= end_datetime_exclusive:
            raise ValueError("El fin exclusivo debe ser posterior al inicio.")

        # GoalTransaction is authoritative. During a pre-migration rolling
        # window, legacy rows without a corresponding import remain visible.
        signed_applied_amount = case(
            (
                GoalTransaction.transaction_type == GoalTransactionType.allocation,
                GoalTransaction.applied_amount,
            ),
            (
                GoalTransaction.transaction_type == GoalTransactionType.release,
                -GoalTransaction.applied_amount,
            ),
            else_=Decimal("0"),
        )
        gt_stmt = (
            select(GoalTransaction.goal_id, func.sum(signed_applied_amount))
            .where(GoalTransaction.user_id == user_id)
            .where(GoalTransaction.created_at >= start_datetime)
            .where(GoalTransaction.created_at < end_datetime_exclusive)
            .where(
                GoalTransaction.transaction_type.in_(
                    (
                        GoalTransactionType.allocation,
                        GoalTransactionType.release,
                    )
                )
            )
            .group_by(GoalTransaction.goal_id)
        )

        legacy_migrated_ids = select(GoalTransaction.legacy_contribution_id).where(
            GoalTransaction.legacy_contribution_id.isnot(None)
        )
        gc_stmt = (
            select(GoalContribution.goal_id, func.sum(GoalContribution.amount))
            .where(GoalContribution.user_id == user_id)
            .where(GoalContribution.created_at >= start_datetime)
            .where(GoalContribution.created_at < end_datetime_exclusive)
            .where(GoalContribution.id.not_in(legacy_migrated_ids))
            .group_by(GoalContribution.goal_id)
        )

        gt_result = await self.session.execute(gt_stmt)
        gc_result = await self.session.execute(gc_stmt)

        totals = defaultdict(Decimal)
        for row in gt_result.all():
            totals[row[0]] += Decimal(str(row[1] or 0))
        for row in gc_result.all():
            totals[row[0]] += Decimal(str(row[1] or 0))

        return dict(totals)

    async def create(self, goal: Goal) -> Goal:
        self.session.add(goal)
        await self.session.flush()
        return goal

    async def create_contribution(self, contribution: GoalContribution) -> GoalContribution:
        self.session.add(contribution)
        await self.session.flush()
        return contribution

    async def create_transaction(self, transaction: GoalTransaction) -> GoalTransaction:
        self.session.add(transaction)
        await self.session.flush()
        return transaction

    async def get_transaction_by_id(self, tx_id: UUID) -> GoalTransaction | None:
        result = await self.session.execute(
            select(GoalTransaction).where(GoalTransaction.id == tx_id)
        )
        return result.scalar_one_or_none()

    async def get_transaction_by_command_id(self, command_id: UUID) -> GoalTransaction | None:
        result = await self.session.execute(
            select(GoalTransaction).where(GoalTransaction.command_id == command_id)
        )
        return result.scalar_one_or_none()

    async def get_transaction_by_legacy_contribution_id(
        self, legacy_id: UUID
    ) -> GoalTransaction | None:
        result = await self.session.execute(
            select(GoalTransaction).where(GoalTransaction.legacy_contribution_id == legacy_id)
        )
        return result.scalar_one_or_none()

    async def list_transactions_by_goal(
        self, goal_id: UUID, user_id: UUID, limit: int = 50, offset: int = 0
    ) -> tuple[list[tuple[GoalTransaction, str | None]], int]:
        count_stmt = (
            select(func.count())
            .select_from(GoalTransaction)
            .where(GoalTransaction.goal_id == goal_id)
            .where(GoalTransaction.user_id == user_id)
        )
        total = await self.session.scalar(count_stmt) or 0

        result = await self.session.execute(
            select(GoalTransaction, FinancialEvent.description)
            .outerjoin(FinancialEvent, FinancialEvent.id == GoalTransaction.event_id)
            .where(GoalTransaction.goal_id == goal_id)
            .where(GoalTransaction.user_id == user_id)
            .order_by(GoalTransaction.created_at.desc(), GoalTransaction.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [(row[0], row[1]) for row in result.all()], total

    async def calculate_reserved_by_account(self, account_id: UUID, user_id: UUID) -> Decimal:
        _, _, reserved_amount = _source_reservation_amounts()

        result = await self.session.execute(
            select(reserved_amount)
            .where(GoalTransaction.account_id == account_id)
            .where(GoalTransaction.user_id == user_id)
        )
        val = result.scalar_one_or_none()
        return Decimal(str(val)) if val is not None else Decimal("0.00")

    async def get_reserved_amounts_for_user(self, user_id: UUID) -> dict[UUID, Decimal]:
        _, _, reserved_amount = _source_reservation_amounts()
        result = await self.session.execute(
            select(
                GoalTransaction.account_id,
                reserved_amount.label("reserved"),
            )
            .join(Account, Account.id == GoalTransaction.account_id)
            .where(GoalTransaction.user_id == user_id)
            .where(Account.user_id == user_id)
            .where(
                GoalTransaction.transaction_type.in_(
                    (
                        GoalTransactionType.allocation,
                        GoalTransactionType.legacy_import,
                        GoalTransactionType.release,
                    )
                )
            )
            .group_by(GoalTransaction.account_id)
        )
        return {row.account_id: Decimal(str(row.reserved)) for row in result.all()}

    async def calculate_reserved_by_goal_and_account(
        self, user_id: UUID, goal_id: UUID, account_id: UUID
    ) -> Decimal:
        _, _, reserved_amount = _source_reservation_amounts()

        result = await self.session.execute(
            select(reserved_amount)
            .where(GoalTransaction.goal_id == goal_id)
            .where(GoalTransaction.account_id == account_id)
            .where(GoalTransaction.user_id == user_id)
        )
        val = result.scalar_one_or_none()
        return Decimal(str(val)) if val is not None else Decimal("0.00")

    async def calculate_total_reserved_by_goal(self, user_id: UUID, goal_id: UUID) -> Decimal:
        _, _, reserved_amount = _applied_reservation_amounts()

        result = await self.session.execute(
            select(reserved_amount)
            .select_from(GoalTransaction)
            .join(Account, Account.id == GoalTransaction.account_id)
            .where(GoalTransaction.goal_id == goal_id)
            .where(GoalTransaction.user_id == user_id)
            .where(Account.user_id == user_id)
        )
        val = result.scalar_one_or_none()
        return Decimal(str(val)) if val is not None else Decimal("0.00")

    async def get_reservations_by_account(
        self, user_id: UUID, goal_id: UUID
    ) -> list[GoalAccountReservation]:
        source_alloc, source_rel, source_reserved = _source_reservation_amounts()
        applied_alloc, applied_rel, applied_reserved = _applied_reservation_amounts()

        stmt = (
            select(
                Account.id.label("account_id"),
                Account.name.label("account_name"),
                Account.currency.label("account_currency"),
                source_alloc.label("contributed_amount"),
                source_rel.label("released_amount"),
                source_reserved.label("reserved_amount"),
                Goal.currency.label("goal_currency"),
                applied_alloc.label("applied_contributed_amount"),
                applied_rel.label("applied_released_amount"),
                applied_reserved.label("applied_reserved_amount"),
                Account.is_active.label("account_is_active"),
            )
            .join(Account, Account.id == GoalTransaction.account_id)
            .join(Goal, Goal.id == GoalTransaction.goal_id)
            .where(GoalTransaction.goal_id == goal_id)
            .where(GoalTransaction.user_id == user_id)
            .where(Account.user_id == user_id)
            .where(Goal.user_id == user_id)
            .group_by(
                Account.id,
                Account.name,
                Account.currency,
                Account.is_active,
                Goal.currency,
            )
            .having((source_reserved != 0) | (applied_reserved != 0))
            .order_by(Account.id)
        )

        result = await self.session.execute(stmt)
        return [
            GoalAccountReservation(
                account_id=row.account_id,
                account_name=row.account_name,
                account_currency=row.account_currency,
                contributed_amount=Decimal(str(row.contributed_amount)),
                released_amount=Decimal(str(row.released_amount)),
                reserved_amount=Decimal(str(row.reserved_amount)),
                goal_currency=row.goal_currency,
                applied_contributed_amount=Decimal(str(row.applied_contributed_amount)),
                applied_released_amount=Decimal(str(row.applied_released_amount)),
                applied_reserved_amount=Decimal(str(row.applied_reserved_amount)),
                account_is_active=row.account_is_active,
            )
            for row in result.all()
        ]

    async def calculate_reserved_by_user_currency(
        self,
        user_id: UUID,
    ) -> dict[str, Decimal]:
        signed_source_amount = case(
            (
                GoalTransaction.transaction_type.in_(
                    (GoalTransactionType.allocation, GoalTransactionType.legacy_import)
                ),
                GoalTransaction.source_amount,
            ),
            (
                GoalTransaction.transaction_type == GoalTransactionType.release,
                -GoalTransaction.source_amount,
            ),
            else_=Decimal("0"),
        )
        currency = func.upper(GoalTransaction.source_currency)
        result = await self.session.execute(
            select(currency, func.coalesce(func.sum(signed_source_amount), Decimal("0")))
            .join(Account, Account.id == GoalTransaction.account_id)
            .where(GoalTransaction.user_id == user_id)
            .where(Account.user_id == user_id)
            .where(Account.is_active.is_(True))
            .where(
                GoalTransaction.transaction_type.in_(
                    (
                        GoalTransactionType.allocation,
                        GoalTransactionType.legacy_import,
                        GoalTransactionType.release,
                    )
                )
            )
            .group_by(currency)
        )
        return {row[0]: Decimal(str(row[1] or 0)) for row in result.all()}

    async def calculate_progress_by_goal(self, goal_id: UUID, user_id: UUID) -> Decimal:
        _, _, progress_amount = _applied_reservation_amounts()

        result = await self.session.execute(
            select(progress_amount)
            .where(GoalTransaction.goal_id == goal_id)
            .where(GoalTransaction.user_id == user_id)
        )
        val = result.scalar_one_or_none()
        return Decimal(str(val)) if val is not None else Decimal("0.00")

    async def count_non_legacy_transactions(self) -> int:
        result = await self.session.execute(
            select(func.count(GoalTransaction.id)).where(
                GoalTransaction.legacy_contribution_id.is_(None)
            )
        )
        return result.scalar_one() or 0
