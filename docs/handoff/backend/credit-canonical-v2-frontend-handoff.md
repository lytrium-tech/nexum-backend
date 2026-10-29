# Backend -> Frontend Handoff: Credit Domain (Canonical Closure)

> [!IMPORTANT]
> **Operational Status & Scope Notice:**
> - **Frontend Status:** The frontend codebase remains **completely unchanged**; frontend QA, client component migration, and UI updates have **not yet begun**.
> - **Backend Status:** The backend Credit Domain canonical closure implementation is fully completed, schema-migrated, and unit/service verified. Physical PostgreSQL 17 contention gate certification on CT101 is scheduled for Phase F.
> - **API Path:** The canonical path is strictly `/api/v1/credit`. There is **no `/v2` API version prefix**; canonical closure is an internal architectural closure under the established `/api/v1/credit` route tree.
> - **Scope:** Revolving credit cards only. Fixed-schedule loans and obligations are governed strictly by `/api/v1.7/obligations`.
> - **Alpha Environment Reality:** In Alpha, external bank integrations do not exist. All credit cards in the database are user-created (`provenance: "manual"` or `"manual_input"`). All credit limits, transaction records, and payments originate from manual input; statement generation, cycle cutoffs, and waterfall allocations are Nexum-modeled projections (`provenance: "nexum_modeled"`). Bank-authoritative facts (`provenance: "issuer_authoritative"`) are architected into the data model and schema contracts but are not claimed as existing in Alpha.

---

## 1. Provenance & Fact Hierarchy

Credit Domain metrics and balances explicitly distinguish between bank facts, manual inputs, and modeled projections:
- `issuer_authoritative`: Connected bank statement or transaction fact. Not present in Alpha; architected for future bank sync.
- `manual_input` / `manual`: User-supplied data (e.g., initial card parameters, recorded purchases, manual payments).
- `nexum_modeled`: Nexum internal schedule, closure, or waterfall derivation (e.g., cycle dates, available credit estimate, installment schedules, early payment previews).
- `legacy_unknown`: Historical records predating canonical activation.

### Debt & Available Credit Provenance Language
- `total_debt`: In Alpha, `total_debt` is a **canonical derivation** (the deterministic sum of posted billed, unbilled, and legacy principal minus unbilled adjustment allocations in card currency). It is **not** an issuer statement fact.
- `available_credit`: For all manual cards in Alpha, this is a **Nexum-modeled estimate** (`credit_limit - total_debt`). It can be negative if total debt exceeds the approved limit and is never artificially clamped with `max(0, ...)`.
- `minimum_payment`: Defaults to **`null` (`None`)** because Nexum does not invent arbitrary bank formulas. It is only non-null when supplied by an explicit statement fact.

---

## 2. Exact API Endpoint & Schema Contracts (`/api/v1/credit`)

All credit routes are prefixed under `/api/v1/credit`.

---

### 2.1 Cards Management

#### `GET /api/v1/credit/cards`
Returns `list[CreditCardRead]`.

#### `POST /api/v1/credit/cards`
- **Status:** `201 Created`
- **Request Body (`CreditCardCreate`):**
```json
{
  "name": "Bancolombia Visa",
  "bank": "Bancolombia",
  "credit_limit": 5000000.00,
  "cutoff_day": 15,
  "due_day": 5,
  "currency": "COP",
  "management_fee": 25000.00,
  "monthly_interest_rate": 0.021,
  "annual_interest_rate": 0.285,
  "network": "Visa",
  "franchise": "Oro"
}
```
*Note on Field Names:* Property is `bank` (not `bank_name`), `currency` (not `card_currency`). There is no `last_four_digits` field.

#### `GET /api/v1/credit/cards/{card_id}`
Returns `CreditCardRead`.

#### `PATCH /api/v1/credit/cards/{card_id}`
- **Path Parameter:** `card_id` (`UUID`)
- **Request Body (`CreditCardUpdate`):** All fields optional (`name`, `bank`, `credit_limit`, `cutoff_day`, `due_day`, `management_fee`, `monthly_interest_rate`, `annual_interest_rate`, `network`, `franchise`, `is_active`).
- Returns `CreditCardRead`.

#### `DELETE /api/v1/credit/cards/{card_id}`
- **Status:** `204 No Content`

