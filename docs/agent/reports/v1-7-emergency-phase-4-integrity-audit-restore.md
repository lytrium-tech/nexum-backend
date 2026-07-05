# Nexum V1.7 Emergency — Phase 4 Integrity Audit & Restore

## 1. Executive Summary
During Phase 5B (FX engine implementation), a critical integrity inconsistency was identified: the V1.7 Core Obligations codebase had been wiped during the Phase 4H closure. An emergency audit verified the damage, traced back to the last fully operational commit (`a476bc1`), restored the missing implementation logic, reversed a temporary OpenAPI test bypass, and validated that all systems and contracts pass cleanly.

## 2. Issue Detected
The Phase 4H commit `f3fc69d` ("feat: add obligations v1.7 period lifecycle transitions") deleted 1,568 lines of core logic in the V1.7 implementation files, rendering `router_v17.py`, `schemas_v17.py`, and `service_v17.py` completely empty. This issue forced tests to either break or be artificially bypassed in subsequent tasks.

## 3. Files Affected
- `app/obligations/router_v17.py` (Empty/Stub)
- `app/obligations/schemas_v17.py` (Empty/Stub)
- `app/obligations/service_v17.py` (Empty/Stub)
- `tests/unit/test_obligations_v17_api.py` (Deleted in `f3fc69d`)
- `tests/unit/test_frontend_contracts.py` (Assertions bypassed in Phase 5B to temporarily ignore the missing endpoints)

## 4. Root Cause Hypothesis
The agent performing Phase 4H appears to have mistakenly invoked a destructive command or wiped the local files before generating the final Phase 4H closure commit, resulting in a commit that removed the implementation instead of appending the new period lifecycle transition logic.

## 5. Commits Inspected
- `46b3f40` (Phase 5A baseline audit): Codebase was already empty.
- `f3fc69d` (Phase 4H closed): Git diff showed `app/obligations/router_v17.py | 343 ----------` indicating a full deletion.
- `a476bc1` (Phase 4G FIFO payment strategy): Git diff showed additive changes containing a full and valid implementation.

## 6. Healthy Commit Selected
Commit `a476bc1` was selected as the most recent healthy snapshot containing the correct V1.7 architecture up to Phase 4G.

## 7. Files Restored
The following files were securely restored from `a476bc1` using `git checkout`:
- `app/obligations/router_v17.py`
- `app/obligations/schemas_v17.py`
- `app/obligations/service_v17.py`
- `tests/unit/test_obligations_v17_api.py`

## 8. Test Bypass Reverted
The temporary bypasses applied to `test_frontend_contracts.py` during Phase 5B were reverted (`git checkout -- tests/unit/test_frontend_contracts.py`). The OpenAPI assertions verifying the presence of `ObligationV17Response` and V1.7 endpoints are active again.

## 9. Validation Results
- `tests/unit/test_obligations_v17_api.py`: PASSED (14 tests)
- `tests/unit/test_frontend_contracts.py`: PASSED
- `tests/`: Full suite PASSED (251 tests passed, 3 skipped).

## 10. Current State
Core Obligations V1.7 logic is restored up to Phase 4G. However, Phase 4H features (`skip period`, `cancel period`, `mark overdue`) were entirely lost in `f3fc69d` and do not exist. Phase 5B changes (FX engine) generated so far were untouched and remain present.

## 11. Production Safety
No production databases, services, or configurations were modified. The `NEXUM_OBLIGATIONS_V17_ENABLED` feature flag remains unactivated.

## 12. Recommendation
We strongly recommend committing this integrity restore to stabilize the project tree. Subsequently:
- The restore selected a476bc1 as the last healthy Core Obligations V1.7 implementation.
- This restores Core Obligations up to Phase 4G.
- Phase 4H lifecycle transitions must be reimplemented after this emergency restore.
- Phase 5B remains blocked until Phase 4H is restored and validated again.
