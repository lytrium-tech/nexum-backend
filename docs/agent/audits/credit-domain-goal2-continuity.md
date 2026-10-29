# Nexum Credit Domain Goal 2 — Continuity State

## Checkpoint
- **checkpoint:** `goal2-phase-f-harness-ready`
- **timestamp:** 2026-10-29T02:15:00-05:00
- **domain:** Credit Domain (`app/credit`)
- **current_role:** Single-tier Execution Worker
- **target_artifact:** `docs/agent/reports/credit-domain-goal2-physical-release-gate-report.md`

---

## Completed Scope

0. **Phase A: Canonical Additive Schema Foundation (Commit `38aac22`):**
   - Implemented partitioned financial foundation for Credit V2: `credit_cards`, `credit_card_billing_cycles`, `credit_card_statements`, `credit_card_statement_components`, `credit_card_statement_facts`, `credit_card_transactions`, `credit_card_installments`, `credit_card_payments`, `credit_card_payment_allocations`, `credit_card_adjustments`, `credit_card_adjustment_allocations`, `credit_card_early_payments`.
   - Forward migration `credit_v2_canonical_check` corrected legacy installment check constraint `credit_card_installments_paid_check_v2` to enforce `paid_amount <= total_amount` (closing F-15).
   - Indexed foreign keys `event_id` across credit tables (closing F-18).
   - Added stable exception types and request fingerprints.

1. **Phase B: Canonical Cycles, Purchases, Installments & FX (Commit `b3e1d11`):**
   - Automatic activation frontier: cards activate upon first canonical purchase, leaving historical legacy data in `legacy_unknown` partition.
   - Financial time standardized to America/Bogota (`financial_date()`).
   - Cutoff boundaries and short-month clamping implemented.
   - Multi-currency canonical purchases record `original_amount`, `original_currency`, `booked_amount`, `booked_currency`, and immutable FX snapshot evidence.
   - Installment principal conservation preserves cents distribution.

2. **Phase C: Canonical Immutable Statements & Scheduled Cycle Closure (Commit `70f6168`):**
   - Migration `credit_v2_canonical_closure` introduced database immutability triggers `trg_cc_statements_immutable` and `trg_cc_stmt_comp_immutable` preventing raw SQL UPDATE or DELETE on closed/settled statements and components (SQLSTATE `20000`).
   - Partial unique index `uq_cc_statements_canonical_cycle_id` guarantees exactly-once statement materialization for canonical cycles.
   - Statement reconciliation: `statement_balance = sum(debit components) - sum(credit components)`.
   - Legitimate net negative balances carry forward as explicit positive-magnitude `prior_credit` components without silent clamping.
   - Operational batch cycle closure runner in `CreditCycleClosureService` and `scripts/cron/close_credit_cycles.py`.

3. **Phase D: Canonical Payments, Allocations, Prepayments, Reversals & Concurrency (Commit `f30cc61`):**
   - Closed F-13: Authoritative Goal reservations strictly enforced under row lock (`account.balance - reserved >= payment_amount`).
   - Closed F-14: Global lock hierarchy `Account -> CreditCard -> Installments` enforced across all mutating payment and prepayment flows; preview refactored to lock-free read-only operation.
   - Closed F-16: Deep idempotency guard fingerprints `(user_id, operation_type, card_id, account_id, amounts, currencies)`.
   - Closed F-19: Total payable debt unified across guard, waterfall, and response debt; statement charges mirroring installments deduplicated.
   - Full payment reversal restores Account balance, restores installment paid amount, and records compensating debit adjustment.
   - Targeted early prepayment (`/pay_early`) creates `credit_card_early_payments` record AND `credit_card_transactions` prepayment record (closing F-08).

4. **Phase E: Consumer Alignment, API Contracts & Frontend Handoff (Commit `b2aa7d8`):**
   - Standardized `app/credit/router.py` endpoints with FastAPI dependency injection and error handling via `nexum_error_handler`.
   - Intelligence and conversational services canonicalized; legacy SQL views marked deprecated.
   - OpenAPI regenerated and contract verified (`test_committed_openapi_matches_runtime_app_openapi`).
   - Backend->Frontend handoff document published at `docs/handoff/backend/credit-canonical-v2-frontend-handoff.md`.

