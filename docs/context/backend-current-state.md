# Backend Current State

This document is the source of truth for the implemented and validated backend state.

## Global Context
- Phase: Backend V1.4 Multi-Currency Trust & Estimated FX.
- Database: Supabase PostgreSQL.
- Architecture: Event-Sourced Ledger with Materialized Views for balance derivations.
- Tech Stack: FastAPI, SQLAlchemy, Pydantic, PostgreSQL.

## Completed Features
- **Backend V1.4**: Multi-Currency Trust & Estimated FX. Enforced `currency` in all creation schemas and removed COP fallback. Implemented `estimated_totals` using Dolar API Colombia for USD->COP visualization layer.
- **Backend V1.3**: Alpha Blocker Fixes. Implemented `covered` period status, enforced multi-currency global totals zeroing to prevent unsafe sums, fixed ledger currency assignments, and restored `include_archived` querying for accounts and obligations.
- **Sprint 7 (V1.2)**: Regression & Alpha Recheck. All V1.2 features validated.
- **Sprint 6 (V1.2)**: Chat context audit & prompt safety. LLM operates strictly as NLU, backend calculates truth.
- **Sprint 5 (V1.2)**: Credit Card cycle semantics, billed vs unbilled debt, next payment estimates.
- **Sprint 4 (V1.2)**: Obligations period lifecycle, overdue semantics, account archiving (soft delete).
- **Sprint 3 (V1.2)**: Goals period semantics, daily requirements, and currency minimum unit rounding.
- **Sprint 2 (V1.2)**: Passive multi-currency aggregation (`totals_by_currency`) and currency minimum units.
- **Sprint 1 (V1.2)**: Snapshot period semantics (`current_period` vs `historical`), cashflow subtypes (`credit_card_consumption`, `debt_payments`, etc.).
- **Sprint 0 (V1.2)**: Database cleanup & views pruning.

## API Contracts
- Exposed fully via `openapi.json` and mirrored to `../docs/contracts/openapi.json`.
- Key entities: `users`, `accounts`, `categories`, `financial_events`, `goals`, `obligations`, `credit_cards`.
- Read operations are resolved through Views.
- Write operations are exclusively `financial_events` via the Ledger.

## Financial Truth
Backend explicitly owns financial calculations:
- Frontend must not deduce `free_money`, `committed_outflows`, `available_credit`, `payment_required`, `remaining_required_this_period`, or `totals_by_currency`.
- "Backend calculates. Frontend represents. LLM explains."
- Multi-currency representation utilizes `totals_by_currency` for financial truth and `estimated_totals` strictly for unified visualization. Ledger remains untampered in original currencies.

## Subsystems Status
- **Intelligence**: Operates Snapshot and AI integrations (Gemini). Serves `estimated_totals` FX layer.
- **Credit**: Cycle-aware debt calculations active.
- **Goals**: Event-driven contributions with period-aware requirements.
- **Obligations**: Periodic deductions via Ledger with overdue and paid semantics.
- **Transfers**: Handled as `transfer_in`/`transfer_out` events without altering `net_cashflow`.
- **Conversations**: Rule-based prompt safety strictly enforces backend-calculated data over LLM hallucinations.

## Pending for V2
- Complex banking engine for credit cards (Compound Interest).
- Advanced AI financial advisor/scenario engine.
