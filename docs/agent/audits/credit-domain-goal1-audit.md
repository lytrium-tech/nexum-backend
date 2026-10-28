# Nexum Credit Domain Goal 1 — Phase A Inventory & Audit Draft

**Audit Metadata:**
- **Goal:** Credit Domain Goal 1 Audit (Phase A Inventory Draft)
- **Document Structure:** 28-Topic Phase A Inventory Baseline (preliminary audit draft prior to Phase B reconstruction and final consolidated domain report)
- **Target System:** Nexum Core Financial Engine — Credit Domain (`app/credit`)
- **Author/Worker:** Execution Worker (Phase A Inventory)
- **Reviewer:** Codex (Architect & Independent Verifier)
- **Status:** Phase A Verified Corrections / Draft Baseline
- **Evidence Classification Standards:**
  - `[CURRENT REALITY]`: Grounded directly in active repository files (`app/`, `tests/`, `openapi.json`).
  - `[VERIFIED]`: Directly proven via inspected code logic, AST definitions, migration schemas, or test assertions.
  - `[DOCUMENTED CLAIM]`: Found in historical markdown documentation (`docs/`) or docstrings without code-level execution verification.
  - `[PENDING VERIFICATION]`: Awaiting runtime testing, physical CT101 PostgreSQL 17 validation, live database inspection, or Phase B reconstruction.

---

## 1. Executive Summary & Audit Metadata
This document presents the Phase A evidence-based inventory and preliminary audit draft for the Nexum Credit Domain (`app/credit`). It establishes the physical schema, ORM entity, Pydantic DTO, endpoint, service, repository, test, tooling, and cross-domain contract baseline required for subsequent architectural reconstruction.

Key initial findings from Phase A inspection:
- **Debt Calculation Dualism & Misleading Metadata:** The physical schema retains view `v_credit_card_debt` aggregating raw `credit_card_transactions` (purchases minus payments), but `CreditCardRepository` and `CreditCardService` dynamically calculate debt by aggregating `principal_amount + interest_amount - paid_amount` from `credit_card_installments`. Meanwhile, `_data_quality()` in `app/credit/service.py:53` explicitly reports `"current_debt": "computed_from_credit_card_transactions"`, contradicting active code execution.
- **Dormant Statements Subsystem:** Physical tables `credit_card_statements` and `credit_card_statement_charges` exist, along with ORM models and API routes, but no backend service, scheduled worker, or event listener currently generates or updates statements.
- **Vestigial Persisted Storage:** Column `credit_cards.current_debt` is persisted with a default of `0.00` but is never updated by purchase or payment operations; service read models intentionally bypass it.
- **Frontend Integration Footprint:** The frontend consumer at `frontend/src/app/app/credit/CreditClient.tsx` actively invokes 7 Credit server actions (`createCreditCardAction`, `purchaseCreditCardAction`, `payCreditCardAction`, `getCreditStatementsAction`, `getCreditInstallmentsAction`, `payEarlyPurchaseAction`, `previewCreditEarlyPaymentAction`), displaying computed balances, cycle dates, and installment schedules.

---

## 2. Phase A Scope, Methodology & Classification Standards
Phase A execution is strictly non-mutating and read-only. No application code, migrations, database schemas, test suites, or configurations were modified.
Inspected areas:
- Credit Domain: `app/credit/` (`models.py`, `schemas.py`, `router.py`, `service.py`, `repository.py`, `utils.py`, `exceptions.py`).
- Cross-Domain Touchpoints: `app/accounts/`, `app/ledger/`, `app/goals/`, `app/fx/`, `app/intelligence/`, `app/conversations/`, `app/core/`, `app/api/router.py`.
- Alembic Migrations: `alembic/versions/` (specifically baseline `v1_5_baseline.py` and lineage helpers in `scripts/migration/`).
- OpenAPI & Frontend Consumer: `openapi.json` and `frontend/src/app/app/credit/CreditClient.tsx`.
- Test & Smoke Tooling: `tests/unit/test_credit*.py`, `scripts/smoke/smoke_credit*.py`.

---

