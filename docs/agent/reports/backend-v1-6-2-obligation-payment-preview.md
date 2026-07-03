# Backend Phase A Sprint Closure: V1.6.2 Obligation Payment Preview

## 1. Objective Achieved
Implemented the Backend Phase A for the V1.6.2 Obligation Payment Preview. The backend now robustly supports calculating `remaining_amount` and provides a safe endpoint for frontend to preview exact payment impacts (including cross-currency FX) before committing to a ledger event.

## 2. Changes Made
- **Models**: Added `@property remaining_amount` to `ObligationPeriod`. It gracefully handles edge cases (`paid/skipped/cancelled` = `0.00`, missing `amount` = `None`).
- **Schemas**: 
  - Updated `ObligationPeriodRead` to include `remaining_amount`.
  - Added `ObligationPaymentPreviewCreate` and `ObligationPaymentPreviewRead` to define the preview contract.
- **Services**: 
  - Created `pay_preview_specific_period` in `ObligationService`. It shares `_calculate_fx_and_amounts` logic with the real execution endpoint, guaranteeing consistent quotes.
  - Implemented `can_pay` detection based on source account balance to allow frontend to safely lock the UI without triggering hard 400 errors prematurely.
- **Router**: Exposed `POST /api/v1/obligations/periods/{period_id}/pay/preview`.
- **OpenAPI**: Regenerated `openapi.json` successfully.
- **Tests**: Appended preview-specific test cases (`test_pay_preview_specific_period_same_currency`, `test_pay_preview_specific_period_overpayment`) to `tests/unit/test_obligations_payment.py`. All tests passed successfully.

## 3. Financial Invariants Protected
- Overpayment is heavily restricted. Exceeding `remaining_amount` throws `OBLIGATION_PAYMENT_EXCEEDS_REMAINING_BALANCE` in both preview and real payment.
- The preview does NOT create ledger events or mutate the database.
- Missing periods (undefined amounts) are safely blocked via `OBLIGATION_PERIOD_AMOUNT_REQUIRED`.

## 4. Next Steps for Frontend Phase B
- Frontend must rely on `remaining_amount` provided in the period schema.
- Implement the preview modal to trigger `POST /periods/{period_id}/pay/preview`.
- Disable confirm buttons when `can_pay == False`.

## 5. Artifacts Updated
- `docs/project/12-backend-changelog.md`
- `docs/context/backend-current-state.md`
- `../docs/handoff/backend/backend-to-frontend-v1-6-2.md`
