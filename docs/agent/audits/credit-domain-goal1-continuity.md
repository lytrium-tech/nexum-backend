# Nexum Credit Domain Goal 1 — Continuity State

## Checkpoint
- **checkpoint:** `phase-b-domain-reconstruction-draft`
- **timestamp:** 2026-10-27T23:44:00-05:00
- **domain:** Credit Domain (`app/credit`)
- **current_role:** Single-tier Execution Worker
- **target_artifact:** `docs/agent/audits/credit-domain-goal1-audit.md`

---

## Completed Scope
1. **End-to-End Lifecycle Call Path Reconstruction:**
   - Traced complete call paths, entities, values, transaction ownership, API representations, and unit tests for:
     - Card creation (`POST /cards` -> `service.create_card` -> `repo.add` -> `_enrich_card`).
     - Purchase execution (`POST /purchases` -> limit check -> `LedgerEventCreate` [neutral] -> `CreditCardTransaction` -> `_build_installments`).
     - Cycle assignment and installment creation (`_installment_amounts` with last-installment residual quantization).
     - Billed vs unbilled status calculation (`_calculate_card_status` -> `repo.get_card_status_data`).
     - Statement endpoints (`GET /statements` -> select from unpopulated `credit_card_statements`).
     - Bill payment (`POST /payments` -> account lock -> FX rate lookup -> overpayment check -> Ledger event [outflow] -> `account.balance` debit -> `CreditCardTransaction` [payment] -> `_apply_payment_to_waterfall`).
     - Waterfall allocation and installment mutation (`inst.paid_amount += applied`).
     - Next cycle progression (passive on-read boundary advancement).
2. **Cutoff Semantics & Date Rule Proof:**
   - Proved cutoff semantics across three operational dates (before cutoff, on cutoff, after cutoff).
   - Uncovered critical defect: `_build_installments` unconditionally derives `scheduled_period = today.strftime("%Y-%m")` (`service.py:112`), ignoring cutoff days. Post-cutoff purchases within the same calendar month are immediately classified as `billed` by `_calculate_card_status` (`service.py:818`, `repository.py:129`), demanding payment in the current cycle rather than the next cycle.
   - Clarified that `scheduled_period` is month-only (`YYYY-MM`) and on-cutoff vs post-cutoff behavior depends on the status evaluation date, avoiding claims that unproduced closed statements were mutated.
   - Identified all uses of `date.today()`, `financial_date()`, `America/Bogota`, database `now()`, and `scheduled_period`.
   - Proved timezone split-brain between Python naive local `date.today()` and PostgreSQL `now() AT TIME ZONE 'America/Bogota'`. Credit completely bypasses Nexum's canonical `financial_date()`.
3. **One-Installment vs Multi-Installment Mechanics:**
   - Documented `_installment_amounts` quantization assigning rounding residuals to the final installment.
   - Reconstructed flat interest formula on isolated installment principals.
   - Documented single-installment interest penalty: 1-installment purchases incur interest whenever `monthly_interest_rate > 0`.
   - Proved overpayment wall: standard payments permit paying ahead only for revolving unbilled debt (`installments_total == 1`). Cardholders with multi-installment debt cannot pay ahead via standard payments.
   - Reconstructed `/pay_early` eligibility and principal schedule compression.
4. **Interest Storage & Amortization Formulas:**
   - Cataloged interest fields and rate units (monthly percentage).
   - Reconstructed current formula: `interest = (principal * rate / 100).quantize(0.01)`.
   - Documented explicit placeholders, absence of French/German amortization, absence of daily average balance (ADB) accrual, and lack of compounding.
5. **Minimum Payment & Read Model Contradiction Matrix:**
   - Reconstructed minimum payment formula (5% or 50,000 COP / 15 USD).
   - Exposed severe API contradictions:
     - `/cards` returns `statement_balance = null`, while `/status` returns `billed_debt`.
     - `/cards` returns `minimum_payment = 0.00`, while `/status` returns the calculated formula.
     - `v_credit_card_debt` aggregates transactions, contradicting active installment-based repository queries.
6. **Payment Waterfall & Allocation Engine Reconstruction:**
   - Traced step-by-step sequential waterfall execution (Tiers 1 through 7).
   - Proved non-deterministic payment allocation ordering: `list_pending_installments_for_update` orders by `scheduled_period, installment_number` without tiebreakers. Equal installments allocate in arbitrary physical heap scan order.
   - Proved complete loss of allocation auditability: no junction table links payment transactions to specific installments or statement charges.
7. **Partial Payment, Overpayment & Prepayment Behavior:**
   - Reconstructed partial payment behavior, overpayment rejection, and `/pay_early` execution.
   - Proved `/pay_early` transaction void: early payments insert to `credit_card_early_payments` but create no row in `credit_card_transactions`.
8. **Refunds, Reversals, Adjustments & Credit Balance Audit:**
   - Proved total absence of merchant refunds, reversals, chargebacks, and adjustments.
   - Proved immediate single-phase settlement (no pending/authorization states).
   - Proved negative debt (saldo a favor / true credit balance) is mathematically and logically prohibited.
9. **Multi-Currency & FX Engine Audit:**
   - Clarified foreign purchase behavior: the API lacks original currency and amount inputs, implicitly trusting pre-converted input and recording the supplied amount as card currency.
   - Reconstructed cross-currency payments, server-side FX rate lookup, and JSONB audit trails.
10. **Candidate Findings Register:**
    - Formulated 12 concrete candidate findings categorized with provisional P0, P1, and P2 severities and exact source references.
11. **Alignment with Founder Directions:**
    - Removed invented product decisions.
    - Explicitly recorded only approved Founder directions (bank authority, statement snapshots, post-cutoff attribution, prepayment concept, overpayment cap, atomic reconciliation, goal reservation balance reduction, currency separation, canonical Bogota clock).
    - Cataloged open business rules in Section 27.
12. **Restored 28-Section Canonical Structure:**
    - Document organized under the exact 28 required sections, with sections 12-13 marked PENDING PHASE C, 19-20 marked PENDING VERIFICATION, and 24-28 marked PENDING PHASE E / OPEN PRODUCT DECISION.

---

## Evidence Pending
- **CT101 Physical Gate Verification:** Execution of physical PostgreSQL 17 test passes on isolated LXC CT101 (Proxmox) to observe query execution plans, locking behavior, and physical heap scan order on equal installments.
- **Production Database Validation:** Inspection of production databases for customer purchases made post-cutoff that suffered premature billing.
- **Early Payment Reconciliation Audit:** Verification of whether production databases contain divergence between Ledger event totals and `credit_card_transactions` due to early prepayments.

---

## Exact Next Step
- **Codex independent verification, checkpoint commit/push, then Phase C invariants:**
  1. Codex reviews the updated audit (`docs/agent/audits/credit-domain-goal1-audit.md`) and continuity (`docs/agent/audits/credit-domain-goal1-continuity.md`).
  2. Codex performs independent code inspections and verifies the candidate findings.
  3. Codex commits and pushes the Phase B checkpoint.
  4. Progression to Phase C financial invariants definition.
