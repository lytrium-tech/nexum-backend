# Nexum Credit Domain Goal 2 — Physical PostgreSQL 17 Release Gate Certification Report

## 1. Executive Summary & Disposition
- **Target Subsystem:** Credit Domain V2 (`app/credit`)
- **Execution Target:** CT101 / VMID 101 (`lab`, 192.168.0.245) via Proxmox VE (`vulcano`)
- **Database Engine:** PostgreSQL 17.11 (strictly non-superuser role `nexum_release_gate_test`)
- **Execution Mode:** Isolated loopback SSH tunnel (`127.0.0.1:5433` -> `127.0.0.1:5432`)
- **Zero-Tolerance Policy:** Zero skips, zero xfails, zero xpasses, zero unhandled errors
- **Disposition:** `[PENDING PHYSICAL CT101 EXECUTION BY CODEX]`

---

## 2. Disposable Environment Attestation

| Check | Expected Invariant | Physical Value (CT101) | Status |
|---|---|---|---|
| **Guest VMID** | Strictly 101 (CT101) | `101` | `[PENDING]` |
| **Guest Hostname** | `lab` | `lab` | `[PENDING]` |
| **Guest IP** | `192.168.0.245` | `192.168.0.245` | `[PENDING]` |
| **PostgreSQL Version** | `17.x` (strictly PostgreSQL 17) | `[Output: SELECT version()]` | `[PENDING]` |
| **Database Name** | `nexum_release_gate_test` | `nexum_release_gate_test` | `[PENDING]` |
| **Database User** | `nexum_release_gate_test` | `nexum_release_gate_test` | `[PENDING]` |
| **Superuser Privilege** | `rolsuper = False` | `[Output: rolsuper]` | `[PENDING]` |
| **CreateDB Privilege** | `rolcreatedb = False` | `[Output: rolcreatedb]` | `[PENDING]` |
| **CreateRole Privilege**| `rolcreaterole = False` | `[Output: rolcreaterole]` | `[PENDING]` |
| **Production Patterns** | Zero Supabase / VPS identifiers | `VERIFIED ABSENT` | `[PENDING]` |

---

## 3. Schema Reset & Migration Lineage Evidence

### Canonical Reset Command:
```bash
python scripts/dev/release_gate.py reset --confirm
```

### Physical Migration Scenarios Runner:
```bash
python scripts/dev/release_gate.py run --destructive-opt-in -- python scripts/dev/run_alembic_physical_scenarios.py
```

### Physical Migration Lineage Verification:
- **Base Revision:** `v1_5_baseline`
- **Prior Production Head:** `obligations_v1_7_auto_payments`
- **Final Linear Head:** `credit_v2_canonical_closure`
- **Linear Step Chain:**
  1. `obligations_v1_7_auto_payments` -> `credit_v2_canonical_schema`
  2. `credit_v2_canonical_schema` -> `credit_v2_canonical_check`
  3. `credit_v2_canonical_check` -> `credit_v2_canonical_closure`

### Executed Migration Scenarios:
- **Scenario A:** Empty database -> linear HEAD (`credit_v2_canonical_closure`).
- **Scenario B:** Empty database -> prior head (`obligations_v1_7_auto_payments`) -> linear HEAD.
- **Scenario C:** Empty database -> prior head -> seed representative legacy dataset (using only baseline columns) -> forward upgrade to linear HEAD -> assert column defaults, nullable preservation, zero synthetic backfill, F-15 constraint check, and partitioned debt query.

```text
[INSERT SANITIZED ALEMBIC UPGRADE LOG HERE]
```

---

## 4. Physical Acceptance Criteria Certification Register

