"""Permanent guarded pytest entrypoint for Nexum Release Gate (CT101).

Guarantees:
1. Complete .env isolation (no loading of user or production environment files).
2. Fail-closed guards: fails immediately with exit code 1 (not skip/xfail) if:
   - ARGOS_DATABASE_URL is absent or empty.
   - Target database, user, or host does not strictly match CT101 loopback tunnel.
   - NEXUM_ALLOW_DESTRUCTIVE_TEST_DB=1 opt-in is absent.
3. Strict zero-tolerance: fails certification if ANY test skips, xfails, xpasses, or if 0 tests are collected.
4. Secret safety: never prints passwords, credentials, raw parse exceptions, or skip reasons.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

EXPECTED_DATABASE = "nexum_release_gate_test"
EXPECTED_USER = "nexum_release_gate_test"
SAFE_TUNNEL_HOSTS = {"127.0.0.1", "localhost", "::1"}
EXPECTED_TUNNEL_PORT = 5433
GATE_OPT_IN = "NEXUM_ALLOW_DESTRUCTIVE_TEST_DB"

DEFAULT_CANONICAL_SUITES = [
    "tests/unit/test_obligations_reservations_postgres.py",
    "tests/unit/test_obligations_legacy_postgres.py",
    "tests/unit/test_obligations_auto_payments_postgres.py",
    "tests/unit/test_alembic_lineage_compatibility.py",
]


def validate_gate_guards() -> tuple[str, str]:
    """Validate all security guards fail-closed before importing app code or starting pytest."""
    db_url = os.environ.get("ARGOS_DATABASE_URL", "").strip()
    if not db_url:
        print("RELEASE_GATE_FATAL: ARGOS_DATABASE_URL is not set or empty", file=sys.stderr)
        sys.exit(1)

    opt_in = os.environ.get(GATE_OPT_IN, "").strip()
    if opt_in != "1":
        print(
            f"RELEASE_GATE_FATAL: {GATE_OPT_IN}=1 opt-in is required for destructive test DB operations",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        parsed = urlsplit(db_url)
    except Exception:
        # Never print raw exception text which might contain credentials
        print(
            "RELEASE_GATE_FATAL: Malformed ARGOS_DATABASE_URL; failed to parse URL",
            file=sys.stderr,
        )
        sys.exit(1)

    scheme = parsed.scheme.split("+")[0]
    if scheme != "postgresql":
        print(
            f"RELEASE_GATE_FATAL: Invalid URL scheme '{parsed.scheme}'; must be postgresql",
            file=sys.stderr,
        )
        sys.exit(1)

    if parsed.hostname not in SAFE_TUNNEL_HOSTS:
        print(
            f"RELEASE_GATE_FATAL: Unsafe target host '{parsed.hostname}'; strictly requires local loopback tunnel",
            file=sys.stderr,
        )
        sys.exit(1)

    if parsed.port != EXPECTED_TUNNEL_PORT:
        print(
            f"RELEASE_GATE_FATAL: Unsafe target port {parsed.port}; strictly requires SSH tunnel port {EXPECTED_TUNNEL_PORT}",
            file=sys.stderr,
        )
        sys.exit(1)

    if parsed.username != EXPECTED_USER:
        print(
            f"RELEASE_GATE_FATAL: Unsafe DB user '{parsed.username}'; strictly requires '{EXPECTED_USER}'",
            file=sys.stderr,
        )
        sys.exit(1)

    db_name = parsed.path.lstrip("/")
    if db_name != EXPECTED_DATABASE:
        print(
            f"RELEASE_GATE_FATAL: Unsafe DB name '{db_name}'; strictly requires '{EXPECTED_DATABASE}'",
            file=sys.stderr,
        )
        sys.exit(1)

    return db_url, opt_in


def setup_isolated_test_environment() -> None:
    """Isolate process from default .env loading and service credentials."""
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    os.environ.update(
        APP_ENV="test",
        SUPABASE_URL="",
        SUPABASE_SERVICE_ROLE_KEY="",
        GEMINI_API_KEY="",
        FX_PROVIDER="static",
    )

    try:
        from pydantic_settings import BaseSettings

        orig_init = BaseSettings.__init__
        BaseSettings.__init__ = lambda self, **kwargs: orig_init(
            self, **dict(kwargs, _env_file=None)
        )
    except ImportError:
        pass


class GateZeroTolerancePlugin:
    """Pytest plugin enforcing zero tolerance for skips, xfails, xpasses, or zero collection."""

    def __init__(self):
        self.collected = 0
        self.rejected_nodes: list[tuple[str, str]] = []  # (nodeid, outcome_type)
        self.failed_nodes: list[str] = []
        self.passed_count = 0

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)
        if self.collected == 0:
            print(
                "\nRELEASE_GATE_FATAL: Zero tests collected in physical suite!",
                file=sys.stderr,
            )
            session.shouldstop = "Zero tests collected in physical suite"

    def pytest_runtest_logreport(self, report):
        if report.when in ("setup", "call"):
            if hasattr(report, "wasxfail"):
                # Captures both xfail (skipped) and xpass (passed with xfail mark)
                outcome = "xpass" if report.passed else "xfail"
                self.rejected_nodes.append((report.nodeid, outcome))
            elif report.skipped:
                # Regular skip without printing raw reason text
                self.rejected_nodes.append((report.nodeid, "skipped"))
            elif report.failed:
                self.failed_nodes.append(report.nodeid)
            elif report.passed and report.when == "call":
                self.passed_count += 1

    def pytest_sessionfinish(self, session, exitstatus):
        if self.collected == 0:
            session.exitstatus = 1
            return

        if self.rejected_nodes or self.failed_nodes:
            session.exitstatus = 1

        if self.rejected_nodes:
            print(
                f"\nRELEASE_GATE_FATAL: Prohibited test outcomes detected ({len(self.rejected_nodes)})! Skips, xfails, and xpasses are strictly forbidden in Release Gate certification:",
                file=sys.stderr,
            )
            for nodeid, outcome in self.rejected_nodes:
                print(f"  - {nodeid}: [{outcome}]", file=sys.stderr)

        print(
            f"\n[Release Gate Summary] Total Collected: {self.collected} | Passed: {self.passed_count} | Failed: {len(self.failed_nodes)} | Rejected: {len(self.rejected_nodes)}"
        )


def main(args: list[str] | None = None) -> int:
    if args is None:
        args = sys.argv[1:]

    # 1. Enforce fail-closed guards
    validate_gate_guards()

    # 2. Isolate test environment
    setup_isolated_test_environment()

    # 3. Import pytest and register zero-tolerance plugin
    import pytest

    plugin = GateZeroTolerancePlugin()

    # If no test target paths specified, run canonical default physical suites
    pytest_args = list(args)
    has_target = any(not a.startswith("-") for a in pytest_args)
    if not has_target:
        pytest_args.extend(DEFAULT_CANONICAL_SUITES)

    exit_code = pytest.main(pytest_args, plugins=[plugin])

    if plugin.collected == 0 or plugin.rejected_nodes or plugin.failed_nodes or exit_code != 0:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
