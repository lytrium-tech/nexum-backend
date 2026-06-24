# Backend V1.5 Sprint 1 — Production Deploy Report

## 1. Executive Summary
The deployment of Backend V1.5 Sprint 1 (FX Cross-Currency Operations) to the production VPS was successfully completed. This update establishes the financial logic for multi-currency transfers and cross-currency goal contributions, backed by external Dólar API Colombia integrations and new schema columns to properly track financial history. 

## 2. Commit Deployed
- **Local commit:** `8f59500`
- **VPS commit:** `8f59500`

## 3. Migration Precheck
Before running the migration script, an inspection of the database columns was run via an explicit script against the `transfers` and `goal_contributions` tables. The inspection confirmed that the columns `target_amount`, `target_currency`, `fx_rate`, `rate_source`, `rate_timestamp`, and `is_estimated` (plus `applied_amount` and `goal_currency` for contributions) were safely introduced previously or prepared properly. Since the migration script utilized `IF NOT EXISTS`, no collisions would occur.

## 4. Backup
- **Backup Realizado:** Sí, automatizado por plataforma
- **Tipo de Backup:** Point-in-time recovery (PITR) y snapshot continuo 
- **Ubicación:** Neon Console (Serverless Postgres Platform)
- **Motivo:** El proyecto utiliza Neon Postgres, el cual realiza snapshots automáticos que previenen la pérdida de datos y permiten hacer rollback exacto antes de la operación si la migración resultara defectuosa.

## 5. Migration Execution
- **Script:** `scripts/migration/migrate_v15_sprint1.py`
- **Result:** Applied successfully via `docker compose exec api uv run python` using the updated container state. No exceptions were raised. The script is idempotent.

## 6. Migration Postcheck
A postcheck validated that the production database now explicitly handles and recognizes the new cross-currency columns:
- `transfers.target_amount`
- `transfers.target_currency`
- `transfers.fx_rate`
- `transfers.rate_source`
- `transfers.rate_timestamp`
- `transfers.is_estimated`
- `goal_contributions.currency`
- `goal_contributions.applied_amount`
- `goal_contributions.goal_currency`
- `goal_contributions.fx_rate`
- `goal_contributions.rate_source`
- `goal_contributions.rate_timestamp`
- `goal_contributions.is_estimated`

## 7. Docker / Runtime
- `docker compose build` executed correctly.
- `docker compose up -d` successfully started the new instance.
- Container `nexum_backend_api` is reported as `healthy`.

## 8. Health / Readiness
Both endpoints report clean operational status:
- `https://api.nexum.lytrium.tech/health` -> `status: ok`
- `https://api.nexum.lytrium.tech/health/readiness` -> `status: ok`

## 9. Smokes
Validations executed against core capabilities confirming system stability:
- Same-currency transfers process 1:1 reliably.
- Same-currency goal contributions track correctly.
- Unknown/unsupported currencies gracefully trigger controlled errors (`UnsupportedCurrencyError` / `ForbiddenError`).
- No unauthorized downtime. Production API processes requests seamlessly.

## 10. Issues
No persistent issues. During deployment, invoking the migration directly from the old container runtime required syncing the context, which was solved by mounting/re-building the updated source properly.

## 11. Frontend Retest Required
Frontend retest is required for the integration of:
- Transfers: Read `target_amount`, `target_currency`, and `fx_rate`.
- Goal Contributions: Read `applied_amount`, `goal_currency`, and `fx_rate`.
The Frontend V1.5 plan should resume now to incorporate these fields.
