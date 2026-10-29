# Credit Goal 2 — Final Backend Evidence and Release Gate Report

**Report date:** 2026-10-29
**Recovery checkpoint:** `67f0e1d`
**Branch:** `feature/credit-canonical-closure`
**Final Alembic head:** `credit_v2_refund_type`
**Decision:** Implementation and evidence generation complete; awaiting Founder review and explicit GO/NO-GO for Frontend Sync + QA. Codex does not certify readiness for frontend on the Founder's behalf.

## 1. Gate summary

| Gate | Result | Evidence |
|---|---|---|
| Canonical model, financial invariants, legacy boundary | **PASS** | Forward migrations and service contracts; representative legacy PostgreSQL dataset in scenario C |
| Alembic empty/prior-head/legacy migrations | **PASS** | Physical runner scenarios A/B/C, exit 0 |
| Goal reservations (F-13) | **PASS** | `test_pg05_f13_goal_reservation_enforced_under_lock_zero_mutation` |
| Deadlock regression (F-14) | **PASS** | `test_pg08d_f14_standard_vs_early_payment_deadlock_elimination`; no SQLSTATE 40P01 |
| Installment constraint (F-15) | **PASS** | `test_pg02_f15_installment_check_constraint_physical`; assessed-interest settlement accepted, over-ceiling rejected |
| Material idempotency replay (F-16) | **PASS** | `test_pg07_f16_exact_idempotent_retry_and_conflicting_replay` |
| Statement/payable set (F-19) | **PASS** | `test_pg10_f19_payable_set_consistency_mirrored_charge_no_double_count` |
| No synthetic legacy history / no double count | **PASS** | `test_pg11_canonical_and_legacy_debt_partitioning_no_double_count`; scenario C |
| Payment persistence order and allocation FK | **PASS** | Parent is assigned UUID and flushed before FK-dependent allocations; PG04b, PG08f, and PG09b passed after correction |
| Account/Ledger/Credit atomic rollback | **PASS** | PG09a Ledger failure and PG09b allocation failure assert no Account debit, Payment, Payment transaction, or allocations |
| Cross-currency purchase/payment evidence | **PASS** | PG06; source Account debit equals Ledger source amount; booked card amount uses injected canonical FX snapshot |
| Full Credit PostgreSQL suite | **PASS, 29/29** | Final post-correction run on CT101 PostgreSQL 17.11, no failed/rejected tests |
| OpenAPI artifact/runtime parity | **PASS** | Canonical exporter SHA-256 `988bc0b7ddfc9796de6f925db46e84fcb9d4e86ebab266fdbc239efc50ea08c1`; strict parity test 1/1 with locked FastAPI/Pydantic |
| Lint | **PASS** | Ruff on all changed Python implementation, migration, helper and test files |
| Working tree and branch safety | **PASS** | Branch is isolated from main; changed paths are Credit backend/tests/migrations/docs only; no frontend files changed |
| Cleanup / CT stopped | **PASS** | Final release_gate reset confirmed public schema empty; status readback: `LXC Status: stopped`, destructive opt-in `DISABLED (0)` |
| Optional Obligations regression | **FAIL (out of scope)** | Separate attempt: 49 passed, 45 errors, 1 failed due unrelated Obligations cases; no Obligations changes |
| Windows Python 3.14 async test execution | **BLOCKED (environment)** | Proactor startup hangs before test body in socketpair / pytest-asyncio runner setup; Linux PostgreSQL gate is authoritative |

## 2. Physical environment and scope

CT101 is the authorized isolated disposable Vulcano guest (VMID 101, node `vulcano`, host `lab`, 192.168.0.245). Tests used the dedicated loopback SSH tunnel on port 5433 and the disposable database/user `nexum_release_gate_test`; no production database, Supabase, credential, service, or cron was used. Database engine observed in the prior physical run: PostgreSQL 17.11. The runner enforces the test database/user/loopback identity and stops CT101 in its finally path.

No frontend code, production resources, or main branch were modified. No merge, deployment, production migration, scheduler activation, or Founder QA occurred.

## 3. Migration evidence

The final linear chain is:

```text
v1_5_baseline
  -> obligations_v1_7_auto_payments
  -> credit_v2_canonical_schema
  -> credit_v2_canonical_check
  -> credit_v2_canonical_closure
  -> credit_v2_refund_type
```

- **A — empty:** Alembic from empty database to `credit_v2_refund_type`: **PASS**.
- **B — prior head:** empty -> `obligations_v1_7_auto_payments` -> final head: **PASS**.
- **C — representative legacy:** prior head -> deterministic synthetic legacy data -> forward to final head: **PASS**.
- Refund migration is additive and forward-only. Downgrade refuses before mutation when refund transaction facts exist.
- F-15 is an additive corrected check, not a historical migration edit.

Scenario C preserved legacy records as `legacy_unknown` / `legacy`; retained NULL historical cycle, original amount/currency, booked amount/currency, FX and assessed interest where unknown; created no synthetic historical cycles/statements and assigned no legacy purchase to an invented statement. Debt conservation readback: canonical = 0.00, legacy compatibility = 120,000.00 (105,000.00 installment + 15,000.00 statement charge), total = 120,000.00. Thus canonical + legacy compatibility debt does not double count.

## 4. Physical Credit suite

