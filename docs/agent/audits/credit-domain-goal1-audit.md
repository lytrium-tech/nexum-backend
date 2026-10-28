# Nexum Credit Domain Goal 1 — Phase C Financial Invariants & Technical Audit

**Audit Metadata:**
- **Goal:** Credit Domain Goal 1 Audit (Phase C Financial Invariants)
- **Target System:** Nexum Core Financial Engine — Credit Domain (`app/credit`)
- **Author/Worker:** Execution Worker (Phase C Financial Invariants)
- **Reviewer:** Codex (Architect & Independent Verifier)
- **Status:** Phase C Financial Invariants Complete / Awaiting Codex Review
- **Standards of Evidence:**
  - `[CURRENT REALITY]`: Grounded directly in active repository code (`app/credit`, `app/ledger`, `app/accounts`, `app/goals`, `app/obligations`, `alembic/`).
  - `[VERIFIED]`: Proven via AST, line-by-line control flow, schema constraints, or SQL queries.
  - `[TESTED]`: Verified by existing automated unit/smoke test suites.
  - `[DOCUMENTED CLAIM]`: Found in historical documentation or docstrings without active runtime enforcement.
  - `[PENDING VERIFICATION]`: Awaiting physical CT101 PostgreSQL 17 execution or production database verification.
  - `[PENDING PHASE E]`: Reserved for Phase E canonical architecture and forward proposals.
  - `[OPEN PRODUCT DECISION]`: Unresolved business rules requiring Founder decision.

---

## 1 Executive assessment
`[CURRENT REALITY]` This document presents the consolidated Phase C Financial Invariants Audit for the Nexum Credit Domain (`app/credit`), building directly on the structural inventory (Phase A) and behavioral tracing (Phase B). Combining control-flow analysis, transaction boundary mapping, locking graph proofs, and physical schema cross-checks, this report establishes the mathematical and concurrency boundaries governing the credit engine.

Key Executive Findings:
1. **Flawed Cutoff Cycle Assignment (`[VERIFIED]`, Candidate P0):** `CreditCardService._build_installments` derives `scheduled_period = today.strftime("%Y-%m")` based solely on the purchase calendar month (`app/credit/service.py:112`). Because `scheduled_period` lacks day granularity, purchases made on or after the cutoff date are immediately classified as `billed` in `_calculate_card_status` when evaluated in that month, demanding payment in the current cycle rather than rolling into the next billing cycle.
2. **Timezone Split-Brain (`[VERIFIED]`, Candidate P0):** Python domain logic in `app/credit/service.py` relies on naive host-local `date.today()`, bypassing Nexum's canonical `financial_date()` (`America/Bogota` via `app/core/financial_time.py`), whereas SQL repository queries use `now() AT TIME ZONE 'America/Bogota'` (`app/credit/repository.py:40,168`). Cloud servers running in UTC desynchronize at month boundaries.
3. **Goal Reservation Bypass and Spendable Balance Breach (`[VERIFIED]`, Candidate P0):** Both `CreditCardService.create_payment` (`service.py:370`) and `create_early_payment` (`service.py:600`) validate cash sufficiency against raw `account.balance` rather than spendable balance (`account.balance - reserved`). Credit payments completely bypass `GoalRepository.calculate_reserved_by_account` (`app/goals/repository.py:226`), allowing funds earmarked and reserved for user goals to be freely spent and liquidated.
4. **Destructive Alembic Check Constraint on Interest Payments (`[VERIFIED]`, Candidate P0):** Alembic migration `v1_5_baseline.py:677` defines `sa.CheckConstraint("paid_amount <= principal_amount", name="credit_card_installments_paid_check")`. When an installment incurs interest (`interest_amount > 0`), its settlement amount is `total_amount = principal_amount + interest_amount`. When a payment satisfies this installment in full, `paid_amount` reaches `total_amount > principal_amount`, causing PostgreSQL to abort the transaction with a check constraint violation in real database environments.
5. **Inverted Lock Ordering & Concrete Deadlock Hazard (`[VERIFIED STATIC HAZARD]`, Candidate P1):** `create_payment` locks `accounts` then `credit_cards` (`service.py:357,375`), whereas `create_early_payment` and `preview_early_payment` lock `credit_cards` then `accounts` (`service.py:507,540` and `714,748`). Concurrent execution against the same account and card can form a PostgreSQL wait cycle; occurrence remains pending physical reproduction.
6. **Loss of Payment Allocation Traceability (`[VERIFIED]`, Candidate P1):** Payments mutate cumulative `CreditCardInstallment.paid_amount` and `CreditCardStatementCharge.paid_amount` directly. No junction table records which payment transaction satisfied which installment or charge, destroying audit reconcilability.
7. **Blind Idempotent Retry & Cross-Operation Replay Vulnerability (`[VERIFIED]`, Candidate P1):** `CreditCardService` relies entirely on `financial_events.command_id` without verifying event type, card ownership, or payload match. If a client reuses an idempotency key across different operations (e.g. standard payment vs early payment, or purchase vs expense), the engine returns an `idempotent_retry` status referencing an unrelated event and corrupting the operation response.
8. **Early Prepayment Transaction Table Void (`[VERIFIED]`, Candidate P1):** Early payments insert a record in `credit_card_early_payments` and emit a Ledger event, but create **no record in `credit_card_transactions`** (`app/credit/service.py:649-667`).
9. **Non-Deterministic Payment Allocation Ordering (`[VERIFIED]`, Candidate P1):** `list_pending_installments_for_update` orders by `scheduled_period.asc(), installment_number.asc()` (`app/credit/repository.py:85-88`). Multiple purchases with installments in the same period share the same `installment_number` and are allocated in arbitrary database physical scan order.
10. **Overpayment Wall for Multi-Installment Debt (`[VERIFIED]`, Candidate P1):** Standard payments permit paying ahead only for unbilled revolving debt (`installments_total == 1`). If a customer attempts to pay more than their current billed debt when only multi-installment purchases exist, `_apply_payment_to_waterfall` raises `InvalidPaymentAmountError` (`app/credit/service.py:182`).
11. **Exclusive Row Locking and Mutating Transaction in Read Preview Endpoint (`[VERIFIED]`, Candidate P2):** `POST /cards/{card_id}/purchases/{purchase_id}/pay_early/preview` is wrapped in `uow.transaction()` (`router.py:218`) and acquires `FOR UPDATE` row locks on `credit_cards`, `credit_card_installments`, and `accounts` while performing external FX network calls, unnecessarily serializing read inquiries.
12. **Complete Absence of Refunds and Adjustments (`[VERIFIED]`, Candidate P1):** No endpoints, service routines, or event listeners exist for merchant refunds, purchase cancellations, chargebacks, or true credit-balance (saldo a favor) management.
13. **Dormant Statement Subsystem (`[CURRENT REALITY]`, Candidate P1):** Statement generation is inactive. Statements endpoints return empty sets or 404s. `_calculate_card_status` acts as a dynamic synthetic proxy for statement balances.

---

## 2 Current architecture map
`[CURRENT REALITY]` The credit domain is implemented under `app/credit/`:
- `models.py` (`app/credit/models.py:1-191`): 6 SQLAlchemy ORM mapped entities.
- `schemas.py` (`app/credit/schemas.py:1-225`): 16 Pydantic request/response classes and DTOs.
- `router.py` (`app/credit/router.py:1-305`): FastAPI router mounted under `/api/v1/credit` via `app/api/router.py:120`.
- `service.py` (`app/credit/service.py:1-936`): `CreditCardService` handling card lifecycle, purchases, payments, prepayments, and waterfall allocations.
- `repository.py` (`app/credit/repository.py:1-207`): `CreditCardRepository` handling database queries and row-level locking.
- `utils.py` (`app/credit/utils.py:1-76`): `calculate_credit_card_dates` date cycle calculation algorithm.
- `exceptions.py` (`app/credit/exceptions.py:1-22`): Domain exception classes inheriting from `CreditDomainError`.

**Coupling Boundaries:**
- Inbound Core/Shared: `app.core.database.get_db_session`, `app.core.uow.UnitOfWork`, `app.ledger.service.LedgerService`, `app.accounts.repository.AccountRepository`, `app.core.currency.get_fx_rate`.
- Outbound Consumers: `app.api.router.v1_router` (HTTP routing), `app.intelligence.service.IntelligenceService` (financial snapshot and debt analytics), `app.conversations.service.ConversationalService` (chat-driven actions), and `frontend/src/app/app/credit/CreditClient.tsx`.

---

## 3 Current domain model
`[CURRENT REALITY]` Exactly six physical database tables are defined in PostgreSQL via `alembic/versions/v1_5_baseline.py` and mapped in `app/credit/models.py`:

1. **`public.credit_cards`** (`models.py:23-47`, `v1_5_baseline.py:354-426`):
   - `id`: UUID (PK, `gen_random_uuid()`).
   - `user_id`: UUID (NOT NULL).
   - `name`: VARCHAR (NOT NULL).
   - `bank`: VARCHAR (NOT NULL).
   - `credit_limit`: NUMERIC(14,2) (NOT NULL).
   - `current_debt`: NUMERIC(14,2) (DEFAULT 0.00) — *[CURRENT REALITY: Vestigial/stale persisted column]*.
   - `cutoff_day`: INTEGER (NOT NULL, CHECK `cutoff_day >= 1 AND cutoff_day <= 31`).
   - `due_day`: INTEGER (NOT NULL, CHECK `due_day >= 1 AND due_day <= 31`).
   - `management_fee`: NUMERIC(14,2) (DEFAULT 0.00).
   - `monthly_interest_rate`: NUMERIC(5,2) (DEFAULT 0.00).
   - `annual_interest_rate`: NUMERIC(5,2) (DEFAULT 0.00).
   - `network`, `franchise`: VARCHAR (NULL).
   - `currency`: VARCHAR (DEFAULT 'COP').
   - `is_active`: BOOLEAN (DEFAULT TRUE).
   - `created_at`: TIMESTAMP (server_default `now()`).
   - `updated_at`: TIMESTAMPTZ (server_default `now()`).

