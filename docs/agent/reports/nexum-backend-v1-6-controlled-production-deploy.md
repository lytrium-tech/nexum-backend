# Nexum Backend V1.6 — Controlled Production Deploy

## 1. Executive Summary
The Backend V1.6 Obligations Core has been successfully deployed to the production VPS. The deployment was performed with strict safety constraints, including a pre-migration database backup and the execution of the `migrate_v16_sprint1_obligations_reset.py` script. The backend API is up and healthy.

## 2. Pre-Deploy State
- **VPS Commit before deploy:** `ca7b165`
- **Docker Status:** Running and healthy

## 3. Backup Details
- **Backup Type:** Full PostgreSQL dump from Supabase pooler via `pg_dump` (Postgres 17 image) + Environment variables & Compose files.
- **Backup Location:** `/opt/backups/nexum/20260702-152500/`
- **Contents:**
  - `db.dump` (529KB)
  - `backend.env`
  - `docker-compose.yml`
  - `docker-compose.prod.yml`
  - `git-head.txt`

## 4. Code Deployed
- **Latest Commit Deployed:** `0486862 test: stabilize obligations v1.6 contracts`
- **Build Status:** Image `nexum-backend-api` built successfully using `docker compose build`.

## 5. Migration Execution
- **Script:** `scripts.migration.migrate_v16_sprint1_obligations_reset`
- **Command:** `docker compose run --rm api .venv/bin/python -m scripts.migration.migrate_v16_sprint1_obligations_reset`
- **Result:** Successful. The script safely dropped legacy obligation tables and initialized the new schema (`obligations`, `obligation_periods`, `obligation_payments`).

## 6. Docker Runtime
- **Container Name:** `nexum_backend_api`
- **Status:** `Up (healthy)`

## 7. Health / Readiness
- `GET /health` -> `{"status":"ok","service":"nexum-backend"}`
- `GET /health/readiness` -> `{"status":"ok","service":"nexum-backend"}`

## 8. OpenAPI Validation
- **Status:** N/A (Disabled in Production)
- The production environment configures `openapi_url=None` when `DEBUG=False`. 
- **Important Note for Frontend:** 
  - Producción no expone `openapi.json` actualmente.
  - Frontend debe usar el `openapi.json` versionado en el repositorio backend para sincronizar contratos.
  - La API real ya está operando en V1.6.

## 9. Smoke Results
- **Commit VPS:** `0486862`
- **Docker Healthy:** Yes
- **Health/Readiness:** Ok
- **OpenAPI:** Disabled dynamically in Prod.

## 10. Frontend Transition Warning
**ACTIVE:** La API real ya está en V1.6, por lo que la vista actual de obligations en el frontend puede estar temporalmente incompatible y rota. Esta situación persistirá hasta completar el Frontend V1.6 Sync usando el contrato `openapi.json` del repo.

## 11. Issues / Rollback Notes
- **Issues Found:** None.
- **Rollback Commit:** `ca7b165`
- **Rollback DB:** A full DB dump is available at `/opt/backups/nexum/20260702-152500/db.dump`. Restore via `psql` if necessary.
- **Rollback Command:** `git checkout ca7b165 && docker compose build && docker compose up -d` (followed by DB restore).

## 12. Next Step: Frontend V1.6 Sync
The backend is stable and awaits frontend consumption of the new `ObligationPeriod` structures.
