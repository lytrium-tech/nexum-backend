import importlib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core import database
from app.goals.service import AutoContributionProcessorSummary
from scripts.cron import process_auto_contributions as cli

ROOT = Path(__file__).parents[2]


@pytest.fixture
def runtime():
    session = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_factory = MagicMock(return_value=session_context)

    with (
        patch.object(database, "init_engine", new_callable=AsyncMock) as init_engine,
        patch.object(database, "close_engine", new_callable=AsyncMock) as close_engine,
        patch.object(database, "_session_factory", session_factory),
        patch.object(cli, "GoalService") as service_class,
    ):
        service = service_class.return_value
        service.process_due_auto_contributions = AsyncMock(
            return_value=AutoContributionProcessorSummary()
        )
        yield init_engine, close_engine, session_factory, service


def test_import_safety():
    with (
        patch.object(database, "init_engine", new_callable=AsyncMock) as init_engine,
        patch.object(database, "close_engine", new_callable=AsyncMock) as close_engine,
    ):
        importlib.reload(cli)

    init_engine.assert_not_awaited()
    close_engine.assert_not_awaited()


def test_cli_bootstrap_loads_all_required_metadata():
    from app.core.database import Base

    importlib.reload(cli)
    tables = Base.metadata.tables

    assert "users" in tables
    assert "accounts" in tables
    assert "goals" in tables
    assert "goal_transactions" in tables
    assert "financial_events" in tables


@pytest.mark.asyncio
async def test_bootstrap_reads_session_factory_after_init(monkeypatch):
    session_context = AsyncMock()
    session_context.__aenter__.return_value = AsyncMock()
    session_factory = MagicMock(return_value=session_context)

    async def initialize():
        monkeypatch.setattr(database, "_session_factory", session_factory)

    monkeypatch.setattr(database, "_session_factory", None)
    monkeypatch.setattr(database, "init_engine", initialize)
    monkeypatch.setattr(database, "close_engine", AsyncMock())
    processor = AsyncMock(return_value=AutoContributionProcessorSummary())
    monkeypatch.setattr(
        cli,
        "GoalService",
        MagicMock(return_value=MagicMock(process_due_auto_contributions=processor)),
    )

    assert await cli.async_main() == cli.EXIT_OK
    processor.assert_awaited_once()


@pytest.mark.asyncio
async def test_processor_called_once_with_aware_utc_now_and_default_batch(runtime):
    _, _, _, service = runtime
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    now_factory = MagicMock(return_value=now)

    assert await cli.async_main(now_factory=now_factory) == cli.EXIT_OK

    now_factory.assert_called_once_with()
    service.process_due_auto_contributions.assert_awaited_once_with(
        now=now,
        batch_size=50,
    )


@pytest.mark.asyncio
async def test_custom_batch_size(runtime):
    _, _, _, service = runtime

    assert await cli.async_main(batch_size=10) == cli.EXIT_OK
    assert service.process_due_auto_contributions.await_args.kwargs["batch_size"] == 10


@pytest.mark.parametrize("value", ["0", "-1", "51", "abc", "1.5"])
def test_invalid_batch_size_is_argparse_exit_two_without_async_execution(value):
    with patch.object(cli.asyncio, "run") as asyncio_run:
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["--batch-size", value])

    assert exc_info.value.code == 2
    asyncio_run.assert_not_called()


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (AutoContributionProcessorSummary(selected=0), cli.EXIT_OK),
        (
            AutoContributionProcessorSummary(selected=2, succeeded=2),
            cli.EXIT_OK,
        ),
        (
            AutoContributionProcessorSummary(selected=2, skipped=2),
            cli.EXIT_OK,
        ),
        (
            AutoContributionProcessorSummary(selected=1, reconciled=1),
            cli.EXIT_OK,
        ),
        (
            AutoContributionProcessorSummary(
                selected=3,
                succeeded=1,
                skipped=1,
                technical_failures=1,
            ),
            cli.EXIT_PARTIAL_TECHNICAL_FAILURE,
        ),
    ],
)
@pytest.mark.asyncio
async def test_summary_exit_codes(runtime, summary, expected):
    _, _, _, service = runtime
    service.process_due_auto_contributions.return_value = summary

    assert await cli.async_main() == expected


