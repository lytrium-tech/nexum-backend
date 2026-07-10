# Nexum V1.8 — Obligations One-Button Payment

## Summary
In V1.8, we successfully simplified the UI for obligations by replacing "Pagar obligación" and "Pagar periodo" with a single "Pagar" button. The backend now routes the request intelligently through `pay_obligation_smart`, which delegates to the FIFO or single-period logic based on the internal state, keeping the complexity invisible to the user.

## Changes Implemented
- **Backend:**
  - Added `pay_obligation_smart` method in `ObligationV17Service` that determines if the payment can be applied. It runs `refresh_overdue_periods` and checks if the oldest unpaid period requires an amount definition before delegating to `pay_obligation_fifo`.
  - Added `POST /api/v1.7/obligations/{obligation_id}/payments/smart` endpoint in `router_v17.py`.
  - Refactored tests to mock `pay_obligation_smart` effectively.
- **Frontend:**
  - Replaced the separate action buttons with a unified primary action button.
  - Handled the UI logic to display "Definir monto" or "Sin saldo por pagar" conditionally based on period status and sequence.

## Status
- **Backend Validation:** Completed. 287 tests passed.
- **Frontend Validation:** Completed. Linter and build passed successfully.
- **Smoke Testing:** Pending manual QA from Steven in Production.

## Known Limitations
- Background worker migration for O(1) performance and full unification of "Saltar/Cancelar" actions remain on the roadmap.
