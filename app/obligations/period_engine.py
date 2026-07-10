import calendar
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.obligations.models import Obligation, ObligationPeriod


def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def generate_period_key(frequency: str, d: date, sequence_number: int) -> str:
    if frequency == "monthly":
        return f"{d.year}-{d.month:02d}"
    elif frequency == "yearly":
        return f"{d.year}"
    elif frequency == "weekly":
        return f"{d.year}-W{d.isocalendar()[1]:02d}-SEQ{sequence_number}"
    elif frequency == "biweekly":
        return f"{d.year}-BW-SEQ{sequence_number}"
    elif frequency == "one_time":
        return "ONE-TIME"
    return f"{d.year}-{d.month:02d}-{sequence_number}"


def calculate_period_bounds(
    obligation: Obligation, sequence_number: int
) -> tuple[date, date, date]:
    """Returns (start_date, end_date, due_date)"""
    freq = obligation.frequency
    first_due = obligation.first_due_date
    start = obligation.start_date
    interval = obligation.interval_count or 1

    # Defaults if missing
    due_day = obligation.due_day or first_due.day
    due_month = obligation.due_month or first_due.month

    if freq == "monthly":
        target_month_date = add_months(start.replace(day=1), (sequence_number - 1) * interval)

        p_start = date(target_month_date.year, target_month_date.month, 1)
        p_end_day = calendar.monthrange(p_start.year, p_start.month)[1]
        p_end = date(p_start.year, p_start.month, p_end_day)

        actual_due_day = min(due_day, p_end_day)
        p_due = date(p_start.year, p_start.month, actual_due_day)
        return p_start, p_end, p_due

    elif freq == "yearly":
        target_year = start.year + ((sequence_number - 1) * interval)
        p_start = date(target_year, 1, 1)
        p_end = date(target_year, 12, 31)

        p_due_day = min(due_day, calendar.monthrange(target_year, due_month)[1])
        p_due = date(target_year, due_month, p_due_day)
        return p_start, p_end, p_due

    elif freq == "weekly":
        days_per_interval = 7 * interval
        days_shift = (sequence_number - 1) * days_per_interval

        p_start = start + timedelta(days=days_shift)
        p_end = p_start + timedelta(days=days_per_interval - 1)

        due_offset = (first_due - start).days
        p_due = p_start + timedelta(days=due_offset)
        return p_start, p_end, p_due

    elif freq == "biweekly":
        days_per_interval = 14 * interval
        days_shift = (sequence_number - 1) * days_per_interval

        p_start = start + timedelta(days=days_shift)
        p_end = p_start + timedelta(days=days_per_interval - 1)

        due_offset = (first_due - start).days
        p_due = p_start + timedelta(days=due_offset)
        return p_start, p_end, p_due

    elif freq == "one_time":
        return start, obligation.end_date or first_due, first_due

    raise ValueError(f"Unsupported frequency {freq}")


class PeriodEngine:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def sync_periods(self, obligation: Obligation, current_date: date) -> None:
        """
        Generates current and next periods.
        Updates 'overdue' statuses for past unpaid periods.
        """
        # First, refresh overdue
        await self.refresh_overdue(obligation.id, current_date)

        if obligation.status != "active":
            return

        current_seq = self._find_sequence_for_date(obligation, current_date)

        max_seq = 1 if obligation.frequency == "one_time" else current_seq + 1

        if obligation.end_count:
            max_seq = min(max_seq, obligation.end_count)

        for seq in range(current_seq, max_seq + 1):
            if obligation.end_date:
                # Si el periodo de inicio es mayor que el end_date, paramos.
                p_start, _, _ = calculate_period_bounds(obligation, seq)
                if p_start > obligation.end_date:
                    break
            await self._ensure_period_exists(obligation, seq, current_date)

    async def refresh_overdue(self, obligation_id: UUID, current_date: date) -> None:
        stmt = select(ObligationPeriod).where(
            ObligationPeriod.obligation_id == obligation_id,
            ObligationPeriod.status == "pending_payment",
            ObligationPeriod.due_date < current_date,
        )
        result = await self.session.execute(stmt)
        periods = result.scalars().all()
        for p in periods:
            p.status = "overdue"

    async def skip_period(self, period_id: UUID) -> ObligationPeriod:
        stmt = select(ObligationPeriod).where(ObligationPeriod.id == period_id).with_for_update()
        result = await self.session.execute(stmt)
        period = result.scalar_one_or_none()

        if not period:
            raise ValueError("Period not found")
        if period.status in ("paid", "skipped", "cancelled"):
            raise ValueError(f"Cannot skip period in status {period.status}")

        period.status = "skipped"
        await self.session.flush()
        return period

    def _find_sequence_for_date(self, obligation: Obligation, d: date) -> int:
        if d < obligation.start_date:
            return 1

        interval = obligation.interval_count or 1
        if obligation.frequency == "monthly":
            months_diff = (d.year - obligation.start_date.year) * 12 + (
                d.month - obligation.start_date.month
            )
            return (months_diff // interval) + 1
        elif obligation.frequency == "yearly":
            years_diff = d.year - obligation.start_date.year
            return (years_diff // interval) + 1
        elif obligation.frequency == "weekly":
            days_diff = (d - obligation.start_date).days
            return (days_diff // (7 * interval)) + 1
        elif obligation.frequency == "biweekly":
            days_diff = (d - obligation.start_date).days
            return (days_diff // (14 * interval)) + 1
        elif obligation.frequency == "daily":
            days_diff = (d - obligation.start_date).days
            return (days_diff // interval) + 1
        return 1

    async def _ensure_period_exists(
        self, obligation: Obligation, sequence_number: int, current_date: date
    ) -> ObligationPeriod:
        p_start, p_end, p_due = calculate_period_bounds(obligation, sequence_number)
        p_key = generate_period_key(obligation.frequency, p_start, sequence_number)

        stmt = select(ObligationPeriod).where(
            ObligationPeriod.obligation_id == obligation.id, ObligationPeriod.period_key == p_key
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        is_fixed = obligation.payment_mode in ("fixed", "partial_allowed", "fixed_full_payment")
        amount = obligation.base_amount if is_fixed else None
        status = "pending_payment" if is_fixed else "pending_amount_definition"

        if status == "pending_payment" and p_due < current_date:
            status = "overdue"

        new_period = ObligationPeriod(
            obligation_id=obligation.id,
            period_key=p_key,
            sequence_number=sequence_number,
            start_date=p_start,
            end_date=p_end,
            due_date=p_due,
            amount=amount,
            currency=obligation.currency,
            status=status,
        )
        self.session.add(new_period)
        await self.session.flush()
        return new_period
