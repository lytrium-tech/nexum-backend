"""Alembic physical migration scenarios runner for Nexum PostgreSQL Release Gate (CT101).

Guarantees:
1. Complete .env isolation (no loading of user or production environment files).
2. Fail-closed guards: fails immediately with exit code 1 if:
   - ARGOS_DATABASE_URL is absent or empty.
   - Target database, user, or host does not strictly match CT101 loopback tunnel.
   - NEXUM_ALLOW_DESTRUCTIVE_TEST_DB=1 opt-in is absent.
3. Certifies all 3 physical Alembic migration scenarios:
   - Scenario A: Empty database -> linear HEAD (credit_v2_refund_type).
   - Scenario B: Empty database -> prior head (obligations_v1_7_auto_payments) -> linear HEAD.
   - Scenario C: Empty database -> prior head -> representative legacy dataset seeded BEFORE
                 forward migrations -> forward migrations to linear HEAD -> assert column defaults,
                 nullable preservation, zero synthetic backfill, F-15 constraint behavior, and debt partitioning.
4. Secret safety: never prints passwords, credentials, raw parse exceptions, or skip reasons.
5. Leaves database at clean linear HEAD upon successful completion.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.dev.run_physical_gate import (  # noqa: E402
    EXPECTED_DATABASE,
    EXPECTED_USER,
    setup_isolated_test_environment,
    validate_gate_guards,
)

FINAL_LINEAR_HEAD = "credit_alpha_interest_opening"
PRIOR_PRODUCTION_HEAD = "obligations_v1_7_auto_payments"

# Representative Legacy Dataset IDs
LEGACY_USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
LEGACY_ACCOUNT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
LEGACY_CARD_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
LEGACY_STMT_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
LEGACY_TX_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
LEGACY_INST_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")
LEGACY_CHARGE_ID = uuid.UUID("77777777-7777-7777-7777-777777777777")


def to_async_url(url: str) -> str:
    """Ensure database URL has asyncpg driver prefix for SQLAlchemy async engine."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def get_alembic_config(db_url: str) -> Config:
    """Create configured Alembic Config instance pointing to the verified test DB."""
    ini_path = REPO_ROOT / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", to_async_url(db_url))
    os.environ["DATABASE_URL"] = to_async_url(db_url)
    return cfg


async def reset_public_schema(db_url: str) -> None:
    """Drop and re-create empty public schema inside guarded test DB."""
    engine = create_async_engine(to_async_url(db_url), isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        # Strict in-engine validation
        res = await conn.execute(
            text(
                "SELECT current_database(), current_user, rolsuper FROM pg_roles WHERE rolname = current_user"
            )
        )
        row = res.fetchone()
        if not row:
            raise RuntimeError("Failed to query database identity")
        curr_db, curr_user, rolsuper = row[0], row[1], row[2]
        if curr_db != EXPECTED_DATABASE or curr_user != EXPECTED_USER:
            raise RuntimeError(
                f"Unsafe DB identity during schema reset: db={curr_db}, user={curr_user}"
            )
        if rolsuper:
            raise RuntimeError("Safety violation: test user must not have superuser privileges")

        # Terminate active connections (except this one)
        await conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        )
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE;"))
        await conn.execute(text(f"CREATE SCHEMA public AUTHORIZATION {EXPECTED_USER};"))
        await conn.execute(text(f"GRANT ALL ON SCHEMA public TO {EXPECTED_USER};"))
    await engine.dispose()


async def get_current_alembic_revision(db_url: str) -> str | None:
    """Read version_num from alembic_version table."""
    engine = create_async_engine(to_async_url(db_url))
    async with engine.connect() as conn:
        try:
            res = await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            rev = res.scalar()
        except Exception:
            rev = None
    await engine.dispose()
    return rev


def normalize_trigger_status(status: str | bytes | None) -> str:
    """Normalize PostgreSQL pg_trigger.tgenabled status value to string.

    PostgreSQL catalog column "char" can be returned as str or bytes depending
    on driver/protocol configuration (e.g., 'O' vs b'O').
    Enabled status is 'O' (origin/enabled).
    Disabled status is 'D' (disabled), 'R' (replica), or 'A' (always).
    """
    if status is None:
        return ""
    if isinstance(status, bytes):
        return status.decode("ascii", errors="replace").strip()
    if isinstance(status, str):
        return status.strip()
    return str(status).strip()


