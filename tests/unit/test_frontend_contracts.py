import json
import uuid
from decimal import Decimal

from app.credit.schemas import CreditCardRead
from app.goals.schemas import GoalRead
from app.intelligence.schemas import IntelligenceSnapshotRead, SnapshotTruth
from app.obligations.schemas import ObligationRead


def test_goal_read_contract():
    fields = GoalRead.model_fields.keys() | GoalRead.model_computed_fields.keys()
    assert "remaining_required_this_period" in fields


def test_obligation_read_contract():
    fields = ObligationRead.model_fields.keys() | ObligationRead.model_computed_fields.keys()
    assert "remaining_amount" in fields
    assert "period_status" in fields
    assert "is_pending" in fields


def test_credit_card_read_contract():
    fields = CreditCardRead.model_fields.keys() | CreditCardRead.model_computed_fields.keys()
    assert "current_debt" in fields
    assert "total_debt" in fields
    assert "estimated_current_debt" in fields
    assert "billed_debt" in fields
    assert "unbilled_debt" in fields
    assert "payment_required" in fields
    assert "next_payment_estimate" in fields
    assert "statement_balance" in fields

    card = CreditCardRead(
        id=uuid.uuid4(),
        name="Test Card",
        bank="Test Bank",
        credit_limit=Decimal("1000.00"),
        cutoff_day=15,
        due_day=30,
        is_active=True,
        current_debt=Decimal("500.00"),
        total_debt=Decimal("500.00"),
        billed_debt=Decimal("200.00"),
        unbilled_debt=Decimal("300.00"),
        available_credit=Decimal("500.00"),
        payment_required=Decimal("200.00"),
        next_payment_estimate=Decimal("100.00"),
        estimated_current_debt=Decimal("500.00"),
        monthly_cc_payment=Decimal("100.00")
    )
    assert card.total_debt == card.current_debt
    assert card.estimated_current_debt == card.current_debt
    assert card.payment_required == card.billed_debt
    assert card.statement_balance is None


def test_snapshot_truth_contract():
    fields = IntelligenceSnapshotRead.model_fields.keys()
    assert "truth" in fields
    truth_fields = SnapshotTruth.model_fields.keys()
    assert "free_money" in truth_fields


def test_openapi_contains_v11_fields():
    with open("openapi.json", "r") as f:
        spec = json.load(f)

    schemas = spec.get("components", {}).get("schemas", {})

    goal_props = schemas.get("GoalRead", {}).get("properties", {})
    assert "remaining_required_this_period" in goal_props

    obs_props = schemas.get("ObligationRead", {}).get("properties", {})
    assert "remaining_amount" in obs_props
    assert "period_status" in obs_props
    assert "is_pending" in obs_props

    cc_props = schemas.get("CreditCardRead", {}).get("properties", {})
    assert "current_debt" in cc_props
    assert "total_debt" in cc_props
    assert "estimated_current_debt" in cc_props
    assert "billed_debt" in cc_props
    assert "unbilled_debt" in cc_props

    truth_props = schemas.get("SnapshotTruth", {}).get("properties", {})
    assert "free_money" in truth_props
