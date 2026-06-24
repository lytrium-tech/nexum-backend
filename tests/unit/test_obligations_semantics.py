import uuid
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from freezegun import freeze_time

from app.obligations.schemas import ObligationRead


def test_paid_obligation_is_not_pending_in_same_period():
    o = ObligationRead(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Arriendo",
        amount=Decimal("1000.00"),
        payment_mode="fixed_full_payment",
        due_day=5,
        frequency="monthly",
        is_active=True,
        category_id=None,
        currency="COP",
        metadata_={},
        created_at=datetime.now(),
        updated_at=datetime.now(),
        paid_this_period=Decimal("1000.00"),
    )
    assert o.period_status == "paid"
    assert o.remaining_amount == Decimal("0.00")
    assert not o.is_pending


def test_overdue_obligation_after_due_day():
    now = datetime.now(ZoneInfo("America/Bogota"))
    past_due = now.date().day - 1
    if past_due < 1:
        # Can't test easily if it's the 1st of the month
        return

    o = ObligationRead(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Arriendo",
        amount=Decimal("1000.00"),
        payment_mode="fixed_full_payment",
        due_day=past_due,
        frequency="monthly",
        is_active=True,
        category_id=None,
        currency="COP",
        metadata_={},
        created_at=datetime.now(),
        updated_at=datetime.now(),
        paid_this_period=Decimal("0.00"),
    )
    assert o.period_status == "overdue"


def test_not_overdue_if_already_paid():
    now = datetime.now(ZoneInfo("America/Bogota"))
    past_due = now.date().day - 1
    if past_due < 1:
        return

    o = ObligationRead(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Arriendo",
        amount=Decimal("1000.00"),
        payment_mode="fixed_full_payment",
        due_day=past_due,
        frequency="monthly",
        is_active=True,
        category_id=None,
        currency="COP",
        metadata_={},
        created_at=datetime.now(),
        updated_at=datetime.now(),
        paid_this_period=Decimal("1000.00"),
    )
    assert o.period_status == "paid"
    assert o.remaining_amount == Decimal("0.00")


def test_creation_with_already_paid_this_period():
    now = datetime.now(ZoneInfo("America/Bogota"))
    current_period = f"{now.year}-{now.month:02d}"

    o = ObligationRead(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Arriendo",
        amount=Decimal("1000.00"),
        payment_mode="fixed_full_payment",
        due_day=5,
        frequency="monthly",
        is_active=True,
        category_id=None,
        currency="COP",
        metadata_={"skip_periods": [current_period]},
        created_at=datetime.now(),
        updated_at=datetime.now(),
        paid_this_period=Decimal("0.00"),
    )
    assert o.period_status == "covered"
    assert o.remaining_amount == Decimal("0.00")


@freeze_time("2026-06-15 12:00:00")
def test_active_obligation_pending_before_due_date():
    o = ObligationRead(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Arriendo",
        amount=Decimal("1000.00"),
        payment_mode="fixed_full_payment",
        due_day=20,
        frequency="monthly",
        is_active=True,
        category_id=None,
        currency="COP",
        metadata_={},
        created_at=datetime.now(),
        updated_at=datetime.now(),
        paid_this_period=Decimal("0.00"),
    )
    assert o.period_status == "pending"
    assert o.is_pending is True


@freeze_time("2026-06-25 12:00:00")
def test_active_obligation_overdue_after_due_date():
    o = ObligationRead(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Arriendo",
        amount=Decimal("1000.00"),
        payment_mode="fixed_full_payment",
        due_day=20,
        frequency="monthly",
        is_active=True,
        category_id=None,
        currency="COP",
        metadata_={},
        created_at=datetime.now(),
        updated_at=datetime.now(),
        paid_this_period=Decimal("0.00"),
    )
    assert o.period_status == "overdue"
    assert o.is_pending is True


@freeze_time("2026-06-15 12:00:00")
def test_partial_payment_returns_partial():
    o = ObligationRead(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Arriendo",
        amount=Decimal("1000.00"),
        payment_mode="partial_allowed",
        due_day=20,
        frequency="monthly",
        is_active=True,
        category_id=None,
        currency="COP",
        metadata_={},
        created_at=datetime.now(),
        updated_at=datetime.now(),
        paid_this_period=Decimal("500.00"),
    )
    assert o.period_status == "partial"
    assert o.is_pending is True


@freeze_time("2026-06-15 12:00:00")
def test_inactive_obligation_returns_inactive():
    o = ObligationRead(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Arriendo",
        amount=Decimal("1000.00"),
        payment_mode="fixed_full_payment",
        due_day=20,
        frequency="monthly",
        is_active=False,
        category_id=None,
        currency="COP",
        metadata_={},
        created_at=datetime.now(),
        updated_at=datetime.now(),
        paid_this_period=Decimal("0.00"),
    )
    assert o.period_status == "inactive"
    assert o.is_pending is False


@freeze_time("2026-06-15 12:00:00")
def test_due_date_and_days_calculated_correctly():
    o = ObligationRead(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Arriendo",
        amount=Decimal("1000.00"),
        payment_mode="fixed_full_payment",
        due_day=20,
        frequency="monthly",
        is_active=True,
        category_id=None,
        currency="COP",
        metadata_={},
        created_at=datetime.now(),
        updated_at=datetime.now(),
        paid_this_period=Decimal("0.00"),
    )
    assert o.next_due_date == "2026-06-20"
    assert o.days_until_due == 5


@freeze_time("2026-06-15 12:00:00")
def test_monthly_recurrence_reappears_next_period():
    o = ObligationRead(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Arriendo",
        amount=Decimal("1000.00"),
        payment_mode="fixed_full_payment",
        due_day=20,
        frequency="monthly",
        is_active=True,
        category_id=None,
        currency="COP",
        metadata_={"skip_periods": ["2026-05"]},
        created_at=datetime.now(),
        updated_at=datetime.now(),
        paid_this_period=Decimal("0.00"),
    )
    assert o.period_status == "pending"
