# Nexum V1.7 Phase 5A — FX Engine Baseline Audit

## 1. Executive Summary
Phase 5A conducts a baseline audit to evaluate the existing FX (Foreign Exchange) capabilities in Nexum Core Obligations V1.7. This report identifies what models and logic are already established, the gaps preventing full FX functionality, and recommends a scope for Phase 5B.

## 2. Current Backend State
The backend working tree is clean following Phase 4H. Core Obligations V1.7 operates reliably behind `NEXUM_OBLIGATIONS_V17_ENABLED=false` but currently lacks robust FX conversion paths for cross-currency payments.

## 3. Existing FX Models
The `app/obligations/models.py` file already defines:
- `ExchangeRate`: For cached FX rates.
- `FXQuote`: For persistent user quotes with expiration and specific targeted amounts.

## 4. Existing FX Tables / Migrations
- `alembic/versions/v1_7_phase2_1_additive_migration.py` defines the table creations for `exchange_rates` and `fx_quotes`, and adds `quote_id` and `idempotency_key` to `obligation_payments`.
- This migration has NOT been executed in production.

## 5. Existing FX Schemas / API Contracts
- Missing. The V1.7 schema files (`app/obligations/schemas_v17.py`) do not contain definitions for `FXRatesLatestResponse`, `FXQuoteRequest`, or `FXQuoteResponse`.

## 6. Existing FX Services / Business Logic
- The legacy `app/fx/provider.py` exists (introduced around V1.4) which implements a basic `FxRateProvider` with DolarAPI.
- Advanced FX quote state management and caching are missing.

## 7. Currency Fields Across Obligations / Periods / Payments
- **Obligations**: `currency` is present.
- **Periods**: `currency` is present.
- **Payments**: `currency`, `source_amount`, `source_currency`, `fx_rate`, `rate_source`, and `rate_timestamp` are actively defined in the models.

## 8. Decimal / NUMERIC Safety
- Safety is upheld at the model layer. All money-related fields (`amount`, `source_amount`, `fx_rate`, `target_amount`) correctly use `Decimal` in Python and `Numeric(18,4)` / `Numeric(15,2)` / `Numeric(18,8)` in SQLAlchemy mappings.

## 9. OpenAPI / Tests Coverage
- No FX specific API routes (`/api/v1/fx/...`) have been wired.
- No tests exist testing FX quote expiration, FX rate persistence, or tolerance calculations.

## 10. Gaps Found
- The FX schemas are completely unimplemented.
- The FX routers and services are missing.
- There is no logic tying `quote_id` enforcement in the payment engine.

## 11. Risks
- Inaccurate decimal precision if calculations are accidentally mixed with Python `float`. The old `app/fx/provider.py` returns `float`, which MUST be updated to `Decimal`.
- Exposing FX routes before enforcing `NEXUM_OBLIGATIONS_V17_ENABLED`.

## 12. Recommended Phase 5B Scope
The following scope is recommended for Phase 5B:
- Upgrade `app/fx/provider.py` to use strict `Decimal` types.
- Implement the FX Schemas (`FXRatesLatestResponse`, `FXQuoteRequest`, `FXQuoteResponse`).
- Implement the `/api/v1/fx/rates/latest` endpoint (caching logic).
- Implement the `/api/v1/fx/quote` endpoint (quote generation logic).
- Do not modify payment execution logic yet (defer to 5C).

## 13. Production Safety
This audit produced no code modifications. Production was not touched during this audit. No production database, service, or configuration changes were made.
