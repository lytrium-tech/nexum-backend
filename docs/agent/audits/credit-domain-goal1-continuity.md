# Nexum Credit Domain Goal 1 — Continuity State

## Checkpoint
- **checkpoint:** `goal1-final-publication-verified`
- **timestamp:** 2026-10-28T00:21:00-05:00
- **domain:** Credit Domain (`app/credit`)
- **current_role:** Single-tier Execution Worker
- **target_artifact:** `docs/agent/audits/credit-domain-goal1-audit.md`

---

## Completed Scope
0. **Phase E External Reconciliation and Canonical Design:**
   - Accessed NAS successfully through the authorized external route and reconciled Domain Map, Financial Engine archive, legacy context and pre-refactor SQL snapshot.
   - Read Outline Database Schema, Financial Engine, Ledger & Idempotency, Domain Map, Current State and Backend Changelog via MCP; recorded contradictions.
   - Inspected the Supabase MCP capability boundary before any production query. Because it permits writes and supplies no read-only credential/role attestation, did not connect and recorded `production_not_verified`.
   - Completed the required eight-column source-of-truth matrix, documentary entity/cardinality model, canonical invariants, Intelligence readiness, Founder decisions and proposed Goal 2 scope.
   - Phase E AntiGravity conversation `600274f6-3858-433f-80ba-2bdfe55597f8` was interrupted after it stalled without repository edits; Codex completed and independently owns the draft.
   - NAS publication readback matched local SHA-256 for both audit and continuity artifacts.
   - Outline summary published and read back as document `7059016a-90fb-4ccf-aabc-8b1a381db757` (`Credit Goal 1 — Audit & Canonical Model`).
   - Final local Credit pytest invocation stalled without output and was interrupted; it is not represented as passed or failed.
0. **Phase D PostgreSQL 17 Physical Gate:**
   - Validated exact disposable target CT101/VMID 101, host `lab`, canonical gate path, database/user `nexum_release_gate_test`, and absence of production identifiers.
   - Reset to an empty schema and migrated with real Alembic to head `obligations_v1_7_auto_payments` on PostgreSQL 17.11.
   - Physically reproduced SQLSTATE `23514` from `credit_card_installments_paid_check` when an interest-bearing installment was settled to `total_amount` (F-15).
   - Physically confirmed zero `event_id` indexes on Credit transactions/early payments (F-18 schema fact).
   - Rolled back all synthetic evidence rows and confirmed zero residue.
   - Verified final authoritative CT101 state `stopped`; destructive opt-in disabled.
   - AntiGravity Phase D conversation: `b76b4c60-09c9-4461-a799-a34f358e9ece`.
1. **Transaction and Session Ownership Boundary (Primary Question 25):**
   - Traced Unit of Work lifecycle across all mutating endpoints (`POST /cards`, `PATCH /cards/{id}`, `DELETE /cards/{id}`, `POST /purchases`, `POST /payments`, `POST /pay_early`, `POST /pay_early/preview`).
   - Proved that UoW begins in FastAPI routers via `uow = UnitOfWork(session)` and `async with uow.transaction():`.
   - Proved that commit/rollback is managed by `UnitOfWork.transaction()` in `app/core/uow.py:28-40` with outer safety in `get_db_session()` (`app/core/database.py:117-145`).
   - Proved that in all Credit mutating flows, `LedgerEvent` (`financial_events`), `Account` balance debit, `CreditCardTransaction`, `CreditCardInstallment` mutations, and `CreditCardStatementCharge` mutations share the **exact same `AsyncSession`** and execute within **one atomic PostgreSQL database transaction**.
   - Identified that conversational routes (`POST /api/v1/conversations/message`) do not open an explicit `UnitOfWork`, relying on `get_db_session()` session lifecycle.
2. **Idempotency Engine, Savepoint & Retry Flow (Primary Question 26):**
   - Proved idempotency is enforced exclusively at the Ledger level via `financial_events` and partial unique index `idx_financial_events_command_id`.
   - Traced savepoint flow: `LedgerRepository.record_event` executes inside `async with self.session.begin_nested():`. On `IntegrityError`, it rolls back to the savepoint and retrieves the existing event without aborting the parent transaction.
   - Proved that resource locks (`credit_cards`, `accounts`, `credit_card_installments`) are acquired **before** idempotency detection in all flows.
   - Documented returned values on retry: endpoints return DTOs with `status = "idempotent_retry"`, re-evaluating live debt dynamically rather than returning original historical responses.
   - Uncovered blind retry vulnerability (**F-16**): `CreditCardService` fails to verify if the retrieved idempotent event matches the operation type, card, or amount, returning valid retry DTOs for foreign operations.
   - Identified routes lacking `Idempotency-Key` or `command_id`: `POST /cards`, `PATCH /cards/{id}`, `DELETE /cards/{id}`.