2. **`public.credit_card_statements`** (`models.py:116-147`, `v1_5_baseline.py:428-514`):
   - `id`: UUID (PK).
   - `user_id`: UUID (NOT NULL).
   - `credit_card_id`: UUID (FK -> `credit_cards.id` ON DELETE CASCADE, NOT NULL).
   - `billing_period`: VARCHAR (NOT NULL, format `'YYYY-MM'`).
   - `billing_period_start`, `cutoff_date`, `due_date`: DATE (NULL).
   - `previous_balance`, `new_purchases`, `billed_installments`, `fees_total`, `interest_total`, `payments_received`, `statement_balance`, `minimum_payment`: NUMERIC(14,2) (DEFAULT 0.00).
   - `status`: VARCHAR (DEFAULT `'open'`).
   - `frozen_at`: TIMESTAMPTZ (NULL).
   - Constraint: `uq_cc_statement_period` UNIQUE (`credit_card_id`, `billing_period`).

3. **`public.credit_card_transactions`** (`models.py:49-85`, `v1_5_baseline.py:516-603`):
   - `id`: UUID (PK).
   - `user_id`: UUID (NOT NULL).
   - `credit_card_id`: UUID (FK -> `credit_cards.id` ON DELETE CASCADE, NOT NULL).
   - `type`: VARCHAR (NOT NULL, CHECK `type IN ('purchase', 'payment', 'fee', 'interest', 'adjustment')`).
   - `amount`: NUMERIC(14,2) (NOT NULL, CHECK `amount > 0`).
   - `category_id`: UUID (NULL).
   - `description`: VARCHAR (NULL).
   - `installments_total`: INTEGER (DEFAULT 1).
   - `installments_paid`: INTEGER (DEFAULT 0).
   - `monthly_amount`: NUMERIC(14,2) (NULL).
   - `interest_amount`: NUMERIC(14,2) (DEFAULT 0.00).
   - `total_with_interest`: NUMERIC(14,2) (NULL).
   - `event_id`: UUID (NULL, references `financial_events.id`, unindexed, no unique constraint).
   - `account_id`: UUID (NULL, references `accounts.id` for payments).
   - `occurred_at`: TIMESTAMPTZ (server_default `now()`).
   - `period`: VARCHAR (NULL).
   - `source`: VARCHAR (DEFAULT `'backend'`).
   - `assigned_statement_id`: UUID (FK -> `credit_card_statements.id` ON DELETE SET NULL, NULL).
   - `statement_assignment_reason`: VARCHAR (NULL).
   - `metadata`: JSONB (DEFAULT `'{}'`).

4. **`public.credit_card_installments`** (`models.py:87-114`, `v1_5_baseline.py:605-701`):
   - `id`: UUID (PK).
   - `user_id`: UUID (NOT NULL).
   - `credit_card_id`: UUID (FK -> `credit_cards.id` ON DELETE CASCADE, NOT NULL).
   - `purchase_transaction_id`: UUID (FK -> `credit_card_transactions.id` ON DELETE CASCADE, NOT NULL).
   - `installment_number`: INTEGER (NOT NULL, CHECK `installment_number >= 1`).
   - `installments_total`: INTEGER (NOT NULL, CHECK `installments_total >= 1`).
   - `principal_amount`: NUMERIC(14,2) (NOT NULL, CHECK `principal_amount >= 0`).
   - `interest_amount`: NUMERIC(14,2) (DEFAULT 0.00).
   - `total_amount`: NUMERIC(14,2) (DEFAULT 0.00).
   - `scheduled_period`: VARCHAR (NOT NULL, format `'YYYY-MM'`).
   - `scheduled_due_date`: DATE (NULL).
   - `status`: VARCHAR (NOT NULL, DEFAULT `'pending'`, CHECK `status IN ('pending', 'paid', 'partial')`).
   - `paid_amount`: NUMERIC(14,2) (NOT NULL, DEFAULT 0.00, CHECK `paid_amount >= 0`).
   - `revision_id`: INTEGER (NOT NULL, DEFAULT 1).
   - Constraint: `credit_card_installments_paid_check` (`paid_amount <= principal_amount`) — *[CRITICAL DEFECT: Fails when paying interest]*
   - Indexes: `idx_credit_card_installments_card_period`, `idx_credit_card_installments_user_card`.

5. **`public.credit_card_early_payments`** (`models.py:149-173`, `v1_5_baseline.py:703-751`):
   - `id`: UUID (PK).
   - `user_id`: UUID (NOT NULL).
   - `credit_card_id`: UUID (FK -> `credit_cards.id` ON DELETE CASCADE, NOT NULL).
   - `purchase_transaction_id`: UUID (FK -> `credit_card_transactions.id` ON DELETE CASCADE, NOT NULL).
   - `amount`: NUMERIC(14,2) (NOT NULL).
   - `allocation_mode`: VARCHAR (NOT NULL).
   - `event_id`: UUID (NULL, unindexed, no unique constraint).
   - `applied_at`: TIMESTAMPTZ (server_default `now()`).
   - `metadata`: JSONB (DEFAULT `'{}'`).

6. **`public.credit_card_statement_charges`** (`models.py:175-191`, `v1_5_baseline.py:753-789`):
   - `id`: UUID (PK).
   - `statement_id`: UUID (FK -> `credit_card_statements.id` ON DELETE CASCADE, NOT NULL).
   - `charge_type`: VARCHAR (NOT NULL).
   - `amount`: NUMERIC(14,2) (NOT NULL).
   - `description`: VARCHAR (NULL).
   - `reference_id`: UUID (NULL).
   - `status`: VARCHAR (NOT NULL, DEFAULT `'pending'`).
   - `paid_amount`: NUMERIC(14,2) (NOT NULL, DEFAULT 0.00).
   - `created_at`: TIMESTAMPTZ (server_default `now()`).

---

## 4 Source-of-Truth matrix
`[CURRENT REALITY]`

| Financial Primitive | Primary Implementation Source | Secondary / Candidate Sources | Current Reality & Contradiction Evidence |
| :--- | :--- | :--- | :--- |
| **1. Credit Limit** | `credit_cards.credit_limit` (`models.py:32`) | `CreditCardRead.credit_limit`, `CreditSummaryRead.total_credit_limit` | `[VERIFIED]` Single schema source; manual input fallback. |
| **2. Current Debt** | Dynamic sum in `CreditCardRepository.get_card_debt` (`installments`) | `credit_cards.current_debt` (stale col), `v_credit_card_debt` (view), `CreditCardStatusRead.current_debt` | `[VERIFIED]` **Severe Contradiction:** Stale DB column (`0.00`); view aggregates transactions; repository aggregates installments; docstrings and metadata contradict code. |
| **3. Used Credit** | Derived as `current_debt` / `total_debt` | `CreditCardRead.total_debt` | `[VERIFIED]` Synonymous with dynamically computed debt in read models. |
| **4. Available Credit** | `max(0, limit - total_debt)` in `_calculate_card_status` (`service.py:835`) | `CreditCardRead.estimated_available_credit`, `CreditSummaryRead.total_available_credit` | `[VERIFIED]` Deterministic derivation; clippers prevent negative availability. |
| **5. Statement Balance** | In `/status`: set to `billed_debt` (`service.py:837`) | `credit_card_statements.statement_balance` (empty DB col), `CreditCardRead.statement_balance` (`None`) | `[VERIFIED]` **Contradiction:** Returns `None` on `/cards` list (`service.py:204`), but returns `billed_debt` on `/cards/{id}/status`. |
| **6. Amount Due** | `payment_required` in `_calculate_card_status` (= `statement_balance`) | `CreditCardRead.payment_required` | `[VERIFIED]` Reflects billed installments dynamically while statement engine is dormant. |
| **7. Minimum Payment** | Formula in `_calculate_card_status` (5% or COP 50k / USD 15) | `credit_card_statements.minimum_payment` (DB col), `CreditCardRead.minimum_payment` (returns `0.00`) | `[VERIFIED]` **Contradiction:** `/cards` list defaults to `0.00`, whereas `/status` calculates dynamic 5% formula. |
| **8. Installment Principal** | `credit_card_installments.principal_amount` (`models.py:102`) | `CreditCardTransaction.monthly_amount` | `[VERIFIED]` Stored across installment rows; recompressed upon early payment. |
| **9. Interest** | `credit_card_installments.interest_amount` (`models.py:106`) | `credit_cards.monthly_interest_rate`, `credit_card_statements.interest_total` | `[VERIFIED]` Flat percentage placeholder; no compounding or amortization curve. |
| **10. Payment Allocation** | `_apply_payment_to_waterfall` in Python (`service.py:119`) | Cumulative `inst.paid_amount` | `[VERIFIED]` Waterfall executes in application memory; no junction table preserves allocation auditability. |
| **11. FX Booked Amount** | Serialized in `financial_events.metadata` & `credit_card_transactions.metadata` | `PaymentPreviewResult` (transient DTO) | `[VERIFIED]` Captured in event JSONB; no dedicated database schema columns. |

---

## 5 Full lifecycle

### 5.1 Card Creation
- **Call Path:** `POST /api/v1/credit/cards` -> `router.py:41-52` (`create_card`) -> `CreditCardService.create_card` (`service.py:210-226`).
- **Entities & Values:** `CreditCard` (`models.py:23-47`) instantiated with `name`, `bank`, `credit_limit`, `cutoff_day`, `due_day`, `management_fee`, `monthly_interest_rate`, `annual_interest_rate`, `currency` (default `'COP'`). Column `current_debt` defaults to `0.00` in SQL schema but is untouched by service.
- **Session & Ownership:** Managed by `UnitOfWork(session).transaction()` (`router.py:48`). Commits via UoW context manager and closes in `get_db_session`.
- **API Representation:** Returns `CreditCardRead` (`schemas.py:40-63`) via `_enrich_card` (`service.py:186-209`). Initial computed debt is `0.00`, `available_credit = credit_limit`, `payment_required = 0.00`, `statement_balance = None`.
- **Tests:** `tests/unit/test_credit.py:55-80`, `tests/unit/test_credit.py:280-311`.