## 3. Credit Domain Architectural Boundaries & Module Topology
`[CURRENT REALITY]` The credit domain is implemented in `app/credit/`:
- `models.py` (`app/credit/models.py:1-191`): 6 SQLAlchemy ORM mapped entities.
- `schemas.py` (`app/credit/schemas.py:1-225`): 16 Pydantic request/response classes and DTOs.
- `router.py` (`app/credit/router.py:1-305`): FastAPI router mounted under `/api/v1/credit` via `app/api/router.py:120`.
- `service.py` (`app/credit/service.py:1-936`): `CreditCardService` handling card lifecycle, purchases, payments, prepayments, and waterfall allocations.
- `repository.py` (`app/credit/repository.py:1-207`): `CreditCardRepository` handling database queries and row-level locking.
- `utils.py` (`app/credit/utils.py:1-76`): `calculate_credit_card_dates` date cycle calculation algorithm.
- `exceptions.py` (`app/credit/exceptions.py:1-22`): Domain exception classes inheriting from `CreditDomainError`.

**Coupling Boundaries:**
- Inbound Core/Shared: `app.core.database.get_db_session`, `app.core.uow.UnitOfWork`, `app.ledger.service.LedgerService`, `app.accounts.repository.AccountRepository`, `app.core.currency.get_fx_rate`.
- Outbound Consumers: `app.api.router.v1_router` (HTTP routing), `app.intelligence.service.IntelligenceService` (financial snapshot and debt analytics), `app.conversations.service.ConversationalService` (chat-driven actions).

---

## 4. Database Tables & Physical Schema Inventory
`[CURRENT REALITY]` Six tables are defined in PostgreSQL via `alembic/versions/v1_5_baseline.py`:

1. **`public.credit_cards`** (`v1_5_baseline.py:354-426`):
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

2. **`public.credit_card_statements`** (`v1_5_baseline.py:428-514`):
   - `id`: UUID (PK).
   - `user_id`: UUID (NOT NULL).
   - `credit_card_id`: UUID (FK -> `credit_cards.id` ON DELETE CASCADE, NOT NULL).
   - `billing_period`: VARCHAR (NOT NULL, format `'YYYY-MM'`).
   - `billing_period_start`, `cutoff_date`, `due_date`: DATE (NULL).
   - `previous_balance`, `new_purchases`, `billed_installments`, `fees_total`, `interest_total`, `payments_received`, `statement_balance`, `minimum_payment`: NUMERIC(14,2) (DEFAULT 0.00).
   - `status`: VARCHAR (DEFAULT `'open'`).
   - `frozen_at`: TIMESTAMPTZ (NULL).
   - Constraint: `uq_cc_statement_period` UNIQUE (`credit_card_id`, `billing_period`).

3. **`public.credit_card_transactions`** (`v1_5_baseline.py:516-603`):
   - `id`: UUID (PK).
   - `user_id`: UUID (NOT NULL).
   - `credit_card_id`: UUID (FK -> `credit_cards.id` ON DELETE CASCADE, NOT NULL).
   - `type`: VARCHAR (NOT NULL, CHECK `type IN ('purchase', 'payment', 'fee', 'interest')`).
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

4. **`public.credit_card_installments`** (`v1_5_baseline.py:605-701`):
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

5. **`public.credit_card_early_payments`** (`v1_5_baseline.py:703-751`):
   - `id`: UUID (PK).
   - `user_id`: UUID (NOT NULL).
   - `credit_card_id`: UUID (FK -> `credit_cards.id` ON DELETE CASCADE, NOT NULL).
   - `purchase_transaction_id`: UUID (FK -> `credit_card_transactions.id` ON DELETE CASCADE, NOT NULL).
   - `amount`: NUMERIC(14,2) (NOT NULL).
   - `allocation_mode`: VARCHAR (NOT NULL).
   - `event_id`: UUID (NULL).
   - `applied_at`: TIMESTAMPTZ (server_default `now()`).
   - `metadata`: JSONB (DEFAULT `'{}'`).

6. **`public.credit_card_statement_charges`** (`v1_5_baseline.py:753-789`):
   - `id`: UUID (PK).
   - `statement_id`: UUID (FK -> `credit_card_statements.id` ON DELETE CASCADE, NOT NULL).
   - `charge_type`: VARCHAR (NOT NULL).
   - `amount`: NUMERIC(14,2) (NOT NULL).
   - `description`: VARCHAR (NULL).
   - `reference_id`: UUID (NULL).
   - `status`: VARCHAR (NOT NULL, DEFAULT `'pending'`).
   - `paid_amount`: NUMERIC(14,2) (NOT NULL, DEFAULT 0.00).