5. **Phase F: Persistent Physical Release Gate Harness & Certification Ready (Current Checkpoint):**
   - Dedicated physical release-gate test suite: `tests/postgres/test_credit_canonical_release_gate.py` (25 test cases) covering all 12 acceptance criteria:
     1. Alembic linear head from empty (`None`) through `credit_v2_canonical_closure`; upgrade/downgrade from prior production head `obligations_v1_7_auto_payments`.
     2. F-15 corrected installment check constraint (`credit_card_installments_paid_check_v2`) physically accepts assessed-interest full settlement and rejects `paid_amount > total_amount` with SQLSTATE `23514`.
     3. Billing cycle cutoff assignment with Bogota financial date (`occurred_at` boundary timestamps for Oct 15 vs Oct 16); statements exactly once via partial unique index `uq_cc_statements_canonical_cycle_id`; bounded catch-up repeated progression; raw PostgreSQL immutability triggers `trg_cc_statements_immutable` and `trg_cc_stmt_comp_immutable` block UPDATE/DELETE with SQLSTATE `20000`.
     4. Standard payment waterfall, partial payment, future principal prepayment conservation, targeted early payment with prepayment transaction record (F-08), voluntary overpayment rejection, full payment reversal, and legitimate refund card credit (`prior_credit`).
     5. F-13 authoritative Goal reservation rejection under lock with verified physical zero-mutation.
     6. Cross-currency purchase and payment Account/Ledger equality inside one atomic transaction.
     7. F-16 exact idempotent retry and conflicting replay rejection across operation types and amounts.
     8. Concurrency: duplicate statement workers serialization, purchases near limit under lock, concurrent payments with physical lock wait (`wait_for_blocked`), F-14 standard vs early payment deadlock elimination (zero `40P01`), stale identity-map refresh with `populate_existing=True`, and deterministic waterfall physical allocation order (`allocation_order` 1, 2, 3...).
     9. Atomic rollback on Ledger failure AND atomic rollback on allocation failure.
     10. F-19 complete payable set consistency: mirrored statement charges never double counted.
     11. Explicit canonical debt + legacy compatibility debt without double counting.
     12. Synthetic row cleanup and zero-residue certification with fail-closed statement trigger verification.
   - Operational migration runner: `scripts/dev/run_alembic_physical_scenarios.py` executing:
     - **Scenario A:** Empty DB -> final linear head (`credit_v2_canonical_closure`).
     - **Scenario B:** Empty DB -> prior head (`obligations_v1_7_auto_payments`) -> final linear head.
     - **Scenario C:** Empty DB -> prior head -> seed representative legacy dataset using ONLY columns available at prior head -> forward migration to final head -> assert column defaults, nullable preservation, zero synthetic backfill, F-15 constraint check, and partitioned debt query.
     - Final cleanup restores clean schema at final head.
     - **Driver Trigger Status Normalization:** Fixed CT101 PostgreSQL driver manifestation where `pg_trigger.tgenabled` ("char" type) returns `b'O'` instead of `'O'`. Added `normalize_trigger_status()` and `validate_trigger_status_map()` to safely decode bytes and strip strings while strictly enforcing fail-closed rejection of disabled (`D`, `b'D'`), replica (`R`), or unknown states.
   - Non-DB local contract test suite: `tests/unit/test_credit_fixture_contracts.py` (16/16 passing) validating mapped attribute instantiations, command_id requirement on all mutating service calls, repository boundaries, and fail-closed trigger status normalization across str and bytes.
   - Enhanced `tests/unit/postgres_helpers.py` with fail-closed trigger enable/disable, `verify_statement_triggers_enabled`, mapped attribute queries, and complete residue checks across 25 tables.
   - Updated `scripts/dev/run_physical_gate.py` with `--scenarios` / `--with-scenarios` and deterministic suite selection (`--suite credit|obligations|all`, `--credit`).
   - Passed all 168 canonical credit unit tests, 25 runner unit tests, 4 alembic lineage unit tests, and 16 contract unit tests locally.

---

## Exact Instructions for Codex CT101 Physical Execution

Codex should execute the physical release-gate suite on disposable container CT101 (Vulcano, VMID 101) following independent code review:

### Prerequisites:
1. PVE API and SSH access to lab environment (`nexum-lab`, 192.168.0.245).
2. Environment variables set:
   ```bash
   export NEXUM_ALLOW_DESTRUCTIVE_TEST_DB=1
   export NEXUM_PVE_API_URL="https://192.168.0.200:8006/api2/json"
   export NEXUM_PVE_TOKEN_ID="..."
   export NEXUM_PVE_TOKEN_SECRET="..."
   ```

### Execution Steps:
1. **Preflight validation:**
   ```bash
   python scripts/dev/release_gate.py validate
   ```
2. **Clean schema reset on CT101:**
   ```bash
   python scripts/dev/release_gate.py reset --confirm
   ```
3. **Execute Physical Alembic Migration Scenarios (A, B, and C):**
   ```bash
   python scripts/dev/release_gate.py run --destructive-opt-in -- python scripts/dev/run_alembic_physical_scenarios.py
   ```
4. **Execute Credit physical release gate suite:**
   ```bash
   python scripts/dev/release_gate.py run --destructive-opt-in -- python scripts/dev/run_physical_gate.py --suite credit
   ```
5. **Execute All physical release gates (Full regression):**
   ```bash
   python scripts/dev/release_gate.py run --destructive-opt-in -- python scripts/dev/run_physical_gate.py --suite all
   ```
6. **Post-execution cleanup & container stopped state verification:**
   ```bash
   python scripts/dev/release_gate.py status
   ```
   Authoritative status must read: `LXC Status: stopped` and destructive opt-in returned to `0`.
7. **Fill Evidence Report:**
   Populate `docs/agent/reports/credit-domain-goal2-physical-release-gate-report.md` with sanitized output, test counts, and execution metrics.