3. **Resource Lock Ordering & Deadlock Hazards (Primary Question 27):**
   - Reconstructed physical lock order across all endpoints.
   - Proved critical inverted lock ordering between standard bill payments and early prepayments (**F-14**):
     - `create_payment`: Locks `accounts` -> `credit_cards` -> `installments` -> `charges`.
     - `create_early_payment` and `preview_early_payment`: Lock `credit_cards` -> `installments` -> `accounts`.
   - Proved concrete deadlock risk: concurrent standard and early payments on the same card and account produce cyclical waits and trigger PostgreSQL deadlock detection (`40P01`).
   - Discovered exclusive row locking and mutating transactions in the read-only preview endpoint (**F-17**): `preview_early_payment` locks card, installments, and account `FOR UPDATE` in a `uow.transaction()` while calling external FX services.
4. **Concurrency Audit (Primary Question 28):**
   - Proved card credit limit enforcement is safely serialized under PostgreSQL `READ COMMITTED` by row lock on `credit_cards`.
   - Proved account cash debit is safely serialized by row lock on `accounts`.
   - Audited concurrent standard vs early payment deadlock hazard (**F-14**).
   - Audited duplicate `command_id` replay across disparate operations (**F-16**).
   - Audited post-mutation response calculations: responses return transient in-memory derivations (`estimated_debt - applied_amount`) without re-querying database state, risking subtle divergence.
5. **ORM Identity-Map Hazards (Primary Question 29):**
   - Identified that `CreditCardRepository.get_card_debt` executes raw SQL (`session.execute(text(...))`), bypassing SQLAlchemy's identity map.
   - Identified that `_apply_payment_to_waterfall` mutates in-memory ORM objects without flushing before returning, meaning raw SQL queries within the same transaction would see stale data.
   - Analyzed savepoint rollback behavior: savepoint rollbacks in `record_event` discard `FinancialEvent` but retain previously loaded ORM objects in session memory (`expire_on_commit=False`).
6. **Goal Reservations Enforcement (Primary Question 30):**
   - Located authoritative reservation calculation in `GoalRepository.calculate_reserved_by_account` and spendable balance definition in `AccountService._set_available_balance` (`available_balance = account.balance - reserved`).
   - Proved that `CreditCardService.create_payment` and `create_early_payment` inspect only raw `account.balance`, completely ignoring Goal reservations (**F-13**).
   - Formulated mathematical proof showing that accounts with reserved goal funds can be drained down to zero spendable cash by credit card payments.
   - Classified severity as **Candidate P0** (Accounting Invariant Violation / Fund Reservation Compromise).
7. **Ownership & Authorization Audit (Primary Question 31):**
   - Verified strict card ownership checks across CRUD, purchases, and status endpoints.
   - Verified account ownership checks in payments, noting error handling inconsistency (`AccountForbiddenError` in payments vs generic `ValueError` in early payments).
   - Verified transaction, installment, and statement authorization through card linking.
8. **Financial Reconciliation & Money Conservation (Primary Question 32):**
   - Cataloged exact amount and currency storage across Accounts, Ledger, and Credit layers.
   - Proved that Account debit strictly equals Ledger event source amount.
   - Proved that Account debit equals the Ledger event source amount inside one database transaction.
   - Identified that end-to-end Credit debt reduction is not proven when statement charges exist: the waterfall can allocate to charges while the guard and response use installment-only debt.
   - Proved atomic rollback from the shared UoW structure; physical rollback scenarios remain pending CT101.
   - Proved that post-commit auditability is broken due to total lack of payment allocation junction records (**F-05**) and the omission of `credit_card_transactions` rows for early payments (**F-08**).
9. **Alembic Physical Constraints Cross-Check (Primary Question 33):**
   - Uncovered critical check constraint defect in `alembic/versions/v1_5_baseline.py:677` (**F-15**): `credit_card_installments_paid_check` enforces `paid_amount <= principal_amount`. When an installment incurs interest, paying it in full sets `paid_amount` to `total_amount > principal_amount`, causing PostgreSQL to abort transactions with `CheckViolation`.
   - Identified missing `command_id` on credit entities.
   - Identified unindexed and non-unique foreign keys on `credit_card_transactions.event_id` and `credit_card_early_payments.event_id`, causing sequential table scans on retry lookups (**F-18**).
10. **Updated Audit Sections & Candidate Findings Register:**
    - Updated report sections 1, 5, 12, 13, 21, 22, 23, 25, 28 while strictly preserving the 28 top-level section structure.
    - Added candidate findings **F-13** through **F-18** with exact file paths, line references, consequences, and Goal 2 remediation items.
    - Formalized 8 canonical financial invariants in Section 25.

---

## Evidence Pending
- **CT101 Physical Gate Verification (PostgreSQL 17):**
  1. Physical reproduction of `40P01` deadlock under concurrent execution of standard payments and early prepayments (or early payment previews) on CT101.
  2. Execution-plan cost at representative cardinality for unindexed `event_id` lookups; the missing index itself is physically confirmed.
  3. Physical confirmation of heap scan order non-determinism in `list_pending_installments_for_update`.
  4. Service-level physical scenarios for Goal reservations, cross-operation replay, and statement-charge response mismatch.