def validate_trigger_status_map(triggers: dict) -> None:
    """Validate that required immutability triggers exist and are enabled ('O').

    Accepts both str and bytes for trigger names and tgenabled statuses.
    Raises RuntimeError fail-closed if any expected trigger is missing or disabled.
    """
    expected = {"trg_cc_statements_immutable", "trg_cc_stmt_comp_immutable"}
    norm_triggers = {
        (k.decode("ascii", errors="replace").strip() if isinstance(k, bytes) else str(k).strip()): v
        for k, v in triggers.items()
    }
    if set(norm_triggers.keys()) != expected:
        missing = expected - set(norm_triggers.keys())
        raise RuntimeError(f"Missing expected immutability triggers: {missing}")
    for name, raw_status in norm_triggers.items():
        norm_status = normalize_trigger_status(raw_status)
        if norm_status != "O":
            raise RuntimeError(
                f"Trigger {name} is in status {raw_status!r} (normalized: {norm_status!r}), expected 'O' (enabled)"
            )


async def verify_triggers_and_table_state(db_url: str) -> None:
    """Verify that immutable statement triggers are present and enabled ('O')."""
    engine = create_async_engine(to_async_url(db_url))
    async with engine.connect() as conn:
        stmt = text(
            """
            SELECT tgname, tgenabled
            FROM pg_trigger
            WHERE tgname IN ('trg_cc_statements_immutable', 'trg_cc_stmt_comp_immutable');
            """
        )
        res = await conn.execute(stmt)
        triggers = dict(res.all())
        validate_trigger_status_map(triggers)
    await engine.dispose()


async def insert_legacy_seed_dataset(db_url: str) -> None:
    """Seed representative legacy data using ONLY columns available at prior head obligations_v1_7_auto_payments."""
    engine = create_async_engine(to_async_url(db_url))
    async with engine.begin() as conn:
        # 1. User
        await conn.execute(
            text(
                """
                INSERT INTO users (id, email, name, status, created_at, updated_at)
                VALUES (:id, 'legacy-gate-test@lytrium.tech', 'Legacy Gate User', 'active', now(), now());
                """
            ),
            {"id": LEGACY_USER_ID},
        )
        # 2. Account
        await conn.execute(
            text(
                """
                INSERT INTO accounts (id, user_id, name, currency, balance, created_at, updated_at)
                VALUES (:id, :user_id, 'Legacy Account', 'COP', 1000000.00, now(), now());
                """
            ),
            {"id": LEGACY_ACCOUNT_ID, "user_id": LEGACY_USER_ID},
        )
        # 3. Credit Card (only baseline columns)
        await conn.execute(
            text(
                """
                INSERT INTO credit_cards (
                    id, user_id, name, bank, credit_limit, current_debt,
                    cutoff_day, due_day, management_fee, monthly_interest_rate,
                    annual_interest_rate, currency, is_active, created_at, updated_at
                ) VALUES (
                    :id, :user_id, 'Legacy Visa', 'Banco Test', 5000000.00, 300000.00,
                    15, 5, 0.00, 0.0210, 0.2800, 'COP', true, now(), now()
                );
                """
            ),
            {"id": LEGACY_CARD_ID, "user_id": LEGACY_USER_ID},
        )
        # 4. Statement (only baseline columns)
        await conn.execute(
            text(
                """
                INSERT INTO credit_card_statements (
                    id, user_id, credit_card_id, billing_period, billing_period_start,
                    cutoff_date, due_date, previous_balance, new_purchases, billed_installments,
                    fees_total, interest_total, payments_received, statement_balance,
                    minimum_payment, status, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :card_id, '2026-09', '2026-08-16',
                    '2026-09-15', '2026-10-05', 0.00, 200000.00, 100000.00,
                    0.00, 0.00, 0.00, 300000.00, 50000.00, 'open', now(), now()
                );
                """
            ),
            {"id": LEGACY_STMT_ID, "user_id": LEGACY_USER_ID, "card_id": LEGACY_CARD_ID},
        )
        # 5. Transaction (only baseline columns)
        await conn.execute(
            text(
                """
                INSERT INTO credit_card_transactions (
                    id, user_id, credit_card_id, type, amount, description,
                    installments_total, installments_paid, occurred_at, source,
                    metadata, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :card_id, 'purchase', 300000.00, 'Legacy Purchase',
                    3, 0, now(), 'n8n_chat', '{}'::jsonb, now(), now()
                );
                """
            ),
            {"id": LEGACY_TX_ID, "user_id": LEGACY_USER_ID, "card_id": LEGACY_CARD_ID},
        )
        # 6. Installment (only baseline columns)
        await conn.execute(
            text(
                """
                INSERT INTO credit_card_installments (
                    id, user_id, credit_card_id, purchase_transaction_id, installment_number,
                    installments_total, principal_amount, scheduled_period, status,
                    paid_amount, interest_amount, total_amount, scheduled_due_date,
                    revision_id, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :card_id, :tx_id, 1,
                    3, 100000.00, '2026-10', 'pending',
                    0.00, 5000.00, 105000.00, '2026-11-05',
                    1, now(), now()
                );
                """
            ),
            {
                "id": LEGACY_INST_ID,
                "user_id": LEGACY_USER_ID,
                "card_id": LEGACY_CARD_ID,
                "tx_id": LEGACY_TX_ID,
            },
        )
        # 7. Statement charge (only baseline columns)
        await conn.execute(
            text(
                """
                INSERT INTO credit_card_statement_charges (
                    id, statement_id, charge_type, amount, description,
                    reference_id, status, paid_amount, created_at
                ) VALUES (
                    :id, :statement_id, 'fee', 15000.00, 'Legacy management fee',
                    NULL, 'pending', 0.00, now()
                );
                """
            ),
            {"id": LEGACY_CHARGE_ID, "statement_id": LEGACY_STMT_ID},
        )
    await engine.dispose()