---

## 5. Alembic Migrations Inventory & Schema Evolution
`[CURRENT REALITY]`
- Baseline: `alembic/versions/v1_5_baseline.py` establishes the consolidated state of the 6 physical tables and legacy views.
- Evolutionary lineage scripts:
  - `scripts/migration/migrate_v11_sprint5.py`: Initial credit semantics v1.1.
  - `scripts/migration/migrate_v15_sprint4.py`: Introduced `credit_card_statements`, statement FKs on transactions, and installment fields (`interest_amount`, `total_amount`, `scheduled_due_date`, `revision_id`).
  - `scripts/migration/migrate_v15_sprint5.py`: Introduced `credit_card_early_payments` and `credit_card_statement_charges`.

---

## 6. Database Views, Functions, & Triggers Inventory
`[CURRENT REALITY]`
1. **`public.v_credit_card_debt`** (`v1_5_baseline.py:1499-1544`):
   - SQL view aggregating `credit_card_transactions`.
   - Computes:
     - `purchases_total = sum(CASE WHEN type = 'purchase' THEN COALESCE(total_with_interest, amount) ELSE 0 END)`.
     - `payments_total = sum(CASE WHEN type = 'payment' THEN amount ELSE 0 END)`.
     - `credit_card_debt = GREATEST(purchases_total - payments_total, 0)`.
     - `monthly_cc_payment = LEAST(monthly_purchases_total, credit_card_debt)`.
   - **Critical Architectural Gap:** Bypasses `credit_card_installments` and completely omits early prepayments (which are stored in `credit_card_early_payments` without transaction rows).
2. **`public.v_financial_snapshot_current_month`** (`v1_5_baseline.py:1597-1640`):
   - SQL view joining `v_credit_card_debt` as `card_totals`.
   - Computes `safe_money` and `free_money` by subtracting `monthly_cc_payment` from available cash.
   - **Architectural Disconnect:** `app/intelligence/service.py` bypasses this SQL view at runtime, substituting its own Python-based calculation over `CreditCardService.get_credit_summary`.

---

## 7. SQLAlchemy ORM Models Inventory
`[CURRENT REALITY]` Defined in `app/credit/models.py`:
- `CreditCard` (`models.py:23-47`): Maps to `credit_cards`.
- `CreditCardTransaction` (`models.py:49-85`): Maps to `credit_card_transactions`.
- `CreditCardInstallment` (`models.py:87-114`): Maps to `credit_card_installments`.
- `CreditCardStatement` (`models.py:116-147`): Maps to `credit_card_statements`.
- `CreditCardEarlyPayment` (`models.py:149-173`): Maps to `credit_card_early_payments`.
- `CreditCardStatementCharge` (`models.py:175-191`): Maps to `credit_card_statement_charges`.

---

## 8. Pydantic Schemas & DTO Inventory
`[CURRENT REALITY]` Exactly 16 classes defined in `app/credit/schemas.py`:
1. `CreditCardBase` (`schemas.py:8-20`): Core card attributes (name, bank, limit, cutoff, due_day, rates, currency).
2. `CreditCardCreate` (`schemas.py:22-23`): Card creation input.
3. `CreditCardUpdate` (`schemas.py:26-38`): Card update payload.
4. `CreditCardRead` (`schemas.py:40-63`): Full card read model with computed debt fields and backward-compatible aliases.
5. `CreditCardPurchaseCreate` (`schemas.py:65-72`): Purchase payload.
6. `CreditCardPurchaseResult` (`schemas.py:74-83`): Purchase confirmation result.
7. `CreditCardPaymentCreate` (`schemas.py:85-90`): Bill payment payload.
8. `CreditCardPaymentResult` (`schemas.py:92-101`): Payment confirmation result.
9. `CreditCardEarlyPaymentCreate` (`schemas.py:103-109`): Prepayment payload.
10. `CreditCardEarlyPaymentResult` (`schemas.py:111-119`): Prepayment result.
11. `CreditCardEarlyPaymentPreviewCreate` (`schemas.py:120-122`): Prepayment quote request.
12. `PaymentPreviewResult` (`schemas.py:124-132`): Multi-currency quote response.
13. `CreditCardStatusRead` (`schemas.py:134-160`): Single card cycle status and breakdown.
14. `CreditSummaryRead` (`schemas.py:162-170`): Aggregated multi-card portfolio summary.
15. `CreditCardInstallmentRead` (`schemas.py:172-205`): Single installment read model with computed properties (`remaining_principal`, `is_pay_early_eligible`).
16. `CreditCardStatementRead` (`schemas.py:207-225`): Billing statement read model.

