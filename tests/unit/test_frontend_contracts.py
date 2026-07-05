import json
import uuid
from decimal import Decimal

from app.credit.schemas import CreditCardRead
from app.goals.schemas import GoalRead
from app.intelligence.schemas import IntelligenceSnapshotRead, SnapshotTruth
from app.obligations.schemas import ObligationPeriodRead, ObligationRead


def test_goal_read_contract():
    fields = GoalRead.model_fields.keys() | GoalRead.model_computed_fields.keys()
    assert "remaining_required_this_period" in fields


def test_obligation_read_contract():
    fields = ObligationRead.model_fields.keys() | ObligationRead.model_computed_fields.keys()
    assert "status" in fields
    assert "payment_mode" in fields
    assert "frequency" in fields


def test_obligation_period_read_contract():
    fields = ObligationPeriodRead.model_fields.keys()
    assert "amount" in fields
    assert "paid_amount" in fields
    assert "status" in fields


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
        currency="COP",
        is_active=True,
        current_debt=Decimal("500.00"),
        total_debt=Decimal("500.00"),
        billed_debt=Decimal("200.00"),
        unbilled_debt=Decimal("300.00"),
        available_credit=Decimal("500.00"),
        payment_required=Decimal("200.00"),
        next_payment_estimate=Decimal("100.00"),
        estimated_current_debt=Decimal("500.00"),
        monthly_cc_payment=Decimal("100.00"),
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


def test_openapi_contract_v15_v17():
    from app.main import app
    
    spec = app.openapi()

    schemas = spec.get("components", {}).get("schemas", {})

    # V1.5 Legacy Schemas Validation
    goal_props = schemas.get("GoalRead", {}).get("properties", {})
    assert "remaining_required_this_period" in goal_props

    obs_props = schemas.get("ObligationRead", {}).get("properties", {})
    assert "status" in obs_props
    assert "payment_mode" in obs_props

    period_props = schemas.get("ObligationPeriodRead", {}).get("properties", {})
    assert "amount" in period_props
    assert "paid_amount" in period_props
    assert "status" in period_props

    cc_props = schemas.get("CreditCardRead", {}).get("properties", {})
    assert "current_debt" in cc_props
    assert "total_debt" in cc_props
    assert "estimated_current_debt" in cc_props
    assert "billed_debt" in cc_props
    assert "unbilled_debt" in cc_props

    truth_props = schemas.get("SnapshotTruth", {}).get("properties", {})
    assert "free_money" in truth_props

    # V1.7 New Schemas Validation
    assert "ObligationV17Response" in schemas
    assert "ObligationPeriodV17Response" in schemas
    assert "ObligationPaymentV17Response" in schemas
    assert "EmptyStateResponse" in schemas
    assert "ApiErrorResponse" in schemas
    assert "ObligationV17CreateRequest" in schemas

    # Routes Validation
    paths = spec.get("paths", {})
    
    # Check V1.5 routes still exist
    assert "/api/v1/obligations" in paths
    
    # Check V1.7 routes exist
    assert "/api/v1.7/obligations" in paths
    assert "/api/v1.7/obligations/{obligation_id}" in paths
    assert "/api/v1.7/obligations/{obligation_id}/periods" in paths