#### Entity Schema: `CreditCardRead`
| Field | Type | Nullable | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | No | Card unique identifier. |
| `name` | `string` | No | Card display name. |
| `bank` | `string` | No | Issuing bank name. |
| `credit_limit` | `Decimal` | No | Approved credit limit in card currency. |
| `cutoff_day` | `int` | No | Cutoff day of the month (1-31). |
| `due_day` | `int` | No | Payment due day of the month (1-31). |
| `currency` | `string` | No | 3-letter ISO card currency (e.g. `COP`). |
| `is_active` | `bool` | No | Whether the card is active. |
| `total_debt` | `Decimal` | No | Canonical derivation of total debt in card currency. |
| `available_credit` | `Decimal` | No | Nexum-modeled estimated available credit (`credit_limit - total_debt`). |
| `billed_debt` | `Decimal` | No | Current amount due from closed statements. |
| `unbilled_debt` | `Decimal` | No | Unbilled debt from open cycle and future installments. |
| `payment_required` | `Decimal` | No | Required payment for current cycle (equals `billed_debt`). |
| `minimum_payment` | `Decimal` | **Yes (`null`)** | Null unless supplied by statement fact. |
| `next_payment_estimate` | `Decimal` | No | Nexum-modeled estimate of upcoming payment. |
| `statement_balance` | `Decimal` | **Yes (`null`)** | Balance snapshot of last closed statement (`null` if unclosed). |
| `canonical_mode` | `bool` | No | True if card operates under canonical closure. |
| `canonical_activated_at`| `DateTime` | Yes | Instant when card activated canonical mode. |
| `provenance` | `string` | Yes | Fact provenance (e.g. `manual_input`). |
| `lifecycle_status` | `string` | Yes | Card lifecycle status. |
| `management_fee` | `Decimal` | No | Monthly management fee. |
| `monthly_interest_rate` | `Decimal` | No | Monthly interest rate. |
| `annual_interest_rate` | `Decimal` | No | Annual interest rate. |
| `network` | `string` | Yes | Card network (e.g. Visa). |
| `franchise` | `string` | Yes | Card franchise (e.g. Clásica, Oro). |
| `data_quality` | `dict` | No | Metadata dictionary tracking provenance and estimation. |
| *Deprecated Aliases* | | | `current_debt`, `estimated_current_debt`, `monthly_cc_payment`, `estimated_available_credit` (marked `"deprecated": true`). |

---

### 2.2 Status & Aggregation

#### `GET /api/v1/credit/cards/{card_id}/status`
Returns `CreditCardStatusRead`:
- `card_id` (`UUID`)
- `payment_due_day` (`int`)
- `next_payment_due_date` (`string | null`, ISO `YYYY-MM-DD`)
- `total_debt`, `available_credit`, `billed_debt`, `unbilled_debt`, `payment_required`, `minimum_payment`, `next_payment_estimate`, `statement_balance`, `canonical_mode`, `canonical_activated_at`, `provenance`, `data_quality`.
- *Deprecated Aliases:* `current_debt`, `estimated_current_debt`, `monthly_cc_payment`, `estimated_available_credit`.

#### `GET /api/v1/credit/summary`
Returns `CreditSummaryRead`:
- `total_credit_limit` (`Decimal`): Aggregated credit limit across all cards.
- `total_debt` (`Decimal`): Aggregated total posted debt across all cards in card currency.
- `total_available_credit` (`Decimal`): Aggregated available credit across all cards.
- `total_monthly_cc_payment` (`Decimal`): Aggregated monthly payment obligations.
- `total_payment_required` (`Decimal`): Aggregated required payments due in current cycle.
- `total_next_payment_estimate` (`Decimal`): Aggregated next payment estimate.
- `cards`: `list[CreditCardStatusRead]`

---

### 2.3 Purchases

#### `POST /api/v1/credit/cards/{card_id}/purchases`
- **Path Parameter:** `card_id` (`UUID`)
- **Required Header:** `Idempotency-Key` (UUID format)
- **Request Body (`CreditCardPurchaseCreate`):**
```json
{
  "original_amount": 120.00,
  "original_currency": "USD",
  "currency": "COP",
  "installments_total": 6,
  "description": "Monitor Dell",
  "category_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "lifecycle_status": "posted",
  "occurred_at": "2026-10-29T10:00:00Z"
}
```
*Field Clarifications & Multi-Currency Semantics:*
- No `credit_card_id` in the body (it is in the route path `/cards/{card_id}/purchases`).
- Property is `description` (not `notes` or `merchant`).
- Property is `installments_total` (not `installments`).
- Cross-currency purchase inputs:
  - `original_amount` & `original_currency`: Merchant transaction amount and currency (e.g. 120.00 USD).
  - `currency`: Optional card currency assertion (e.g. "COP"). It asserts the client's expected card booked currency and must match the card's currency. It does **not** allow the client to dictate the converted booked amount or exchange rate.
  - Converted booked amount: The client never supplies `booked_amount` (not an input field) or a conflicting `amount`. The backend authoritative `FXService` derives `booked_amount` and exchange rate snapshot during execution.