---

## 9. API Endpoints Inventory & Lifecycle Classification
`[CURRENT REALITY]` Defined in `app/credit/router.py`:

| HTTP Method & Path | Operation Handler | Lifecycle Classification | Consumer & Evidence Justification |
| :--- | :--- | :--- | :--- |
| `POST /api/v1/credit/cards` | `create_card` (`router.py:41-52`) | **CANONICAL** | Consumed by frontend `createCreditCardAction`. |
| `GET /api/v1/credit/cards` | `list_cards` (`router.py:54-61`) | **CANONICAL** | Primary initial data query in `CreditClient.tsx`. |
| `GET /api/v1/credit/cards/{card_id}` | `get_card` (`router.py:63-75`) | **CANONICAL** | Single card detail retrieval. |
| `PATCH /api/v1/credit/cards/{card_id}` | `update_card` (`router.py:77-92`) | **CANONICAL** | Card parameter adjustment. |
| `DELETE /api/v1/credit/cards/{card_id}` | `delete_card` (`router.py:94-108`) | **CANONICAL** | Soft deletion / deactivation. |
| `POST /api/v1/credit/cards/{card_id}/purchases` | `create_purchase` (`router.py:110-136`) | **CANONICAL** | Consumed by `purchaseCreditCardAction`. |
| `POST /api/v1/credit/cards/{card_id}/payments` | `create_payment` (`router.py:137-168`) | **CANONICAL** | Consumed by `payCreditCardAction`. |
| `POST /api/v1/credit/cards/{card_id}/purchases/{purchase_id}/pay_early` | `create_early_payment` (`router.py:170-209`) | **CANONICAL** | Consumed by `payEarlyPurchaseAction`. |
| `POST /api/v1/credit/cards/{card_id}/purchases/{purchase_id}/pay_early/preview` | `preview_early_payment` (`router.py:210-237`) | **CANONICAL** | Consumed by `previewCreditEarlyPaymentAction`. |
| `GET /api/v1/credit/summary` | `get_credit_summary` (`router.py:239-246`) | **CANONICAL** | Consumed by Intelligence domain. |
| `GET /api/v1/credit/cards/{card_id}/status` | `get_card_status` (`router.py:248-260`) | **CANONICAL** | Card cycle & debt status. |
| `GET /api/v1/credit/cards/{card_id}/installments` | `list_card_installments` (`router.py:262-274`) | **CANONICAL** | Consumed by `getCreditInstallmentsAction`. |
| `GET /api/v1/credit/cards/{card_id}/statements` | `list_statements` (`router.py:276-288`) | **AMBIGUOUS / DORMANT** | Consumed by `getCreditStatementsAction`, but table is unpopulated. |
| `GET /api/v1/credit/cards/{card_id}/statements/{period}` | `get_statement` (`router.py:290-305`) | **AMBIGUOUS / DORMANT** | Exposed in API, table unpopulated. |
| `GET /api/v1/intelligence/credit-cards` | `get_credit_cards` (`app/intelligence/router.py`) | **LEGACY-BUT-REQUIRED** | Intelligence proxy endpoint returning `IntelligenceCreditCardRead`. |

---

