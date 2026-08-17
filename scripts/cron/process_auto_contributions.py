"""CLI interno para procesar auto-contribuciones de Goals.

Comando canónico dentro de la imagen runtime:
    /app/.venv/bin/python -m scripts.cron.process_auto_contributions
"""

import argparse
import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import NoReturn

from app.accounts.repository import AccountRepository
from app.api.router import api_router  # noqa: F401 (bootstrap de metadata)
from app.core import database
from app.core.logging import configure_logging, get_logger
from app.goals.repository import GoalRepository
from app.goals.service import AutoContributionProcessorSummary, GoalService
from app.ledger.repository import LedgerRepository

logger = get_logger(__name__)

EXIT_OK = 0
EXIT_GLOBAL_FAILURE = 1
EXIT_PARTIAL_TECHNICAL_FAILURE = 2


def _batch_size(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("batch size must be an integer") from exc
    if not 1 <= parsed <= 50:
        raise argparse.ArgumentTypeError("batch size must be between 1 and 50")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process due auto contributions")
    parser.add_argument(
        "--batch-size",
        type=_batch_size,
        default=50,
        help="Number of schedules to process in this batch (max 50)",
    )
    return parser


def _summary_log_fields(
    summary: AutoContributionProcessorSummary | None,
) -> dict[str, int]:
    if summary is None:
        return {
            "selected": 0,
            "succeeded": 0,
            "skipped": 0,
            "reconciled": 0,
            "technical_failures": 0,
            "missed_occurrences": 0,
            "completed_goals": 0,
            "paused_schedules": 0,
            "cancelled_schedules": 0,
        }
    return {
        "selected": summary.selected,
        "succeeded": summary.succeeded,
        "skipped": summary.skipped,
        "reconciled": summary.reconciled,
        "technical_failures": summary.technical_failures,
        "missed_occurrences": summary.missed_occurrences,
        "completed_goals": summary.completed_goals,
        "paused_schedules": summary.paused_schedules,
        "cancelled_schedules": summary.cancelled_schedules,
    }


async def async_main(
    *,
    batch_size: int = 50,
    now_factory: Callable[[], datetime] | None = None,
) -> int:
    """Inicializa infraestructura, ejecuta un batch y libera sus recursos."""
    initialization_started = False
    summary: AutoContributionProcessorSummary | None = None
    exit_code = EXIT_GLOBAL_FAILURE

    try:
        configure_logging()
        logger.info("auto_contribution_cli_started", batch_size=batch_size)

        initialization_started = True
        await database.init_engine()

        # No existe aún una API pública de session factory. Leer el atributo desde
        # el módulo es obligatorio porque init_engine() lo reasigna al inicializarse.
        session_factory = database._session_factory
        if session_factory is None:
            logger.error("auto_contribution_cli_database_not_initialized")
        else:
            now = now_factory() if now_factory is not None else datetime.now(UTC)
            async with session_factory() as session:
                goal_service = GoalService(
                    repository=GoalRepository(session),
                    account_repo=AccountRepository(session),
                    ledger_repo=LedgerRepository(session),
                )
                summary = await goal_service.process_due_auto_contributions(
                    now=now,
                    batch_size=batch_size,
                )

            exit_code = (
                EXIT_PARTIAL_TECHNICAL_FAILURE if summary.technical_failures > 0 else EXIT_OK
            )
    except Exception:
        logger.exception("auto_contribution_cli_failed")
        exit_code = EXIT_GLOBAL_FAILURE
    finally:
        if initialization_started:
            try:
                await database.close_engine()
            except Exception:
                logger.exception("auto_contribution_cli_cleanup_failed")
                exit_code = EXIT_GLOBAL_FAILURE

    logger.info(
        "auto_contribution_cli_finished",
        **_summary_log_fields(summary),
        exit_code=exit_code,
    )
    return exit_code


def main(
    args_list: Sequence[str] | None = None,
    *,
    now_factory: Callable[[], datetime] | None = None,
) -> int:
    """Parsea argumentos y propaga el resultado async como código de proceso."""
    args = _build_parser().parse_args(args_list)
    try:
        return asyncio.run(async_main(batch_size=args.batch_size, now_factory=now_factory))
    except KeyboardInterrupt:
        logger.warning("auto_contribution_cli_interrupted")
        return EXIT_GLOBAL_FAILURE
    except Exception:
        logger.exception("auto_contribution_cli_unhandled_failure")
        return EXIT_GLOBAL_FAILURE


def _run_as_process() -> NoReturn:
    raise SystemExit(main())


if __name__ == "__main__":
    _run_as_process()
