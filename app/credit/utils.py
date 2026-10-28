import calendar
from datetime import date, timedelta

from app.credit.exceptions import CreditLifecycleInvalidityError

VALID_LIFECYCLE_STATUSES = {"pending", "posted", "reversed"}
VALID_LIFECYCLE_TRANSITIONS = {
    "pending": {"posted", "reversed"},
    "posted": {"reversed"},
    "reversed": set(),
}


def validate_lifecycle_status(status: str) -> None:
    """Validate that the lifecycle status is one of the supported domain states."""
    if status not in VALID_LIFECYCLE_STATUSES:
        raise CreditLifecycleInvalidityError(
            message=f"Invalid lifecycle status: {status}. Allowed statuses: {sorted(VALID_LIFECYCLE_STATUSES)}",
            detail={"status": status, "allowed": sorted(VALID_LIFECYCLE_STATUSES)},
        )


def validate_lifecycle_transition(current_status: str, new_status: str) -> None:
    """Validate that a lifecycle state transition is legally permissible."""
    validate_lifecycle_status(new_status)
    if current_status == new_status:
        return
    allowed = VALID_LIFECYCLE_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        raise CreditLifecycleInvalidityError(
            message=f"Invalid lifecycle transition from '{current_status}' to '{new_status}'",
            detail={"from": current_status, "to": new_status, "allowed": sorted(allowed)},
        )


def get_cycle_dates_for_period(
    year: int, month: int, cutoff_day: int, due_day: int
) -> tuple[date, date, date]:
    """Calculates canonical boundaries (start_date, cutoff_date, due_date) for a given cycle.

    - cutoff_date: cutoff_day clamped to the last day of the cycle month.
    - start_date: day after the previous month's cutoff date.
    - due_date: due_day clamped to the last day of the due month.
      If due_day > cutoff_day, due month is the cycle month.
      If due_day <= cutoff_day, due month is the following month.
    """
    last_day = calendar.monthrange(year, month)[1]
    cutoff_date = date(year, month, min(cutoff_day, last_day))

    prev_year = year if month > 1 else year - 1
    prev_month = month - 1 if month > 1 else 12
    prev_last_day = calendar.monthrange(prev_year, prev_month)[1]
    prev_cutoff = date(prev_year, prev_month, min(cutoff_day, prev_last_day))
    start_date = prev_cutoff + timedelta(days=1)

    if due_day > cutoff_day:
        due_year = year
        due_month = month
    else:
        due_year = year if month < 12 else year + 1
        due_month = month + 1 if month < 12 else 1
    due_last_day = calendar.monthrange(due_year, due_month)[1]
    due_date = date(due_year, due_month, min(due_day, due_last_day))

    return start_date, cutoff_date, due_date


def determine_cycle_for_date(
    target_date: date, cutoff_day: int, due_day: int
) -> tuple[str, date, date, date]:
    """Determines the canonical billing cycle for a purchase on target_date.

    Returns (billing_period, start_date, cutoff_date, due_date).
    - If target_date <= this_month_cutoff: belongs to cycle for this calendar month.
    - If target_date > this_month_cutoff: belongs to cycle for the following calendar month.
    """
    year = target_date.year
    month = target_date.month
    last_day = calendar.monthrange(year, month)[1]
    this_month_cutoff = date(year, month, min(cutoff_day, last_day))

    if target_date <= this_month_cutoff:
        target_year = year
        target_month = month
    else:
        target_year = year if month < 12 else year + 1
        target_month = month + 1 if month < 12 else 1

    billing_period = f"{target_year:04d}-{target_month:02d}"
    start_date, cutoff_date, due_date = get_cycle_dates_for_period(
        target_year, target_month, cutoff_day, due_day
    )
    return billing_period, start_date, cutoff_date, due_date


def calculate_credit_card_dates(
    current_date: date, cutoff_day: int, due_day: int
) -> tuple[date, date, date]:
    """Calculates current cycle start, cycle end, and next payment due date

    based on current_date, cutoff_day, and due_day.
    Preserves exact compatibility with legacy and test callers.
    """
    _, start_date, cutoff_date, due_date = determine_cycle_for_date(
        current_date, cutoff_day, due_day
    )
    return start_date, cutoff_date, due_date
