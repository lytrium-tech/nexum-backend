import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.intelligence.repository import IntelligenceRepository


@pytest.mark.asyncio
async def test_get_obligations_metrics_v16_model():
    session_mock = AsyncMock()

    class MockRow:
        def __init__(self, pending_count, pending_amount):
            self.pending_count = pending_count
            self.pending_amount = pending_amount

    class MockMapping:
        def first(self):
            return {"pending_count": 2, "pending_amount": Decimal("150.00")}

    class MockResult:
        def mappings(self):
            return MockMapping()

    session_mock.execute.return_value = MockResult()

    repo = IntelligenceRepository(session_mock)
    res = await repo.get_obligations_metrics(uuid.uuid4(), "2026-07")

    # Verify snapshot no consulta obligations.amount ni usa vista legacy
    query_str = str(session_mock.execute.call_args[0][0])
    assert "obligations.amount" not in query_str
    assert "v_pending_obligations_current_month" not in query_str
    assert "obligation_periods op" in query_str

    assert res["pending_count"] == 2
    assert res["pending_amount"] == Decimal("150.00")


@pytest.mark.asyncio
async def test_get_pending_obligations_v16_model():
    session_mock = AsyncMock()

    class MockRow:
        def __init__(self, obligation_id, name, amount, due_day, frequency, is_pending):
            self.obligation_id = obligation_id
            self.name = name
            self.amount = amount
            self.due_day = due_day
            self.frequency = frequency
            self.is_pending = is_pending
        
        def keys(self):
            return ["obligation_id", "name", "amount", "due_day", "frequency", "is_pending"]
            
        def __getitem__(self, item):
            return getattr(self, item)
            
        def __iter__(self):
            for k in self.keys():
                yield k, getattr(self, k)

    class MockMapping:
        def all(self):
            return [
                MockRow(uuid.uuid4(), "Fixed Obligation", Decimal("100.00"), 15, "monthly", True),
                MockRow(uuid.uuid4(), "Partial Obligation", Decimal("50.00"), 15, "monthly", True),
                MockRow(uuid.uuid4(), "Variable Null Obligation", Decimal("0.00"), 15, "monthly", True)
            ]

    class MockResult:
        def mappings(self):
            return MockMapping()

    session_mock.execute.return_value = MockResult()

    repo = IntelligenceRepository(session_mock)
    res = await repo.get_pending_obligations(uuid.uuid4())

    query_str = str(session_mock.execute.call_args[0][0])
    
    # get_pending_obligations no usa vista legacy
    assert "v_pending_obligations_current_month" not in query_str
    assert "obligation_periods" in query_str
    
    # Exclude paid, skipped, cancelled by using IN
    assert "pending_payment" in query_str
    assert "partially_paid" in query_str
    assert "overdue" in query_str

    assert len(res) == 3
    assert res[0]["name"] == "Fixed Obligation"
    assert res[1]["amount"] == Decimal("50.00")


@pytest.mark.asyncio
async def test_get_obligations_metrics_currency_safety():
    from app.intelligence.repository import IntelligenceRepository
    from unittest.mock import AsyncMock
    import uuid

    mock_db = AsyncMock()
    repo = IntelligenceRepository(mock_db)

    # Let's just execute the method and ensure the query contains the currency safety patch
    from unittest.mock import MagicMock
    mock_result = MagicMock()
    mock_result.mappings.return_value.first.return_value = {"pending_count": 1, "pending_amount": 100}
    mock_db.execute.return_value = mock_result

    res = await repo.get_obligations_metrics(uuid.uuid4(), "2026-07")
    assert res["pending_amount"] == 100
    
    # Extract query
    call_args = mock_db.execute.call_args
    query_text = str(call_args[0][0])
    assert "o.currency = 'COP'" in query_text

@pytest.mark.asyncio
async def test_get_pending_obligations_pending_definition_safety():
    from app.intelligence.repository import IntelligenceRepository
    from unittest.mock import AsyncMock
    import uuid

    mock_db = AsyncMock()
    repo = IntelligenceRepository(mock_db)

    from unittest.mock import MagicMock
    mock_result = MagicMock()
    # Mock returning one normal and one pending_amount_definition
    mock_result.mappings.return_value.all.return_value = [
        {"obligation_id": uuid.uuid4(), "amount": 100},
        {"obligation_id": uuid.uuid4(), "amount": 0} # Pydantic expects Decimal or 0, our COALESCE gives 0
    ]
    mock_db.execute.return_value = mock_result

    res = await repo.get_pending_obligations(uuid.uuid4())
    assert len(res) == 2
    
    # Extract query
    call_args = mock_db.execute.call_args
    query_text = str(call_args[0][0])
    assert "pending_amount_definition" in query_text
    assert "COALESCE(op.amount" in query_text