- **Production Database Validation:**
  1. Inspection of live accounts to verify if credit card payments have historically reduced account balances below `goal_reserved_amount`.
  2. Inspection of production databases for orphaned `credit_card_early_payments` missing from `credit_card_transactions`.
  3. Inspection of live databases for drift in persisted `credit_cards.current_debt`.
- **No required Goal 1 evidence remains pending.** Deferred physical scenarios and production verification are explicitly bounded findings, not hidden completion claims.

---

## Exact Next Step
- **Goal 2 authorization:** Approved on 2026-10-28 with the five Founder clarifications governing correction loops, legacy `amount`, non-fabricated legacy history, staged forward migrations, and Nexum-modeled waterfall provenance.
- **Goal 2 branch:** `feature/credit-canonical-closure`, based on verified `origin/main` `9fc5d14f724fd7c9defd620dde3e730e3d24feac`.
- **Phase A checkpoint:** Canonical additive schema foundation implemented and independently reviewed. It introduces explicit canonical/legacy partitioning, cycle/statement/component facts, canonical payments and exact allocations, adjustments/refunds/reversals, FX/provenance fields, UUID command identifiers, stable errors, request fingerprints, and a forward correction of the installment payment ceiling. No ambiguous legacy financial history is backfilled.
- **Phase A validation:** 23 synchronous foundation tests pass locally; Ruff, Alembic single-head validation, diff hygiene, unrelated-file review, and secret scan are green. Three async repository tests cannot start under the local Windows Python 3.14 Proactor event loop and remain assigned to the authoritative Linux/PostgreSQL gate; no production code was changed for that environment limitation.
- **Phase B checkpoint:** Canonical cycle, purchase, installment, and FX domain behavior implemented on top of Phase A foundation without synthesizing legacy history or reinterpreting legacy `amount`. Key behaviors:
  - New cards are canonical by backend policy (`canonical_mode=True`, `canonical_activated_at`, `provenance="manual_input"`); `canonical_mode` is removed from public create/update input schemas preventing client partition tampering.
  - Automatic activation frontier: migrated legacy cards activate automatically upon the first successful new canonical purchase command under the card lock, persisting a deterministic server UTC timestamp. Pre-existing transactions/installments remain in the legacy compatibility partition (`legacy_unknown`) and are never remapped or assigned cycles; only new commands establish the canonical frontier. Failed purchases roll back tentative activation atomically.
  - Pre-activation purchase rejection: backdated purchases predating canonical activation instant raise `CreditActivationBoundaryError`; historical cycles are never synthesized.
  - Closed and settled cycle mutation protection: purchases cannot leak into `closed` or `settled` billing cycles (`CreditBillingCycleClosedError`).
  - Financial time standardized to `financial_date()` (America/Bogota) exclusively, with zero domain reliance on host `date.today()`.
  - Deterministic cutoff boundaries and short-month clamping (`determine_cycle_for_date`, `calculate_credit_card_dates`). Purchases after cutoff deterministically belong to the next closing cycle.
  - Lock hierarchy: external FX snapshot/provider resolution strictly precedes canonical `SELECT FOR UPDATE` write lock; revalidation of card active state, currency, and debt/limit occurs under lock.
  - Amount validation: same-currency amounts must agree; cross-currency inputs cannot dictate booked amount when backend FX applies; backward compatibility for single-amount inputs preserved. Conflicting inputs reject with `CreditAmbiguousAmountError`.
  - Negative available credit is preserved without `max(0, ...)` masking; purchases are rejected when available credit is non-positive or insufficient.
  - Multi-currency canonical purchases preserve `original_amount`, `original_currency`, `booked_amount`, and `booked_currency`, with immutable FX snapshot evidence (`FXService`, `ExchangeRate`, `nexum_modeled` provenance). Legacy `amount` remains booked card currency alias.
  - Targeted FX error handling: maps established FX provider/validation errors to stable error while letting programming/database errors propagate and roll back.
  - Installment principal conservation: deterministic rounding distributes remainder cents so that installment principal amounts sum exactly to booked principal.
  - Real assessed interest is strictly separated from projected interest: rate configuration alone never books assessed interest, and single-installment purchases incur zero interest.
  - Minimum payment remains nullable/unknown unless supplied by issuer/statement fact without inventing formulas.
  - Mutually exclusive debt partitioning (`get_card_debt_partitioned`) guarantees canonical and legacy compatibility debt sum without double counting; no `unittest.mock` imports in production.
  - Lifecycle separation: posted purchases create/assign billing cycles, enforce credit limits under lock, generate installment schedules, and increase posted debt; pending purchases record immutable authorization/commercial/FX facts without cycle assignment, without installments, and without altering posted debt or available credit, and do not perform closed/settled cycle checks.
- **Phase B validation:** 37 focused unit tests pass in `tests/unit/test_credit_canonical_phase_b.py`; all 47 existing credit unit tests remain green (84 total passing). Ruff clean on all modified files; Alembic head remains verified at `credit_v2_canonical_check`.
- **Exact next step:** Proceed to Phase C (canonical billing cycle statements and statement components).
