# Nexum Credit Domain Goal 1 — Continuity State

## Checkpoint
- **checkpoint:** `phase-c-financial-invariants-draft`
- **timestamp:** 2026-10-27T23:55:00-05:00
- **domain:** Credit Domain (`app/credit`)
- **current_role:** Single-tier Execution Worker
- **target_artifact:** `docs/agent/audits/credit-domain-goal1-audit.md`

---

## Completed Scope
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
  2. Physical confirmation of `CheckViolation: credit_card_installments_paid_check` when applying payments to interest-bearing installments on real PostgreSQL.
  3. Execution of `EXPLAIN ANALYZE` on `get_transaction_by_event` to measure table scan cost on unindexed `event_id`.
  4. Physical confirmation of heap scan order non-determinism in `list_pending_installments_for_update`.
- **Production Database Validation:**
  1. Inspection of live accounts to verify if credit card payments have historically reduced account balances below `goal_reserved_amount`.
  2. Inspection of production databases for orphaned `credit_card_early_payments` missing from `credit_card_transactions`.
  3. Inspection of live databases for drift in persisted `credit_cards.current_debt`.

---

## Exact Next Step
- **Codex independent verification, checkpoint commit/push, then Phase D PostgreSQL:**
  1. Codex reviews the updated audit (`docs/agent/audits/credit-domain-goal1-audit.md`) and continuity (`docs/agent/audits/credit-domain-goal1-continuity.md`).
  2. Codex independently verifies the control flow proofs, lock inversion deadlock hazard, Alembic check constraint conflict, and goal reservation bypass findings.
  3. Codex commits and pushes the Phase C checkpoint.
  4. Progression to Phase D PostgreSQL physical validation on CT101.
