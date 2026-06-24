# Backend V1.5 — Financial Consistency Audit

## 1. Recommended Version
**Backend V1.5.0-alpha** - Financial Consistency & Multi-Currency Stabilization.

## 2. Executive Summary
This audit reviews the current state of Backend V1.4.1 and outlines the requirements for V1.5 to achieve financial correctness before moving to V2. The primary focus is resolving mathematical inconsistencies in cross-currency operations (transfers and goal contributions) and solidifying time-based semantics for credit cards, goals, and obligations. The guiding principle remains: "Backend calculates. Frontend represents." without mutating historical ledger events or real balances.

## 3. Evidence Reviewed
- `docs/context/backend-current-state.md`
- `docs/project/03-api-contracts.md`
- `docs/project/11-frontend-handoff.md`
- `docs/project/12-backend-changelog.md`
- Handoff documents (`backend-to-frontend.md`, `frontend-to-backend.md`)
- Existing OpenAPI contracts and service implementations (Transfers, Goals, Credit Cards, Intelligence)

## 4. Findings Classification
| Finding | Category | Priority | Affected |
|---------|----------|----------|----------|
| 1. Credit Card Installments Interest | BACKEND CALCULATION ISSUE / PRODUCT DECISION | HIGH | backend, frontend |
| 2. Cross-Currency Transfers | DATA MODEL ISSUE / BUG | BLOCKER | backend, frontend |
| 3. Cross-Currency Goal Contributions | DATA MODEL ISSUE / BUG | BLOCKER | backend, frontend |
| 4. Unified Converted API Values | PRODUCT DECISION / CONTRACT GAP | MEDIUM | shared, frontend |
| 5. Goals Time-Based States | PRODUCT DECISION / BACKEND CALCULATION ISSUE | MEDIUM | backend, frontend |
| 6. Obligations Time-Based States | PRODUCT DECISION / BACKEND CALCULATION ISSUE | HIGH | backend, frontend |

## 5. Credit Card Installments Interest
**Analysis:**
Currently, `_installment_amounts` divides the principal amount evenly but ignores `interest_amount` or `monthly_interest_rate`.
**Recommendation:**
For V1.5, we should model an explicit estimation based on the given rate at the time of purchase, applying the interest to the installments. True revolving credit compound interest is deferred to V2, but V1.5 must not ignore the interest rate provided by the user.

## 6. Cross-Currency Transfers
**Analysis:**
Currently, transferring between accounts of different currencies uses the same literal `amount` for both `transfer_out` and `transfer_in`, resulting in financial corruption (e.g., 7 USD -> 7 COP).
**Recommendation:**
Block cross-currency transfers at the API level unless an explicit `fx_rate` or `target_amount` is provided. If `target_amount` is provided, `transfer_out` uses `amount` in `source_currency`, and `transfer_in` uses `target_amount` in `target_currency`. Dólar API can be used to suggest the rate.

## 7. Cross-Currency Goal Contributions
**Analysis:**
Similar to transfers, contributing from a COP account to a USD goal subtracts COP but adds the literal amount to the USD goal.
**Recommendation:**
Block cross-currency goal contributions unless an explicit `applied_amount` (in goal currency) and `fx_rate` are provided. The account ledger event must use the source currency, while the goal contribution record uses the goal currency.

## 8. Unified Converted API Values
**Analysis:**
V1.4 introduced `estimated_totals` in the Snapshot API. Expanding this to other summary endpoints requires care to not overwrite real balances.
**Recommendation:**
Expose `estimated_totals` alongside `totals_by_currency` in summary endpoints (Accounts, Goals, Obligations, Credit) where useful, ensuring they are explicitly marked as `is_estimated = true`.

## 9. Goals Time-Based States
**Analysis:**
State changes (like `pending`) at the start of a new month.
**Recommendation:**
Calculate state dynamically on-read based on the current date, `target_date`, and `contributed_this_period`. Do not use cron jobs for this.

## 10. Obligations Time-Based States
**Analysis:**
Obligations need to transition between `pending`, `covered`, `paid`, and `overdue`.
**Recommendation:**
Compute these states dynamically based on the current date, `due_day`, and `paid_this_period`. No cron job is required.

## 11. Backend V1.5 Recommended Scope
- Fix cross-currency logic for Transfers and Goal Contributions (Blockers).
- Implement dynamic time-based properties for Goals and Obligations.
- Include explicit interest estimation for Credit Card installments.
- Extend `estimated_totals` to specific summary endpoints.

## 12. Out Of Scope For V1.5
- Complex compound interest revolving credit calculations (Deferred to V2).
- Automatic historical ledger multi-currency conversions.
- Configurable base currency per user (Keep COP as global base for V1.5).
- Cron jobs for state mutations.

## 13. Product Decisions Needed
- **Credit Interest:** Should the interest on installments be calculated using a simple flat rate per installment for V1.5, or a full amortization table?
- **Cross-Currency:** Should the API strictly require `target_amount`/`fx_rate` from the frontend, or attempt to auto-convert using the backend FX provider and fail if unavailable?

## 14. OpenAPI Contract Changes Needed
- **Transfers:** Add `target_amount`, `target_currency`, `fx_rate` to `TransferCreate`.
- **Goals:** Add `applied_amount`, `fx_rate` to `GoalContributionCreate`.
- **Credit Cards:** Add `interest_amount` to Installments Read models.
- **Summaries:** Add `estimated_totals` object to relevant domain summary responses.

## 15. Database / Migration Changes Needed
- `transfers` table: Add `target_amount`, `target_currency`, `fx_rate`.
- `goal_contributions` table: Add `applied_amount`, `fx_rate`.
- `credit_card_installments` table: Ensure `interest_amount` can be stored or calculated.

## 16. Tests Required
- Cross-currency transfer prevention and correct ledger entries.
- Cross-currency goal contribution ledger entries.
- Dynamic period state transitions for Goals and Obligations based on mocked dates.
- Installment generation with interest applied.

## 17. Suggested Sprint Roadmap
- **Sprint 1:** Cross-currency fixes (Transfers & Goals) + DB Migrations.
- **Sprint 2:** Time-Based dynamic calculations (Goals & Obligations).
- **Sprint 3:** Credit Card interest estimations + `estimated_totals` expansion.
- **Sprint 4:** Regression & Frontend Alignment.

## 18. Risks
- Frontend V1.4 might break if cross-currency validations are enforced strictly before Frontend V1.5 is ready.
- Changing `TransferCreate` and `GoalContributionCreate` contracts could require immediate frontend updates.

## 19. Questions For Steven
- Do you approve adding `target_amount` and `fx_rate` to the creation payloads to solve the cross-currency blockers?
- For credit card installments, is a simple flat estimation acceptable for V1.5, or do we need exact banking amortization?
- Should we deploy these changes iteratively or batch them entirely into a single V1.5 release to avoid frontend breaking in between?

## 20. Go / No-Go For Implementation
Waiting for Steven's approval on Product Decisions and Scope.