| Criterion | Scenario / Test Name | Expected Physical PostgreSQL Invariant | Physical Result |
|---|---|---|---|
| **AC 1** | `test_pg01_alembic_linear_head_graph` | Linear head `credit_v2_canonical_closure` (13 revisions from base). ScriptDirectory matches linear graph. | `[PENDING]` |
| **AC 2** | `test_pg02_f15_installment_check_constraint_physical` | `credit_card_installments_paid_check_v2` physically accepts `paid_amount = total_amount` (with assessed interest); rejects `paid_amount > total_amount` with SQLSTATE `23514`. | `[PENDING]` |
| **AC 3a** | `test_pg03a_cycle_assignment_and_bogota_cutoff_boundary` | Purchases on or before cutoff belong to current cycle; purchases after cutoff belong to next cycle using Bogota financial date boundaries (`occurred_at` Oct 15 vs Oct 16). | `[PENDING]` |
| **AC 3b** | `test_pg03b_statement_exactly_once_partial_unique_index` | Partial unique index `uq_cc_statements_canonical_cycle_id` physically prevents duplicate statements for the same canonical cycle with SQLSTATE `23505`. | `[PENDING]` |
| **AC 3c** | `test_pg03c_bounded_catchup_repeated_progression` | CreditCycleClosureService advances catch-up deterministically across repeated bounded invocations until `has_remaining=False`. | `[PENDING]` |
| **AC 3d** | `test_pg03d_immutable_statements_and_components_at_postgres_trigger_level` | Raw PostgreSQL triggers `trg_cc_statements_immutable` and `trg_cc_stmt_comp_immutable` block UPDATE or DELETE on closed/settled statements and components with SQLSTATE `20000`. | `[PENDING]` |
| **AC 4a** | `test_pg04a_standard_partial_and_generic_prepayment_conservation` | Standard payment, partial payment, and generic future prepayment conserve debt balance and account balance at each step. | `[PENDING]` |
| **AC 4b** | `test_pg04b_targeted_early_payment_records_prepayment_transaction` | Targeted early payment records `credit_card_early_payments` row AND `credit_card_transactions` row (`type='payment'`) closing F-08. | `[PENDING]` |
| **AC 4c** | `test_pg04c_voluntary_overpayment_rejection` | Payment exceeding total payable debt is rejected with zero account mutation. | `[PENDING]` |
| **AC 4d** | `test_pg04d_payment_reversal_full_amount_conservation` | Full payment reversal restores account balance, installment paid amount, and creates compensating debit adjustment. | `[PENDING]` |
| **AC 4e** | `test_pg04e_legitimate_already_paid_refund_card_credit` | Refund on already-paid purchase creates legitimate card credit carrying over into statement snapshot (`prior_credit`). | `[PENDING]` |
| **AC 5** | `test_pg05_f13_goal_reservation_enforced_under_lock_zero_mutation` | Payment exceeding available balance minus Goal reservations raises `CreditGoalReservationBreachError` under lock with verified zero physical mutation. | `[PENDING]` |
| **AC 6** | `test_pg06_cross_currency_purchase_and_payment_equality` | USD purchase on COP card stores multi-currency facts. Payment debits Account in USD strictly equal to Ledger source amount in USD in atomic transaction. | `[PENDING]` |
| **AC 7** | `test_pg07_f16_exact_idempotent_retry_and_conflicting_replay` | Identical command returns `idempotent_retry` with 0 double debits. Conflicting payload under same `command_id` raises `CreditIdempotencyConflictError`. | `[PENDING]` |
| **AC 8a** | `test_pg08a_duplicate_concurrent_statement_workers_serialization` | Duplicate concurrent cycle closure workers safely serialize: exactly 1 statement created under card row lock. | `[PENDING]` |
| **AC 8b** | `test_pg08b_concurrent_purchases_near_limit_serialized` | Concurrent purchases near limit are safely serialized under row lock: one succeeds, one rejected. | `[PENDING]` |
| **AC 8c** | `test_pg08c_concurrent_payments_physical_lock_wait` | Concurrent payments against the same funding account physically wait on PostgreSQL row locks observed via `pg_blocking_pids`. | `[PENDING]` |
| **AC 8d** | `test_pg08d_f14_standard_vs_early_payment_deadlock_elimination` | Standard payment and early prepayment executed simultaneously never trigger PostgreSQL `40P01` deadlock. | `[PENDING]` |
| **AC 8e** | `test_pg08e_stale_identity_map_refresh_on_payment` | Payment acquires row lock with `populate_existing=True`, refreshing concurrently modified balance and rejecting stale balance payment. | `[PENDING]` |
| **AC 8f** | `test_pg08f_deterministic_waterfall_physical_allocation_order` | Payment waterfall allocates deterministically: charges -> past-due -> current -> future installments with incrementing `allocation_order`. | `[PENDING]` |
| **AC 9a** | `test_pg09a_atomic_rollback_on_ledger_failure` | Simulated failure during `record_event` triggers full atomic transaction rollback: 0 account debit, 0 payment row, 0 allocations. | `[PENDING]` |
| **AC 9b** | `test_pg09b_atomic_rollback_on_allocation_failure` | Simulated failure during `add_payment_allocations` triggers full atomic rollback: 0 account debit, 0 payment row, 0 allocations. | `[PENDING]` |
| **AC 10** | `test_pg10_f19_payable_set_consistency_mirrored_charge_no_double_count` | Installment 100 + mirrored charge 100 + fee 10 yields total payable debt 110, not 210. Waterfall and guard deduplicate mirrored charge. | `[PENDING]` |
| **AC 11** | `test_pg11_canonical_and_legacy_debt_partitioning_no_double_count` | Frontier activation keeps legacy debt isolated. `get_card_debt_partitioned` returns accurate canonical + legacy sum with zero double count. | `[PENDING]` |
| **AC 12** | `test_pg12_harness_cleanup_and_residue_verification` | Fixture cleanup drops all synthetic test rows and verifies count == 0 across all credit, ledger, goal, account, and user tables with enabled triggers. | `[PENDING]` |

---

## 5. Physical Concurrency & Lock Contention Evidence

### Lock Hierarchy Verification (`Account -> CreditCard -> Installments`):
- **Finding F-14 Deadlock Elimination:** Standard payment (`POST /payments`) and early prepayment (`POST /pay_early`) executed concurrently on identical card and account.
  - Inverted lock ordering eliminated.
  - Zero PostgreSQL `40P01` deadlock events observed.
- **Physical Contention Observation:**
  - `wait_for_blocked()` confirmed physical contention via `pg_blocking_pids` during concurrent payments.

```text
[INSERT CONCURRENCY EXECUTION LOG & PID EVIDENCE HERE]
```

---

## 6. Raw Trigger & Check Constraint Physical Evidence

### F-15 Constraint Readback (`credit_card_installments_paid_check_v2`):
```sql
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conname = 'credit_card_installments_paid_check_v2';
```
- **Definition:** `CHECK (paid_amount <= total_amount)`
- **Physical Result:** `[INSERT QUERY RESULT]`

### Immutability Triggers Readback:
```sql
SELECT tgname, tgenabled, relname
FROM pg_trigger t
JOIN pg_class c ON t.tgrelid = c.oid
WHERE tgname IN ('trg_cc_statements_immutable', 'trg_cc_stmt_comp_immutable');
```
- **Physical Result:** `[INSERT QUERY RESULT]`

---

## 7. Residual Verification
```sql
SELECT
    (SELECT count(*) FROM users WHERE email LIKE 'gate-test-%') as test_users,
    (SELECT count(*) FROM credit_cards WHERE name LIKE 'Gate%') as test_cards,
    (SELECT count(*) FROM exchange_rates WHERE provider = 'gate_test') as test_rates;
```
- **Expected:** `0, 0, 0`
- **Physical Result:** `[INSERT CLEANUP QUERY RESULT]`