@pytest.mark.asyncio
async def test_global_processor_failure_is_exit_one(runtime):
    _, close_engine, _, service = runtime
    service.process_due_auto_contributions.side_effect = RuntimeError("database unavailable")

    assert await cli.async_main() == cli.EXIT_GLOBAL_FAILURE
    close_engine.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_on_success_and_partial_failure(runtime):
    init_engine, close_engine, _, service = runtime

    assert await cli.async_main() == cli.EXIT_OK
    service.process_due_auto_contributions.return_value = AutoContributionProcessorSummary(
        technical_failures=1
    )
    assert await cli.async_main() == cli.EXIT_PARTIAL_TECHNICAL_FAILURE

    assert init_engine.await_count == 2
    assert close_engine.await_count == 2


@pytest.mark.asyncio
async def test_init_failure_attempts_safe_cleanup(runtime):
    init_engine, close_engine, _, service = runtime
    init_engine.side_effect = RuntimeError("bootstrap failed")

    assert await cli.async_main() == cli.EXIT_GLOBAL_FAILURE
    close_engine.assert_awaited_once()
    service.process_due_auto_contributions.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_failure_overrides_success(runtime):
    _, close_engine, _, _ = runtime
    close_engine.side_effect = RuntimeError("close failed")

    assert await cli.async_main() == cli.EXIT_GLOBAL_FAILURE


@pytest.mark.asyncio
async def test_repeated_invocation_reinitializes_and_closes(runtime):
    init_engine, close_engine, _, service = runtime

    assert await cli.async_main() == cli.EXIT_OK
    assert await cli.async_main() == cli.EXIT_OK

    assert init_engine.await_count == 2
    assert close_engine.await_count == 2
    assert service.process_due_auto_contributions.await_count == 2


@pytest.mark.parametrize("return_code", [0, 1, 2])
def test_main_returns_async_exit_code(return_code):
    with (
        patch.object(cli, "async_main", new=MagicMock(return_value=object())),
        patch.object(cli.asyncio, "run", return_value=return_code),
    ):
        assert cli.main([]) == return_code


@pytest.mark.parametrize("return_code", [0, 1, 2])
def test_process_boundary_propagates_main_return_code(return_code):
    with patch.object(cli, "main", return_value=return_code):
        with pytest.raises(SystemExit) as exc_info:
            cli._run_as_process()

    assert exc_info.value.code == return_code


def test_keyboard_interrupt_returns_global_failure():
    with (
        patch.object(cli, "async_main", new=MagicMock(return_value=object())),
        patch.object(cli.asyncio, "run", side_effect=KeyboardInterrupt),
    ):
        assert cli.main([]) == cli.EXIT_GLOBAL_FAILURE


def test_actual_module_process_propagates_argparse_exit_two():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.cron.process_auto_contributions",
            "--batch-size",
            "invalid",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2


def test_runtime_packaging_and_canonical_command():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    source = Path(cli.__file__).read_text(encoding="utf-8")

    assert "WORKDIR /app" in dockerfile
    assert "COPY scripts/ ./scripts/" in dockerfile
    assert 'command: [".venv/bin/uvicorn"' in compose
    assert "/app/.venv/bin/python -m scripts.cron.process_auto_contributions" in source


def test_cli_has_no_http_secret_or_domain_implementation():
    source = Path(cli.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "FastAPI",
        "TestClient",
        "requests",
        "httpx",
        "INTERNAL_CRON_SECRET",
        "GoalTransaction",
        "FinancialEvent",
        "account.balance",
        "current_amount",
        "--now",
        "--dry-run",
    ):
        assert forbidden not in source
