# Backend V1.5 Sprint 4 — Production Deploy Report

## 1. Executive Summary
This report confirms the successful production deployment of Backend V1.5 Sprint 4 (Credit Card Statements Foundation). The deploy includes the creation of `credit_card_statements` and updates to `credit_card_installments` and `credit_card_transactions` for robust deterministic calculations.

## 2. Commit Deployed
- **Local commit:** d6d2729
- **VPS commit:** d6d2729

## 3. Migration Precheck
- Verified the absence of `credit_card_statements` table on VPS.
- Verified absence of newly required columns in existing credit tables.

## 4. Backup
- **Backup realizado:** sí.
- **Tipo de backup:** PITR automatizado por Neon DB.

## 5. Migration Execution
- **File:** `scripts/migration/migrate_v15_sprint4.py`
- Executed on the VPS using the `nexum-backend-api` container environment ensuring correct `.venv` propagation.

## 6. Migration Postcheck
- Successfully verified the creation of `credit_card_statements` with all required columns.
- Successfully verified the insertion of `assigned_statement_id` and `statement_assignment_reason` into `credit_card_transactions`.
- Successfully verified the insertion of `interest_amount`, `total_amount`, `scheduled_due_date`, and `revision_id` into `credit_card_installments`.

## 7. Docker / Runtime
- `docker compose build` and `docker compose up -d` successfully executed.
- `nexum_backend_api` container is running and reporting healthy.

## 8. Health / Readiness
- `/health` endpoint returns `ok`.
- `/health/readiness` endpoint returns `ok`.

## 9. Smokes
- Due to the absence of a secure QA token, destructive smokes were omitted.
- Local endpoint mapping validated successfully (health/readiness bounds).

## 10. Issues
- None detected. The python script execution pipeline initially lacked the `.venv` injection within the Docker context, which was immediately corrected and properly applied.

## 11. Sprint 5 Next Step
- Focus directly on execution engines (fees auto-generation, daily ADB interest loops, waterfall allocation logic, minimum payment bounds).
