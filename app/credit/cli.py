"""Credit statement closure CLI entry point."""

from scripts.cron.close_credit_cycles import (
    EXIT_GLOBAL_FAILURE,
    EXIT_OK,
    EXIT_PARTIAL_TECHNICAL_FAILURE,
    async_main,
    main,
)

__all__ = [
    "EXIT_GLOBAL_FAILURE",
    "EXIT_OK",
    "EXIT_PARTIAL_TECHNICAL_FAILURE",
    "async_main",
    "main",
]

if __name__ == "__main__":
    import sys

    sys.exit(main())
