# Nexum V1.7 Phase 7C — Summary Contract Hardening

## 1. Executive Summary
This phase successfully hardened the `GET /api/v1.7/obligations/summary` contract and implemented the `GET /api/v1.7/obligations/intelligence-context` endpoint for AI integration, ensuring that all UI and AI reads are deterministic, safe, and stable before frontend integration.

## 2. Files Changed
- `app/obligations/router_v17.py`: Added regex query validation for `month` and exposed `/intelligence-context`.
- `app/obligations/schemas_v17.py`: Changed decimal fields to `str` for precise serialization, and added schemas for `intelligence-context`.
- `app/obligations/service_v17.py`: Hardened summary ordering, decimal formatting, and implemented `get_intelligence_context` logic.
- `tests/unit/test_obligations_v17_api.py`: Added comprehensive tests for invalid month inputs and the new intelligence context endpoint.
- `docs/agent/reports/v1-7-phase-7c-summary-contract-hardening.md`: This report.

## 3. Summary Contract Hardening
The summary contract has been stabilized:
- `month` is now strictly validated to `YYYY-MM`.
- `Decimal` values are correctly serialized as two-decimal strings (e.g. `"100.00"`).
- `requires_action` is structured cleanly to provide only required UI context (no internal logic leaked).
- Ordering is guaranteed to be deterministic across responses.

## 4. Ordering Rules
- **Totals by Currency**: Always sorted alphabetically by `currency` ascending.
- **Requires Action**: Always sorted by `due_date` ascending, followed by `name` ascending.

## 5. Status Counts Contract
`status_counts` guarantees all possible period statuses (including `pending_payment`, `partially_paid`, `paid`, `overdue`, `pending_amount_definition`, `skipped`, `cancelled`) are present in the response, defaulting to `0` instead of missing keys, enabling safe frontend parsing.

## 6. Month Validation
The `month` query parameter is strictly validated using the pattern `^\d{4}-\d{2}$`. Any invalid request immediately returns an HTTP 422 Unprocessable Entity, preventing malformed downstream queries. Defaulting to the current month behaves correctly when the parameter is omitted.

## 7. Serialization Rules
To preserve financial precision and avoid floating-point drift on the frontend, monetary fields (`pending_amount`, `paid_amount`, `overdue_amount`) within the `totals_by_currency` map are strictly formatted and serialized as strings with exactly 2 decimal places.

## 8. Intelligence Context Decision
**Decision: A) Implementar ahora en Phase 7C.**
Because the intelligence context fundamentally relies on the same aggregated facts computed by `get_summary`, it was seamlessly implemented without introducing new FX calculations, financial events, or duplicated aggregation loops. It surfaces structured facts (`risk_flags`, `narrative_facts`) based strictly on existing state variables.

## 9. OpenAPI Validation
Both `/summary` and `/intelligence-context` are successfully exposed by the FastAPI runtime and validate correctly using Pydantic. No changes were committed to `openapi.json` statically.

## 10. Tests Result
Test suites have been updated and executed successfully:
- Unit tests cover empty states, invalid month inputs, and successful summary responses.
- Intelligence context tests ensure risk flags and narrative facts correctly trigger upon overdue or pending definition status.
- All test suites (Obligations, FX, Frontend Contracts, Intelligence Repository) pass with 100% success rate.

## 11. V1.5 Compatibility
No V1.5 endpoints or schemas were touched.

## 12. Production Safety
No database migrations were executed and no production services were altered.

## 13. Issues / Blockers
- **Ruff Warnings**: `UP042` remains flagged in `app/obligations/enums_v17.py` due to inheriting from both `str` and `Enum`. This is a known, non-blocking warning intentionally left untouched to avoid out-of-scope `--unsafe-fixes`.

## 14. Recommendation
The contracts are robust, the AI integration path is established, and the frontend can now safely ingest V1.7 summary data without ambiguity. Ready for Phase 7D (Frontend Dashboard Integration).
