> **HISTORICAL NOTICE:** Este documento conserva diseos, planes o reportes histricos. No debe ser considerado la fuente de verdad actual. Para la documentacin t?cnica cannica, consulte [docs/project/](../project/00-backend-overview.md).

# Backend V1.5 — Production Patch Deploy Report

## 1. Executive Summary
Backend V1.5 production patch deploy to fix contract gaps detected by Frontend V1.5 Phase 4 has been completed successfully. The production API is now aligned with the latest OpenAPI specification.

## 2. Commit Deployed
`63d5578` - fix(backend-v1.5): expose credit statements and safe early payment contract

## 3. Scope
- Deployed endpoints for statements list and details.
- Deployed schema changes making `amount` optional in early payments.
- Exposed `remaining_principal` in installments read schema.

## 4. Migration Status
Migration: not required.
All changes were schema-only or calculated properties (`remaining_principal` is a `@computed_field`).

## 5. Deployment Steps
1. Validated local tests (`197 passed`) and git tree.
2. Verified `openapi.json` and handoff documents.
3. Connected to VPS via SSH (`lytrium-vps`).
4. Pulled latest changes from `main` (`git pull origin main`).
5. Rebuilt Docker container (`docker compose build --no-cache api`).
6. Started new container (`docker compose up -d api`).

## 6. Health Checks
- `https://api.nexum.lytrium.tech/health`: ok
- `https://api.nexum.lytrium.tech/health/readiness`: ok
Runtime commit includes `63d5578`.

## 7. Endpoint Verification
- `GET /api/v1/credit/cards/{card_id}/statements`: Endpoint runtime verification pending authenticated frontend QA.
- `GET /api/v1/credit/cards/{card_id}/installments`: Endpoint runtime verification pending authenticated frontend QA.

## 8. Logs
No startup errors.
No migration errors.
No import errors.
No router/schema errors.
Docker container started successfully.

## 9. Runtime QA Readiness
The production backend is fully ready for Frontend V1.5 Runtime QA.

## 10. Known Limitations
None.

## 11. Final Status
Backend V1.5 production patches deployed.
Ready for Frontend V1.5 Runtime QA.

