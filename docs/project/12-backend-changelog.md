# Changelog del Backend

## [v1.5.0-alpha] - Backend V1.5 Credit Card Advanced Model
- Implemented Persisted Statements with immutable frozen state.
- Developed multi-tier Payment Allocation Waterfall (Past Due -> Interest -> Fees -> Billed Quotas -> Billed Revolving -> Unbilled Revolving).
- Implemented Early Future Installment Payments avoiding frozen statement disruption.
- Established rigorous constraints protecting future unbilled installments and preventing excess overpayments.
- Validated FX cross-currency flows across Goals and Transfers.
- Stabilized and aligned OpenAPI contracts for full frontend consumption.

## [v1.4.2-alpha] - Backend V1.4.2 Snapshot Hotfix
- Fixed 500 Internal Server Error in Snapshot generation caused by missing null-safety check for `billed_debt` during currency aggregation.
- Fixed 500 Internal Server Error (AttributeError) in Dashboard/Snapshot and Conversational flows by updating `list_active` calls to `list_by_user` for ObligationRepository.

## [v1.4.1-alpha] - Backend V1.4.1 Production Stabilization
- Fixed hardcoded "COP" suffix in conversational multi-action responses.
- Fixed 500 Internal Server Error in Snapshot generation when `billed_debt` is None.
- Fixed 404 Not Found error during archived account reactivation by propagating `include_inactive`.

## [v1.4.0-alpha] - Backend V1.4 Multi-Currency Trust & Estimated FX
- Enforced `currency` code requirement across all creation schemas and AI parsing logic. Legacy `COP` fallback removed.
- Implemented `FxRateProvider` protocol.
- Integrated Dólar API Colombia for USD->COP exchange rate visualization layer.
- Added `estimated_totals` unified Net Worth estimation to Snapshot API.

## [v1.3.0-alpha] - Backend V1.3 Alpha Blocker Fixes
- Introduced `covered` period status for obligations paid outside Nexum.
- Secured Snapshot multi-currency by zeroing global values when multiple currencies are present (`cross_currency_global_totals_disabled`).
- Ledger events now require an explicit `currency` from source entities.
- Restored `include_archived` querying capability for Accounts and Obligations list endpoints.

## [v1.2.0-alpha] - Backend V1.2 Alpha Baseline
- Sprint 7 regression passed. Backend V1.2 closed.
- Snapshot period semantics & cashflow subtypes separated.
- Passive multi-currency support and currency minimum units implemented.
- Goals period semantics and rounding finalized.
- Obligations period lifecycle and account archiving (soft delete) completed.
- Credit Card cycle semantics stabilized (billed vs unbilled, next payment estimate).
- Chat Context audited and Prompt Safety hardened.

## [v1.1.0-alpha] - Backend V1.1 Closed Alpha
- Sprint 7 regression & alpha readiness passed. Backend V1.1 closed. Production verified. Alpha Ready.
- Financial Truth, Credit Semantics, and Obligations V1.1 successfully deployed to production.
- OpenAPI schemas aligned for frontend consumption.
- Security and Ownership hardened.

## [v0.1.0-alpha] - Alpha Privada
- MVP Técnico finalizado.
- Autenticación JWT implementada y validada criptográficamente (JWKS).
- Soporte multi-tenant con perfil de usuario nativo (public.users).
- Integración con IA (Gemini) en dominios financieros.
- Testing y despliegue productivo estable.