## 10. Credit Service Business Logic Inventory
`[CURRENT REALITY]` Implemented in `CreditCardService` (`app/credit/service.py:44-936`):
- `_data_quality` (`service.py:51-57`): Exposes data quality dictionary containing `"current_debt": "computed_from_credit_card_transactions"`.
- `_installment_amounts` (`service.py:66-71`): Quantizes base installment and explicitly assigns rounding residual to the LAST installment (`amounts[-1] = quantized - sum(amounts[:-1], Decimal("0.00"))`).
- `_build_installments` (`service.py:76-117`): Creates `CreditCardInstallment` rows with computed interest and scheduled dates.
- `_apply_payment_to_waterfall` (`service.py:119-185`): Enforces sequential payment waterfall: (1) Past due, (2) Current period interest, (3) Statement charges, (4) Billed installment principal, (5) Billed revolving principal, (6) Unbilled revolving principal. Throws `InvalidPaymentAmountError` on excess overpayment.
- `_enrich_card` (`service.py:186-209`): Omits persisted `card.current_debt`, delegates to `_calculate_card_status`, and constructs `CreditCardRead`.
- `create_purchase` (`service.py:261-348`): Enforces credit limit against `get_card_debt`, records neutral `credit_card_purchase` Ledger event, persists transaction and installments.
- `create_payment` (`service.py:350-494`): Validates account funds, applies FX conversion if currencies differ, enforces overpayment check, emits outflow `credit_card_payment` Ledger event, decrements `account.balance`, creates payment transaction, and runs waterfall allocation.
- `create_early_payment` (`service.py:496-704`): Enforces multi-installment and future period rules, computes FX, emits outflow `credit_card_payment` Ledger event, decrements `account.balance`, inserts `CreditCardEarlyPayment`, and re-allocates remaining principal across unbilled installments.
- `preview_early_payment` (`service.py:705-804`): Non-mutating FX quote preview.
- `_calculate_card_status` (`service.py:806-875`): Calls `repo.get_card_status_data` to split debt into `billed_debt` and `unbilled_debt`, derives `available_credit`, sets `statement_balance = billed_debt`, and calculates `minimum_payment`.
- `get_credit_summary` (`service.py:886-907`): Aggregates limits and debts across active cards.

---

## 11. Credit Repository & Query Inventory
`[CURRENT REALITY]` Implemented in `CreditCardRepository` (`app/credit/repository.py:11-207`):
- `get_card_debt` (`repository.py:35-48`):
  - **Misleading Docstring as Evidence:** Docstring at line 36 states `"""Returns (credit_card_debt, monthly_cc_payment) from the view."""`.
  - **Actual Code Execution:** Directly executes SQL against `credit_card_installments`:
    ```sql
    SELECT
        COALESCE(SUM(principal_amount + interest_amount - paid_amount), 0) as credit_card_debt,
        COALESCE(SUM(CASE WHEN scheduled_period = to_char((now() AT TIME ZONE 'America/Bogota'), 'YYYY-MM') THEN principal_amount + interest_amount - paid_amount ELSE 0 END), 0) as monthly_cc_payment
    FROM credit_card_installments
    WHERE credit_card_id = :card_id AND status != 'paid'
    ```
- `get_card_status_data` (`repository.py:125-160`): Aggregates billed and unbilled purchases against `cycle_end_date` using `credit_card_installments`. Explicitly sets `0 as total_payments` because payments are already netted inside `inst.paid_amount`.
- `get_next_payment_estimate` (`repository.py:162-171`): Sums unpaid principal for the current Bogota calendar month.
- `list_statements`, `get_statement` (`repository.py:173-193`): Selects from `CreditCardStatement`.
- `list_unpaid_statement_charges_for_update` (`repository.py:194-207`): Selects from `CreditCardStatementCharge`.

---

## 12. Ledger Domain Integration & Event Sourcing
`[CURRENT REALITY]`
- Purchase Flow: Emits `financial_events` with `event_type="credit_card_purchase"`, `direction="neutral"`, and card UUID in `metadata`. Neutral direction prevents cash accounts from being reduced upon purchase.
- Payment Flow: Emits `financial_events` with `event_type="credit_card_payment"`, `direction="outflow"`, linked to `account_id`, with complete FX metadata.
- Early Prepayment Flow: Emits `financial_events` with `event_type="credit_card_payment"`, `direction="outflow"`.
- Ledger idempotency is guaranteed via `command_id` normalization (`service.py:73-74`).

---

## 13. Accounts Domain Coupling & Card vs Account Representation
`[CURRENT REALITY]`
- **Domain Separation:** In `app/accounts/models.py:32`, `Account` check constraint restricts types to `('bank', 'wallet', 'cash', 'savings')`. Credit cards are not accounts and possess independent persistence.
- **Direct Balance Mutation:** In `CreditCardService.create_payment` (`service.py:459`) and `create_early_payment` (`service.py:647`), the service directly modifies `account.balance -= payload.amount` after acquiring a row lock via `AccountRepository.get_by_id_for_update`.

---

