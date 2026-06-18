# Nexum Backend Current State

## Workspace Organization

The backend workspace has been reorganized by responsibility before committing cleanup changes.

Current organization decisions:

- `AGENTS.md` stores lightweight permanent agent rules.
- `docs/context/` stores living backend context and must be versioned.
- `docs/agent/` stores generated reports, audits, cleanup notes, and sprint reports in approved subfolders.
- `docs/project/` stores stable backend documentation.
- `docs/architecture/` stores technical architecture references.
- `scripts/` is grouped into `db`, `dev`, `migration`, and `smoke`.

## Safety Boundaries

- Application code under `app/` was not reorganized in this phase.
- Automated tests under `tests/` were not reorganized in this phase.
- Scripts were moved and selected obsolete helpers were removed, but no scripts were executed.
- SQL snapshots were kept in place and not executed.
- `scripts/smoke/smoke_intelligence.py` now imports `cleanup` from `scripts/dev/cleanup_dev.py`.

## Current Sprint Status

- **Sprint 5 (Credit Semantics)**: Completed and validated.
- **Sprint 6**: Audited and confirmed as NOT_STARTED. No implementation or technical scope exists yet.