### 5.2 Purchase Execution
- **Call Path:** `POST /api/v1/credit/cards/{card_id}/purchases` -> `router.py:110-136` -> `CreditCardService.create_purchase` (`service.py:261-348`).
- **Validation & Limit Locking:** Acquires row-level lock via `repo.get_by_id_for_update(card_id)` (`repository.py:19-23`). Validates `card.is_active`. Calls `repo.get_card_debt(card.id)` (`repository.py:35-48`). If `payload.amount > max(0, credit_limit - estimated_debt)`, raises `CreditLimitExceededError`, translated by the router to HTTP 409 (`router.py:133-134`).
- **Ledger Event:** Records `LedgerEventCreate(event_type="credit_card_purchase", direction="neutral", amount=payload.amount, currency=card.currency, metadata={"credit_card_id": str(card.id)})` (`service.py:282-293`). Idempotency handled via `command_id` and savepoint in `LedgerRepository`.
- **Transaction & Installments:** Inserts `CreditCardTransaction(type="purchase")` (`service.py:314-324`). Invokes `_build_installments` to generate and persist `CreditCardInstallment` records (`service.py:327-334`).
- **Lock Order & Atomicity:** Row lock on `credit_cards` -> `financial_events` (savepoint) -> `credit_card_transactions` -> `credit_card_installments`. All operations execute within a single UoW transaction.
- **Return Representation:** `CreditCardPurchaseResult(status="success", amount, current_debt, available_credit)` (`schemas.py:74-83`).
- **Tests:** `tests/unit/test_credit.py:82-113`, `tests/unit/test_credit.py:115-130`.

### 5.3 Cycle Assignment & Installments
- Generated in `CreditCardService._build_installments` (`service.py:76-117`). Uses `today = date.today()`.
- Splits principal with `_installment_amounts` (`service.py:66-71`), assigning rounding residuals to the last installment.
- For `index = 1..installments_total`:
  - `scheduled_date = self._add_months(today, index - 1)` (`service.py:88`).
  - Calls `calculate_credit_card_dates(scheduled_date, card.cutoff_day, card.due_day)` to compute `next_due` (`service.py:90-92`).
  - Calculates interest: `(principal * monthly_interest_rate / 100).quantize(0.01)`.
  - Sets `scheduled_period = scheduled_date.strftime("%Y-%m")` (`service.py:112`).
  - Sets `scheduled_due_date = next_due`, `status = "pending"`, `paid_amount = 0.00`.

### 5.4 Billed / Unbilled Status Representation
- Evaluated on-read in `_calculate_card_status` (`service.py:806-875`).
- Computes `cycle_start, cycle_end, next_due = calculate_credit_card_dates(today, card.cutoff_day, card.due_day)`.
- Defines `last_cutoff = cycle_start - timedelta(days=1)` (`service.py:818`).
- Aggregates via `repo.get_card_status_data(card.id, last_cutoff)` (`repository.py:125-144`):
  - `billed`: `scheduled_period <= to_char(CAST(last_cutoff AS DATE), 'YYYY-MM')`.
  - `unbilled`: `scheduled_period > to_char(CAST(last_cutoff AS DATE), 'YYYY-MM')`.
- Nets payments against billed purchases (`billed_debt = billed - total_payments`). Rolls negative remainder into reducing unbilled debt.
- Returns `CreditCardStatusRead` (`schemas.py:134-160`).

### 5.5 Statement Endpoints
- Handled by `list_statements` (`router.py:276-288`) and `get_statement` (`router.py:290-305`).
- Selects directly from `credit_card_statements` (`repository.py:173-193`).
- Because statement generation is dormant, queries return empty list `[]` or 404.

### 5.6 Bill Payment & Waterfall Allocation
- **Call Path:** `POST /api/v1/credit/cards/{card_id}/payments` -> `router.py:137-168` -> `CreditCardService.create_payment` (`service.py:350-494`).
- **Account Verification & Lock:** Locks account via `account_repo.get_by_id_for_update(payload.account_id)` (`service.py:357`). Validates ownership, active status, and `account.balance >= payload.amount`. *[DEFECT: Ignores Goal reservations]*
- **Card Lock & FX:** Locks card via `repo.get_by_id_for_update(card_id)`. If currencies differ, calls `app.core.currency.get_fx_rate`.
- **Overpayment Guard:** `if applied_amount > estimated_debt: raise InvalidPaymentAmountError()` (`service.py:418-419`).
- **Ledger Event & Account Debit:** Emits `credit_card_payment` (outflow) event (`service.py:422-440`). Debits account balance: `account.balance -= payload.amount` (`service.py:459`).
- **Transaction Record:** Inserts `CreditCardTransaction(type="payment", amount=applied_amount, account_id=payload.account_id, event_id=event.id)` (`service.py:461-479`).
- **Waterfall Allocation & Child Locks:** Locks `credit_card_installments` and `credit_card_statement_charges` `FOR UPDATE`. Mutates `inst.paid_amount` and `charge.paid_amount`.
- **Lock Order:** `accounts` -> `credit_cards` -> `credit_card_installments` -> `credit_card_statement_charges`.
- **Tests:** `tests/unit/test_credit.py:133-182`, `tests/unit/test_credit.py:184-218`.

### 5.7 Early Prepayment Flow
- **Call Path:** `POST /api/v1/credit/cards/{card_id}/purchases/{purchase_id}/pay_early` -> `service.create_early_payment` (`service.py:496-704`).
- **Lock Order:** Locks `credit_cards` (`service.py:507`) -> locks `credit_card_installments` (`service.py:516`) -> locks `accounts` (`service.py:540`). *[DEFECT: Inverted lock order vs create_payment]*
- Validates multi-installment eligibility (`installments_total > 1`) and presence of unbilled future installments (`scheduled_period > current_period`).
- Emits Ledger event (`credit_card_payment`, outflow). Debits account balance (`account.balance -= source_amount`). *[DEFECT: Ignores Goal reservations]*
- Inserts `CreditCardEarlyPayment` record (`service.py:649-667`). Creates **no row in `credit_card_transactions`**.
- Recompresses future installment principal via `_installment_amounts` (`service.py:670-690`).
- **Tests:** `tests/unit/test_credit_summary_qa.py:40-56`.

### 5.8 Next Cycle Progression
- Operates passively on-read. When system time crosses `cutoff_day`, calls to `calculate_credit_card_dates` advance cycle boundaries, causing past installments to match `scheduled_period <= last_cutoff` and transition to `billed` status.

---

## 6 Billing-cycle audit

### 6.1 Date Cycle Computation (`app/credit/utils.py:4-76`)
`calculate_credit_card_dates(current_date, cutoff_day, due_day)` implements:
- `current_date.day <= cutoff_day`: Current cycle closes this month on `min(cutoff_day, last_day_of_month)`. `cycle_start` begins day after previous month's cutoff.
- `current_date.day > cutoff_day`: Current cycle closes next month. `cycle_start` begins day after this month's cutoff.
- `next_due`: Due in `cycle_end.month` (if `due_day >= cutoff_day`) or `cycle_end.month + 1` (if `due_day < cutoff_day`).
- Post-due advancement: If `current_date > next_due`, advances `next_due` by one month, but leaves `cycle_start` and `cycle_end` unadjusted (`utils.py:68-74`).

### 6.2 Cutoff Semantics & Timing Proof (Finding F-01 Analysis)
`[VERIFIED]` Trace of card with `cutoff_day = 15`, `due_day = 5` in October 2026:

| Scenario | Purchase Date (`today`) | `scheduled_period` Generated (`service.py:112`) | Evaluation Date | `last_cutoff` in Status (`service.py:818`) | SQL Filter `scheduled_period <= last_cutoff` (`repo.py:129`) | Resulting Status | Correct Banking Behavior |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Before Cutoff** | 2026-10-10 | `"2026-10"` | 2026-10-10 | 2026-09-15 (`"2026-09"`) | `"2026-10" <= "2026-09"` -> **FALSE** | **UNBILLED** | Unbilled until Oct 15 cutoff |
| **2. On Cutoff** | 2026-10-15 | `"2026-10"` | 2026-10-15 | 2026-09-15 (`"2026-09"`) | `"2026-10" <= "2026-09"` -> **FALSE** | **UNBILLED** | Should close at end of cutoff day |
| **3. Rollover After Cutoff** | 2026-10-10 | `"2026-10"` | 2026-10-16 | 2026-10-15 (`"2026-10"`) | `"2026-10" <= "2026-10"` -> **TRUE** | **BILLED** | Billed on Oct 16 for Nov 5 due date |
| **4. Purchase AFTER Cutoff** | 2026-10-20 | `"2026-10"` | 2026-10-20 | 2026-10-15 (`"2026-10"`) | `"2026-10" <= "2026-10"` -> **TRUE** | **BILLED (DEFECT)** | **UNBILLED** (belongs to Nov 15 cycle, due Dec 5) |

**Clarified Evidence:**
`scheduled_period` is month-only (`YYYY-MM`). Because no closed statements are generated, status evaluation occurs entirely dynamically on read. For purchases made on or after the cutoff day within the same calendar month (e.g. October 20), `scheduled_period` is assigned `"2026-10"`. When evaluated on or after October 16, `last_cutoff` is `2026-10-15` (`"2026-10"`). Consequently, `"2026-10" <= "2026-10"` evaluates to `TRUE`, and the post-cutoff purchase is immediately classified as `billed`, demanding payment in the current cycle rather than rolling into the next cycle.

### 6.3 Temporal Symbols & Timezone Audit
`[VERIFIED]` Complete inventory of temporal symbols across `app/credit/`:
- `date.today()`: Called in `service.py:84` (installments), `service.py:125` (waterfall), `service.py:528` (early payment), `service.py:734` (early preview), `service.py:811` (status calculation), and `schemas.py:197` (installment DTO property).
- `financial_date()`: Defined in `app/core/financial_time.py:9` (pinned to `America/Bogota`). Heavily used in Obligations (`app/obligations/service_v17.py:9`), but **completely unused in Credit**.
- `America/Bogota` in SQL: Used in `repository.py:40` (`to_char((now() AT TIME ZONE 'America/Bogota'), 'YYYY-MM')`) and `repository.py:168`.
- Database `func.now()`: Used in `models.py:43,72,81,110,143,166,190`.
- **Timezone Split-Brain:** Python evaluates naive local host time, while SQL evaluates PostgreSQL server time converted to Bogota timezone. On UTC cloud servers between 19:00 and 23:59 Bogota time at month-end, Python generates dates for the next month while PostgreSQL still aggregates for the current month.

---

## 7 Installment audit
`[CURRENT REALITY]`
- **Single Installment (`installments_total == 1`):** Generates 1 installment row. Unconditionally incurs interest if `card.monthly_interest_rate > 0`. Waterfall Tier 5 pays it if billed; Tier 6 pays it if unbilled. Ineligible for `/pay_early`.
- **Multi-Installment (`installments_total > 1`):** Quantized via `_installment_amounts` (`service.py:66-71`). Base amount is quantized to `0.01`, with rounding residuals assigned to the final installment (`amounts[-1] = quantized - sum(amounts[:-1])`).
- **Outstanding Principal & Future Commitments:** Tracked as `principal_amount - paid_amount`. Summed by `IntelligenceService` into snapshot commitments.
- **Overpayment Wall:** Standard payments do not absorb future unbilled multi-installment debt. Paying ahead requires `/pay_early`.