## 14. Obligations Domain Interaction & Payment Overlaps
`[CURRENT REALITY]`
- Obligations (`app/obligations/`) and Credit Cards (`app/credit/`) are distinct debt engines.
- In `IntelligenceService` snapshot aggregation (`app/intelligence/service.py:165`), `committed_outflows` sums pending obligations and credit card `billed_debt`.

---

## 15. Goals Domain Coupling & Snapshot Resource Allocation
`[CURRENT REALITY]`
- No direct foreign keys link goals to credit cards.
- Indirect interaction occurs in Financial Snapshot calculation: `free_money` subtracts `minus_credit_cards_required_payment` (`billed_debt`) alongside obligations and goals commitments.

---

## 16. FX & Multi-Currency Architecture
`[CURRENT REALITY]`
- Card currency is locked to `credit_cards.currency` (default `'COP'`).
- Purchases do not support cross-currency conversion; amount is accepted directly in `card.currency`.
- Bill payments across different currencies call `app.core.currency.get_fx_rate(source_currency, target_currency)`.
- Metadata captured: `fx_rate`, `rate_source`, `rate_timestamp`, `is_estimated`, `applied_amount`, `applied_currency`.

---

## 17. Intelligence Domain Consumption & Analytics
`[CURRENT REALITY]`
- `app/intelligence/service.py:131` directly executes `CreditCardService(self.session).get_credit_summary(user_id)`.
- Exposes:
  - `SnapshotDebt`: `credit_card_total_debt = billed_debt + unbilled_debt`.
  - Snapshot Free Money: `minus_credit_cards_required_payment = billed_debt`.
  - `IntelligenceDebtRead`: `total_estimated_credit_card_debt`, `credit_cards`.

---

## 18. Conversational Layer Integration & Intent Mapping
`[CURRENT REALITY]` Defined in `app/conversations/service.py`:
- Intent `create_credit_card_purchase` (`service.py:883-892`): Dispatches to `credit_service.create_purchase`.
- Intent `create_credit_card_payment` (`service.py:919-928`): Dispatches to `credit_service.create_payment`.

---

## 19. Frontend Consumer Observations (CreditClient.tsx)
`[VERIFIED]` Inspected at `frontend/src/app/app/credit/CreditClient.tsx`:
- Actively consumes **7 Credit server actions**:
  1. `createCreditCardAction` (`CreditClient.tsx:5`)
  2. `purchaseCreditCardAction` (`CreditClient.tsx:5`)
  3. `payCreditCardAction` (`CreditClient.tsx:5`)
  4. `getCreditStatementsAction` (`CreditClient.tsx:42`)
  5. `getCreditInstallmentsAction` (`CreditClient.tsx:198`)
  6. `payEarlyPurchaseAction` (`CreditClient.tsx:5`)
  7. `previewCreditEarlyPaymentAction` (`CreditClient.tsx:5`)
- Displayed attributes: `card.name`, `card.bank`, `card.currency`, `card.credit_limit`, `card.available_credit`, `card.total_debt`, `card.billed_debt`, `card.unbilled_debt`, `card.payment_required`, `card.next_payment_estimate`, `card.cutoff_day`, `card.due_day`, `card.statement_balance`.
- Graceful handling: Shows empty state message *"Aún no hay extractos generados para esta tarjeta"* when `statements.length === 0`.

---

## 20. Source-of-Truth Candidate Matrix & Duplications (The 11 Financial Primitives)
`[CURRENT REALITY]`