async def verify_legacy_dataset_post_migration(db_url: str) -> None:
    """Verify legacy dataset columns and behavior after forward migration to HEAD."""
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import sessionmaker

    from app.credit.repository import CreditCardRepository

    engine = create_async_engine(to_async_url(db_url))
    async with engine.connect() as conn:
        async with conn.begin():
            # 1. Assert card columns receive correct backward-compatible defaults
            card_row = (
                await conn.execute(
                    text(
                        "SELECT canonical_mode, canonical_activated_at, provenance, lifecycle_status "
                        "FROM credit_cards WHERE id = :id"
                    ),
                    {"id": LEGACY_CARD_ID},
                )
            ).fetchone()
            assert card_row is not None, "Legacy card missing post-migration"
            canonical_mode, activated_at, card_prov, lifecycle_status = card_row
            assert canonical_mode is False, f"Expected canonical_mode=False, got {canonical_mode}"
            assert activated_at is None, f"Expected canonical_activated_at=None, got {activated_at}"
            assert card_prov == "legacy_unknown", (
                f"Expected provenance='legacy_unknown', got {card_prov}"
            )
            assert lifecycle_status == "active", (
                f"Expected lifecycle_status='active', got {lifecycle_status}"
            )

            # 2. Assert transaction columns receive correct defaults with no synthetic cycle
            tx_row = (
                await conn.execute(
                    text(
                        "SELECT debt_partition, provenance, cycle_id, original_amount, original_currency, "
                        "booked_amount, booked_currency, fx_rate FROM credit_card_transactions WHERE id = :id"
                    ),
                    {"id": LEGACY_TX_ID},
                )
            ).fetchone()
            assert tx_row is not None, "Legacy tx missing post-migration"
            tx_part, tx_prov, tx_cycle, orig_amt, orig_curr, booked_amt, booked_curr, fx_rate = (
                tx_row
            )
            assert tx_part == "legacy", f"Expected debt_partition='legacy', got {tx_part}"
            assert tx_prov == "legacy_unknown", (
                f"Expected provenance='legacy_unknown', got {tx_prov}"
            )
            assert tx_cycle is None, f"Expected cycle_id=None, got {tx_cycle}"
            assert orig_amt is None and orig_curr is None
            assert booked_amt is None and booked_curr is None
            assert fx_rate is None

            # 3. Assert installment columns receive correct defaults with no synthetic cycle
            inst_row = (
                await conn.execute(
                    text(
                        "SELECT debt_partition, provenance, cycle_id, original_amount, assessed_interest_amount "
                        "FROM credit_card_installments WHERE id = :id"
                    ),
                    {"id": LEGACY_INST_ID},
                )
            ).fetchone()
            assert inst_row is not None, "Legacy installment missing post-migration"
            inst_part, inst_prov, inst_cycle, inst_orig_amt, assessed_int = inst_row
            assert inst_part == "legacy", f"Expected debt_partition='legacy', got {inst_part}"
            assert inst_prov == "legacy_unknown", (
                f"Expected provenance='legacy_unknown', got {inst_prov}"
            )
            assert inst_cycle is None, f"Expected cycle_id=None, got {inst_cycle}"
            assert inst_orig_amt is None

            # 4. Assert statement received correct defaults with no synthetic cycle
            stmt_row = (
                await conn.execute(
                    text(
                        "SELECT debt_partition, provenance, cycle_id, minimum_payment "
                        "FROM credit_card_statements WHERE id = :id"
                    ),
                    {"id": LEGACY_STMT_ID},
                )
            ).fetchone()
            assert stmt_row is not None, "Legacy statement missing post-migration"
            stmt_part, stmt_prov, stmt_cycle, min_pmt = stmt_row
            assert stmt_part == "legacy", f"Expected debt_partition='legacy', got {stmt_part}"
            assert stmt_prov == "legacy_unknown", (
                f"Expected provenance='legacy_unknown', got {stmt_prov}"
            )
            assert stmt_cycle is None, f"Expected cycle_id=None, got {stmt_cycle}"

            # 5. Assert ZERO synthetic billing cycles or components were backfilled
            cycles_count = (
                await conn.execute(
                    text("SELECT count(*) FROM credit_card_billing_cycles WHERE user_id = :uid"),
                    {"uid": LEGACY_USER_ID},
                )
            ).scalar_one()
            assert cycles_count == 0, f"Expected 0 synthetic billing cycles, found {cycles_count}"

            comps_count = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM credit_card_statement_components WHERE user_id = :uid"
                    ),
                    {"uid": LEGACY_USER_ID},
                )
            ).scalar_one()
            assert comps_count == 0, (
                f"Expected 0 synthetic statement components, found {comps_count}"
            )

            # 6. Physical F-15 constraint check on legacy installment:
            # Settle to total_amount (105,000 COP > principal 100,000 COP): must physically succeed under v2 check!
            await conn.execute(
                text(
                    "UPDATE credit_card_installments SET paid_amount = 105000.00, status = 'paid' WHERE id = :id"
                ),
                {"id": LEGACY_INST_ID},
            )
            # Attempt to exceed total payable ceiling inside savepoint: must fail physically with CheckViolation
            check_violation_caught = False
            try:
                async with conn.begin_nested():
                    await conn.execute(
                        text(
                            "UPDATE credit_card_installments SET paid_amount = 105000.01 WHERE id = :id"
                        ),
                        {"id": LEGACY_INST_ID},
                    )
            except Exception as exc:
                err_msg = str(exc).lower()
                assert "credit_card_installments_paid_check_v2" in err_msg or "23514" in err_msg
                check_violation_caught = True
            assert check_violation_caught, (
                "Check constraint credit_card_installments_paid_check_v2 failed to reject 105000.01"
            )

            # Reset paid_amount back to 0.00 for debt query verification (clean savepoint / transaction)
            await conn.execute(
                text(
                    "UPDATE credit_card_installments SET paid_amount = 0.00, status = 'pending' WHERE id = :id"
                ),
                {"id": LEGACY_INST_ID},
            )

    # 7. Assert partitioned debt calculation isolates legacy debt with zero canonical leakage
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as sess:
        repo = CreditCardRepository(sess)
        debt_data = await repo.get_card_debt_partitioned(LEGACY_CARD_ID)
        assert debt_data["canonical_debt"] == Decimal("0.00"), (
            f"Expected canonical_debt=0, got {debt_data['canonical_debt']}"
        )
        # Legacy debt strictly includes legacy installment (105k) + legacy statement charge (15k) = 120k
        assert debt_data["legacy_debt"] == Decimal("120000.00"), (
            f"Expected legacy_debt=120000.00 (105k inst + 15k fee), got {debt_data['legacy_debt']}"
        )
        assert debt_data["total_debt"] == Decimal("120000.00"), (
            f"Expected total_debt=120000.00, got {debt_data['total_debt']}"
        )
        assert debt_data["total_debt"] == debt_data["canonical_debt"] + debt_data["legacy_debt"], (
            "Debt partition conservation violated"
        )

    await engine.dispose()


