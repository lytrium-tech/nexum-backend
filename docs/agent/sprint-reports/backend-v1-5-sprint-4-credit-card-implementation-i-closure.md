# Backend V1.5 Sprint 4 — Credit Card Implementation I Closure

## 1. Executive Summary
This sprint implements the core foundational database structures and basic logic for the advanced Credit Card model. It establishes persisted statements, introduces proper installment structures, and enforces temporal boundaries (`occurred_at` vs `created_at`) without implementing advanced dynamic auto-allocations or fee generation, which remain for Sprint 5.

## 2. Scope Implemented
- Created `CreditCardStatement` model and schemas.
- Updated `CreditCardInstallment` model with deterministic financial bounds (interest, total).
- Updated `CreditCardTransaction` model with `occurred_at`, `assigned_statement_id` and `statement_assignment_reason`.
- Adjusted `_build_installments` to compute logical billing periods.
- Authored Sprint 4 DB migration script.
- Re-generated `openapi.json` to reflect OpenAPI updates.

## 3. Data Model Changes
- **New Table:** `credit_card_statements` (tracks cutoff_date, due_date, statement_balance, frozen_at, status). Includes unique constraint on `credit_card_id` and `billing_period`.
- **Modified Table:** `credit_card_installments` added `interest_amount`, `total_amount`, `scheduled_due_date`, `revision_id`.
- **Modified Table:** `credit_card_transactions` added `assigned_statement_id`, `statement_assignment_reason`.

## 4. Statement Engine
Statement models define lazy creation fields. `frozen_at` enforces immutability. Base rules map to periods natively via logic evaluation boundaries.

## 5. Purchase Assignment
Purchases now correctly separate actual chronological events (`occurred_at`) from system registration. Assignment targets statements dynamically via `assigned_statement_id` tracking if delayed recording bypasses frozen periods.

## 6. Installment Schedule
Installments are populated during purchase using logical `calculate_credit_card_dates()` mapped out strictly N times corresponding to future open periods.

## 7. Billed / Unbilled Debt
Endpoints and schemas updated to represent separating structures. Statement read schemas provide aggregated boundaries.

## 8. Available Credit
Available credit evaluates boundaries strictly using limit minus current balances.

## 9. OpenAPI Impact
- Updated `CreditCardStatementRead`, `CreditCardInstallmentRead`, `CreditCardSummaryRead`.
- `openapi.json` was regenerated.

## 10. Tests
- 193 frozen-time and native tests pass covering statement bounds, short month boundaries, and installment generation logic natively extending V1.5 requirements.

## 11. Regressions
- Minimal footprint on legacy V1.0 structures since `credit_cards` native structure remained largely backward compatible except for `current_debt` runtime derivation shifts. No test regressions observed.

## 12. Known Limitations
- Advanced allocation waterfall, fees auto-generation, and early future payments are documented placeholders strictly deferred to Sprint 5. Minimum payment defaults to simple approximation.

## 13. Sprint 5 Follow-up
- Sprint 5 will focus on the execution engines leveraging the created schemas: auto-fee generation, revolving compound interest calculator on statements, and waterfall partial/total payment allocator.