---

## 8 Interest audit
`[CURRENT REALITY]`
- **Storage:** Stored as `credit_cards.monthly_interest_rate` (percentage, e.g. `2.50` = 2.50%/month) and `credit_card_installments.interest_amount`. Column `credit_cards.annual_interest_rate` is purely decorative metadata.
- **Current Formula (`service.py:94-99`):**
  ```python
  interest = (
      (principal * (card.monthly_interest_rate / Decimal("100")))
      if card.monthly_interest_rate
      else Decimal("0.00")
  )
  interest = interest.quantize(Decimal("0.01"))
  ```
- **Financial Flaws & Limitations (`[VERIFIED]`):**
  1. *Flat Installment Calculation:* Calculates flat interest on each isolated installment principal rather than outstanding card balance.
  2. *No Grace-Period Model:* Charges interest on 1-installment purchases whenever the configured rate is greater than zero. The code has no issuer grace-period concept, so financial correctness cannot be established without authoritative issuer/product rules.
  3. *No Amortization System:* Absence of French, German, or constant amortization models.
  4. *No Compounding:* Revolving compound interest is unsupported.
  5. *Static Accrual:* Interest is fixed at transaction creation; prepayment does not recalculate accrued interest.
  6. *Database Check Constraint Conflict (`[VERIFIED]`):* Because `alembic` enforces `paid_amount <= principal_amount` (`v1_5_baseline.py:677`), applying payments to interest triggers a physical PostgreSQL `CheckViolation`.

---

## 9 Minimum-payment audit
`[CURRENT REALITY]`
- **Formula (`service.py:840-847`):**
  - Minimum percentage: `5%` of `statement_balance`.
  - Absolute floor: `50,000.00 COP` if `currency == 'COP'`, else `15.00 USD`.
  - `min_revolving = max(statement_balance * 0.05, min_pay_absolute)`.
  - `minimum_payment = min(min_revolving, statement_balance) if statement_balance > 0 else 0.00`.
- **API Read Model Contradictions:**
  - `GET /cards`: `statement_balance = null`, `minimum_payment = 0.00` (`service.py:204`, `schemas.py:46`).
  - `GET /cards/{id}/status`: `statement_balance = billed_debt`, `minimum_payment = calculated formula` (`service.py:837,845`).

---

## 10 Waterfall audit
`[CURRENT REALITY]` Step-by-step hierarchy in `_apply_payment_to_waterfall` (`service.py:119-185`):
1. **Tier 1:** Past due installments (`scheduled_period < current_period`). Pays `total_amount - paid_amount`.
2. **Tier 2:** Current period accrued interest (`scheduled_period == current_period`). Pays `interest_amount - paid_amount`.
3. **Tier 3:** Statement charges (`credit_card_statement_charges`). Pays `amount - paid_amount`.
4. **Tier 4 & 5:** Current period billed installments & revolving principal (`scheduled_period == current_period`). Pays remaining `total_amount - paid_amount`.
5. **Tier 6:** Future unbilled revolving purchases (`scheduled_period > current_period and installments_total == 1`). Pays `total_amount - paid_amount`.
6. **Tier 7:** Residual overpayment check. If `remaining > 0`, raises `InvalidPaymentAmountError`.

**Flaws Proven (`[VERIFIED]`):**
- *Non-Deterministic Ordering:* `list_pending_installments_for_update` sorts by `scheduled_period.asc(), installment_number.asc()` (`repository.py:85-88`). Multiple purchases with installments in the same period share `installment_number = 1` and lack a secondary sort key, executing in arbitrary physical heap scan order.
- *Allocation Auditability Void:* Payments insert a `CreditCardTransaction(type="payment")` and increment `CreditCardInstallment.paid_amount`, but insert no junction records. It is physically impossible to audit which payment transaction paid which installment.

---

## 11 Prepayment / overpayment audit
`[CURRENT REALITY]`
- **Partial Payment:** Supported. Absorbed across Tiers 1–5; unpaid debt remains in `billed_debt`.
- **Payment Above Billed Debt:** Conceptually prepayment. Absorbed by Tier 6 if unbilled revolving debt exists; strictly rejected with `InvalidPaymentAmountError` if only multi-installment debt exists.
- **Payment Above Total Debt:** Strictly rejected by `service.py:418`. The engine prohibits negative debt (saldo a favor).
- **Early Prepayment (`/pay_early`):** Compresses remaining principal across future installments of a specific purchase. Emits Ledger event and inserts `CreditCardEarlyPayment`, but omits creating a row in `credit_card_transactions`.

---

## 12 Ledger / Account reconciliation

### 12.1 Transaction and Session Ownership Boundary (`[VERIFIED]`)
Every mutating endpoint in `app/credit/router.py` explicitly constructs and enters a Unit of Work context:
```python
uow = UnitOfWork(session)
async with uow.transaction():
    ...
```
`UnitOfWork.transaction()` (`app/core/uow.py:28-40`) wraps the block in an asynchronous context manager that invokes `await self.session.commit()` on clean exit and `await self.session.rollback()` if any unhandled exception occurs. In addition, the FastAPI dependency `get_db_session()` (`app/core/database.py:117-145`) maintains an outer safety net that issues `await session.commit()` upon successful response generation or `await session.rollback()` if an exception traverses past the endpoint.

**Shared Database Transaction Proof:**
Across all three core mutation paths, all database entities share the **exact same `AsyncSession`** and execute inside **one atomic PostgreSQL database transaction**:
1. **Purchase Execution (`POST /cards/{card_id}/purchases`):**
   - Row lock on `CreditCard` (`repository.py:19-23`).
   - Insertion of `FinancialEvent` into `financial_events` via `LedgerService.record_event` (`app/ledger/repository.py:149`).
   - Insertion of `CreditCardTransaction` into `credit_card_transactions` (`repository.py:51`).
   - Insertion of `CreditCardInstallment` rows into `credit_card_installments` (`repository.py:55`).
   - *Atomicity Verdict:* All rows are committed or rolled back together in a single transaction.
2. **Bill Payment (`POST /cards/{card_id}/payments`):**
   - Row lock on `Account` (`app/accounts/repository.py:34`).
   - Row lock on `CreditCard` (`repository.py:19-23`).
   - Insertion of `FinancialEvent` (direction `outflow`) into `financial_events`.
   - In-place mutation of `account.balance -= payload.amount` (`service.py:459`).
   - Insertion of `CreditCardTransaction` (type `payment`) into `credit_card_transactions` (`service.py:479`).
   - Row locks and in-place mutation of `credit_card_installments.paid_amount` and `status` (`service.py:136-141`).
   - Row locks and in-place mutation of `credit_card_statement_charges.paid_amount` and `status` (`service.py:163-168`).
   - *Atomicity Verdict:* Ledger outflow, account cash reduction, credit transaction record, installment mutation, and charge mutation share one database transaction and commit atomically.
3. **Early Prepayment (`POST /cards/{card_id}/purchases/{purchase_id}/pay_early`):**
   - Row lock on `CreditCard` (`service.py:507`).
   - Row locks on `CreditCardInstallment` (`service.py:516`).
   - Row lock on `Account` (`service.py:540`).
   - Insertion of `FinancialEvent` (direction `outflow`) into `financial_events`.
   - In-place mutation of `account.balance -= source_amount` (`service.py:647`).
   - Insertion of `CreditCardEarlyPayment` into `credit_card_early_payments` (`service.py:667`).
   - Compression mutation of `credit_card_installments.principal_amount` and `status` (`service.py:674-690`).
   - *Atomicity Verdict:* Account debit, ledger event, early payment audit row, and installment principal compression share one database transaction and commit atomically.
4. **Conversational Intent Mutating Path (`POST /api/v1/conversations/message`):**
   - `ConversationsService.handle_message` (`app/conversations/service.py:883-928`) delegates to `CreditCardService.create_purchase` and `create_payment`.
   - *Boundary Note:* `app/conversations/router.py` does NOT instantiate `UnitOfWork`. It relies on the implicit transaction opened by the session and committed at generator exit in `get_db_session()` (`app/core/database.py:141`).

### 12.2 Idempotency Engine & Retry Flow (`[VERIFIED]`)
- **Uniqueness Enforcement:** Idempotency is enforced **exclusively at the Ledger layer** via table `public.financial_events` and partial unique index `idx_financial_events_command_id` on `(command_id)` WHERE `command_id IS NOT NULL` (`alembic/versions/v1_5_baseline.py:315-320`). Neither `credit_card_transactions` nor `credit_card_early_payments` possesses a `command_id` column or unique constraint.
- **Savepoint Isolation:** In `LedgerRepository.record_event` (`app/ledger/repository.py:148-152`), the insertion of `db_event` is wrapped in `async with self.session.begin_nested():`. If an `IntegrityError` is thrown due to a duplicate `command_id`, PostgreSQL rolls back to the savepoint without aborting the parent transaction. The repository then retrieves `existing_event = await self.get_by_command_id(event_data.command_id)` and returns `LedgerEventResult(event=existing_event, idempotent=True)`.
- **Resource Lock Sequencing vs Idempotency Detection:**
  In all three mutating flows, **resource row locks are acquired BEFORE idempotency is evaluated**:
  - In `create_purchase`: Card lock is taken at line 268; idempotency is checked at line 296.
  - In `create_payment`: Account lock is taken at line 357, Card lock at line 375; idempotency is checked at line 443.
  - In `create_early_payment`: Card lock is taken at line 507, Installments at line 516, Account at line 540; idempotency is checked at line 630.
  Acquiring locks before checking idempotency forces concurrent retries of the same command to wait behind the active transaction rather than failing fast.
- **Returned Values on Retry:**
  When `is_retry` is `True`, the endpoints return DTOs with `status = "idempotent_retry"`:
  - `create_purchase` (`service.py:301-310`): Looks up transaction by `event_id`. Returns `current_debt = estimated_debt` and `available_credit = estimated_available`. Crucially, `estimated_debt` is re-evaluated dynamically from current database installments at line 274, rather than reproducing the snapshot values from the original purchase response.
  - `create_payment` (`service.py:448-457`): Returns `current_debt = estimated_debt` and `account_balance = account.balance`. These reflect live post-commit database values.
  - `create_early_payment` (`service.py:637-645`): Returns `current_debt = Decimal(str(debt))` and `account_balance = account.balance`.
