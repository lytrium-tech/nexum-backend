# Backend Current State

This document is the source of truth for the implemented and validated backend state.

## Global Context
- Phase: Backend V1.5 Credit Card Advanced Model.
- Database: Supabase PostgreSQL.
- Architecture: Event-Sourced Ledger with advanced credit card cycle state engine.
- Tech Stack: FastAPI, SQLAlchemy, Pydantic, PostgreSQL.

## Completed Features
- **Backend V1.6.2**: Obligation Payment Preview. `remaining_amount` included directly on `ObligationPeriodRead`. Implemented `POST /api/v1/obligations/periods/{period_id}/pay/preview` for zero-risk real-time FX payment quotes.
- **Backend V1.6.1**: Intelligence Snapshot Fix. Corrected intelligence repository to use ObligationPeriod as the source of truth, eliminating legacy obligations.amount and broken views.
- **Backend V1.6**: Obligations Core. Implemented ObligationPeriod engine for precise state tracking, partial payments, and dynamic completion cycles.
- **Backend V1.5**: Ready for final handoff. Implemented Credit Card Advanced Model (Persisted Statements, Payment Waterfall, Early Payments). Enforced goal/obligation time semantics and cross-currency multi-account flows.
- **Backend V1.4**: Multi-Currency Trust & Estimated FX. Enforced `currency` in all creation schemas and removed COP fallback. Implemented `estimated_totals` using Dolar API Colombia for USD->COP visualization layer.
- **Backend V1.3**: Alpha Blocker Fixes. Implemented `covered` period status, enforced multi-currency global totals zeroing to prevent unsafe sums, fixed ledger currency assignments, and restored `include_archived` querying for accounts and obligations.
- **Backend V1.2**: Stabilized Alpha with Period Semantics for Snapshot, Goals, Obligations, Credit Cards and Conversations NLU safety.

## API Contracts
- Exposed fully via `openapi.json` and mirrored to `../docs/contracts/openapi.json`.
- Key entities: `users`, `accounts`, `categories`, `financial_events`, `goals`, `obligations`, `credit_cards`, `credit_card_statements`, `credit_card_installments`.
- Read operations are resolved through Views and Models.
- Write operations are exclusively via structured command services.

## Financial Truth
Backend explicitly owns financial calculations:
- Frontend must not deduce `free_money`, `committed_outflows`, `available_credit`, `payment_required`, `remaining_required_this_period`, or `totals_by_currency`.
- "Backend calculates. Frontend represents. LLM explains."
- Multi-currency representation utilizes `totals_by_currency` for financial truth and `estimated_totals` strictly for unified visualization. Ledger remains untampered in original currencies.
- Credit Card payment execution is strictly governed by the backend's Payment Allocation Waterfall.

## Subsystems Status
- **Credit**: Advanced V1.5 Model active. Explicit statements, frozen snapshots, early installment payments, fees/taxes/insurance charges, and multi-tier payment allocation waterfall.
- **Goals**: Time-aware cross-currency contributions.
- **Obligations**: V1.6 Core active. Time-aware dynamic deductions, period-based engine, and partial payments.
- **Intelligence**: Snapshot and Conversational engine. V1.6.1 snapshot uses ObligationPeriod as source of truth.
- **Ledger/Cash**: Cross-currency transfers and strict multi-currency balances.

## Pending for V2
- Complex banking engine for credit cards (Compound Interest).
- Advanced AI financial advisor/scenario engine.
