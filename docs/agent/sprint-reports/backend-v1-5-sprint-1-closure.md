# Backend V1.5 Sprint 1 Closure

## Overview
- **Objective:** FX Foundation, Cross-Currency Transfers, and Cross-Currency Goal Contributions
- **Status:** Completed successfully.

## Files Changed
- `app/core/currency.py`: Added FX rate logic utilizing Dólar API Colombia.
- `app/goals/models.py`: Added currency and FX tracking columns for goal contributions.
- `app/goals/schemas.py`: Updated to receive and return currency, fx_rate, applied_amount, etc.
- `app/goals/service.py`: Replaced literal sums with FX conversion using `get_fx_rate`.
- `app/transfers/models.py`: Added FX tracking fields.
- `app/transfers/schemas.py`: Updated models to support source_currency, target_currency, fx_rate, and target_amount.
- `app/transfers/service.py`: Integrated `get_fx_rate` for cross-currency calculations.
- `scripts/migration/migrate_v15_sprint1.py`: Added columns for new models using SQLAlchemy connection text.
- `openapi.json`: Updated specifications for V1.5 schemas.

## Verification
- Ruff: 0 remaining errors.
- Pytest: 183 passed.
- Git Check: Clean code, trailing whitespaces removed.

## Known Limitations
- Dólar API Colombia does not supply EUR or MXN natively. We currently rely on Dólar API Colombia to fetch USD and convert USD->COP. Missing currencies throw `UnsupportedCurrencyError`.
- FX operations outside COP and USD are strictly blocked at the service level to prevent incorrect financial representations.
