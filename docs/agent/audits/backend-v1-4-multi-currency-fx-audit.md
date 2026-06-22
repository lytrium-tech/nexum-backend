# Backend V1.4 — Multi-Currency Trust & Estimated FX Audit

## 1. Recommended Version
Backend V1.4

## 2. Executive Summary
This audit addresses the final steps to solidify multi-currency trust in Nexum. We have confirmed the root causes for ledger events falling back to COP, analyzed the impact on snapshot calculations, and proposed a read-only "Estimated FX Layer" architecture using a base currency to safely provide consolidated global totals without risking actual financial records.

## 3. Evidence Reviewed
- `frontend-to-backend-v1-3.md`: Confirms frontend does not override event.currency, highlighting the backend's responsibility.
- `app/ledger/schemas.py` and `app/accounts/schemas.py` defaults: Identified `Field(default="COP")` fallbacks.
- `app/ledger/repository.py`: Analyzed `insert_event`.
- Snapshot service: Evaluated current `SnapshotTruth` handling of multi-currency.
- Existing tests: Confirmed we need to adapt mock accounts/cards to explicitly define currencies.

## 4. Ledger Currency Root Cause
In previous iterations, `LedgerEventCreate` defaulted to "COP". If a service layer method (like creating an income or opening balance) failed to explicitly extract `account.currency` or `credit_card.currency` and map it to `LedgerEventCreate(currency=...)`, the event silently fell back to COP. Furthermore, the `financial_events` DB table has `server_default="COP"`. Any missing application-level mapping results in COP.

## 5. Ledger Currency Fix Plan
- Ensure EVERY service creating ledger events (cash, accounts, goals, obligations, transfers, credit) explicitly reads `account.currency` or `card.currency` and maps it to `LedgerEventCreate(currency=...)`.
- Remove `default="COP"` from `LedgerEventCreate` schema to force application-level awareness and make it a required string field.
- Remove blind COP fallbacks from `AccountSummary` and other aggregations unless explicitly configured as base_currency.
- Tests must be updated (mock_account.currency = "USD", etc.) since `currency` will be strictly required.

## 6. Estimated FX Architecture Proposal
Introduce a robust, non-mutating layer for calculating estimated totals across mixed currencies:
- Introduce `estimated_totals` in `SnapshotTruth`.
- Provide an `FxRateProvider` interface with a `StaticFxRateProvider` (for tests/fallback) and a structure for future `ExternalFxRateProvider`.
- When `SnapshotTruth` detects multiple currencies, it calculates `estimated_total_base_currency` by converting all `totals_by_currency` balances using the provider's current rates into the `base_currency`.
- The real balances (`account.balance`) and the `financial_events` table remain untouched.

## 7. Base Currency Strategy
Following Steven's decision, `base_currency` will be hardcoded to `"COP"` for V1.4. This is purely the target currency for the `estimated_totals` consolidated view. It will be explicitly documented that `account.currency` (the real ledger currency) is independent of the `base_currency` (the display/estimation currency). In V2, `base_currency` will become a user-configurable profile setting.

## 8. External FX API Options
A brief evaluation of FX options for future external integration:
1. **Open Exchange Rates**: Free tier (1k requests/month), reliable, requires API key. Good for base USD conversions.
2. **ExchangeRate-API**: Free tier (1.5k requests/month), no API key for basic endpoint, but better with key.
3. **Frankfurter**: Open-source, no API key required, tracks European Central Bank. Highly reliable but limited exotic currencies.
4. **Mock/Static Provider**: For V1.4 and tests, we will implement `StaticFxRateProvider` with hardcoded fallback rates (e.g., 1 USD = 4000 COP, 1 EUR = 4300 COP) to build the architecture before integrating network calls.

## 9. Data Model
No new tables or migrations required for Ledger Currency (since `currency` already exists in `financial_events`).
No new tables required for Estimated FX since it's a runtime calculation.

## 10. Snapshot Impact
The `GET /api/v1/intelligence/snapshot` will output:
```json
{
  "totals_by_currency": { ... },
  "estimated_totals": {
    "base_currency": "COP",
    "estimated_total_base_currency": 1500000.0,
    "is_estimated": true,
    "rate_source": "static_mock",
    "rate_timestamp": "2026-06-22T00:00:00Z",
    "fx_rates_used": {
      "USD_COP": 4000.0
    }
  }
}
```
Global legacy totals (`available_real`, etc.) will continue to return 0 when multiple currencies exist, enforcing the use of `estimated_totals` or `totals_by_currency`.

## 11. OpenAPI Changes Needed
- Remove `default="COP"` from `LedgerEventCreate`.
- Add `estimated_totals` object definition to `SnapshotTruth` response model.
- Document `is_estimated` flag to ensure frontend correctly displays the "≈" symbol.

## 12. Database / Migration Changes Needed
None. Both Ledger Currency and Estimated FX use existing columns and runtime logic.

## 13. Tests Required
- Test that `LedgerEventCreate` throws ValidationError if `currency` is missing.
- Test that all services (cash, obligations, etc.) correctly propagate `account.currency`.
- Test `SnapshotTruth` generation calculates `estimated_totals` correctly using a `StaticFxRateProvider` when given mixed currencies.
- Test that `estimated_totals` fails gracefully (or is omitted) if the provider fails.

## 14. Implementation Plan
1. **Ledger Strictness**: Update `LedgerEventCreate` to make `currency` mandatory. Fix any compilation/test errors arising from missing currency maps in services.
2. **FX Interface**: Create `app/fx/provider.py` with `FxRateProvider` protocol and `StaticFxRateProvider`.
3. **Snapshot Update**: Inject `FxRateProvider` into `IntelligenceService`, calculate `estimated_totals`, and map to `SnapshotTruth`.
4. **OpenAPI**: Regenerate `openapi.json`.
5. **Validation**: Run pytest and ruff. Write final report.

## 15. Risks
- Frontend might mistake `estimated_total_base_currency` for a true accounting value. Explicit naming and the `is_estimated` flag mitigate this.

## 16. Questions For Steven
None.

## 17. Go / No-Go For Implementation
GO.
