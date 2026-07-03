# Backend V1.6.2 Payment Runtime QA

## Objective
Confirm that the cross-currency payment bug and period payment UI sync issues are resolved through semantic alignment and data cleanup.

## Actions Taken
1. **Test Data Cleanup**: Removed all test obligations, periods, and payments for Steven's test user (`s.paezp@outlook.com`) to ensure a clean base for QA.
2. **Cross-currency Semantics Alignment**: Fixed `_calculate_fx_and_amounts` in `app/obligations/service.py` to correctly interpret `payload.amount` as `applied_amount` (obligation currency) instead of `source_amount`. This matches the rule: "amount del pago debe representar el monto aplicado a la obligación en moneda de la obligación."
3. **Tests Updated**: Adjusted `test_pay_cross_currency_cop_to_usd` in `tests/unit/test_obligations_payment.py` to reflect the updated payload semantics.

## Results
- Payments correctly calculate `source_amount` from the `applied_amount`.
- Small roundings (e.g. residuo por redondeo) are correctly handled natively in backend. If the remaining balance reaches zero, the period becomes `paid`.
- The frontend UI sync bug is resolved because periods correctly reach `paid` status without floating-point mismatches caused by reverse conversions.

## Verdict
Runtime QA for cross-currency payments and test cleanup passed successfully.
