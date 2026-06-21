# Backend Current State

This document is the source of truth for the implemented and validated backend state.

## Global Context
- Phase: Backend V1.2 - Sprint 0 (Database Cleanup & Views Pruning) Closed.
- Database: Supabase PostgreSQL (Test Data wiped, Views Pruned, Reseeded).
- Architecture: Event-Sourced Ledger with Materialized Views for balance derivations.
- Tech Stack: FastAPI, SQLAlchemy, Pydantic, PostgreSQL.

## Completed Features
- **Sprint 0**: Test database wiped and reseeded with `DEV_USER_ID`. Legacy and unused views dropped.
- **Sprint 6 (V1.1)**: Frontend contract alignment (snapshot truth vs frontend derivation separation).
- **Sprint 5 (V1.1)**: Credit Cards refactored into event-sourcing with installments and billing engine basics.
- **Sprint 4 (V1.1)**: Obligations implemented via Ledger.
- **Sprint 3 (V1.1)**: Goals implemented via Ledger.
- **Sprint 2**: Conversational memory and intelligence enhancements.
- **Sprint 1**: Auth, Users, Cashflow, Categories basic Ledger.

## API Contracts
- Exposed fully via `openapi.json` and mirrored to `../docs/contracts/openapi.json`.
- Key entities: `users`, `accounts`, `categories`, `financial_events`, `goals`, `obligations`, `credit_cards`.
- Read operations are resolved through Views.
- Write operations are exclusively `financial_events` via the Ledger.

## Financial Truth
Backend explicitly owns financial calculations:
- Frontend must not deduce `free_money`, `committed_outflows`, `available_credit`, `payment_required`.
- Dashboard Snapshot currently aggregates some historical data, pending isolation in V1.2 Sprint 1.

## Subsystems Status
- **Intelligence**: Operates Snapshot and AI integrations (Gemini). Chat is limited to single intents.
- **Credit**: Dynamic debt calculations active.
- **Goals**: Event-driven contributions.
- **Obligations**: Periodic deductions via Ledger.
- **Transfers**: Handled as `transfer_in`/`transfer_out` events without altering `net_cashflow`.

## Pending for V1.2
- Snapshot Period Semantics (Isolating current month from historical).
- Passive Multi-Currency aggregation grouping.
- Goal periodic requirement rounding.
- Credit Card specific `debt_payment` vs `credit_card_consumption` logic.
- Period lifecycle reactivation for Obligations.
- Account / Category Archiving (Soft Delete).
