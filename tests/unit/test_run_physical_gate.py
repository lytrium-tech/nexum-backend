"""Unit tests for the guarded Release Gate pytest runner (scripts/dev/run_physical_gate.py)."""

import os
from unittest.mock import MagicMock, patch

import pytest

from scripts.dev.run_physical_gate import (
    GateZeroTolerancePlugin,
    main,
    validate_gate_guards,
)


def test_validate_gate_guards_fails_if_no_url():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(SystemExit) as exc:
            validate_gate_guards()
        assert exc.value.code == 1


def test_validate_gate_guards_fails_if_no_opt_in():
    with patch.dict(
        os.environ,
        {
            "ARGOS_DATABASE_URL": "postgresql://nexum_release_gate_test:p@127.0.0.1:5433/nexum_release_gate_test"
        },
        clear=True,
    ):
        with pytest.raises(SystemExit) as exc:
            validate_gate_guards()
        assert exc.value.code == 1


@pytest.mark.parametrize(
    "bad_url",
    [
        "postgresql://nexum_release_gate_test:p@192.168.0.245:5433/nexum_release_gate_test",  # Not loopback
        "postgresql://nexum_release_gate_test:p@127.0.0.1:5432/nexum_release_gate_test",  # Wrong port
        "postgresql://postgres:p@127.0.0.1:5433/nexum_release_gate_test",  # Wrong user
        "postgresql://nexum_release_gate_test:p@127.0.0.1:5433/postgres",  # Wrong DB
        "mysql://nexum_release_gate_test:p@127.0.0.1:5433/nexum_release_gate_test",  # Wrong scheme
    ],
)
def test_validate_gate_guards_fails_on_unsafe_urls(bad_url: str):
    with patch.dict(
        os.environ,
        {"ARGOS_DATABASE_URL": bad_url, "NEXUM_ALLOW_DESTRUCTIVE_TEST_DB": "1"},
        clear=True,
    ):
        with pytest.raises(SystemExit) as exc:
            validate_gate_guards()
        assert exc.value.code == 1


def test_validate_gate_guards_fails_on_malformed_url_without_leaking_secrets(capsys):
    secret_in_url = "postgresql://nexum_release_gate_test:secret_pass_123@::invalid_url"
    with patch("scripts.dev.run_physical_gate.urlsplit", side_effect=ValueError("Parse failed")):
        with patch.dict(
            os.environ,
            {"ARGOS_DATABASE_URL": secret_in_url, "NEXUM_ALLOW_DESTRUCTIVE_TEST_DB": "1"},
            clear=True,
        ):
            with pytest.raises(SystemExit) as exc:
                validate_gate_guards()
            assert exc.value.code == 1

    captured = capsys.readouterr()
    assert "secret_pass_123" not in captured.err
    assert "Parse failed" not in captured.err
    assert "Malformed ARGOS_DATABASE_URL" in captured.err


def test_validate_gate_guards_passes_on_valid_tunnel_url():
    valid_url = (
        "postgresql+asyncpg://nexum_release_gate_test:secret@127.0.0.1:5433/nexum_release_gate_test"
    )
    with patch.dict(
        os.environ,
        {"ARGOS_DATABASE_URL": valid_url, "NEXUM_ALLOW_DESTRUCTIVE_TEST_DB": "1"},
        clear=True,
    ):
        db_url, opt_in = validate_gate_guards()
        assert db_url == valid_url
        assert opt_in == "1"


def test_gate_zero_tolerance_plugin_fails_on_skips():
    plugin = GateZeroTolerancePlugin()
    plugin.collected = 5

    mock_report = MagicMock()
    mock_report.skipped = True
    mock_report.failed = False
    mock_report.passed = False
    mock_report.when = "call"
    mock_report.nodeid = "tests/unit/test_foo.py::test_case"
    del mock_report.wasxfail  # not an xfail

    plugin.pytest_runtest_logreport(mock_report)
    assert len(plugin.rejected_nodes) == 1
    assert plugin.rejected_nodes[0] == ("tests/unit/test_foo.py::test_case", "skipped")

    session = MagicMock()
    session.exitstatus = 0
    plugin.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1


def test_gate_zero_tolerance_plugin_fails_on_xfail():
    plugin = GateZeroTolerancePlugin()
    plugin.collected = 5

    mock_report = MagicMock()
    mock_report.skipped = True
    mock_report.failed = False
    mock_report.passed = False
    mock_report.when = "call"
    mock_report.nodeid = "tests/unit/test_foo.py::test_xfail_case"
    mock_report.wasxfail = "expected failure reason"

    plugin.pytest_runtest_logreport(mock_report)
    assert len(plugin.rejected_nodes) == 1
    assert plugin.rejected_nodes[0] == ("tests/unit/test_foo.py::test_xfail_case", "xfail")

    session = MagicMock()
    session.exitstatus = 0
    plugin.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1


