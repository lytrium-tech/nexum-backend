from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.unit.postgres_helpers import GateCleanupError, GateFixture


@pytest.mark.asyncio
async def test_gate_fixture_cleanup_success():
    """Test A: Normal cleanup succeeds silently and commits all 11 stages plus 11 residue checks."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 0
    mock_session.execute.return_value = mock_result

    fixture = GateFixture(mock_session)
    await fixture.cleanup()

    assert mock_session.rollback.call_count == 1  # initial rollback
    assert mock_session.execute.call_count == 22  # 11 delete stages + 11 residue checks
    assert mock_session.commit.call_count == 1


@pytest.mark.asyncio
async def test_gate_fixture_cleanup_failure_propagates():
    """Test B: Failure of a stage propagates GateCleanupError with stage and type only, excluding secrets."""
    mock_session = AsyncMock()
    fake_secret = (
        "FATAL: password='super_secret_db_password_123' token='ghp_secret_token_abc' rejected"
    )
    # Make execute fail on the second call (obligation_auto_payment_configs)
    mock_session.execute.side_effect = [
        MagicMock(),  # 1: obligation_auto_payment_runs
        RuntimeError(fake_secret),  # 2: obligation_auto_payment_configs
        MagicMock(),  # 3: goal_transactions
        MagicMock(),  # 4: obligation_payments
        MagicMock(),  # 5: obligation_periods
        MagicMock(),  # 6: obligations
        MagicMock(),  # 7: financial_events
        MagicMock(),  # 8: goals
        MagicMock(),  # 9: accounts
        MagicMock(),  # 10: fx_quotes
        MagicMock(),  # 11: users
    ]
    fixture = GateFixture(mock_session)

    with pytest.raises(GateCleanupError) as exc_info:
        await fixture.cleanup()

    err = exc_info.value
    assert len(err.errors) == 1
    assert err.errors[0][0] == "obligation_auto_payment_configs"
    # Stage name and exception type are reported
    assert "[obligation_auto_payment_configs] RuntimeError" in str(err)
    # Raw error text and secrets MUST NOT leak into exception message
    assert "super_secret_db_password_123" not in str(err)
    assert "ghp_secret_token_abc" not in str(err)
    assert "FATAL" not in str(err)
    # Programmatic error preservation on .errors property
    assert err.errors[0][1].args[0] == fake_secret

    # Commit should NOT be called on error
    assert mock_session.commit.call_count == 0
    # Rollback should be called initially and for error recovery
    assert mock_session.rollback.call_count == 2
    # All 11 delete stages are attempted
    assert mock_session.execute.call_count == 11


@pytest.mark.asyncio
async def test_gate_fixture_cleanup_continues_all_stages_on_failure():
    """Test C: Even when the first stage fails, all remaining delete stages are still attempted."""
    mock_session = AsyncMock()
    # First stage (obligation_auto_payment_runs) fails, remaining 10 delete stages succeed
    mock_session.execute.side_effect = [
        RuntimeError("First stage failure"),
    ] + [MagicMock() for _ in range(10)]
    fixture = GateFixture(mock_session)

    with pytest.raises(GateCleanupError) as exc_info:
        await fixture.cleanup()

    err = exc_info.value
    assert len(err.errors) == 1
    assert err.errors[0][0] == "obligation_auto_payment_runs"
    assert "[obligation_auto_payment_runs] RuntimeError" in str(err)
    assert "First stage failure" not in str(err)
    # Verify all 11 delete stages were attempted
    assert mock_session.execute.call_count == 11
    assert mock_session.commit.call_count == 0
    assert mock_session.rollback.call_count == 2


@pytest.mark.asyncio
async def test_gate_fixture_cleanup_residue_detected_raises_cleanup_error():
    """Test D: When post-cleanup residue is detected, GateCleanupError is raised with residue stage."""
    mock_session = AsyncMock()
    # 11 delete stages succeed
    delete_mocks = [MagicMock() for _ in range(11)]
    # 11 residue checks: users returns 0, accounts returns 2 (residue!), rest return 0
    residue_mocks = []
    for i in range(11):
        m = MagicMock()
        # index 1 in residue_checks is accounts
        m.scalar_one.return_value = 2 if i == 1 else 0
        residue_mocks.append(m)

    mock_session.execute.side_effect = delete_mocks + residue_mocks
    fixture = GateFixture(mock_session)

    with pytest.raises(GateCleanupError) as exc_info:
        await fixture.cleanup()

    err = exc_info.value
    assert len(err.errors) == 1
    assert err.errors[0][0] == "residue_accounts"
    assert "[residue_accounts] RuntimeError" in str(err)
    # Commit was called before residue verification
    assert mock_session.commit.call_count == 1
