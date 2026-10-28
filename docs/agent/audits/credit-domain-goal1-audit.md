# Nexum Credit Domain Goal 1 — Phase B Domain Reconstruction & Technical Audit

**Audit Metadata:**
- **Goal:** Credit Domain Goal 1 Audit (Phase B Domain Reconstruction)
- **Target System:** Nexum Core Financial Engine — Credit Domain (`app/credit`)
- **Author/Worker:** Execution Worker (Phase B Domain Reconstruction)
- **Reviewer:** Codex (Architect & Independent Verifier)
- **Status:** Phase B Domain Reconstruction Complete / Awaiting Codex Review
- **Standards of Evidence:**
  - `[CURRENT REALITY]`: Grounded directly in active repository code (`app/credit`, `app/ledger`, `app/accounts`, `app/intelligence`, `alembic/`).
  - `[VERIFIED]`: Proven via AST, line-by-line control flow, schema constraints, or SQL queries.
  - `[TESTED]`: Verified by existing automated unit/smoke test suites.
  - `[DOCUMENTED CLAIM]`: Found in historical documentation or docstrings without active runtime enforcement.
  - `[PENDING VERIFICATION]`: Awaiting physical CT101 PostgreSQL 17 execution or production database verification.
  - `[PENDING PHASE C]`: Reserved for Phase C invariants and reconciliation definition.
  - `[PENDING PHASE E]`: Reserved for Phase E canonical architecture and forward proposals.
  - `[OPEN PRODUCT DECISION]`: Unresolved business rules requiring Founder decision.

---

## 1 Executive assessment
`[CURRENT REALITY]` This document presents the consolidated Phase B Domain Reconstruction for the Nexum Credit Domain (`app/credit`). Combining the structural inventory from Phase A with the rigorous behavioral tracing of Phase B, this report establishes the definitive technical and mathematical reality of how credit cards, purchases, installments, billing cycles, payments, waterfalls, and cross-domain integrations currently operate.

Key Executive Findings:
1. **Flawed Cutoff Cycle Assignment (`[VERIFIED]`):** `CreditCardService._build_installments` derives `scheduled_period = today.strftime("%Y-%m")` based solely on the purchase calendar month (`app/credit/service.py:112`). Because `scheduled_period` lacks day granularity, purchases made on or after the cutoff date are immediately classified as `billed` in `_calculate_card_status` when evaluated in that month, demanding payment in the current cycle rather than rolling into the next billing cycle.
2. **Timezone Split-Brain (`[VERIFIED]`):** Python domain logic in `app/credit/service.py` relies on naive host-local `date.today()`, bypassing Nexum's canonical `financial_date()` (`America/Bogota` via `app/core/financial_time.py`), whereas SQL repository queries use `now() AT TIME ZONE 'America/Bogota'` (`app/credit/repository.py:40,168`). Cloud servers running in UTC desynchronize at month boundaries.
3. **Loss of Payment Allocation Traceability (`[VERIFIED]`):** Payments mutate cumulative `CreditCardInstallment.paid_amount` and `CreditCardStatementCharge.paid_amount` directly. No junction table records which payment transaction satisfied which installment or charge, destroying audit reconcilability.
4. **Non-Deterministic Payment Allocation Ordering (`[VERIFIED]`):** `list_pending_installments_for_update` orders by `scheduled_period.asc(), installment_number.asc()` (`app/credit/repository.py:85-88`). Multiple purchases with installments in the same period share the same `installment_number` and are allocated in arbitrary database physical scan order.
5. **Overpayment Wall for Multi-Installment Debt (`[VERIFIED]`):** Standard payments permit paying ahead only for unbilled revolving debt (`installments_total == 1`). If a customer attempts to pay more than their current billed debt when only multi-installment purchases exist, `_apply_payment_to_waterfall` raises `InvalidPaymentAmountError` (`app/credit/service.py:182`).
6. **Early Prepayment Transaction Void (`[VERIFIED]`):** Early payments insert a record in `credit_card_early_payments` and emit a Ledger event, but create **no record in `credit_card_transactions`** (`app/credit/service.py:649-667`).
7. **Complete Absence of Refunds and Adjustments (`[VERIFIED]`):** No endpoints, service routines, or event listeners exist for merchant refunds, purchase cancellations, chargebacks, or true credit-balance (saldo a favor) management.
8. **Dormant Statement Subsystem (`[CURRENT REALITY]`):** Statement generation is inactive. Statements endpoints return empty sets or 404s. `_calculate_card_status` acts as a dynamic synthetic proxy for statement balances.

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
   - `event_id`: UUID (NULL, references `financial_events.id`).
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
   - Indexes: `idx_credit_card_installments_card_period`, `idx_credit_card_installments_user_card`.