def run_scenario_a(db_url: str, cfg: Config) -> None:
    """Scenario A: Empty database -> linear HEAD (credit_v2_refund_type)."""
    print("[Scenario A] Starting: Empty database -> linear HEAD...")
    asyncio.run(reset_public_schema(db_url))
    rev_initial = asyncio.run(get_current_alembic_revision(db_url))
    if rev_initial is not None:
        raise RuntimeError(f"Expected empty DB at Scenario A start, found revision: {rev_initial}")

    command.upgrade(cfg, "head")

    rev_final = asyncio.run(get_current_alembic_revision(db_url))
    if rev_final != FINAL_LINEAR_HEAD:
        raise RuntimeError(
            f"Scenario A failed: expected head '{FINAL_LINEAR_HEAD}', found '{rev_final}'"
        )

    asyncio.run(verify_triggers_and_table_state(db_url))
    print(
        f"[Scenario A] PASS: Upgraded from empty database to final linear HEAD '{FINAL_LINEAR_HEAD}'."
    )


def run_scenario_b(db_url: str, cfg: Config) -> None:
    """Scenario B: Empty database -> prior head -> forward to linear HEAD."""
    print("[Scenario B] Starting: Empty database -> prior head -> forward to linear HEAD...")
    asyncio.run(reset_public_schema(db_url))

    # 1. Upgrade to prior production-compatible head
    command.upgrade(cfg, PRIOR_PRODUCTION_HEAD)
    rev_prior = asyncio.run(get_current_alembic_revision(db_url))
    if rev_prior != PRIOR_PRODUCTION_HEAD:
        raise RuntimeError(
            f"Scenario B failed: expected prior head '{PRIOR_PRODUCTION_HEAD}', found '{rev_prior}'"
        )

    # 2. Forward upgrade to final linear HEAD
    command.upgrade(cfg, "head")
    rev_final = asyncio.run(get_current_alembic_revision(db_url))
    if rev_final != FINAL_LINEAR_HEAD:
        raise RuntimeError(
            f"Scenario B failed: expected final head '{FINAL_LINEAR_HEAD}', found '{rev_final}'"
        )

    asyncio.run(verify_triggers_and_table_state(db_url))
    print(
        f"[Scenario B] PASS: Upgraded from prior head '{PRIOR_PRODUCTION_HEAD}' forward to '{FINAL_LINEAR_HEAD}'."
    )


