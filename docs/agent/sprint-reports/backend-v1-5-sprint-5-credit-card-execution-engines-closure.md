# Backend V1.5 Sprint 5 — Credit Card Execution Engines Closure

## 1. Executive Summary
This sprint implemented the execution engines for the advanced credit card model built in Sprint 4. The implementation covers the payment allocation waterfall, early future installment payment processing, statement charges base, minimum payment calculation, and payment_required semantics.

## 2. Scope Implemented
- **Data Model Changes:** Added `credit_card_early_payments` and `credit_card_statement_charges` with their respective migration script. Added `paid_amount` and `status` to statement charges.
- **Service Integration:** Added methods for executing waterfall logic and early payments.

## 3. Payment Allocation Waterfall
Standard payments apply funds deterministically in the exact following order:
1. `past_due` amounts (installments with scheduled_period < current)
2. `accrued interest` (interest portion of current period installments)
3. `fees / insurance / taxes` billed (pending `credit_card_statement_charges`)
4. `billed installments` (principal portion of current period installments)
5. `billed revolving principal` (revolving principal mapped to current period)
6. `unbilled revolving principal` (revolving principal mapped to future periods, only if overpaying statement balance)
*Note: Future unbilled installments are strictly protected and never paid automatically.*

## 4. Fees / Taxes / Insurance
- Model `CreditCardStatementCharge` persists individual fees.
- Statement charges are integrated into the payment allocation waterfall prior to billed installments.

## 5. Minimum Payment
Calculated securely as:
`sum(billed_installments_due) + sum(billed_fees) + sum(past_due_minimums) + max(revolving_balance * min_pay_percentage, min_pay_absolute)`
- Bounded strictly by `statement_balance`.
- Uses configurable defaults (`0.05` percentage and `50000 COP` / `15 USD` absolute) for safety.

## 6. Payment Required
Mapped `payment_required` directly to `statement_balance` to ensure full payment semantics are preserved.

## 7. Interest Foundation
- Installment interest foundation implemented inside `_build_installments`.
- Waterfall allocation accurately routes payments to interest amounts prior to principal amounts.
- *Note:* exact ADB/bank-specific interest for revolving balances is deferred to V2.

## 8. Early Future Installment Payment
Implemented explicit endpoint `POST /api/v1/credit/accounts/{id}/purchases/{purchase_id}/pay_early`.
- Demands `source_account_id` and subtracts funds via standard ledger integration.
- Persists exact trace in `credit_card_early_payments`.
- Recalculates future pending installments downwards while keeping the remaining term length strictly intact.

## 9. Data Model Changes
- `credit_card_early_payments` table created.
- `credit_card_statement_charges` table created with `paid_amount` and `status` columns.
- Handled safely via alembic migration script.

## 10. OpenAPI Impact
Schema export fully refreshed (`openapi.json`).

## 11. Tests
197 tests pass successfully, including:
- Mocks strictly validating the 6-step waterfall order.
- Validations protecting future installments from overpayment.
- Strict rejection of early payments that exceed remaining principal.

## 12. Regressions
None detected. `test_credit.py` mocks updated correctly to pass the new strict waterfall.

## 13. Known Limitations
- Overpaying beyond unbilled revolving debt raises a controlled exception rather than creating positive balances (deferred feature).
- Exact ADB/bank-specific interest deferred to V2.

## 14. Next Step
- Sprint 5 commit
- Sprint 5 production deploy
- Sprint 5 deploy report
- Backend V1.5 remaining backend closeout / Sprint 6
- Frontend alignment deferred until Backend V1.5 is fully closed
