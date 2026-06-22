# Backend V1.4 Multi-Currency Trust & Estimated FX Report

## Overview
Backend V1.4 successfully closes out the multi-currency trust requirements for Nexum by solidifying ledger correctness and introducing a non-mutating FX estimation layer for visualization.

## Key Changes

### 1. Ledger Currency Trust
- **Enforced Currency in Creation Schemas**: Removed all silent `COP` fallbacks in `LedgerEventCreate`, `AccountCreate`, `CreditCardCreate`, `TransferCreate`, `GoalCreate`, and `ObligationCreate`. Frontend and Conversational LLM must explicitly declare the currency.
- **Ledger Integrity**: The ledger remains untampered. Balances are derived strictly in their original currencies without undocumented backend-side mixups.
- **Grouped Balances**: Financial truth is now exclusively provided via `totals_by_currency`. Global legacy fields zero out when multiple currencies are detected to prevent mathematically invalid sums.

### 2. Estimated FX Layer (Visualization)
- **`EstimatedTotals` Object**: Added to `IntelligenceSnapshotRead` to serve as a frontend-friendly unified visualization tool.
- **Provider Protocol**: Implemented `FxRateProvider` protocol.
- **DolarApi Colombia**: Integrated `DolarApiColombiaFxRateProvider` as the official exchange rate source, securely bounded by `httpx` timeouts and error handling.
- **Static Fallback**: Created `StaticFxRateProvider` for robust testing without network dependency.
- **Financial Rule Maintained**: FX conversion is purely superficial (`is_estimated = True`). It calculates against `available_real` across `totals_by_currency` and maps back to a single `base_currency` (COP). It never modifies `cashflow` logic or ledger events.

## Validation Status
- **Tests**: 172 tests passing. Added tests for `get_snapshot_with_fx_provider`.
- **Ruff**: Fully passing.
- **OpenAPI**: Re-exported successfully.

## Frontend Handoff Information
- Frontend must consume `estimated_totals` from `/api/v1/intelligence/snapshot` when it wishes to display unified net worth across mixed currency accounts.
- The base currency is fixed to `COP` per system defaults. Unrecognized currencies will be flagged in the `unsupported_currencies` list and skipped in the summation.

## Conclusion
Backend V1.4 is stable and satisfies all Alpha Blocker requirements for multi-currency handling. Alpha Candidate is cleared for final validation.
