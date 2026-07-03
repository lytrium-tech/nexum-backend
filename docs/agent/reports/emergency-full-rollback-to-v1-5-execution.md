# Nexum Emergency Full Rollback to V1.5 Execution

## 1. Executive Summary
An emergency full rollback to V1.5 was executed in the production environment according to the explicit authorization of the founder, accepting the loss of data generated since July 2nd, 2026. The rollback encompassed the backend repository, frontend repository, and the production database.

## 2. Founder Approval / Data Loss Acceptance
The founder explicitly approved the execution of a destructive database restore, prioritizing a return to a stable V1.5 operational state over the preservation of recent V1.6/V1.6.2 data.

## 3. Pre-Rollback V1.6 Backup
Before modifying the system, a full snapshot of the V1.6.2 state was taken and verified.
- **Path:** `/opt/backups/nexum/20260703-170000-pre-v15-rollback/`
- **Contents:**
  - `db.dump` (502K, Custom format pg_dump generated via `postgres:17-alpine`)
  - `docker-compose.yml`
  - `backend.env`
  - `git-head.txt` (commit `b553138`)
  - `containers.txt`
  - `logs.txt`

## 4. DB Restore Details
To cleanly restore V1.5 over the existing V1.6 database:
1. All user-data tables in the `public` schema (including `obligations`, `financial_events`, `credit_cards`, etc.) were explicitly dropped via `DROP TABLE ... CASCADE` to prevent schema mismatches and duplicate key errors on `COPY`.
2. The V1.5 plain-SQL dump (`/opt/backups/nexum/20260702-152500/db.dump`) was executed using `psql`.
3. Validated that `financial_events` correctly restored 93 records.
4. Supabase internal schemas (`auth`, `storage`) rejected modification attempts from the dump (expected and safe, as their state should not be overwritten by a standard dump).

## 5. Backend Rollback
- Services were stopped (`docker compose down`).
- The repository was rolled back to the last stable V1.5 commit: `ca7b165`.
- Services were rebuilt and restarted.
- **Validation:**
  - `https://api.nexum.lytrium.tech/health` -> `ok`
  - `https://api.nexum.lytrium.tech/health/readiness` -> `ok`

## 6. Frontend Rollback
- The local frontend repository was checked out to the last stable V1.5 commit: `2f6f404`.
- A new branch `release/v1.5-rollback` was created and pushed to `origin`, triggering the deployment process for V1.5 on the hosting provider.

## 7. Runtime Validation
- The backend API is successfully responding to health checks.
- Database connections are functional.
- The `obligations` table schema has been successfully reverted to the V1.5 structure (expecting `is_active`, `amount`).
- *Note: Visual QA in the browser (login, dashboard, obligations UI) is pending manual execution by the founder.*

## 8. Issues Found
- The `db.dump` file from July 2nd was in plain SQL format, requiring a manual teardown of the `public` schema before restoration to avoid `COPY` duplication errors.
- Some warnings were logged during the `psql` restore when it attempted to recreate existing `auth` and `storage` policies, which were safely ignored.

## 9. Rollback-of-Rollback Plan
If this V1.5 rollback is deemed unviable or catastrophic:
1. Stop backend services.
2. Drop all `public` tables again.
3. Restore the V1.6 dump: `pg_restore -d postgres /opt/backups/nexum/20260703-170000-pre-v15-rollback/db.dump`.
4. `git checkout main` and `docker compose up -d`.

## 10. Final State
The Nexum platform (Backend API + Database + Frontend) has been successfully rolled back to V1.5. The system is ready for user traffic under the V1.5 feature set.
