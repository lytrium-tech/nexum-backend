from unittest.mock import AsyncMock

import pytest

from tests.unit.postgres_helpers import GateCleanupError, GateFixture


@pytest.mark.asyncio
async def test_gate_fixture_cleanup_success():
    """Test A: Normal cleanup succeeds silently and commits."""
    mock_session = AsyncMock()
    fixture = GateFixture(mock_session)

    # Should not raise any error
    await fixture.cleanup()

    assert mock_session.rollback.call_count == 1  # initial rollback
    assert mock_session.execute.call_count == 9  # 9 cleanup stages
    assert mock_session.commit.call_count == 1


@pytest.mark.asyncio
async def test_gate_fixture_cleanup_failure_propagates():
    """Test B: Failure of a stage propagates GateCleanupError."""
    mock_session = AsyncMock()
    # Make execute fail on the second call
    mock_session.execute.side_effect = [
        None,
        RuntimeError("DB delete constraint violated"),
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    ]
    fixture = GateFixture(mock_session)

    with pytest.raises(GateCleanupError) as exc_info:
        await fixture.cleanup()

    err = exc_info.value
    assert len(err.errors) == 1
    assert err.errors[0][0] == "obligation_payments"
    assert "DB delete constraint violated" in str(err)
    # Commit should NOT be called on error
    assert mock_session.commit.call_count == 0
    # Rollback should be called initially and for error recovery
    assert mock_session.rollback.call_count == 2


@pytest.mark.asyncio
async def test_gate_fixture_cleanup_continues_all_stages_on_failure():
    """Test C: Even when an early stage fails, subsequent stages are still attempted."""
    mock_session = AsyncMock()
    # First stage fails, remaining stages succeed
    mock_session.execute.side_effect = [
        RuntimeError("First stage failure"),
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    ]
    fixture = GateFixture(mock_session)

    with pytest.raises(GateCleanupError) as exc_info:
        await fixture.cleanup()

    err = exc_info.value
    assert len(err.errors) == 1
    assert err.errors[0][0] == "goal_transactions"
    # Verify all 9 stages were attempted
    assert mock_session.execute.call_count == 9
    assert mock_session.commit.call_count == 0
    assert mock_session.rollback.call_count == 2