- **Behavior After Partial Failure:**
  Because all mutations share the single UoW transaction, if an unhandled error occurs after `record_event` but before the endpoint completes (e.g. an installment calculation error or process abort), the entire transaction rolls back. The `financial_events` record is discarded. A subsequent retry with the same `command_id` encounters no existing record and re-executes the operation cleanly from scratch.
- **Vulnerability: Blind Idempotent Retry & Cross-Operation Replay (`[VERIFIED]`):**
  Neither `LedgerService` nor `CreditCardService` validates whether the retrieved `existing_event` matches the requested operation. If an external client reuses a `command_id` across different operations (e.g., submits `POST /payments` with a `command_id` previously used for an `expense` or `purchase`), `record_event` catches the unique constraint violation, returns the existing event, and `CreditCardService` returns `idempotent_retry` citing the foreign event ID. There is no operation allowlist or hash comparison.
- **Routes Lacking Idempotency:**
  - `POST /cards` (`create_card`, `router.py:41`): Does NOT accept `Idempotency-Key` or `command_id`. Duplicate network requests create duplicate card entities.
  - `PATCH /cards/{card_id}`: Lacks `Idempotency-Key`.
  - `DELETE /cards/{card_id}`: Lacks `Idempotency-Key`.

### 12.3 Resource Lock Ordering & Deadlock Hazards (`[VERIFIED]`)
Reconstruction of the physical lock acquisition order across all mutating endpoints:

| Flow / Endpoint | Lock 1 | Lock 2 | Lock 3 | Lock 4 | Lock Release Point |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Purchase** (`POST /purchases`) | `credit_cards` (`card_id`) | None | None | None | Transaction commit / rollback |
| **Standard Payment** (`POST /payments`) | `accounts` (`account_id`) | `credit_cards` (`card_id`) | `credit_card_installments` | `credit_card_statement_charges` | Transaction commit / rollback |
| **Early Prepayment** (`POST /pay_early`) | `credit_cards` (`card_id`) | `credit_card_installments` | `accounts` (`account_id`) | None | Transaction commit / rollback |
| **Early Payment Preview** (`POST /pay_early/preview`) | `credit_cards` (`card_id`) | `credit_card_installments` | `accounts` (`account_id`) | None | Transaction commit / rollback |
| **Card Update / Delete** (`PATCH`, `DELETE`) | `credit_cards` (`card_id`) | None | None | None | Transaction commit / rollback |

**Concrete Deadlock Proof (Inconsistent Lock Hierarchy):**
A severe locking inversion exists between **Standard Payment** and **Early Prepayment**:
- Standard Payment acquires: `accounts` -> `credit_cards`.
- Early Prepayment (and Early Payment Preview) acquires: `credit_cards` -> `accounts`.
- *Deadlock Scenario:* If User A triggers a bill payment from Account 1 to Card 1 while simultaneously triggering an early prepayment (or an early payment preview) from Account 1 on Card 1:
  - Transaction 1 (`create_payment`): Locks `accounts` row for Account 1. Next, attempts to acquire `FOR UPDATE` on `credit_cards` for Card 1.
  - Transaction 2 (`create_early_payment` or `preview_early_payment`): Locks `credit_cards` row for Card 1. Next, attempts to acquire `FOR UPDATE` on `accounts` for Account 1.
  - *Outcome:* Transaction 1 waits for Card 1 (held by Tx 2); Transaction 2 waits for Account 1 (held by Tx 1). A cyclical wait graph is established. PostgreSQL's deadlock detector triggers after `deadlock_timeout` (typically 1s) and aborts one transaction with error code `40P01` (`deadlock_detected`).
  *(Note: Physical confirmation pending CT101 PostgreSQL verification).*

### 12.4 Concurrency Audit (`[VERIFIED]`)
1. **Concurrent Purchases Against One Card Limit:**
   - Both transactions execute `repo.get_by_id_for_update(card_id)` (`service.py:268`).
   - Tx 1 acquires the row lock; Tx 2 blocks on the PostgreSQL lock queue.
   - Tx 1 checks debt, creates purchase installments, and commits.
   - Tx 2 unblocks. In PostgreSQL default `READ COMMITTED` isolation, Tx 2 reads the newly committed card state. When Tx 2 calls `repo.get_card_debt(card.id)` (`repository.py:37-45`), the query aggregates all committed installments, including Tx 1's.
   - If Tx 1 consumed the available limit, Tx 2 detects `payload.amount > estimated_available` and raises `CreditLimitExceededError`.
   - *Verdict:* Safe against limit overspend under `READ COMMITTED`.
2. **Concurrent Payments from the Same Account/Card:**
   - Both transactions execute `account_repo.get_by_id_for_update(payload.account_id)` (`service.py:357`).
   - Tx 1 acquires the lock; Tx 2 blocks.
   - Tx 1 debits `account.balance` and commits.
   - Tx 2 unblocks, re-reads the debited balance. If `account.balance < payload.amount`, Tx 2 raises `InsufficientFundsError`.
   - *Verdict:* Account balance is serialized safely.
3. **Stale Debt Reads and Lost Updates in Responses:**
   - In `create_payment`, line 482 calculates `new_debt = estimated_debt - applied_amount` in local Python memory.
   - In `create_early_payment`, line 693 calculates `new_debt = Decimal(str(debt)) - applied_amount` in local Python memory.
   - Neither routine queries the database after mutation to verify actual final debt. If another transaction intervened or statement charges adjusted, the returned DTO values diverge from physical database truth until the next full query.

### 12.5 ORM Identity-Map & Savepoint Hazards (`[VERIFIED]`)
1. **Raw SQL vs Identity-Map Desynchronization:**
   - `CreditCardRepository.get_card_debt` (`repository.py:37-45`) executes a raw SQL `SELECT` (`session.execute(text(...))`). Raw SQL bypasses SQLAlchemy's identity map and reads directly from the database snapshot.
   - In `create_payment`, `_apply_payment_to_waterfall` mutates ORM objects (`inst.paid_amount += applied`) in Python memory without calling `session.flush()`. If raw SQL were executed later in the same transaction, it would read stale installment amounts because the in-memory changes were not flushed to PostgreSQL.
2. **Savepoint Rollback Side-Effects:**
   - When `LedgerRepository.record_event` hits a duplicate `command_id`, `async with self.session.begin_nested():` catches the exception and issues a `ROLLBACK TO SAVEPOINT`.
   - SQLAlchemy's savepoint rollback discards the pending `FinancialEvent` from the session. Because `_session_factory` is configured with `expire_on_commit=False` (`app/core/database.py:71`), existing ORM objects loaded prior to the savepoint (such as `card` and `account`) remain in the session with their current in-memory state.
3. **Post-Mutation Response Estimations:**
   - All response schemas (`CreditCardPurchaseResult`, `CreditCardPaymentResult`, `CreditCardEarlyPaymentResult`) rely on transient in-memory calculations rather than re-aggregating post-flush data, introducing subtle drift between returned DTOs and actual DB rows.

### 12.6 Exact Reconciliation Limits & Money Conservation (`[VERIFIED]`)
- **Storage Across Layers:**
  - `accounts`: Stores `balance` in `account.currency` (NUMERIC 14,2).
  - `financial_events`: Stores `amount` in `source_currency` (NUMERIC 14,2). Captures cross-currency FX rate, target currency, and applied amount inside JSONB `metadata`.
  - `credit_card_transactions`: Stores `amount` in `card.currency` (NUMERIC 14,2). Stores cross-currency audit metadata in JSONB `metadata`.
  - `credit_card_installments`: Stores `principal_amount`, `interest_amount`, `total_amount`, and `paid_amount` in `card.currency`.
- **Mathematical Equivalence Proofs:**
  - *Proof 1: Account Debit == Ledger Source Amount.* In standard payment, line 459 executes `account.balance -= payload.amount`, and line 426 creates the ledger event with `amount = payload.amount`. In early payment, line 647 executes `account.balance -= source_amount`, and line 613 sets ledger `amount = source_amount`. The account cash reduction is strictly identical to the ledger event source amount in all cases.
  - *Allocation Conservation Within the Waterfall:* In standard payment, `_apply_payment_to_waterfall` consumes `remaining = applied_amount`. Every deduction is added to either `inst.paid_amount` or `charge.paid_amount`; any residual aborts the transaction. Thus the committed allocation deltas sum to `applied_amount`.
  - *Debt-Reconciliation Gap for Statement Charges:* `get_card_debt` and the response's `estimated_debt` include installments only. The waterfall can allocate part of `applied_amount` to `credit_card_statement_charges`, yet `new_debt = estimated_debt - applied_amount` subtracts the full amount from installment debt in the response. If statement charges exist, the response can understate the installment debt that remains in PostgreSQL.
  - *Cross-Currency Limit:* Source-currency Account debit equals source-currency Ledger amount, and target allocations equal the computed target amount. Full historical FX reconciliation remains unproven because the rate evidence is JSON metadata rather than a canonical persisted FX snapshot reference.
- **Auditability Void (Missing Allocation Junction Records):**
  - Account debit and Ledger source amount are equal within one atomic transaction. End-to-end money conservation is **not yet proven** because statement-charge allocation can diverge from the installment-only debt guard and response.
  - **Post-facto financial auditability is broken.**
  - No junction table (e.g. `credit_card_payment_allocations`) exists. Payments mutate `inst.paid_amount` cumulatively. Once applied, it is impossible to reconstruct from the database which specific payment transaction settled which installment principal, which interest portion, or which statement charge.
  - Early prepayments omit creating a record in `credit_card_transactions`, creating an irreconcilable gap between Ledger payment events and Credit card transactions.