| Financial Primitive | Primary Implementation Source | Secondary / Candidate Sources | Phase A Classification & Contradiction Evidence |
| :--- | :--- | :--- | :--- |
| **1. Credit Limit** | `credit_cards.credit_limit` (`models.py:32`) | `CreditCardRead.credit_limit`, `CreditSummaryRead.total_credit_limit` | **Provisional Phase A Finding (Pending Physical Verification).** Single schema source, but bank provenance/runtime mutations unverified. |
| **2. Current Debt** | Dynamic sum in `CreditCardRepository.get_card_debt` (`installments`) | `credit_cards.current_debt` (stale col), `v_credit_card_debt` (view), `CreditCardStatusRead.current_debt` | **Severe Contradiction & Misleading Metadata.** DB column is stale; view uses transactions; repository uses installments; `_data_quality()` misleadingly claims `"computed_from_credit_card_transactions"`. |
| **3. Used Credit** | Derived as `current_debt` / `total_debt` | `CreditCardRead.total_debt` | **Provisional Phase A Finding.** Synonymous with calculated debt in API. |
| **4. Available Credit** | `max(0, limit - total_debt)` in `_calculate_card_status` (`service.py:835`) | `CreditCardRead.estimated_available_credit` (Pydantic property), `CreditSummaryRead.total_available_credit` | **Provisional Phase A Finding.** Deterministic derivation; physical boundary unverified under live limits. |
| **5. Statement Balance** | In `/status`: set to `billed_debt` (`service.py:837`) | `credit_card_statements.statement_balance` (empty DB col), `CreditCardRead.statement_balance` (`None`) | **Contradiction.** Returns `None` on `/cards` list (`service.py:204`), but returns `billed_debt` on `/cards/{id}/status`. |
| **6. Amount Due** | `payment_required` in `_calculate_card_status` (= `statement_balance`) | `CreditCardRead.payment_required` | **Provisional Phase A Finding.** Reflects billed installments when statements are dormant. |
| **7. Minimum Payment** | Formula in `_calculate_card_status` (5% or COP 50k / USD 15) | `credit_card_statements.minimum_payment` (DB col), `CreditCardRead.minimum_payment` (returns 0.00) | **Contradiction.** `/cards` list defaults to `0.00`, whereas `/status` calculates dynamic 5% rule. |
| **8. Installment Principal** | `credit_card_installments.principal_amount` (`models.py:102`) | `CreditCardTransaction.amount / installments_total` | **Provisional Phase A Finding.** Stored across individual installment rows; remains immutable during early payments. |
| **9. Interest** | `credit_card_installments.interest_amount` (`models.py:106`) | `credit_cards.monthly_interest_rate`, `credit_card_statements.interest_total` | **Documented Limitation.** Simple percentage placeholder; no compounding or amortization engine. |
| **10. Payment Allocation** | `_apply_payment_to_waterfall` in Python (`service.py:119`) | SQL queries in `CreditCardRepository` | **Logic in Application Layer.** Sequential FIFO allocation against installments and charges. |
| **11. FX Booked Amount** | Serialized in `financial_events.metadata` & `credit_card_transactions.metadata` | `PaymentPreviewResult` (transient DTO) | **Provisional Phase A Finding.** Captured in immutable event metadata; no dedicated table columns. |

---

## 21. Credit Card Cycle Semantics & Billing Mechanics
`[CURRENT REALITY]` Implemented in `app/credit/utils.py:calculate_credit_card_dates`:
- Derives `cycle_start`, `cycle_end`, and `next_due` based on `cutoff_day`, `due_day`, and reference date.
- Employs `calendar.monthrange` for month-end clamping (e.g., February 28/29, 30-day months).
- Segregates transactions/installments:
  - Billed: Scheduled period <= cutoff cycle period.
  - Unbilled: Scheduled period > cutoff cycle period.

---

## 22. Installment Schedules & Amortization Mechanics
`[CURRENT REALITY]` Implemented in `CreditCardService._installment_amounts` (`service.py:66-71`) and `_build_installments` (`service.py:76-117`):
- `_installment_amounts` quantizes the base amount and explicitly assigns the rounding residual to the **LAST installment**:
  ```python
  quantized = amount.quantize(Decimal("0.01"))
  base = (quantized / Decimal(total)).quantize(Decimal("0.01"))
  amounts = [base for _ in range(total)]
  amounts[-1] = quantized - sum(amounts[:-1], Decimal("0.00"))
  return amounts
  ```
- Interest formula: `interest = principal * (monthly_interest_rate / 100)`.
- Successive periods computed via `_add_months(today, index - 1)` with cycle due date alignment.

---

## 23. Payment Waterfall & Allocation Behavior
`[CURRENT REALITY]` Sequential hierarchy in `_apply_payment_to_waterfall` (`service.py:119-185`):
1. Past due installments (`scheduled_period < current_period`).
2. Current period accrued interest (`scheduled_period == current_period`).
3. Current period statement charges (`credit_card_statement_charges`).
4. Current period billed installments (`installments_total > 1`).
5. Current period billed revolving purchases (`installments_total == 1`).
6. Future unbilled revolving purchases (`scheduled_period > current_period`, `installments_total == 1`).
- Raises `InvalidPaymentAmountError` if residual payment remains without unbilled revolving debt.

---

