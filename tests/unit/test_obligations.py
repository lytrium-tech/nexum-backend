from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.obligations.schemas import ObligationCreate


def test_create_monthly_fixed_obligation():
    # 1. Se puede crear una obligation fija mensual.
    payload = {
        "name": "Test Fixed Monthly",
        "currency": "COP",
        "type": "indefinite",
        "frequency": "monthly",
        "payment_mode": "fixed",
        "base_amount": "100.00",
        "start_date": date(2026, 7, 1),
        "first_due_date": date(2026, 7, 15),
        "due_day": 15
    }
    obl = ObligationCreate(**payload)
    assert obl.name == "Test Fixed Monthly"
    assert obl.base_amount == Decimal("100.00")

def test_create_variable_monthly_obligation():
    # 2. Se puede crear una obligation variable mensual con amount/base nullable
    payload = {
        "name": "Test Variable Monthly",
        "currency": "COP",
        "type": "indefinite",
        "frequency": "monthly",
        "payment_mode": "variable",
        "base_amount": None,
        "start_date": date(2026, 7, 1),
        "first_due_date": date(2026, 7, 15),
        "due_day": 15
    }
    obl = ObligationCreate(**payload)
    assert obl.base_amount is None

def test_create_one_time_obligation():
    # 3. Se puede crear obligation one_time.
    payload = {
        "name": "Test One Time",
        "currency": "USD",
        "type": "one_time",
        "frequency": "one_time",
        "payment_mode": "fixed",
        "base_amount": "50.00",
        "start_date": date(2026, 7, 1),
        "first_due_date": date(2026, 7, 15),
        "due_day": None
    }
    obl = ObligationCreate(**payload)
    assert obl.frequency == "one_time"

def test_negative_base_amount_fails():
    payload = {
        "name": "Test Negative",
        "currency": "USD",
        "type": "one_time",
        "frequency": "one_time",
        "payment_mode": "fixed",
        "base_amount": "-50.00",
        "start_date": date(2026, 7, 1),
        "first_due_date": date(2026, 7, 15),
    }
    with pytest.raises(ValidationError):
        ObligationCreate(**payload)

def test_legacy_obligation_read_tolerates_null():
    import uuid
    from datetime import datetime
    from app.obligations.schemas import ObligationRead
    payload = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "name": "Test Nullable",
        "description": None,
        "category_id": None,
        "currency": "COP",
        "type": None,
        "frequency": "monthly",
        "payment_mode": "variable",
        "base_amount": None,
        "start_date": None,
        "first_due_date": None,
        "due_day": None,
        "due_month": None,
        "interval_count": 1,
        "end_date": None,
        "end_count": None,
        "status": None,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
        "metadata_": {}
    }
    obl = ObligationRead.model_validate(payload)
    assert obl.type is None
    assert obl.start_date is None
    assert obl.first_due_date is None
    assert obl.status is None