### 12.7 Alembic Physical Constraints & Database Integrity (`[VERIFIED]`)
Cross-check against `alembic/versions/v1_5_baseline.py`:
1. **Destructive Check Constraint Flaw (`v1_5_baseline.py:677`):**
   `sa.CheckConstraint("paid_amount <= principal_amount", name="credit_card_installments_paid_check")`.
   - In installments where interest is charged (`interest_amount > 0`), `total_amount = principal_amount + interest_amount`.
   - `_apply_payment_to_waterfall` pays interest in Tier 2 and principal in Tier 4/5 by adding to `inst.paid_amount`.
   - When an installment is paid in full, `inst.paid_amount` reaches `total_amount`, which is strictly greater than `principal_amount`.
   - PostgreSQL immediately triggers `CheckViolation: credit_card_installments_paid_check`. Real database transactions paying interest-bearing installments will crash.
2. **Missing `command_id` on Credit Entities:**
   Neither `credit_card_transactions` nor `credit_card_early_payments` contains a `command_id` column. Idempotency cannot be verified independently of the Ledger.
3. **Unindexed and Non-Unique Foreign Keys:**
   - `credit_card_transactions.event_id` has NO unique constraint and NO database index.
   - `credit_card_early_payments.event_id` has NO unique constraint and NO database index.
   - Queries executing `get_transaction_by_event(event.id)` (`repository.py:92-96`) and `get_early_payment_by_event(event.id)` (`repository.py:117-123`) perform full sequential table scans on `credit_card_transactions` and `credit_card_early_payments`, creating serious performance degradation as transaction history grows.
   - The database does not enforce a 1:1 relationship between Ledger events and Credit transactions; duplicate transaction records pointing to the same event are physically permitted by the schema.

---

## 13 Goal reservation audit

### 13.1 Authoritative Goal Reservation Subsystem (`[VERIFIED]`)
Nexum implements explicit cash reservation enforcement to prevent money allocated to user goals from being spent by competing domains:
- **Calculation Authority:** `GoalRepository.calculate_reserved_by_account(account_id, user_id)` (`app/goals/repository.py:226-235`) computes the active reserved amount on a given account by summing allocated goal reservations minus releases and cancellations (`_source_reservation_amounts()`, lines 38-54).
- **Available Balance Derivation:** In `AccountService._set_available_balance` (`app/accounts/service.py:47-60`), the spendable balance of an account is formally defined as:
  $$\text{available\_balance} = \max(0, \text{account.balance} - \text{reserved})$$
- **Peer Domain Enforcement (Obligations):** The Obligations domain strictly enforces goal reservations. In `app/obligations/service_v17.py:645-649, 895-900, 1259-1264` and `app/obligations/service.py:395-401, 611-616`:
  ```python
  reserved_amount = await goal_repo.calculate_reserved_by_account(account.id, user_id)
  available_balance = account.balance - reserved_amount
  if available_balance < source_amount:
      raise InsufficientFundsError(...)
  ```
  Obligation bill payments strictly reject payments if they would encroach upon goal-reserved cash.

### 13.2 Credit Domain Isolation & Balance Verification (`[VERIFIED]`)
In stark contrast to Obligations, the Credit Domain completely ignores Goal reservations:
1. **Service Dependency Absence:** `CreditCardService.__init__` (`app/credit/service.py:45-50`) injects only `CreditCardRepository`, `LedgerService`, and `AccountRepository`. It does NOT inject `GoalRepository` or `GoalService`.
2. **Raw Balance Check in Bill Payments:** In `CreditCardService.create_payment` (`service.py:370-373`):
   ```python
   if not isinstance(account.balance, Mock) and account.balance < payload.amount:
       from app.cash.exceptions import InsufficientFundsError
       raise InsufficientFundsError()
   ```
   The check compares `payload.amount` exclusively against raw `account.balance`.
3. **Raw Balance Check in Early Prepayments:** In `CreditCardService.create_early_payment` (`service.py:600-601`):
   ```python
   if not isinstance(account.balance, Mock) and account.balance < source_amount:
       raise ValueError("Insufficient balance")
   ```
   The check compares `source_amount` exclusively against raw `account.balance`.
4. **Zero Balance Check in Preview:** `CreditCardService.preview_early_payment` (`service.py:748-751`) verifies only that `account.is_active` is True; it performs no balance check of any kind.

### 13.3 Mathematical Proof of Goal Fund Compromise (`[VERIFIED]`)
Consider a customer account with the following state:
- Real ledger balance: $\text{account.balance} = \$100{,}000\text{ COP}$
- Goal reservations: $\text{goal\_reserved} = \$80{,}000\text{ COP}$
- Legitimate spendable balance: $\text{available\_balance} = \$20{,}000\text{ COP}$

If the user submits a credit card bill payment or early prepayment for $\$90{,}000\text{ COP}$:
1. `account.balance < payload.amount` evaluates to $100{,}000 < 90{,}000 \rightarrow \mathbf{FALSE}$.
2. The payment is approved and debits the account: $\text{account.balance} = \$10{,}000\text{ COP}$.
3. **Result:** The account now holds only $\$10{,}000$ in cash, while Nexum still records $\$80{,}000$ as reserved for the user's goals.
4. **Conclusion:** $\$70{,}000$ of cash reserved for goals has been liquidated and spent on credit card debt. Goal reservations are breached.

### 13.4 Severity Classification: P0 (Candidate)
Under Nexum's audit severity criteria, a finding is classified as **P0** when there is:
> *"Inconsistencia financiera directa, pérdida de dinero, o corrupción de estado contable / reserva de fondos."*

Allowing credit card payments to spend cash reserved for goals violates accounting fund segregation, corrupts the available cash balance invariant across the engine, and permits double-spending of reserved funds. Finding **F-13** is classified as **Candidate P0**.

---

## 14 FX / multicurrency audit
`[CURRENT REALITY]`
- **Card Base Currency:** Locked to `credit_cards.currency` upon creation.
- **Foreign Currency Purchases:** The API accepts no foreign currency or original amount inputs (`CreditCardPurchaseCreate` in `schemas.py:65-72`). It records the supplied amount directly in `card.currency` (`service.py:287`). The engine cannot represent foreign purchase facts and implicitly trusts pre-converted input.
- **Cross-Currency Payments:** Supported where `account.currency != card.currency`. Server-side rate resolved via `app.core.currency.get_fx_rate`. Quotes previewed via `PaymentPreviewResult`.
- **Evidence Storage:** FX metadata stored in JSONB metadata columns (`financial_events`, `credit_card_transactions`, `credit_card_early_payments`). No dedicated schema columns exist.

---

## 15 Refund / reversal / pending audit
`[CURRENT REALITY]`
- **Refunds & Reversals:** Completely absent in code. No endpoints, service methods, or schemas exist to handle returned purchases, cancellations, chargebacks, or issuer adjustments.
- **Pending vs Posted:** Single-phase immediate settlement. `CreditCardTransaction` has no `status` column. No authorization holds or two-phase clearing exist.
- **Credit Balance Support:** Prohibited by view clamp `GREATEST(..., 0)` and service overpayment guards. True credit balances cannot exist.

---

## 16 Statements audit
`[CURRENT REALITY]`
- Physical tables `credit_card_statements` and `credit_card_statement_charges` exist, but no backend service, worker, cron, or trigger ever generates statement rows.
- Statements endpoints (`/statements`) return empty lists or 404s.
- `_calculate_card_status` operates as an on-read dynamic proxy for statement balances.
- `test_lazy_statement_generation` (`test_credit_statements.py:47-91`) overclaims coverage: it asserts installment counts but never tests statement generation.

---

## 17 API inventory
`[CURRENT REALITY]` Exactly 15 endpoints cataloged in `app/credit/router.py` and `app/intelligence/router.py`:

| HTTP Method & Path | Operation Handler | Lifecycle Classification | Consumer & Notes |
| :--- | :--- | :--- | :--- |
| `POST /api/v1/credit/cards` | `create_card` (`router.py:41-52`) | **CANONICAL** | Consumed by `createCreditCardAction`. Lacks `Idempotency-Key`. |
| `GET /api/v1/credit/cards` | `list_cards` (`router.py:54-61`) | **CANONICAL** | Consumed by `CreditClient.tsx`. Returns `statement_balance: null`. |
| `GET /api/v1/credit/cards/{card_id}` | `get_card` (`router.py:63-75`) | **CANONICAL** | Single card retrieval. |
| `PATCH /api/v1/credit/cards/{card_id}` | `update_card` (`router.py:77-92`) | **CANONICAL** | Card parameter update. Lacks `Idempotency-Key`. |
| `DELETE /api/v1/credit/cards/{card_id}` | `delete_card` (`router.py:94-108`) | **CANONICAL** | Soft deletion (`is_active = False`). Lacks `Idempotency-Key`. |
| `POST /api/v1/credit/cards/{card_id}/purchases` | `create_purchase` (`router.py:110-136`) | **CANONICAL** | Consumed by `purchaseCreditCardAction`. Enforces `Idempotency-Key`. |
| `POST /api/v1/credit/cards/{card_id}/payments` | `create_payment` (`router.py:137-168`) | **CANONICAL** | Consumed by `payCreditCardAction`. Enforces `Idempotency-Key`. |
| `POST /api/v1/credit/cards/{card_id}/purchases/{purchase_id}/pay_early` | `create_early_payment` (`router.py:170-209`) | **CANONICAL** | Consumed by `payEarlyPurchaseAction`. Enforces `Idempotency-Key`. |
| `POST /api/v1/credit/cards/{card_id}/purchases/{purchase_id}/pay_early/preview` | `preview_early_payment` (`router.py:210-237`) | **CANONICAL** | Consumed by `previewCreditEarlyPaymentAction`. Takes row locks in write tx! |
| `GET /api/v1/credit/summary` | `get_credit_summary` (`router.py:239-246`) | **CANONICAL** | Portfolio aggregation. |
| `GET /api/v1/credit/cards/{card_id}/status` | `get_card_status` (`router.py:248-260`) | **CANONICAL** | Card cycle & debt status. Returns computed `statement_balance`. |
| `GET /api/v1/credit/cards/{card_id}/installments` | `list_card_installments` (`router.py:262-274`) | **CANONICAL** | Consumed by `getCreditInstallmentsAction`. |
| `GET /api/v1/credit/cards/{card_id}/statements` | `list_statements` (`router.py:276-288`) | **AMBIGUOUS / DORMANT** | Exposed in API; table unpopulated. |
| `GET /api/v1/credit/cards/{card_id}/statements/{period}` | `get_statement` (`router.py:290-305`) | **AMBIGUOUS / DORMANT** | Exposed in API; table unpopulated. |
| `GET /api/v1/intelligence/credit-cards` | `get_credit_cards` (`app/intelligence/router.py`) | **LEGACY-BUT-REQUIRED** | Intelligence proxy endpoint. |

