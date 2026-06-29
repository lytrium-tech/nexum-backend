# Nexum V1.5 - Credit Summary Installments Hotfix Deploy Report

## Deployment Overview
- **Local commit:** 696b8ea
- **VPS commit:** 696b8ea
- **Migrations executed:** no
- **Docker status:** healthy

## Environment Health
- **Health (`/health`):** ok
- **Readiness (`/health/readiness`):** ok

## Changes Deployed
- **Files changed:** `app/credit/repository.py`
- **Fix applied:** Updated `get_card_status_data` and `get_card_debt` to dynamically calculate credit card debt by aggregating `principal_amount + interest_amount - paid_amount` from `credit_card_installments` rather than `credit_card_transactions`.
- **Impact:** The `summary` endpoint now correctly reflects debt reductions from early payments (`credit_card_early_payments`), keeping `principal_amount` immutable as required.

## Next Steps
- **Runtime QA Retest Needed:** sí (Re-test credit card summary after executing an early payment to confirm `current_debt` decreases and `available_credit` increases accordingly).