def test_gate_zero_tolerance_plugin_fails_on_xpass():
    plugin = GateZeroTolerancePlugin()
    plugin.collected = 5

    mock_report = MagicMock()
    mock_report.skipped = False
    mock_report.failed = False
    mock_report.passed = True
    mock_report.when = "call"
    mock_report.nodeid = "tests/unit/test_foo.py::test_xpass_case"
    mock_report.wasxfail = "expected failure but passed"

    plugin.pytest_runtest_logreport(mock_report)
    assert len(plugin.rejected_nodes) == 1
    assert plugin.rejected_nodes[0] == ("tests/unit/test_foo.py::test_xpass_case", "xpass")

    session = MagicMock()
    session.exitstatus = 0
    plugin.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1


def test_gate_zero_tolerance_plugin_fails_on_zero_collected():
    plugin = GateZeroTolerancePlugin()
    session = MagicMock()
    session.items = []
    plugin.pytest_collection_finish(session)
    assert plugin.collected == 0
    assert session.shouldstop != ""

    session.exitstatus = 0
    plugin.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1


def test_main_exits_nonzero_when_skips_detected():
    with patch("scripts.dev.run_physical_gate.validate_gate_guards") as mock_validate:
        mock_validate.return_value = ("url", "1")
        with patch("scripts.dev.run_physical_gate.setup_isolated_test_environment"):
            with patch("pytest.main") as mock_pytest_main:
                # Simulate pytest returning 0 but plugin catching a skip
                def fake_pytest_main(args, plugins):
                    p = plugins[0]
                    p.collected = 10
                    p.rejected_nodes.append(("test_foo", "skipped"))
                    return 0

                mock_pytest_main.side_effect = fake_pytest_main
                exit_code = main(["tests/unit/test_foo.py"])
                assert exit_code == 1


def test_resolve_suite_args_credit_suite():
    from scripts.dev.run_physical_gate import CREDIT_CANONICAL_SUITES, resolve_suite_args

    args = resolve_suite_args(["--suite", "credit", "-v"])
    assert "-v" in args
    for suite in CREDIT_CANONICAL_SUITES:
        assert suite in args


def test_resolve_suite_args_obligations_suite():
    from scripts.dev.run_physical_gate import OBLIGATIONS_CANONICAL_SUITES, resolve_suite_args

    args = resolve_suite_args(["--suite", "obligations"])
    assert args == OBLIGATIONS_CANONICAL_SUITES


def test_resolve_suite_args_all_suite():
    from scripts.dev.run_physical_gate import DEFAULT_CANONICAL_SUITES, resolve_suite_args

    args = resolve_suite_args(["--suite", "all"])
    assert args == DEFAULT_CANONICAL_SUITES


def test_resolve_suite_args_credit_flag():
    from scripts.dev.run_physical_gate import CREDIT_CANONICAL_SUITES, resolve_suite_args

    args = resolve_suite_args(["--credit"])
    assert args == CREDIT_CANONICAL_SUITES


def test_resolve_suite_args_defaults_when_empty():
    from scripts.dev.run_physical_gate import DEFAULT_CANONICAL_SUITES, resolve_suite_args

    args = resolve_suite_args([])
    assert args == DEFAULT_CANONICAL_SUITES


def test_resolve_suite_args_preserves_custom_targets():
    from scripts.dev.run_physical_gate import resolve_suite_args

    custom = ["tests/postgres/test_credit_canonical_release_gate.py"]
    args = resolve_suite_args(custom)
    assert args == custom


def test_resolve_suite_args_rejects_unknown_suite():
    from scripts.dev.run_physical_gate import resolve_suite_args

    with pytest.raises(SystemExit) as exc:
        resolve_suite_args(["--suite", "unknown_suite"])
    assert exc.value.code == 1


def test_resolve_suite_args_rejects_missing_suite_argument():
    from scripts.dev.run_physical_gate import resolve_suite_args

    with pytest.raises(SystemExit) as exc:
        resolve_suite_args(["--suite"])
    assert exc.value.code == 1


def test_resolve_suite_args_with_scenarios_flag():
    from scripts.dev.run_physical_gate import CREDIT_CANONICAL_SUITES, resolve_suite_args

    args = resolve_suite_args(["--with-scenarios", "--credit"])
    assert args == CREDIT_CANONICAL_SUITES


def test_main_runs_scenarios_and_fails_if_scenarios_fail():
    with patch("scripts.dev.run_physical_gate.validate_gate_guards"):
        with patch("scripts.dev.run_physical_gate.setup_isolated_test_environment"):
            with patch(
                "scripts.dev.run_alembic_physical_scenarios.main", return_value=1
            ) as mock_scenarios:
                exit_code = main(["--scenarios"])
                assert exit_code == 1
                mock_scenarios.assert_called_once()


def test_main_runs_scenarios_only_and_exits_zero_on_success():
    with patch("scripts.dev.run_physical_gate.validate_gate_guards"):
        with patch("scripts.dev.run_physical_gate.setup_isolated_test_environment"):
            with patch(
                "scripts.dev.run_alembic_physical_scenarios.main", return_value=0
            ) as mock_scenarios:
                exit_code = main(["--scenarios"])
                assert exit_code == 0
                mock_scenarios.assert_called_once()