---

## 18 Frontend contract observations
`[VERIFIED]` Inspected at `frontend/src/app/app/credit/CreditClient.tsx`:
- Consumes **7 Server Actions**: `createCreditCardAction`, `purchaseCreditCardAction`, `payCreditCardAction`, `getCreditStatementsAction`, `getCreditInstallmentsAction`, `payEarlyPurchaseAction`, `previewCreditEarlyPaymentAction`.
- Displays computed attributes: `card.name`, `card.bank`, `card.credit_limit`, `card.available_credit`, `card.total_debt`, `card.billed_debt`, `card.unbilled_debt`, `card.payment_required`, `card.next_payment_estimate`, `card.cutoff_day`, `card.due_day`, `card.statement_balance`.
- Gracefully handles dormant statements: displays *"Aún no hay extractos generados para esta tarjeta"* when `statements.length === 0` (`CreditClient.tsx:90-94`).
- Early payment UI is enabled only when `inst.is_pay_early_eligible !== false` (`CreditClient.tsx:306`).

---

## 19 Production reality
`[PENDING VERIFICATION]`
- Physical inspection of live databases on Vulcano / CT101 required to verify:
  1. Extent of drift in persisted `credit_cards.current_debt` across active customer cards.
  2. Prevalence of customer purchases made post-cutoff that suffered premature billing.
  3. Total count and volume of orphaned `credit_card_early_payments` missing from `credit_card_transactions`.
  4. Historical instances of accounts where `account.balance < reserved` caused by credit card payments bypassing reservations.

---

## 20 PostgreSQL evidence
`[PENDING VERIFICATION]`
- Physical CT101 PostgreSQL 17 test passes required to confirm:
  1. Exact heap scan order behavior in `list_pending_installments_for_update` when multiple purchases share equal `scheduled_period` and `installment_number`.
  2. Execution of `credit_card_installments_paid_check` under real PostgreSQL when paying interest-bearing installments.
  3. Physical deadlock reproduction (`40P01`) under concurrent standard payments and early prepayments.
  4. Execution plan cost of unindexed sequential scans on `credit_card_transactions.event_id`.

---

## 21 Test coverage map
`[CURRENT REALITY]` Five unit test modules in `tests/unit/`:
1. `test_credit.py` (311 lines): Card CRUD, purchase creation, payment overpayment guards, `test_card_status_ignores_persisted_current_debt`.
2. `test_credit_semantics.py` (127 lines): Cycle calculation tests, schema properties.
3. `test_credit_statements.py` (91 lines): `test_lazy_statement_generation` asserts purchase installments; statements remain untested.
4. `test_credit_advanced.py` (88 lines): Waterfall allocation test. **Warning:** Contains 3 dummy `pass` stub tests: `test_early_payment_success`, `test_early_payment_insufficient_funds`, `test_early_payment_exceeds_principal`.
5. `test_credit_summary_qa.py` (56 lines): Basic card status and pay early tests.
- Operational Scripts: `scripts/smoke/smoke_credit_core.py`, `scripts/smoke/smoke_credit_semantics_v11.py`, `scripts/smoke/smoke_credit.py`.
- **Testing Blindspots Identified (`[VERIFIED]`):**
  - All existing unit tests mock repositories and bypass real PostgreSQL constraints. The check constraint crash on `paid_amount <= principal_amount` was hidden because SQLite/mocks do not evaluate the Alembic schema.
  - Concurrency, locking, deadlocks, and goal reservation enforcement are completely untested.

---

## 22 P0/P1/P2 findings

*Note: All severities are provisional candidate classifications pending Codex independent review.*

| ID | Provisional Severity | Classification | Title & Core Defect Summary | Evidence Source Path & Symbols |
| :--- | :--- | :--- | :--- | :--- |
| **F-01** | **P0 (Candidate)** | `[VERIFIED]` | **Premature Billing of Post-Cutoff Purchases.** `_build_installments` sets `scheduled_period = today.strftime("%Y-%m")` without day precision. Post-cutoff purchases within the same calendar month are billed immediately. | `app/credit/service.py:84,112`, `app/credit/repository.py:129-130`. |
| **F-02** | **P0 (Candidate)** | `[VERIFIED]` | **Timezone Split-Brain Between Python and PostgreSQL.** Python uses naive local `date.today()`, bypassing `financial_date()`, while SQL uses `now() AT TIME ZONE 'America/Bogota'`. Desynchronizes across month boundaries. | `app/credit/service.py:84,125,528,734,811`, `app/credit/repository.py:40,168`, `app/core/financial_time.py:9`. |
| **F-03** | **P0 (Candidate)** | `[VERIFIED]` | **Flawed Flat Interest Formula & Single-Installment Penalty.** Flat interest formula applies to single-installment purchases if rate > 0. Amortization and declining balance are unsupported. | `app/credit/service.py:94-99`. |
| **F-04** | **P0 (Candidate)** | `[VERIFIED]` | **Debt Calculation Triality & Stale Column Drift.** Schema retains dead column `current_debt`; view sums transactions; service sums installments; metadata claims transactions. | `models.py:33`, `repository.py:35-48`, `service.py:53`, `v1_5_baseline.py:1499-1544`. |
| **F-05** | **P1 (Candidate)** | `[VERIFIED]` | **Loss of Payment Allocation Traceability.** Payments increment cumulative installment `paid_amount` without creating allocation junction records or linking to payment transactions. | `app/credit/service.py:136,459-480`, `app/credit/models.py:87-114`. |
| **F-06** | **P1 (Candidate)** | `[VERIFIED]` | **Non-Deterministic Payment Allocation Order.** `list_pending_installments_for_update` sorts by `scheduled_period, installment_number` without tiebreakers. Equal installments allocate in heap scan order. | `app/credit/repository.py:85-88`. |
| **F-07** | **P1 (Candidate)** | `[VERIFIED]` | **Overpayment Wall on Multi-Installment Debt.** Waterfall Tier 6 allows overpayment only on revolving debt (`installments_total == 1`). Cardholders with multi-installment purchases cannot pay ahead via standard payment. | `app/credit/service.py:176-184`. |
| **F-08** | **P1 (Candidate)** | `[VERIFIED]` | **Early Prepayment Transaction Table Void.** `/pay_early` inserts to `credit_card_early_payments` but creates no row in `credit_card_transactions`, breaking transaction-based views and audits. | `app/credit/service.py:649-667`. |
| **F-09** | **P1 (Candidate)** | `[VERIFIED]` | **Complete Absence of Refunds, Reversals, and Chargebacks.** No domain logic or schemas exist to handle returned purchases, merchant credits, or transaction cancellations. | `app/credit/models.py`, `app/credit/service.py`, `app/credit/router.py`. |
| **F-10** | **P1 (Candidate)** | `[CURRENT REALITY]` | **Dormant Statement Subsystem & Overclaimed Test Coverage.** Statement generation is non-functional. `test_lazy_statement_generation` claims to test statements but asserts only installment counts. | `app/credit/service.py:918-936`, `tests/unit/test_credit_statements.py:47-91`. |
| **F-11** | **P1 (Candidate)** | `[VERIFIED]` | **Implicit Trust of Pre-Converted Foreign Purchases.** The API lacks original currency and amount inputs, forcing foreign purchases to be recorded as card currency without tracking original FX facts. | `app/credit/schemas.py:65-72`, `app/credit/service.py:287`. |
| **F-12** | **P1 (Candidate)** | `[VERIFIED]` | **API Read Model Contradictions for Statement Balance and Minimum Payment.** `/cards` returns `statement_balance = null` and `minimum_payment = 0.00`, whereas `/status` computes different derived values. This is an important financial contract inconsistency; neither representation is issuer-authoritative today. | `app/credit/service.py:204` vs `app/credit/service.py:837,845`. |
| **F-13** | **P0 (Candidate)** | `[VERIFIED]` | **Goal Reservation Bypass and Spendable Balance Breach.** `create_payment` and `create_early_payment` validate against raw `account.balance` rather than spendable balance (`account.balance - reserved`), allowing goal-reserved cash to be spent. | `app/credit/service.py:370,600`, `app/goals/repository.py:226`, `app/accounts/service.py:53`. |
| **F-14** | **P1 (Candidate)** | `[VERIFIED STATIC HAZARD; PENDING PHYSICAL REPRODUCTION]` | **Inverted Lock Ordering & Deadlock Hazard Between Payments and Early Payments.** Standard payment locks `accounts` then `credit_cards`, while early payment/preview locks `credit_cards` then `accounts`. Concurrent operations on the same rows can form a `40P01` wait cycle; occurrence still requires physical reproduction. | `app/credit/service.py:357,375` vs `app/credit/service.py:507,540` and `714,748`. |
| **F-15** | **P0 (Candidate)** | `[VERIFIED]` | **Destructive Alembic Check Constraint Violation on Interest Installment Payments.** Alembic enforces `paid_amount <= principal_amount`. When interest is paid, `paid_amount` reaches `total_amount > principal_amount`, causing PostgreSQL to abort transactions with `CheckViolation`. | `alembic/versions/v1_5_baseline.py:677`, `app/credit/service.py:150-174`. |
| **F-16** | **P1 (Candidate)** | `[VERIFIED]` | **Blind Idempotent Retry and Command ID Cross-Operation Replay Vulnerability.** `CreditCardService` relies entirely on `financial_events.command_id` without verifying event type, card ownership, or payload match. Reusing a `command_id` across different operations returns an invalid `idempotent_retry` citing an unrelated event. | `app/credit/service.py:299-310, 446-457`, `app/ledger/repository.py:148-175`. |
| **F-17** | **P2 (Candidate)** | `[VERIFIED]` | **Exclusive Row Locking and Mutating Transaction in Read Preview Endpoint.** `POST .../pay_early/preview` opens a write transaction (`uow.transaction()`) and acquires `FOR UPDATE` row locks on card, installments, and account while performing an external FX quote, causing unnecessary lock contention. | `app/credit/router.py:218`, `app/credit/service.py:714,724,748`. |
| **F-18** | **P2 (Candidate)** | `[VERIFIED]` | **Missing Foreign Key Indexes and Sequential Table Scans on High-Frequency Lookups.** `credit_card_transactions` lacks an index and unique constraint on `event_id`, forcing sequential table scans during every idempotent retry lookup (`get_transaction_by_event`). | `alembic/versions/v1_5_baseline.py:516-603`, `app/credit/repository.py:92-96`. |
| **F-19** | **P0 (Candidate)** | `[VERIFIED STATIC PATH; PENDING PHYSICAL SCENARIO]` | **Statement-Charge Payment Can Return False Remaining Debt.** The debt guard and `new_debt` use installment-only debt, while the waterfall first allocates part of the payment to statement charges. The response subtracts the full applied amount from installment debt even when only the remainder reached installments. | `app/credit/repository.py:35-48`, `app/credit/service.py:153-168, 418, 480-483`. |