- Single-currency legacy compatibility: For purchases in the same currency as the card, providing `amount` books directly in card currency (`amount` is marked deprecated).
- Rejects with `credit_limit_exceeded` (409) if purchase exceeds available credit line.
- Purchases transacted after the card's `cutoff_day` deterministically schedule their first installment into the *subsequent* billing cycle.
- Single-installment purchases (`installments_total = 1`) incur **$0.00 interest**. Flat interest formula has been eliminated.

- **Response Body (`CreditCardPurchaseResult`):**
```json
{
  "status": "success",
  "event_id": "11111111-2222-3333-4444-555555555555",
  "transaction_id": "66666666-7777-8888-9999-000000000000",
  "amount": 480000.00,
  "current_debt": 480000.00,
  "available_credit": 4520000.00,
  "cycle_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  "billing_period": "2026-10",
  "original_amount": 120.00,
  "original_currency": "USD",
  "booked_amount": 480000.00,
  "booked_currency": "COP",
  "fx_rate": 4000.00,
  "fx_rate_snapshot_id": "ffffffff-eeee-dddd-cccc-bbbbbbbbbbbb",
  "lifecycle_status": "posted",
  "debt_partition": "canonical",
  "provenance": "manual_input"
}
```

---

### 2.4 Payments & Prepayments

#### `POST /api/v1/credit/cards/{card_id}/payments`
- **Path Parameter:** `card_id` (`UUID`)
- **Required Header:** `Idempotency-Key` (UUID format)
- **Request Body (`CreditCardPaymentCreate`):**
```json
{
  "account_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "amount": 250000.00
}
```
*Field Clarifications:*
- Takes strictly `account_id` and `amount` in funding account currency; the target card is identified by the `card_id` path parameter.
- Rejects with `credit_goal_reservation_breach` (409) if `amount > account.balance - calculate_reserved_by_account(...)`.
- Rejects with `insufficient_funds` (409) if account nominal balance is lower than `amount`.
- Rejects with `credit_payment_amount_invalid` (400) if payment exceeds total outstanding payable debt (voluntary overpayment beyond total debt is blocked).
- Waterfall applies in deterministic order: (1) past-due statement charges, (2) past-due installment principal, (3) current period statement charges, (4) current period installment principal, (5) future multi-installment principal.

- **Response Body (`CreditCardPaymentResult`):**
```json
{
  "status": "success",
  "event_id": "11111111-2222-3333-4444-555555555555",
  "transaction_id": "66666666-7777-8888-9999-000000000000",
  "amount": 250000.00,
  "current_debt": 230000.00,
  "available_credit": 4770000.00,
  "account_balance": 1750000.00
}
```
*Crucial Contract Note:* `CreditCardPaymentResult` **does NOT return an allocations array**. Allocations are persisted internally in the database table `credit_card_payment_allocations`.

---

#### `POST /api/v1/credit/cards/{card_id}/purchases/{purchase_id}/pay_early`
Targeted prepayment of installments belonging to a specific purchase.
- **Path Parameters:** `card_id` (`UUID`), `purchase_id` (`UUID`)
- **Required Header:** `Idempotency-Key` (UUID format)
- **Request Body (`CreditCardEarlyPaymentCreate`):**
```json
{
  "account_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "amount": 100000.00,
  "allocation_mode": "reduce_installment_amount"
}
```
- **Response Body (`CreditCardEarlyPaymentResult`):**
  `status`, `event_id`, `early_payment_id`, `amount`, `current_debt`, `available_credit`, `account_balance`.
- Inserts an immutable row into `credit_card_early_payments` AND writes a transaction row in `credit_card_transactions`, ensuring complete auditability.

---

