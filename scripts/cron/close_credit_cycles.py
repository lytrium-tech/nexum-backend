"""Internal CLI to close eligible credit card billing cycles and materialize canonical statements.

Usage:
    python -m scripts.cron.close_credit_cycles [--batch-size 50] [--as-of YYYY-MM-DD] [--card-id UUID] [--user-id UUID] [--dry-run]
"""

import argparse
import asyncio
import uuid
from collections.abc import Callable, Sequence
from datetime import date
from typing import NoReturn

from app.api.router import api_router  # noqa: F401 (bootstrap metadata)
from app.core import database
from app.core.financial_time import financial_date
from app.core.logging import configure_logging, get_logger
from app.credit.closure_service import CreditCycleClosureService
from app.credit.repository import CreditCardRepository
from app.credit.schemas import CreditCardCycleClosureSummary

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


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date format '{value}'. Expected YYYY-MM-DD."
        ) from exc


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid UUID '{value}'.") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Close eligible credit card billing cycles and generate statements"
    )
    parser.add_argument(
        "--batch-size",
        type=_batch_size,
        default=50,
        help="Number of credit cards to process in this batch (max 50)",
    )
    parser.add_argument(
        "--as-of",
        type=_parse_date,
        default=None,
        help="As-of date in YYYY-MM-DD (defaults to financial_date() in America/Bogota)",
    )
    parser.add_argument(
        "--card-id",
        type=_parse_uuid,
        default=None,
        help="Optional specific card UUID to process",
    )
    parser.add_argument(
        "--user-id",
        type=_parse_uuid,
        default=None,
        help="Optional specific user UUID to process",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=24,
        help="Maximum eligible cycles to close per card per invocation (default 24)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Smoke test mode: validate inputs and environment without mutating database",
    )
    return parser


def _summary_log_fields(
    summary: CreditCardCycleClosureSummary | None,
) -> dict[str, int | bool]:
    if summary is None:
        return {
            "cards_inspected": 0,
            "cycles_eligible": 0,
            "cycles_closed": 0,
            "statements_created": 0,
            "statements_skipped": 0,
            "components_created": 0,
            "technical_failures": 0,
            "has_remaining_catchup": False,
            "remaining_catchup_cycles": 0,
        }
    return {
        "cards_inspected": summary.cards_inspected,
        "cycles_eligible": summary.cycles_eligible,
        "cycles_closed": summary.cycles_closed,
        "statements_created": summary.statements_created,
        "statements_skipped": summary.statements_skipped,
        "components_created": summary.components_created,
        "technical_failures": summary.technical_failures,
        "has_remaining_catchup": summary.has_remaining_catchup,
        "remaining_catchup_cycles": summary.remaining_catchup_cycles,
    }


async def async_main(
    *,
    batch_size: int = 50,
    as_of: date | None = None,
    card_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    max_cycles: int = 24,
    dry_run: bool = False,
    session_factory: Callable[[], database.AsyncSession] | None = None,
) -> int:
    """Execute credit cycle closure batch with clean lifecycle."""
    initialization_started = False
    summary: CreditCardCycleClosureSummary | None = None
    exit_code = EXIT_GLOBAL_FAILURE

    try:
        configure_logging()
        target_date = as_of or financial_date()
        logger.info(
            "credit_cycle_closure_cli_started",
            batch_size=batch_size,
            as_of=target_date.isoformat(),
            card_id=str(card_id) if card_id else None,
            user_id=str(user_id) if user_id else None,
            max_cycles=max_cycles,
            dry_run=dry_run,
        )

        if dry_run:
            logger.info("credit_cycle_closure_dry_run_completed", status="success")
            return EXIT_OK

        if session_factory is None:
            initialization_started = True
            await database.init_engine()
            session_factory = database._session_factory

        if session_factory is None:
            logger.error("credit_cycle_closure_cli_database_not_initialized")
            return EXIT_GLOBAL_FAILURE

        async with session_factory() as session:
            repo = CreditCardRepository(session)
            service = CreditCycleClosureService(session=session, repo=repo)
            summary = await service.close_all_eligible_cycles(
                as_of_date=target_date,
                batch_size=batch_size,
                user_id=user_id,
                card_id=card_id,
                session_factory=session_factory,
                max_cycles_per_card=max_cycles,
            )

        exit_code = EXIT_PARTIAL_TECHNICAL_FAILURE if summary.technical_failures > 0 else EXIT_OK
    except Exception as exc:
        logger.error(
            "credit_cycle_closure_cli_failed", error_type=type(exc).__name__, error_message=str(exc)
        )
        exit_code = EXIT_GLOBAL_FAILURE
    finally:
        if initialization_started:
            try:
                await database.close_engine()
            except Exception as exc:
                logger.error(
                    "credit_cycle_closure_cli_cleanup_failed",
                    error_type=type(exc).__name__,
                )
                exit_code = EXIT_GLOBAL_FAILURE

    logger.info(
        "credit_cycle_closure_cli_finished",
        **_summary_log_fields(summary),
        exit_code=exit_code,
    )
    return exit_code


def main(args_list: Sequence[str] | None = None) -> int:
    """Parse CLI args and run async processor."""
    args = _build_parser().parse_args(args_list)
    try:
        return asyncio.run(
            async_main(
                batch_size=args.batch_size,
                as_of=args.as_of,
                card_id=args.card_id,
                user_id=args.user_id,
                max_cycles=args.max_cycles,
                dry_run=args.dry_run,
            )
        )
    except KeyboardInterrupt:
        logger.warning("credit_cycle_closure_cli_interrupted")
        return EXIT_GLOBAL_FAILURE
    except Exception as exc:
        logger.error(
            "credit_cycle_closure_cli_unhandled_failure",
            error_type=type(exc).__name__,
        )
        return EXIT_GLOBAL_FAILURE


def _run_as_process() -> NoReturn:
    raise SystemExit(main())


if __name__ == "__main__":
    _run_as_process()