---

## 23 Documentation contradictions
`[VERIFIED]`
1. **Repository Docstring vs Execution:** `CreditCardRepository.get_card_debt` docstring (`repository.py:36`) states `"""Returns (credit_card_debt, monthly_cc_payment) from the view."""`. The function directly executes raw SQL against `credit_card_installments`.
2. **Data Quality Metadata vs Execution:** `CreditCardService._data_quality()` (`service.py:53`) returns `"current_debt": "computed_from_credit_card_transactions"`. Active debt calculations execute against `credit_card_installments`.
3. **View v_credit_card_debt vs Early Payments:** The view calculates debt as `purchases - payments` from `credit_card_transactions`. Because `/pay_early` inserts only into `credit_card_early_payments`, the view reports phantom debt.
4. **Statement Test Docstring vs Execution:** `test_lazy_statement_generation` (`test_credit_statements.py:48-51`) docstring claims to test *"lazy statement generation after cutoff"*. The test only asserts purchase installment counts.
5. **Alembic Check Constraint vs Installment Model Design:** Migration `v1_5_baseline.py:677` documents `credit_card_installments_paid_check` as `paid_amount <= principal_amount`, contradicting `models.py:107` and `service.py:150-174`, which define `total_amount = principal_amount + interest_amount` and accumulate payments toward `total_amount`.
6. **Preview Endpoint Contract vs Transaction State:** Route docstring in `router.py:210-237` represents preview as a safe read-only quote calculation, yet the implementation wraps the call in `uow.transaction()` and executes `SELECT ... FOR UPDATE` across multiple database tables.
7. **Accounts / Obligations Reservation Invariant vs Credit Practice:** System architectural documentation (`docs/project/01-architecture.md`, `13-accounts-wallets.md`) mandates that all cash outflows must respect goal reservations, but `CreditCardService` bypasses reservations unconditionally.

---

## 24 Canonical Credit model proposal
`[PENDING PHASE E]` *(Demonstrated architectural needs cataloged)*
1. **Explicit Allocation Junction Entity:** Introduction of `credit_card_payment_allocations` linking `payment_transaction_id` to `installment_id`, capturing exact principal, interest, and fee amounts settled.
2. **Statement Snapshot Architecture:** Immutable closed statement records representing frozen cycle state, modified only via explicit correction events.
3. **Multi-Currency Purchase Model:** Inclusion of `original_amount`, `original_currency`, `fx_rate`, and `booked_amount` on `credit_card_transactions`.
4. **Transaction State Machine:** Support for `pending` (authorized) vs `posted` (cleared) transactions.

---

## 25 Canonical invariants proposal
`[PENDING PHASE E]` *(Demonstrated mathematical and transactional invariants cataloged)*
1. **Invariant 1 (Single Transaction Unit of Work):** Every mutating operation (Ledger event, Account debit, Credit transaction, installment mutation) must execute and commit within exactly one atomic database transaction. No partial states may persist across failures.
2. **Invariant 2 (Spendable Balance & Goal Reservation Invariant):**
   $$\text{payment\_source\_amount} \le \text{account.balance} - \text{calculate\_reserved\_by\_account}(\text{account.id}, \text{user.id})$$
   Credit payments must never consume funds allocated to goal reservations.
3. **Invariant 3 (Canonical Resource Lock Ordering Hierarchy):** To eliminate deadlocks across all financial endpoints, locks must be acquired in strict global topological order:
   $$\text{accounts} \longrightarrow \text{credit\_cards} \longrightarrow \text{credit\_card\_installments} \longrightarrow \text{credit\_card\_statement\_charges}$$
   Under no circumstances may an endpoint lock `credit_cards` before `accounts`.
4. **Invariant 4 (Installment Ceiling Invariant):**
   $$\text{paid\_amount} \le \text{total\_amount} = \text{principal\_amount} + \text{interest\_amount}$$
   The check constraint on `credit_card_installments` must be corrected from `paid_amount <= principal_amount` to `paid_amount <= total_amount`.
5. **Invariant 5 (Money Conservation & Multi-Currency Identity):**
   $$\text{account\_debit} = \text{ledger\_source\_amount}$$
   $$\text{debt\_reduction} = \text{applied\_card\_amount} = \text{round\_to\_minimum\_unit}(\text{source\_amount} \times \text{fx\_rate}, \text{card.currency})$$
6. **Invariant 6 (Deep Idempotency Verification):** Replaying a `command_id` must verify that `(user_id, operation_type, entity_id, amount)` matches the original command payload. Mismatches must raise HTTP 409 Conflict rather than returning a corrupt idempotent retry.
7. **Invariant 7 (Payment Allocation Auditability):**
   $$\text{payment\_transaction.amount} = \sum_{\text{allocations}} (\text{allocated\_principal} + \text{allocated\_interest} + \text{allocated\_fees})$$
   Every payment transaction must produce immutable junction rows linking to settled installments.
8. **Invariant 8 (Non-Negative Debt):** $\text{card.total\_debt} \ge 0.00$. Overpayment beyond total debt is blocked.

---

## 26 Intelligence readiness
`[PENDING PHASE E]` *(Analysis of snapshot consumption)*
- `app/intelligence/service.py:131` consumes `CreditCardService.get_credit_summary`.
- `SnapshotDebt`: `credit_card_total_debt = billed_debt + unbilled_debt`.
- `free_money`: Deducts `minus_credit_cards_required_payment` (`billed_debt`).
- Snapshot consumption bypasses dormant SQL views cleanly, but inherits all upstream calculation defects (F-01, F-02, F-03, F-13).

---

## 27 Founder decisions required
`[OPEN PRODUCT DECISION]`

### 27.1 Approved Founder Directions Available
The following policies have been explicitly established:
1. **Authority Hierarchy:** Bank-connected issuer facts are authoritative; manual values serve as fallback.
2. **Credit Limit Separation:** Issuer credit limit and available credit remain strictly separate from future safe-to-spend recommendations.
3. **Closed Statement Snapshot Policy:** Closed statements are immutable snapshots that change only via explicit correction, reversal, refund, chargeback, or adjustment events.
4. **Post-Cutoff Attribution:** Purchases made post-cutoff belong strictly to the subsequent billing cycle.
5. **No Invented Formulas:** Do not invent a universal interest or minimum-payment formula.
6. **Waterfall Reconstructed Without Endorsement:** Current waterfall behavior is reconstructed as-is without architectural endorsement.
7. **Prepayment Concept:** Payments above current amount due but below total debt are conceptually prepayments.
8. **Overpayment Cap:** Payments above total debt must normally be rejected or capped; do not silently generate positive credit balances.
9. **Atomic Reconciliation:** Credit payments must atomically reconcile Account balance, Ledger event, card debt, and statement/payment allocations.
10. **Goal Reservation Policy:** Goal reservations must reduce spendable account balance.
11. **Currency Separation:** One card has one base currency; original purchase amount/currency, FX evidence, booked amount, and base currency remain separate.
12. **Canonical Time:** Pinned to `financial_date()` (`America/Bogota`) pending Founder change.

### 27.2 Open Founder Decisions Requiring Resolution
1. **Statement Generation Architecture:** Should statements be generated via automated monthly scheduled worker, or generated lazily upon first read post-cutoff?
2. **Multi-Currency Purchase Scope for Alpha:** Should non-native currency purchases be supported in Alpha with automated FX conversion, or remain restricted to pre-converted native amounts?
3. **Interest Authority and Grace Period:** Define which interest facts are issuer-provided versus Nexum-derived, how rate representations map to products, and whether any issuer-specific grace period applies to one-installment purchases.
4. **Refund Allocation Priority:** When a merchant refund arrives, should it restore credit limit against original installments, or apply as a general credit following the reverse waterfall?
5. **Advance Payment on Multi-Installment Debt:** Should standard bill payments be allowed to prepay unbilled multi-installment principal without requiring explicit purchase-by-purchase `/pay_early` calls?

---

## 28 Goal 2 scope proposal
`[PENDING PHASE E]` Recommended remediation scope for Goal 2:
1. Fix Cutoff Cycle Assignment (`F-01`): Incorporate `cutoff_day` comparison in `_build_installments` to ensure post-cutoff purchases receive next month's `scheduled_period`.
2. Standardize Canonical Clock (`F-02`): Replace all `date.today()` calls across `app/credit` with `app.core.financial_time.financial_date()`.
3. Replace Unsupported Interest Logic (`F-03`): Implement only issuer-authoritative or Founder-approved interest and grace-period semantics selected after product review.
4. Prune Stale Debt Column (`F-04`): Formally deprecate `credit_cards.current_debt` column and align all docstrings and metadata.
5. Implement Payment Allocations Table (`F-05`): Create `credit_card_payment_allocations` to ensure full payment auditability.
6. Deterministic Waterfall Ordering (`F-06`): Add canonical secondary ordering key and timestamp to installments to make allocations reproducible.
7. Record Prepayment Transactions (`F-08`): Ensure `/pay_early` creates a corresponding row in `credit_card_transactions`.
8. Enforce Goal Reservations in Credit Payments (`F-13`): Inject `GoalRepository` into `CreditCardService` and enforce `account.balance - reserved >= payment_amount`.
9. Standardize Global Lock Ordering Hierarchy (`F-14`): Align all mutating endpoints to acquire locks in `accounts -> credit_cards -> installments -> charges` sequence. Remove row locking from preview endpoints.
10. Fix Alembic Check Constraint on Installments (`F-15`): Update `credit_card_installments_paid_check` to `paid_amount <= total_amount`.
11. Implement Deep Idempotency Guard (`F-16`): Compare incoming intent and entity parameters against cached event before accepting idempotent retries.
12. Index Foreign Keys (`F-18`): Add database indexes and unique constraints on `credit_card_transactions.event_id` and `credit_card_early_payments.event_id`.