#### `POST /api/v1/credit/cards/{card_id}/purchases/{purchase_id}/pay_early/preview`
- **Path Parameters:** `card_id` (`UUID`), `purchase_id` (`UUID`)
- **Request Body (`CreditCardEarlyPaymentPreviewCreate`):** `{"account_id": "...", "amount": 100000.00}`
- **Execution Rules:** Pure read-only operation. Executes without `UnitOfWork` transactions, without `SELECT FOR UPDATE` locks, and without database mutation.
- **Response Body (`PaymentPreviewResult`):**
  `source_amount`, `source_currency`, `target_amount`, `target_currency`, `fx_rate`, `rate_source`, `is_estimated`.

---

### 2.5 Adjustments, Refunds & Reversals

#### `POST /api/v1/credit/cards/{card_id}/adjustments`
- **Path Parameter:** `card_id` (`UUID`)
- **Required Header:** `Idempotency-Key` (UUID format)
- **Request Body (`CreditCardAdjustmentCreate`):**
```json
{
  "original_transaction_id": "66666666-7777-8888-9999-000000000000",
  "amount": 50000.00,
  "adjustment_type": "refund",
  "direction": "credit",
  "provenance": "manual",
  "allocation_source": "nexum_modeled",
  "reason": "Devolución de artículo en tienda"
}
```
*Exact Semantics & Validation Rules:*
1. **Targeting a Purchase Transaction (`type == "purchase"`):**
   - Supported adjustment types: strictly `"refund"`, `"chargeback"`, or `"reversal"`. (Purchase targets do **NOT** support `"statement_credit"`).
   - Direction: strictly `"credit"`.
   - **Financial Invariant:** Refunds and adjustments are **immutable compensating records** with junction rows in `credit_card_adjustment_allocations`. They do **NOT** rewrite, delete, or reduce the original installment's persisted `principal_amount`.
2. **Targeting a Payment Transaction (`type == "payment"`):**
   - Adjustment type: strictly `"reversal"`.
   - Direction: strictly `"debit"`.
   - Reversal amount: must match the **exact full booked amount** of the canonical payment (`InvalidPaymentAmountError` 400 on partial reversal).
   - **Financial Invariant:** Reversing a payment restores the card debt and **restores (credits/inflows) the source funding account** by adding `reversal_source_amount` back to `account.balance` (`account.balance += reversal_source_amount`). It does not debit the account.
3. **Provenance Restrictions:** The manual adjustment endpoint strictly rejects `provenance = "issuer_authoritative"` or `allocation_source = "issuer_authoritative"` (`CreditDomainError` 400).
4. **Response Body (`CreditCardAdjustmentResult`):**
   `status`, `adjustment_id`, `event_id`, `amount`, `current_debt`, `available_credit`, `adjustment_type`, `direction`, `provenance`, `allocation_source`.

---

### 2.6 Billing Cycles & Statements

#### `GET /api/v1/credit/cards/{card_id}/statements`
Returns `list[CreditCardStatementRead]`.

#### `GET /api/v1/credit/cards/{card_id}/statements/{period}`
- **Path Parameters:** `card_id` (`UUID`), `period` (`string`, e.g. `"2026-10"`)
- Returns `CreditCardStatementRead` or raises 404 (`not_found`) if statement does not exist.

#### Schema: `CreditCardStatementRead`
- `id` (`UUID`): Statement identifier.
- `user_id` (`UUID | null`): User identifier.
- `credit_card_id` (`UUID`): Card identifier.
- `billing_period` (`string`, e.g. `"2026-10"`): Statement billing period.
- `cycle_id` (`UUID | null`): Canonical billing cycle identifier.
- `billing_period_start` (`date | null`, ISO `YYYY-MM-DD`): Cycle opening date.
- `cutoff_date` (`date | null`, ISO `YYYY-MM-DD`): Statement cutoff boundary.
- `due_date` (`date | null`, ISO `YYYY-MM-DD`): Payment due date (nullable).
- `previous_balance` (`Decimal`): Prior closed statement net balance carried over.
- `new_purchases` (`Decimal`): Sum of new purchases posted in this cycle.
- `billed_installments` (`Decimal`): Sum of installment principal billed in this cycle.
- `fees_total` (`Decimal`): Total fees billed in this cycle.
- `interest_total` (`Decimal`): Total interest billed in this cycle.
- `payments_received` (`Decimal`): Canonical payments received during the statement window.
- `statement_balance` (`Decimal`): Net snapshot balance (signed sum of debits minus credits).
- `minimum_payment` (`Decimal | null`): Issuer-supplied minimum payment (`null` if unknown).
- `status` (`string`, `"open"` | `"closed"` | `"settled"`): Statement lifecycle status.
- `currency` (`string | null`): Statement currency (card currency).
- `assessed_interest` (`Decimal | null`): Actual assessed interest in this statement.
- `estimated_interest` (`Decimal | null`): Projected interest estimate.
- `provenance` (`string | null`): Fact provenance (e.g. `"nexum_modeled"`, `"manual_input"`).
- `debt_partition` (`string | null`): Debt partition (`"canonical"` or `"legacy"`).
- `frozen_at` (`DateTime | null`): Immutability freeze timestamp.
- `created_at` (`DateTime | null`): Creation timestamp.
- `updated_at` (`DateTime | null`): Last update timestamp.
- `components` (`list[CreditCardStatementComponentRead]`): Detailed typed component line items.
- `facts` (`list[CreditCardStatementFactRead]`): Attached statement facts (e.g. issuer minimum payment).

