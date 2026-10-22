"""Unit tests for Release Gate postgres_helpers: error suppression, tracking, and cleanup safety."""

import uuid

from tests.unit.postgres_helpers import (
    GateCleanupError,
)


def test_gate_cleanup_error_reports_type_and_stage_only_excluding_raw_secrets():
    """GateCleanupError must report stage + exception type ONLY and count; never raw error text containing secrets."""
    raw_secret_leak = (
        "FATAL: password='super_secret_db_password_123' token='ghp_secret_token_abc' rejected"
    )
    errors = [
        ("accounts", RuntimeError(raw_secret_leak)),
        (
            "financial_events",
            ValueError("Sensitive connection string: postgresql://admin:leaked_pass@host:5433/db"),
        ),
    ]
    exc = GateCleanupError(errors)
    msg = str(exc)

    # Strictly assert that NO raw error text or secrets appear in the exception string representation
    assert "super_secret_db_password_123" not in msg
    assert "ghp_secret_token_abc" not in msg
    assert "leaked_pass" not in msg
    assert "postgresql://" not in msg
    assert "Sensitive connection string" not in msg
    assert "FATAL" not in msg

    # Strictly assert that stage names, exception type names, and count are cleanly reported
    assert "[accounts] RuntimeError" in msg
    assert "[financial_events] ValueError" in msg
    assert "GateFixture cleanup failed across 2 stage(s)" in msg

    # Strictly assert that the underlying errors list is preserved on the object for programmatic inspection
    assert len(exc.errors) == 2
    assert exc.errors[0][0] == "accounts"
    assert exc.errors[0][1].args[0] == raw_secret_leak
    assert exc.errors[1][0] == "financial_events"


def test_gate_fixture_exchange_rate_tracking():
    from unittest.mock import MagicMock

    from tests.unit.postgres_helpers import GateFixture

    mock_session = MagicMock()
    fixture = GateFixture(mock_session)
    assert fixture.created_exchange_rate_ids == set()

    rate_id1 = uuid.uuid4()
    rate_id2 = uuid.uuid4()
    fixture.track_exchange_rate(rate_id1)
    fixture.track_exchange_rate(rate_id2)

    assert fixture.created_exchange_rate_ids == {rate_id1, rate_id2}