5. **`public.credit_card_early_payments`** (`models.py:149-173`, `v1_5_baseline.py:703-751`):
   - `id`: UUID (PK).
   - `user_id`: UUID (NOT NULL).
   - `credit_card_id`: UUID (FK -> `credit_cards.id` ON DELETE CASCADE, NOT NULL).
   - `purchase_transaction_id`: UUID (FK -> `credit_card_transactions.id` ON DELETE CASCADE, NOT NULL).
   - `amount`: NUMERIC(14,2) (NOT NULL).
   - `allocation_mode`: VARCHAR (NOT NULL).
   - `event_id`: UUID (NULL).
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
- **Session & Ownership:** Managed by FastAPI dependency `get_db_session` (`app/core/database.py`). Commits upon request completion.
- **API Representation:** Returns `CreditCardRead` (`schemas.py:40-63`) via `_enrich_card` (`service.py:186-209`). Initial computed debt is `0.00`, `available_credit = credit_limit`, `payment_required = 0.00`, `statement_balance = None`.
- **Tests:** `tests/unit/test_credit.py:55-80`, `tests/unit/test_credit.py:280-311`.

### 5.2 Purchase Execution
- **Call Path:** `POST /api/v1/credit/cards/{card_id}/purchases` -> `router.py:110-136` -> `CreditCardService.create_purchase` (`service.py:261-348`).
- **Validation & Limit Locking:** Acquires row-level lock via `repo.get_by_id_for_update(card_id)` (`repository.py:19-23`). Validates `card.is_active`. Calls `repo.get_card_debt(card.id)` (`repository.py:35-48`). If `payload.amount > max(0, credit_limit - estimated_debt)`, raises `CreditLimitExceededError`, translated by the router to HTTP 409 (`router.py:133-134`).
- **Ledger Event:** Records `LedgerEventCreate(event_type="credit_card_purchase", direction="neutral", amount=payload.amount, currency=card.currency, metadata={"credit_card_id": str(card.id)})` (`service.py:282-293`). Idempotency handled via `command_id`.
- **Transaction & Installments:** Inserts `CreditCardTransaction(type="purchase")` (`service.py:314-324`). Invokes `_build_installments` to generate and persist `CreditCardInstallment` records (`service.py:327-334`).
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
- **Account Verification:** Locks account via `account_repo.get_by_id_for_update(payload.account_id)` (`service.py:357`). Validates ownership, active status, and `account.balance >= payload.amount`.
- **Card Lock & FX:** Locks card via `repo.get_by_id_for_update(card_id)`. If currencies differ, calls `app.core.currency.get_fx_rate`.
- **Overpayment Guard:** `if applied_amount > estimated_debt: raise InvalidPaymentAmountError()` (`service.py:418-419`).
- **Ledger Event & Account Debit:** Emits `credit_card_payment` (outflow) event (`service.py:422-440`). Debits account balance: `account.balance -= payload.amount` (`service.py:459`).
- **Transaction Record:** Inserts `CreditCardTransaction(type="payment", amount=applied_amount, account_id=payload.account_id, event_id=event.id)` (`service.py:461-479`).
- **Waterfall Allocation:** Executes `_apply_payment_to_waterfall(user_id, card, applied_amount)` (`service.py:480`), applying funds across Tiers 1–6 and updating `inst.paid_amount` and `charge.paid_amount`.
- **Tests:** `tests/unit/test_credit.py:133-182`, `tests/unit/test_credit.py:184-218`.