#### Persisted Component Types (`chk_cc_stmt_comp_type` in database):
The database constraint enforces exact component types:
- Debits (`+`): `'purchase'`, `'installment'`, `'fee'`, `'interest'`, `'adjustment'`, `'prior_balance'`
- Credits (`-`): `'payment'`, `'refund'`, `'reversal'`, `'prior_credit'`

---

### 2.7 Installments

#### `GET /api/v1/credit/cards/{card_id}/installments`
Returns `list[CreditCardInstallmentRead]`:
- `id` (`UUID`): Installment identifier.
- `credit_card_id` (`UUID`): Associated credit card identifier.
- `purchase_transaction_id` (`UUID`): Linked purchase transaction identifier.
- `installment_number` (`int`): 1-based index of this installment.
- `installments_total` (`int`): Total count of installments for the purchase (e.g. 6).
- `principal_amount` (`Decimal`): Principal portion allocated to this installment in booked currency.
- `scheduled_period` (`string`, ISO `YYYY-MM`): Scheduled billing period.
- `scheduled_due_date` (`date | null`, ISO `YYYY-MM-DD`): Computed due date for this installment.
- `status` (`string`, `"pending"` | `"paid"`): Installment status.
- `paid_amount` (`Decimal`): Amount paid towards this installment.
- `interest_amount` (`Decimal`): Total interest amount for this installment.
- `total_amount` (`Decimal`): Total payable amount (`principal_amount + interest_amount`).
- `revision_id` (`int`): Concurrency revision tracker.
- `assessed_interest_amount` (`Decimal | null`): Actual assessed interest amount.
- `estimated_interest_amount` (`Decimal | null`): Projected estimated interest amount.
- `original_amount` (`Decimal | null`): Merchant original transaction principal amount.
- `original_currency` (`string | null`): Merchant original transaction currency (e.g. `"USD"`).
- `booked_amount` (`Decimal | null`): Booked card currency principal amount.
- `booked_currency` (`string | null`): Booked card currency (e.g. `"COP"`).
- `cycle_id` (`UUID | null`): Assigned canonical billing cycle identifier.
- `lifecycle_status` (`string | null`): Transaction lifecycle status (`"pending"` | `"posted"`).
- `provenance` (`string | null`): Fact provenance (`"manual_input"`, `"nexum_modeled"`).
- `debt_partition` (`string | null`): Debt partition (`"canonical"` | `"legacy"`).
- `remaining_principal` (`Decimal`, computed): Remaining unpaid principal (`max(0, principal_amount - paid_amount)`).
- `is_pay_early_eligible` (`bool`, computed): True if multi-installment, unpaid, and period is in the future.

---

## 3. Stable Domain Error Codes Reference

All credit endpoints route exceptions through FastAPI's global `nexum_error_handler`, returning a structured JSON envelope:
```json
{
  "ok": false,
  "error_code": "string",
  "message": "string",
  "detail": {}
}
```

The table below lists the exact HTTP status codes, machine-readable `error_code` values, and triggering domain conditions:

