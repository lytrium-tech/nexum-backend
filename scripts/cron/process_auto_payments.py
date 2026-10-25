"""Internal CLI to process due automatic payments for Obligations V1.7.

Usage:
    python -m scripts.cron.process_auto_payments [--batch-size 50]
"""

import argparse
import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import NoReturn

from app.accounts.repository import AccountRepository
from app.api.router import api_router  # noqa: F401 (metadata bootstrap)
from app.core import database
from app.core.logging import configure_logging, get_logger
from app.fx.provider import get_default_fx_provider
from app.fx.service import FXService
from app.obligations.auto_repository import ObligationAutoPaymentRepository
from app.obligations.auto_service import (
    AutoPaymentProcessorSummary,
    ObligationAutoPaymentService,
)
from app.obligations.service_v17 import ObligationV17Service

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
    parser = argparse.ArgumentParser(description="Process due obligation auto payments")
    parser.add_argument(
        "--batch-size",
        type=_batch_size,
        default=50,
        help="Number of configs to process in this batch (max 50)",
    )
    return parser


def _summary_log_fields(
    summary: AutoPaymentProcessorSummary | None,
) -> dict[str, int]:
    if summary is None:
        return {
            "selected": 0,
            "succeeded": 0,
            "skipped": 0,
            "failed": 0,
            "technical_failures": 0,
        }
    return {
        "selected": summary.selected,
        "succeeded": summary.succeeded,
        "skipped": summary.skipped,
        "failed": summary.failed,
        "technical_failures": summary.technical_failures,
    }


async def async_main(
    *,
    batch_size: int = 50,
    now_factory: Callable[[], datetime] | None = None,
) -> int:
    """Execute auto payments batch with clean setup and teardown."""
    initialization_started = False
    summary: AutoPaymentProcessorSummary | None = None
    exit_code = EXIT_GLOBAL_FAILURE

    try:
        configure_logging()
        logger.info("obligation_auto_payment_cli_started", batch_size=batch_size)

        initialization_started = True
        await database.init_engine()

        session_factory = database._session_factory
        if session_factory is None:
            logger.error("obligation_auto_payment_cli_database_not_initialized")
        else:
            now = now_factory() if now_factory is not None else datetime.now(UTC)
            async with session_factory() as session:
                v17_service = ObligationV17Service(session)
                fx_service = FXService(provider=get_default_fx_provider(), session=session)
                account_repo = AccountRepository(session)
                auto_repo = ObligationAutoPaymentRepository(session)
                auto_service = ObligationAutoPaymentService(
                    session=session,
                    v17_service=v17_service,
                    fx_service=fx_service,
                    account_repo=account_repo,
                    repo=auto_repo,
                )
                summary = await auto_service.process_due_auto_payments(
                    as_of=now,
                    batch_size=batch_size,
                    session_factory=session_factory,
                )

            exit_code = (
                EXIT_PARTIAL_TECHNICAL_FAILURE if summary.technical_failures > 0 else EXIT_OK
            )
    except Exception as exc:
        logger.error("obligation_auto_payment_cli_failed", error_type=type(exc).__name__)
        exit_code = EXIT_GLOBAL_FAILURE
    finally:
        if initialization_started:
            try:
                await database.close_engine()
            except Exception as exc:
                logger.error(
                    "obligation_auto_payment_cli_cleanup_failed",
                    error_type=type(exc).__name__,
                )
                exit_code = EXIT_GLOBAL_FAILURE

    logger.info(
        "obligation_auto_payment_cli_finished",
        **_summary_log_fields(summary),
        exit_code=exit_code,
    )
    return exit_code


def main(
    args_list: Sequence[str] | None = None,
    *,
    now_factory: Callable[[], datetime] | None = None,
) -> int:
    """Parse CLI args and run async processor."""
    args = _build_parser().parse_args(args_list)
    try:
        return asyncio.run(async_main(batch_size=args.batch_size, now_factory=now_factory))
    except KeyboardInterrupt:
        logger.warning("obligation_auto_payment_cli_interrupted")
        return EXIT_GLOBAL_FAILURE
    except Exception as exc:
        logger.error(
            "obligation_auto_payment_cli_unhandled_failure",
            error_type=type(exc).__name__,
        )
        return EXIT_GLOBAL_FAILURE


def _run_as_process() -> NoReturn:
    raise SystemExit(main())


if __name__ == "__main__":
    _run_as_process()