### 5.7 Early Prepayment Flow
- **Call Path:** `POST /api/v1/credit/cards/{card_id}/purchases/{purchase_id}/pay_early` -> `service.create_early_payment` (`service.py:496-704`).
- Validates multi-installment eligibility (`installments_total > 1`) and presence of unbilled future installments (`scheduled_period > current_period`).
- Emits Ledger event (`credit_card_payment`, outflow). Debits account balance.
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
`[PENDING PHASE C]` *(Proven static facts preserved)*
- **Ledger Events:** Purchases emit `credit_card_purchase` (direction neutral, `currency=card.currency`). Payments emit `credit_card_payment` (direction outflow, `currency=account.currency`).
- **Account Mutation:** Payments directly mutate `account.balance -= payload.amount` (`service.py:459`) under row lock.
- **Reconciliation Requirement:** Phase C must establish atomic reconciliation across Account balances, Ledger events, card debt, and installment payment allocations.

---

## 13 Goal reservation audit
`[PENDING PHASE C]` *(Proven static facts preserved)*
- No foreign keys link `credit_cards` to `goals`.
- In `IntelligenceService`, financial snapshot `free_money` subtracts goal contributions and credit card required payments.
- Goal reservations must reduce spendable account balance without distorting credit debt calculations.

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
| `POST /api/v1/credit/cards` | `create_card` (`router.py:41-52`) | **CANONICAL** | Consumed by `createCreditCardAction`. |
| `GET /api/v1/credit/cards` | `list_cards` (`router.py:54-61`) | **CANONICAL** | Consumed by `CreditClient.tsx`. Returns `statement_balance: null`. |
| `GET /api/v1/credit/cards/{card_id}` | `get_card` (`router.py:63-75`) | **CANONICAL** | Single card retrieval. |
| `PATCH /api/v1/credit/cards/{card_id}` | `update_card` (`router.py:77-92`) | **CANONICAL** | Card parameter update. |
| `DELETE /api/v1/credit/cards/{card_id}` | `delete_card` (`router.py:94-108`) | **CANONICAL** | Soft deletion (`is_active = False`). |
| `POST /api/v1/credit/cards/{card_id}/purchases` | `create_purchase` (`router.py:110-136`) | **CANONICAL** | Consumed by `purchaseCreditCardAction`. |
| `POST /api/v1/credit/cards/{card_id}/payments` | `create_payment` (`router.py:137-168`) | **CANONICAL** | Consumed by `payCreditCardAction`. |
| `POST /api/v1/credit/cards/{card_id}/purchases/{purchase_id}/pay_early` | `create_early_payment` (`router.py:170-209`) | **CANONICAL** | Consumed by `payEarlyPurchaseAction`. |
| `POST /api/v1/credit/cards/{card_id}/purchases/{purchase_id}/pay_early/preview` | `preview_early_payment` (`router.py:210-237`) | **CANONICAL** | Consumed by `previewCreditEarlyPaymentAction`. |
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

---

## 20 PostgreSQL evidence
`[PENDING VERIFICATION]`
- Physical CT101 PostgreSQL 17 test passes required to confirm:
  1. Exact heap scan order behavior in `list_pending_installments_for_update` when multiple purchases share equal `scheduled_period` and `installment_number`.
  2. View performance and lock contention on `v_credit_card_debt` vs `credit_card_installments`.
  3. Strict enforcement of check constraints across live database migrations.

---

## 21 Test coverage map
`[CURRENT REALITY]` Five unit test modules in `tests/unit/`:
1. `test_credit.py` (311 lines): Card CRUD, purchase creation, payment overpayment guards, `test_card_status_ignores_persisted_current_debt`.
2. `test_credit_semantics.py` (127 lines): Cycle calculation tests, schema properties.
3. `test_credit_statements.py` (91 lines): `test_lazy_statement_generation` asserts purchase installments; statements remain untested.
4. `test_credit_advanced.py` (88 lines): Waterfall allocation test. **Warning:** Contains 3 dummy `pass` stub tests: `test_early_payment_success`, `test_early_payment_insufficient_funds`, `test_early_payment_exceeds_principal`.
5. `test_credit_summary_qa.py` (56 lines): Basic card status and pay early tests.
- Operational Scripts: `scripts/smoke/smoke_credit_core.py`, `scripts/smoke/smoke_credit_semantics_v11.py`, `scripts/smoke/smoke_credit.py`.

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