| HTTP Status | `error_code` | Exception Class | Root Cause & Domain Invariant | Suggested Client Action |
| :--- | :--- | :--- | :--- | :--- |
| **400** | `credit_card_inactive` | `CreditCardInactiveError` | Card is deactivated, archived, or deleted (`is_active = false`). | Show inactive card banner; disable actions. |
| **400** | `credit_payment_amount_invalid` | `InvalidPaymentAmountError` | Payment amount is $\le 0$, payment exceeds total debt (voluntary overpayment), early payment exceeds principal, refund exceeds purchase amount, or payment reversal is not exact full amount. | Validate payment input field in UI; cap at total debt. |
| **400** | `credit_lifecycle_invalidity` | `CreditLifecycleInvalidityError` | Invalid lifecycle transition (e.g. attempting to reverse an already reversed purchase/payment or invalid adjustment type). | Refresh card state from server. |
| **400** | `credit_activation_boundary_violation` | `CreditActivationBoundaryError` | Purchase `occurred_at` date predates the card's canonical activation boundary timestamp. | Prompt user to enter date within canonical period. |
| **400** | `credit_ambiguous_amount` | `CreditAmbiguousAmountError` | Conflicting or ambiguous purchase amounts supplied in payload. | Provide single coherent amount. |
| **400** | `unsupported_currency` | `CreditUnsupportedCurrencyError` | Currency requested for conversion is unsupported by the platform. | Prompt user to select supported currency (`COP`, `USD`). |
| **403** | `credit_card_forbidden` | `CreditCardForbiddenError` | Credit card does not belong to the authenticated user. | Display unauthorized toast / refresh session. |
| **403** | `account_inactive` | `CreditAccountInactiveError` | Funding bank account is inactive or archived. | Prompt user to select an active bank account. |
| **403** | `forbidden` | `AccountForbiddenError` | Funding bank account does not belong to authenticated user. | Display unauthorized toast / switch account. |
| **404** | `credit_card_not_found` | `CreditCardNotFoundError` | Credit card ID does not exist. | Redirect user back to credit cards list. |
| **404** | `not_found` | `NotFoundError` | Requested statement or resource not found. | Display not found message. |
| **409** | `credit_limit_exceeded` | `CreditLimitExceededError` | Purchase amount exceeds available credit line (`limit - total_debt`). | Prompt user to make a payment or increase limit. |
| **409** | `credit_goal_reservation_breach` | `CreditGoalReservationBreachError` | Payment amount exceeds spendable balance (`account.balance - reserved_goals`). | Alert user that funds are reserved for active Goals. |
| **409** | `insufficient_funds` | `InsufficientFundsError` | Account nominal balance is lower than payment amount. | Alert user to deposit funds or switch source account. |
| **409** | `credit_idempotency_conflict` | `CreditIdempotencyConflictError` | Command ID replayed with conflicting operation, card, account, or amount parameters. | Regenerate unique `command_id` / `Idempotency-Key`. |
| **409** | `credit_billing_cycle_closed` | `CreditBillingCycleClosedError` | Attempting to schedule a purchase into a closed or settled billing cycle. | Schedule purchase into active open cycle. |
| **409** | `credit_statement_immutable` | `CreditStatementImmutableError` | Closed statement or component is immutable; in-place edit/delete rejected by database trigger. | Use adjustments/refunds to post compensating events. |
| **409** | `credit_allocation_error` | `CreditAllocationError` | Allocation failed to balance or breached component constraints. | Retry allocation or inspect component balances. |
| **409** | `credit_installment_ceiling_error`| `CreditInstallmentCeilingError` | Payment allocation exceeds installment ceiling (`principal + assessed_interest`). | Verify installment outstanding balance. |
| **503** | `fx_rate_unavailable` | `CreditFXRateUnavailableError` | External FX quote service unavailable or rate quote expired. | Prompt user to retry in a few moments. |

---

## 4. Frontend Migration Checklist

When frontend development and QA begin:
1. [ ] Update TypeScript API client models from `openapi.json` (SHA-256: `988BC0B7DDFC9796DE6F925DB46E84FCB9D4E86EBAB266FDBC239EFC50EA08C1`).
2. [ ] Map primary card balance card to `total_debt` (replace references to deprecated `current_debt`).
3. [ ] Label available credit clearly as estimated (`Cupo disponible (estimado Nexum)`).
4. [ ] Handle `minimum_payment === null` gracefully in the UI (e.g. display *"Sin información del banco"* or *"No requerido"* instead of defaulting to `$0.00`).
5. [ ] In payment modal, validate that requested payment does not exceed `card.total_debt` and warn if funding account has goal reservations.
6. [ ] Map stable root-level `error_code` from the response envelope (`error_code`) to localized user-friendly toasts and validation errors.
