from datetime import date, timedelta


def calculate_credit_card_dates(
    current_date: date, cutoff_day: int, due_day: int
) -> tuple[date, date, date]:
    """
    Calculates the current cycle start, cycle end, and next payment due date
    based on the current date, cutoff_day, and due_day.
    """
    # 1. Identify the most recent past or current cutoff date
    if current_date.day <= cutoff_day:
        # We are before or on the cutoff day this month. The current cycle ends this month.
        try:
            cycle_end = current_date.replace(day=cutoff_day)
        except ValueError:
            # E.g. Feb 30 -> handle by clamping to month end
            import calendar

            last_day = calendar.monthrange(current_date.year, current_date.month)[1]
            cycle_end = current_date.replace(day=min(cutoff_day, last_day))

        start_month = cycle_end.month - 1
        start_year = cycle_end.year
        if start_month == 0:
            start_month = 12
            start_year -= 1
        import calendar

        last_day_start = calendar.monthrange(start_year, start_month)[1]
        actual_cutoff = min(cutoff_day, last_day_start)
        cycle_start = date(start_year, start_month, actual_cutoff) + timedelta(days=1)
    else:
        # We are past the cutoff day this month. The current cycle ends next month.
        import calendar

        last_day = calendar.monthrange(current_date.year, current_date.month)[1]
        cycle_start = date(
            current_date.year, current_date.month, min(cutoff_day, last_day)
        ) + timedelta(days=1)

        end_month = current_date.month + 1
        end_year = current_date.year
        if end_month == 13:
            end_month = 1
            end_year += 1
        last_day_end = calendar.monthrange(end_year, end_month)[1]
        cycle_end = date(end_year, end_month, min(cutoff_day, last_day_end))

    # 2. Next payment due date
    # Usually the due date is after the cycle end.
    # If due_day > cutoff_day, it's usually in the same month as cycle_end.
    # If due_day < cutoff_day, it's usually in the month after cycle_end.
    due_month = cycle_end.month
    due_year = cycle_end.year
    if due_day < cutoff_day:
        due_month += 1
        if due_month == 13:
            due_month = 1
            due_year += 1

    import calendar

    last_day_due = calendar.monthrange(due_year, due_month)[1]
    next_due = date(due_year, due_month, min(due_day, last_day_due))

    # If the due date has already passed, the NEXT due date is one month later
    if current_date > next_due:
        due_month += 1
        if due_month == 13:
            due_month = 1
            due_year += 1
        last_day_due_next = calendar.monthrange(due_year, due_month)[1]
        next_due = date(due_year, due_month, min(due_day, last_day_due_next))

    return cycle_start, cycle_end, next_due
