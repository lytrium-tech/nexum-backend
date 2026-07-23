from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from freezegun import freeze_time

from app.goals.schemas import GoalRead


def _make_goal(
    currency: str,
    target_amount: str,
    current_amount: str,
    target_date: date | None = None,
    contributed_this_period: str = "0.00",
    status: str = "active",
) -> GoalRead:
    return GoalRead(
        id=uuid4(),
        name="Test Goal",
        target_amount=Decimal(target_amount),
        current_amount=Decimal(current_amount),
        target_date=target_date,
        currency=currency,
        status=status,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        contributed_this_period=Decimal(contributed_this_period),
    )


def test_cop_rounding_up():
    today = datetime.now(UTC).date()
    target_date = today + timedelta(days=90)
    # 100,000 / 3 months = 33,333.33 => should round UP to 33,350
    goal = _make_goal("COP", "100000.00", "0.00", target_date)
    assert goal.monthly_required == Decimal("33350.00")
    assert goal.required_this_period == Decimal("33350.00")


def test_usd_rounding_up():
    today = datetime.now(UTC).date()
    target_date = today + timedelta(days=90)
    # 100 / 3 = 33.3333 => should round UP to 33.34
    goal = _make_goal("USD", "100.00", "0.00", target_date)
    assert goal.monthly_required == Decimal("33.34")


def test_eur_rounding_up():
    today = datetime.now(UTC).date()
    target_date = today + timedelta(days=90)
    # 100 / 3 = 33.3333 => should round UP to 33.34
    goal = _make_goal("EUR", "100.00", "0.00", target_date)
    assert goal.monthly_required == Decimal("33.34")


def test_unknown_currency_fallback_works():
    today = datetime.now(UTC).date()
    target_date = today + timedelta(days=90)
    # 100 / 3 = 33.3333 => fallback is 0.01 so 33.34
    goal = _make_goal("XYZ", "100.00", "0.00", target_date)
    assert goal.monthly_required == Decimal("33.34")


def test_remaining_required_this_period_no_invisible_cents():
    today = datetime.now(UTC).date()
    target_date = today + timedelta(days=90)
    # 100 USD / 3 = 33.34 monthly required.
    # Contributed 33.34. Remaining should be 0.
    goal = _make_goal("USD", "100.00", "33.34", target_date, contributed_this_period="33.34")
    assert goal.remaining_required_this_period == Decimal("0.00")
    assert goal.period_status == "covered"


def test_flexible_goal_has_no_required_contribution():
    goal = _make_goal("COP", "100000.00", "0.00", target_date=None)
    assert goal.is_flexible is True
    assert goal.monthly_required == Decimal("0.00")
    assert goal.required_this_period == Decimal("0.00")
    assert goal.remaining_required_this_period == Decimal("0.00")
    assert goal.period_status == "flexible"


def test_completed_goal_period_status_is_not_pending():
    today = datetime.now(UTC).date()
    target_date = today + timedelta(days=90)
    goal = _make_goal("COP", "100000.00", "100000.00", target_date, status="completed")
    assert goal.period_status == "completed"
    assert goal.required_this_period == Decimal("0.00")


def test_overfunded_goal_period_status_works():
    today = datetime.now(UTC).date()
    target_date = today + timedelta(days=90)
    goal = _make_goal(
        "COP", "100000.00", "50000.00", target_date, contributed_this_period="40000.00"
    )
    assert goal.period_status == "overfunded"


def test_daily_required_this_period_is_rounded_upward():
    today = datetime.now(UTC).date()
    target_date = today + timedelta(days=90)
    goal = _make_goal("COP", "100000.00", "0.00", target_date)
    # monthly_required = 33350
    # daily_required = 33350 / days_remaining (e.g., 10) = 3335. -> rounds up to 3350
    # Let's mock days_remaining_in_period to 10
    days = goal.days_remaining_in_period
    assert days > 0
    # Expected daily:
    expected_daily = (Decimal("33350.00") / Decimal(str(days))).quantize(
        Decimal("1"), rounding="ROUND_CEILING"
    )
    # Actually currency round up rounds up to next multiple of 50
    expected_daily = ((Decimal("33350.00") / Decimal(str(days))) / Decimal("50")).quantize(
        Decimal("1"), rounding="ROUND_CEILING"
    ) * Decimal("50")

    assert goal.daily_required_this_period == expected_daily


@freeze_time("2026-06-01 12:00:00")
def test_monthly_goal_pending_at_start_of_new_period():
    target_date = date(2026, 12, 31)
    goal = _make_goal("USD", "1000.00", "400.00", target_date, contributed_this_period="0.00")
    assert goal.period_status == "pending"


@freeze_time("2026-06-01 12:00:00")
def test_monthly_goal_completed_for_period_after_contribution():
    target_date = date(2026, 12, 31)
    goal = _make_goal("USD", "1000.00", "400.00", target_date, contributed_this_period="100.00")
    assert goal.period_status in ["covered", "overfunded"]


@freeze_time("2026-06-01 12:00:00")
def test_overdue_goal_returns_overdue_if_target_date_passed():
    target_date = date(2026, 5, 31)
    goal = _make_goal("USD", "1000.00", "400.00", target_date, contributed_this_period="0.00")
    assert goal.period_status == "overdue"


@freeze_time("2026-06-01 12:00:00")
def test_overdue_goal_does_not_double_discount_current_period_contribution():
    target_date = date(2026, 5, 31)
    goal = _make_goal(
        "USD",
        "1000.00",
        "400.00",
        target_date,
        contributed_this_period="100.00",
    )

    assert goal.monthly_required == Decimal("700.00")
    assert goal.remaining_required_this_period == Decimal("600.00")