def run_scenario_c(db_url: str, cfg: Config) -> None:
    """Scenario C: Prior head + representative legacy dataset -> forward to linear HEAD -> assert preservation."""
    print(
        "[Scenario C] Starting: Prior head + legacy dataset seeded BEFORE forward migrations -> linear HEAD..."
    )
    asyncio.run(reset_public_schema(db_url))

    # 1. Upgrade to prior head
    command.upgrade(cfg, PRIOR_PRODUCTION_HEAD)
    rev_prior = asyncio.run(get_current_alembic_revision(db_url))
    if rev_prior != PRIOR_PRODUCTION_HEAD:
        raise RuntimeError(
            f"Scenario C setup failed: expected prior head '{PRIOR_PRODUCTION_HEAD}', found '{rev_prior}'"
        )

    # 2. Seed representative legacy dataset BEFORE forward migrations
    asyncio.run(insert_legacy_seed_dataset(db_url))

    # 3. Execute forward migrations through linear HEAD
    command.upgrade(cfg, "head")
    rev_final = asyncio.run(get_current_alembic_revision(db_url))
    if rev_final != FINAL_LINEAR_HEAD:
        raise RuntimeError(
            f"Scenario C forward migration failed: expected head '{FINAL_LINEAR_HEAD}', found '{rev_final}'"
        )

    # 4. Verify post-migration invariants on seeded rows
    asyncio.run(verify_legacy_dataset_post_migration(db_url))
    asyncio.run(verify_triggers_and_table_state(db_url))

    print(
        f"[Scenario C] PASS: Successfully migrated legacy dataset to '{FINAL_LINEAR_HEAD}' with verified preservation."
    )