---

## 23 Documentation contradictions
`[VERIFIED]`
1. **Repository Docstring vs Execution:** `CreditCardRepository.get_card_debt` docstring (`repository.py:36`) states `"""Returns (credit_card_debt, monthly_cc_payment) from the view."""`. The function directly executes raw SQL against `credit_card_installments`.
2. **Data Quality Metadata vs Execution:** `CreditCardService._data_quality()` (`service.py:53`) returns `"current_debt": "computed_from_credit_card_transactions"`. Active debt calculations execute against `credit_card_installments`.
3. **View v_credit_card_debt vs Early Payments:** The view calculates debt as `purchases - payments` from `credit_card_transactions`. Because `/pay_early` inserts only into `credit_card_early_payments`, the view reports phantom debt.
4. **Statement Test Docstring vs Execution:** `test_lazy_statement_generation` (`test_credit_statements.py:48-51`) docstring claims to test *"lazy statement generation after cutoff"*. The test only asserts purchase installment counts.

---

## 24 Canonical Credit model proposal
`[PENDING PHASE E]` *(Demonstrated architectural needs cataloged)*
1. **Explicit Allocation Junction Entity:** Introduction of `credit_card_payment_allocations` linking `payment_transaction_id` to `installment_id`, capturing exact principal, interest, and fee amounts settled.
2. **Statement Snapshot Architecture:** Immutable closed statement records representing frozen cycle state, modified only via explicit correction events.
3. **Multi-Currency Purchase Model:** Inclusion of `original_amount`, `original_currency`, `fx_rate`, and `booked_amount` on `credit_card_transactions`.
4. **Transaction State Machine:** Support for `pending` (authorized) vs `posted` (cleared) transactions.

---

## 25 Canonical invariants proposal
`[PENDING PHASE E]` *(Demonstrated mathematical invariants cataloged)*
1. **Invariant 1 (Debt Integrity):** `card.total_debt == sum(inst.principal_amount + inst.interest_amount - inst.paid_amount for inst in active_installments)`.
2. **Invariant 2 (Payment Conservation):** `payment_transaction.amount == sum(allocation.amount for allocation in payment_allocations)`.
3. **Invariant 3 (Cycle Attribution):** Purchases on or after `cutoff_date` must not be classified as `billed` until the subsequent cycle closes.
4. **Invariant 4 (Non-Negative Debt):** `card.total_debt >= 0.00`. Overpayment beyond total debt is blocked.

---

## 26 Intelligence readiness
`[PENDING PHASE E]` *(Analysis of snapshot consumption)*
- `app/intelligence/service.py:131` consumes `CreditCardService.get_credit_summary`.
- `SnapshotDebt`: `credit_card_total_debt = billed_debt + unbilled_debt`.
- `free_money`: Deducts `minus_credit_cards_required_payment` (`billed_debt`).
- Snapshot consumption bypasses dormant SQL views cleanly, but inherits all upstream calculation defects (F-01, F-02, F-03).

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
3. Replace Unsupported Interest Logic (`F-03`): implement only the issuer-authoritative or Founder-approved interest and grace-period semantics selected after product review; do not preserve the current universal placeholder as financial truth.
4. Prune Stale Debt Column (`F-04`): Formally deprecate `credit_cards.current_debt` column and align all docstrings and metadata.
5. Implement Payment Allocations Table (`F-05`): Create `credit_card_payment_allocations` to ensure full payment auditability.
6. Deterministic Waterfall Ordering (`F-06`): define the canonical ordering key and persist any missing timestamp or sequence needed to make allocations reproducible; the current installment table has no `created_at` field.
7. Record Prepayment Transactions (`F-08`): Ensure `/pay_early` creates a corresponding row in `credit_card_transactions`.
