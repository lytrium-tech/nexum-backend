# Nexum V1.7 Phase 9I — Backend Deploy with Feature Flag OFF

## 1. Executive Summary
This document summarizes Phase 9I, which successfully deployed the latest V1.7 backend codebase to the production environment while keeping the `NEXUM_OBLIGATIONS_V17_ENABLED` feature flag strictly `false`. This allows V1.5 endpoints to continue operating safely against the newly migrated V1.7 database schema.

## 2. Approval Scope
Steven explicitly approved deploying the production backend with the V1.7 feature flag disabled. The objective was to update the container to `0860b12` without activating any new logic. Frontend deployment, flag activation, and manual data changes were strictly prohibited.

## 3. Production State Before Deploy
- **Backend Current Commit**: `ee2e4bb`
- **Branch**: `main`

## 4. Database Migration State
- **Alembic current**: `v1_7_phase2_1 (head)`
- **Alembic heads**: `v1_7_phase2_1 (head)`
- **Alembic current equals head**: YES
- No additional migrations were executed during this phase.

## 5. Feature Flag State
- **NEXUM_OBLIGATIONS_V17_ENABLED Before**: Absent from `.env`.
- **NEXUM_OBLIGATIONS_V17_ENABLED After**: Added and set to `false`.
- **Method Used**: Remote execution of `echo` to append the variable.
- **Secrets Exposed**: NO

## 6. Backend Sync Result
- **Execution**: `git pull --ff-only origin main`
- **Backend Current Commit After Sync**: `0860b12`
- **Result**: SUCCESS (Fast-forwarded cleanly).

## 7. Deploy Execution
- **Command Used**: `docker compose up -d --build api`
- **Result**: SUCCESS. The `api` container was rebuilt and recreated.
- **Frontend Deploy Performed**: NO

## 8. Runtime Validation
- **Service Status**: `Up (healthy)` (confirmed via Docker).
- **Startup Errors**: NO critical errors observed in startup logs (`docker compose logs api`).
- **Database Connected**: YES

## 9. Smoke Checks
- **V1.5 Health Check**: Executed `curl -fsS http://localhost:8010/health`. Result: SUCCESS (`{"status":"ok","service":"nexum-backend"}`).
- **V1.7 Smoke Check**: Executed `curl -s http://localhost:8010/api/v1.7/obligations/summary`. Result: `401 Unauthorized`. 

V1.7 flag-off smoke returned 401 Unauthorized due to lack of local auth credentials. This did not validate the exact expected 403 feature-disabled response, but it confirmed there was no backend crash. Exact authenticated V1.7 flag-off behavior must be validated in Phase 9J.

## 10. Safety Confirmation
- **Env Changes Performed**: Limited to strictly adding `NEXUM_OBLIGATIONS_V17_ENABLED=false`.
- **Feature Flags Activated**: NO
- **Manual Data Changes**: NO
- **Production DB Modified Outside Prior Migration**: NO
- **Frontend Deploy Performed**: NO

## 11. Gate Decision
**Decision**: `READY_FOR_9J_BACKEND_SMOKE_VALIDATION`

## 12. Recommended Phase 9J
**Phase 9J — Backend Smoke Validation**
With the backend deployed and feature flag OFF, the next step is a rigorous validation by an operator or QA with valid authentication tokens. They must verify that V1.5 core flows (like Dashboard and Snapshots) remain fully operational and confirm that hitting V1.7 endpoints correctly returns the expected `403 feature_flag_disabled` error instead of proceeding.
