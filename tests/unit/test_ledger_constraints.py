import re
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.intelligence.repository import IntelligenceRepository
from app.ledger.enums import EventType
from app.ledger.models import (
    FINANCIAL_EVENT_TYPE_CHECK_SQL,
    FINANCIAL_EVENT_TYPES,
    FinancialEvent,
)


def _constraint_values(sql: str) -> tuple[str, ...]:
    return tuple(re.findall(r"'([^']+)'", sql))


def test_ledger_model_uses_the_complete_canonical_event_type_list():
    model_constraint = next(
        constraint
        for constraint in FinancialEvent.__table_args__
        if getattr(constraint, "name", None) == "financial_events_type_check"
    )

    assert FINANCIAL_EVENT_TYPES == tuple(event_type.value for event_type in EventType)
    assert _constraint_values(model_constraint.sqltext.text) == FINANCIAL_EVENT_TYPES
    assert model_constraint.sqltext.text == FINANCIAL_EVENT_TYPE_CHECK_SQL


def test_ledger_model_preserves_pre_goals_event_types():
    expected_pre_goals_types = {
        "income",
        "expense",
        "credit_card_purchase",
        "credit_card_payment",
        "obligation_payment",
        "goal_contribution",
        "manual_adjustment",
        "transfer_out",
        "transfer_in",
        "opening_balance",
    }

    assert expected_pre_goals_types < set(FINANCIAL_EVENT_TYPES)


def test_ledger_model_adds_balance_adjustment():
    assert FINANCIAL_EVENT_TYPES.count("balance_adjustment") == 1


def test_ledger_model_does_not_admit_unknown_event_types():
    assert "unknown_type" not in FINANCIAL_EVENT_TYPES
    assert set(FINANCIAL_EVENT_TYPES) == {event_type.value for event_type in EventType}


@pytest.mark.asyncio
async def test_balance_adjustment_is_excluded_from_intelligence_cashflow():
    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = {}
    session.execute.return_value = result
    repository = IntelligenceRepository(session)
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 8, 1, tzinfo=UTC)

    await repository.get_cashflow_metrics(uuid.uuid4(), start, end)
    current_query = str(session.execute.call_args[0][0])
    await repository.get_historical_cashflow_metrics(uuid.uuid4(), start)
    historical_query = str(session.execute.call_args[0][0])

    assert "balance_adjustment" not in current_query
    assert "balance_adjustment" not in historical_query


@pytest.mark.asyncio
async def test_only_legacy_outflow_goal_contributions_enter_cashflow():
    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = {}
    session.execute.return_value = result
    repository = IntelligenceRepository(session)
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 8, 1, tzinfo=UTC)

    await repository.get_cashflow_metrics(uuid.uuid4(), start, end)
    current_query = str(session.execute.call_args[0][0])
    await repository.get_historical_cashflow_metrics(uuid.uuid4(), start)
    historical_query = str(session.execute.call_args[0][0])

    expected_filter = "event_type = 'goal_contribution' AND direction = 'outflow'"
    assert expected_filter in current_query
    assert expected_filter in historical_query
