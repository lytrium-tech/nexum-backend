from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext

ESTIMATOR_VERSION = "nexum_interest_v1"


@dataclass(frozen=True)
class PrincipalChange:
    effective_date: date
    amount: Decimal
    zero_interest: bool = False
    event_type: str = "other"
    order_priority: int = 50
    identifier: str | None = None


def monthly_rate_decimal(monthly_interest_rate_pct: Decimal) -> Decimal:
    """Monthly interest rate percentage to rate decimal (applied exactly once)."""
    return monthly_interest_rate_pct / Decimal("100")


def daily_interest_rate(monthly_interest_rate_pct: Decimal) -> Decimal:
    """Daily compound rate: (1 + monthly_rate_decimal) ** (1 / 30) - 1."""
    monthly_dec = monthly_rate_decimal(monthly_interest_rate_pct)
    return (Decimal(1) + monthly_dec) ** (Decimal(1) / Decimal(30)) - Decimal(1)


def build_interest_bearing_principal_timeline(
    changes: Iterable[PrincipalChange],
    period_start: date,
    period_end: date,
) -> list[tuple[date, Decimal]]:
    """Builds a daily (date, interest_bearing_principal) timeline for half-open interval [period_start, period_end).

    Deterministic ordering for same-date events:
      Priority 10: opening_balance (+ principal)
      Priority 20: purchase (+ principal)
      Priority 30: reversal (+ restored principal)
      Priority 40: payment allocation (- principal)
      Priority 50: refund / adjustment (- principal)
      Priority 60: other
    """
    if period_end < period_start:
        raise ValueError("period_end must be on or after period_start")

    events = sorted(
        changes,
        key=lambda item: (
            item.effective_date,
            item.order_priority,
            str(item.identifier or ""),
        ),
    )

    balance = Decimal("0.00")
    cursor = 0
    # Apply all financial facts strictly prior to period_start to establish initial exposure
    while cursor < len(events) and events[cursor].effective_date < period_start:
        event = events[cursor]
        if not event.zero_interest:
            balance += event.amount
        cursor += 1

    timeline: list[tuple[date, Decimal]] = []
    day = period_start
    while day < period_end:
        while cursor < len(events) and events[cursor].effective_date <= day:
            event = events[cursor]
            if not event.zero_interest:
                balance += event.amount
            cursor += 1
        daily_principal = max(Decimal("0.00"), balance)
        timeline.append((day, daily_principal))
        day += timedelta(days=1)

    return timeline


def estimate_interest(
    changes: Iterable[PrincipalChange],
    monthly_interest_rate_pct: Decimal,
    period_start: date,
    period_end: date,
) -> Decimal:
    """Daily accrual on the dated Nexum-modeled, interest-bearing principal basis."""
    if period_end < period_start:
        raise ValueError("period_end must be on or after period_start")
    if monthly_interest_rate_pct <= Decimal("0.00") or period_start == period_end:
        return Decimal("0.00")

    with localcontext() as context:
        context.prec = 28
        daily_rate = daily_interest_rate(monthly_interest_rate_pct)
        timeline = build_interest_bearing_principal_timeline(changes, period_start, period_end)
        total = Decimal("0.00")
        for _day, daily_principal in timeline:
            total += daily_principal * daily_rate
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
