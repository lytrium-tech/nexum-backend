from datetime import date, datetime, UTC
from decimal import Decimal
import uuid

import pytest
from pydantic import ValidationError
from app.obligations.schemas import ObligationPeriodRead, ObligationPaymentRead

def test_create_period_amount_null_pending_definition():
    # 4. Se puede crear obligation_period con amount null y status pending_amount_definition
    payload = {
        "id": uuid.uuid4(),
        "obligation_id": uuid.uuid4(),
        "period_key": "2026-07",
        "sequence_number": 1,
        "start_date": date(2026, 7, 1),
        "end_date": date(2026, 7, 31),
        "due_date": date(2026, 7, 15),
        "amount": None,
        "currency": "COP",
        "paid_amount": "0.00",
        "status": "pending_amount_definition",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC)
    }
    period = ObligationPeriodRead(**payload)
    assert period.amount is None
    assert period.status == "pending_amount_definition"

def test_create_period_amount_defined_pending_payment():
    # 5. Se puede crear obligation_period fixed con amount definido y status pending_payment
    payload = {
        "id": uuid.uuid4(),
        "obligation_id": uuid.uuid4(),
        "period_key": "2026-08",
        "sequence_number": 2,
        "start_date": date(2026, 8, 1),
        "end_date": date(2026, 8, 31),
        "due_date": date(2026, 8, 15),
        "amount": "100.00",
        "currency": "COP",
        "paid_amount": "0.00",
        "status": "pending_payment",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC)
    }
    period = ObligationPeriodRead(**payload)
    assert period.amount == Decimal("100.00")
    assert period.status == "pending_payment"

def test_obligation_payment_saves_amounts_correctly():
    # 9. obligation_payment guarda amount y source_amount correctamente.
    payload = {
        "id": uuid.uuid4(),
        "obligation_id": uuid.uuid4(),
        "obligation_period_id": uuid.uuid4(),
        "account_id": uuid.uuid4(),
        "financial_event_id": None,
        "amount": "100.00",
        "currency": "COP",
        "source_amount": "25.00",
        "source_currency": "USD",
        "fx_rate": "4.00",
        "rate_source": "test",
        "rate_timestamp": None,
        "is_estimated": False,
        "paid_at": datetime.now(UTC),
        "created_at": datetime.now(UTC)
    }
    payment = ObligationPaymentRead(**payload)
    assert payment.amount == Decimal("100.00")
    assert payment.source_amount == Decimal("25.00")