Final run: `python scripts/dev/run_physical_gate.py --suite credit`, wrapped by `scripts/dev/release_gate.py` with the required destructive test opt-in and disposable DB tunnel. **29 passed, 0 failed, 0 rejected/skipped.** Suite teardown verified zero synthetic residue and enabled statement triggers.

Covered scenarios include:

- F-15 constraint accepts legitimate assessed-interest total settlement and rejects paid amount above the payable ceiling (SQLSTATE 23514).
- Bogota financial-date cycle assignment around the cutoff, short-month boundaries, statement exactly-once uniqueness, bounded catch-up, and raw PostgreSQL statement/component immutability.
- Standard/partial payment, future principal prepayment, targeted `/pay_early`, voluntary overpayment rejection, full reversal, and refund-created card credit.
- F-13 Goal reservations: a reserved-cash breach produces no payment/ledger/account/allocation mutation; reservation state is not changed.
- F-16 identical idempotent retry causes no duplicate debit; materially changed replay conflicts.
- F-14 payment vs early payment and concurrent payment contention; no deadlock; observed physical blocker via `pg_blocking_pids`.
- Concurrent purchases near limit, duplicate statement workers, and stale ORM identity-map refresh under row lock.
- PG08f checks persisted allocation order: overdue principal (100,000), closed statement fee (10,000), current principal (40,000), with orders 1/2/3.
- PG09a/09b rollback after Ledger or allocation failure: Account balance unchanged and no Payment, payment transaction, Ledger residue, or allocation survives.
- F-19 payment guard, waterfall, allocations, and resulting debt use a unified set with mirrored installment charges excluded.
- Canonical + legacy debt partition and complete cleanup.

### Cross-currency evidence

PG06 records a USD 50 purchase on a COP card at injected test FX 4,000, booked COP 200,000, then a USD 50 payment. Account debit and Ledger `FinancialEvent` both equal USD 50; Credit payment source fields are USD 50, booked fields COP 200,000, FX rate 4,000 and persisted FX snapshot ID are present on Payment and transaction. All mutations are in one transaction.

## 5. Corrections and regression verification

1. Physical harness failures were traced and corrected: Bogota activation timestamps, PostgreSQL naive/aware timestamp fixture values, savepoint behavior, rollback-expired ORM IDs, exact stable overpayment exception, committed-card setup for concurrent workers, and waterfall expectation aligned to the approved Nexum policy.
2. A real multicurrency defect was found: Credit payments and preview bypassed the canonical injected `FXService` provider, and payment FX was not persisted as reproducible evidence. Credit now obtains its rate/snapshot from the existing FX infrastructure before acquiring write locks, applies the same provider in preview, and stores FX snapshot/rate on Payment and transaction. Physical PG06 confirms source/booked reconciliation.
3. Refund creation was rejected by a legacy transaction-type check. New migration `credit_v2_refund_type` adds refund support without rewriting the historical migration and protects downgrade when refund facts exist. Physical PG04e passes.
4. Physical review found generic payment allocations were being flushed before the parent Payment row while PostgreSQL enforces the FK. After reordering, PostgreSQL then exposed a NULL Payment UUID in targeted early-payment allocations; assigning the canonical UUID before allocations fixed the root cause. The final physical run passed PG04b, PG08f (persisted order and aggregate conservation), and PG09b (atomic allocation failure) as part of 29/29.
5. OpenAPI parity initially failed only in CT's reused Python 3.13 environment (FastAPI 0.162.0 / Pydantic 2.14.2) which is newer than repository lock (FastAPI 0.136.3 / Pydantic 2.13.4). No artifact was rewritten from that environment. Under locked versions, canonical export retains SHA-256 above and parity is 1/1.

## 6. Compatibility and API contract

- Existing route family remains `/api/v1/credit`.
- Legacy `amount` retains historical card-base/booked currency semantics; it is never silently redefined as foreign original amount. New cross-currency clients use `original_amount` / `original_currency`; responses expose `booked_amount` / `booked_currency` plus FX snapshot provenance.
- Manual-card waterfall is explicitly Nexum-modeled simulation policy, not a universal issuer rule. Future issuer-authoritative allocation facts can coexist with historical modeled allocation records.
- Unknown minimum payment remains nullable; no universal percentage/floor or rate-triggered assessed interest is invented.
- Legacy debt remains visibly partitioned and included once; ambiguous history is not retroactively synthesized.
- API contract and frontend consumer details are in `docs/handoff/backend/credit-canonical-v2-frontend-handoff.md`.

## 7. Final cleanup readback

```text
NEXUM_ALLOW_DESTRUCTIVE_TEST_DB=1 python scripts/dev/release_gate.py reset --confirm
python scripts/dev/release_gate.py status
Observed: `[PASS] Schema reset complete ... (empty, synthetic state)`; `LXC Status: stopped`; `Destructive Opt: DISABLED (0)`.
```

The final cleanup removed all synthetic rows/schema objects. The optional cross-domain Obligations attempt also invoked the same lifecycle reset and stopped CT101. Exact local transfer archive and helper artifacts are removed before commit.

## 8. Founder decision

This report supplies backend implementation and gate evidence. **GO/NO-GO for Frontend Sync + QA is reserved to the Founder.** Until that decision is recorded, do not modify frontend, begin frontend QA, merge, deploy, activate scheduling, or graduate Credit.