async def alpha_snapshot(db_url: str, upgraded: bool) -> tuple:
    engine = create_async_engine(to_async_url(db_url))
    try:
        async with engine.connect() as conn:
            values = (
                await conn.execute(
                    text(
                        "SELECT c.current_debt, c.monthly_interest_rate, t.amount, i.principal_amount, "
                        "i.interest_amount, i.paid_amount FROM credit_cards c "
                        "JOIN credit_card_transactions t ON t.credit_card_id = c.id "
                        "JOIN credit_card_installments i ON i.purchase_transaction_id = t.id "
                        "WHERE c.id = :id"
                    ),
                    {"id": LEGACY_CARD_ID},
                )
            ).one()
            if upgraded:
                cols = (
                    await conn.execute(
                        text(
                            "SELECT t.zero_interest, i.opening_balance_amount, i.interest_estimator_version "
                            "FROM credit_card_transactions t JOIN credit_card_installments i "
                            "ON i.purchase_transaction_id = t.id WHERE t.id = :id"
                        ),
                        {"id": LEGACY_TX_ID},
                    )
                ).one()
                assert tuple(cols) == (False, None, None)
            return tuple(values)
    finally:
        await engine.dispose()


def run_alpha_upgrade(db_url: str, cfg: Config) -> None:
    asyncio.run(reset_public_schema(db_url))
    command.upgrade(cfg, PRIOR_PRODUCTION_HEAD)
    asyncio.run(insert_legacy_seed_dataset(db_url))
    command.upgrade(cfg, "credit_v2_refund_type")
    before = asyncio.run(alpha_snapshot(db_url, False))
    command.upgrade(cfg, "head")
    assert asyncio.run(get_current_alembic_revision(db_url)) == FINAL_LINEAR_HEAD
    assert asyncio.run(alpha_snapshot(db_url, True)) == before
    asyncio.run(verify_triggers_and_table_state(db_url))
    print("[Scenario Alpha] PASS: baseline data preserved across Alpha upgrade.")


def main(argv: list[str] | None = None) -> int:
    """Run all 3 physical Alembic migration scenarios against guarded test database."""
    print("=== Nexum Alembic Physical Migration Scenarios ===")

    # 1. Enforce fail-closed guards
    db_url, _ = validate_gate_guards()

    # 2. Isolate test environment
    setup_isolated_test_environment()

    # 3. Build Alembic config
    cfg = get_alembic_config(db_url)

    try:
        run_scenario_a(db_url, cfg)
        run_scenario_b(db_url, cfg)
        run_scenario_c(db_url, cfg)
        run_alpha_upgrade(db_url, cfg)

        # Final step: restore clean HEAD schema for subsequent physical tests
        print("\n[Finalizing] Restoring clean empty schema at linear HEAD for test runner...")
        asyncio.run(reset_public_schema(db_url))
        command.upgrade(cfg, "head")
        asyncio.run(verify_triggers_and_table_state(db_url))
        print(f"[Finalizing] Test database is at clean linear HEAD '{FINAL_LINEAR_HEAD}'.")

        print("\n[PASS] All 3 physical Alembic migration scenarios certified successfully.")
        return 0
    except Exception as exc:
        print(
            f"\n[FATAL] Alembic physical scenario runner failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
