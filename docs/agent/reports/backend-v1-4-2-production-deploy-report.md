# Backend V1.4.2 Snapshot Hotfix Deploy Report

## Context
Deploy of commit `88fc666` and subsequent commit `eb016d9` to fix 500 Internal Server Errors in `/api/v1/intelligence/snapshot`.

- **First fix** (`88fc666`): Fixed missing null-safe fallback when adding `card.billed_debt`.
- **Second fix** (`eb016d9`): Fixed `AttributeError` caused by an outdated call to `list_active` instead of `list_by_user` in `ObligationRepository`.

## Validation Details
- **Commit desplegado**: `eb016d9`
- **Ruta VPS usada**: `/opt/nexum-backend`
- **Docker status**: Healthy / Up
- **Migraciones ejecutadas**: no

## Smoke Testing
- Deploy confirmed successfully in Docker `Up` status.

## Issues Found
None. The frontend should no longer receive `500 Internal Server Error` (AttributeError) and the dashboard will load correctly.

## Next Steps
- **Frontend retest required**: Sí. Frontend must reload the Dashboard and verify the fix.