## 24. Test Suite Map & Coverage Analysis
`[CURRENT REALITY]` Five unit test modules in `tests/unit/`:
1. `test_credit.py` (311 lines): Card CRUD, purchase creation, payment overpayment guards, `test_card_status_ignores_persisted_current_debt`.
2. `test_credit_semantics.py` (116 lines): Billed vs unbilled debt separation, cycle date calculations.
3. `test_credit_statements.py` (91 lines):
   - **Overclaimed Coverage as Evidence:** `test_lazy_statement_generation` (`test_credit_statements.py:47-91`) docstring claims to test *"lazy statement generation after cutoff and proper installment schedule"*. In reality, it only creates a purchase and asserts its 3 installments. It never creates, triggers, fetches, or asserts any `CreditCardStatement`. Actual statement generation remains completely untested.
4. `test_credit_advanced.py` (88 lines): Waterfall allocation test. **Warning:** Contains 3 empty dummy tests (`test_early_payment_success`, `test_early_payment_insufficient_funds`, `test_early_payment_exceeds_principal` contain only `pass`).
5. `test_credit_summary_qa.py` (52 lines): Card status calculation and early payment regression tests.

---

## 25. Operational Scripts, Smokes & Tooling Map
`[CURRENT REALITY]`
1. `scripts/smoke/smoke_credit_core.py`: End-to-end Python script creating cards, purchases, and payments using direct DB sessions.
2. `scripts/smoke/smoke_credit_semantics_v11.py`: HTTP-based smoke test validating cycle semantics against local API.
3. `scripts/smoke/smoke_credit.py`: Basic HTTP smoke script for health, accounts, and card purchases.
4. `scripts/migration/migrate_v15_sprint4.py` & `migrate_v15_sprint5.py`: Historical migration runners.

---

## 26. Architectural Contradictions & Historical Gaps
`[CURRENT REALITY]` Key architectural contradictions and evidence gaps:
1. **Debt Calculation Triality & Misleading Metadata:**
   - Database column `credit_cards.current_debt` (stale/dead).
   - SQL view `v_credit_card_debt` (aggregates `credit_card_transactions`, misses early payments).
   - Domain repository query `get_card_debt` (aggregates `credit_card_installments`).
   - Repository docstring misleadingly claims `get_card_debt` queries *"from the view"*.
   - Service metadata `_data_quality()` misleadingly claims `"current_debt": "computed_from_credit_card_transactions"`.
2. **Precise Early Payment Divergence:**
   - Early prepayment creates a `credit_card_payment` Ledger event (`financial_events`) and a `credit_card_early_payments` record, but creates **no row in `credit_card_transactions`**.
   - An audit trail exists in the Ledger and `credit_card_early_payments`, but any query, view (`v_credit_card_debt`), or report relying strictly on `credit_card_transactions` diverges from reality.
3. **Dormant Statements Subsystem:**
   - Full schema, models, repository methods, and API routes exist for statements, but no statements generator runs.
4. **`statement_balance` Schema Mismatch:**
   - `/cards` list returns `null`.
   - `/cards/{card_id}/status` returns `billed_debt`.

---

## 27. Unknowns Requiring Phase B Reconstruction & Evidence Pending
`[PENDING VERIFICATION]`
- Exact runtime consistency of `v_financial_snapshot_current_month` across historical customer databases.
- Behavior of multi-currency cards under fluctuating live FX rates across multiple billing cycles.
- Whether production databases currently contain orphaned records in `credit_cards.current_debt`.
- Requirements for formal statement generation (monthly scheduled cron vs lazy on-demand closure).

---

## 28. Risk Register & Codex Verification Handoff
`[CURRENT REALITY]`
1. **Financial Drift Risk (HIGH):** External tools or reporting scripts querying `credit_cards.current_debt` or `v_credit_card_debt` receive inaccurate financial figures.
2. **Early Payment Reconciliation Divergence (HIGH):** Any query or external audit relying on `credit_card_transactions` misses early prepayments entirely.
3. **Untested Subsystems & Dummy Tests (MEDIUM):** `test_lazy_statement_generation` does not test statements, and `test_credit_advanced.py` contains 3 empty `pass` stub tests for early payments.

**Handoff to Codex:**
Phase A inventory draft with verified corrections is complete. Ready for final review, checkpoint commit/push, and Phase B architectural reconstruction.
