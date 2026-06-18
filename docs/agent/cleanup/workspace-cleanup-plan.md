# Nexum Backend Workspace Organization Plan

## 1. Purpose

This plan defines the target backend workspace organization before deleting old files.

Current phase:

- Organize by folder responsibility.
- Create permanent agent rules in `AGENTS.md`.
- Separate automatic rules from living context.
- Keep backend application code untouched.
- Do not delete files yet.
- Do not commit or push yet.

## 2. Core Principle

The backend workspace must separate these concerns:

```text
AGENTS.md       -> lightweight permanent rules automatically read by agents
docs/context/   -> living project context, updated when phases close
docs/agent/     -> generated agent outputs: reports, audits, cleanup notes
docs/project/   -> stable backend documentation
docs/architecture/ -> technical architecture references
scripts/        -> executable helper scripts grouped by purpose
app/            -> backend application code, not touched during workspace cleanup
```

## 3. Target Structure

```text
backend/
  AGENTS.md
  app/
  tests/
  scripts/
    db/
    dev/
    migration/
    smoke/
    review-required/
  docs/
    context/
    agent/
      audits/
      cleanup/
      reports/
      sprint-reports/
    project/
    architecture/
    archive/
    sql_snapshots/
  README.md
  pyproject.toml
  uv.lock
  Dockerfile
  docker-compose.yml
  .env.example
  .gitignore
  openapi.json
```

## 4. `AGENTS.md`

`AGENTS.md` must exist in the backend root.

Purpose:

- Permanent project rules for Antigravity and other agents.
- Lightweight enough to be read automatically every time.
- Contains restrictions, permissions, and mandatory workflows.
- Does not contain long project history, sprint reports, or detailed architecture.

Rules to include:

```text
Work only inside backend.
Do not leave C:\Users\Lytrium\Documents\Projects\Nexum\backend.
Do not modify app/ unless explicitly requested.
Do not delete files without approval.
Do not run migrations without approval.
Do not deploy without approval.
Do not git add, commit, or push without approval.
Use docs/context/ for living project context.
Use docs/agent/ for generated reports and audits.
Update docs/context/ after closing a phase.
Mark unknown files as REVIEW_REQUIRED instead of guessing.
```

What must not go in `AGENTS.md`:

```text
Large roadmap details.
Sprint reports.
Long architecture explanations.
Temporary cleanup inventories.
SQL snapshots.
Agent-generated reports.
```

## 5. `docs/context/`

`docs/context/` stores living context that agents cite when needed.

Recommended files:

```text
docs/context/AGENT_CONTEXT.md
docs/context/BACKEND_STRUCTURE.md
docs/context/BACKEND_RULES.md
docs/context/BACKEND_CURRENT_STATE.md
docs/context/BACKEND_PHASES.md
docs/context/NEXUM_BACKEND_V1_1_ROADMAP.txt
```

Purpose:

- Explain how the backend works.
- Explain the official folder structure.
- Track current backend state.
- Track current/closed phases.
- Provide operational context for agents.

Mandatory update rule:

- After a phase closes, update `docs/context/BACKEND_CURRENT_STATE.md`.
- If the phase changes project structure, update `docs/context/BACKEND_STRUCTURE.md`.
- If workflow rules change, update `AGENTS.md` only if the rule is permanent and lightweight.

What must not go here:

```text
One-off reports.
Temporary audits.
Scratch notes.
Local credentials.
Generated logs.
```

## 6. `docs/agent/`

`docs/agent/` stores generated agent output.

Recommended subfolders:

```text
docs/agent/audits/
docs/agent/cleanup/
docs/agent/reports/
docs/agent/sprint-reports/
```

Classification:

```text
docs/agent/audits/         -> audit documents and investigation results
docs/agent/cleanup/        -> workspace cleanup and organization plans
docs/agent/reports/        -> general generated reports
docs/agent/sprint-reports/ -> sprint closure reports
```

Current files to classify:

```text
docs/agent/BACKEND_MVP_CLOSURE_REPORT.md -> docs/agent/reports/
docs/agent/BACKEND_V1_1_SPRINT_1_FINANCIAL_TRUTH_REPORT.md -> docs/agent/sprint-reports/
docs/agent/BACKEND_V1_1_SPRINT_2_CATEGORIES_LIFECYCLE_REPORT.md -> docs/agent/sprint-reports/
docs/agent/BACKEND_V1_1_SPRINT_3_GOALS_CONSISTENCY_REPORT.md -> docs/agent/sprint-reports/
docs/agent/BACKEND_V1_1_SPRINT_4_OBLIGATIONS_REPORT.md -> docs/agent/sprint-reports/
docs/agent/BACKEND_V1_1_SPRINT_5_CREDIT_SEMANTICS_AUDIT.md -> docs/agent/audits/
docs/agent/BACKEND_V1_1_SPRINT_5_CREDIT_SEMANTICS_REPORT.md -> docs/agent/sprint-reports/
docs/agent/NEXUM_BACKEND_WORKSPACE_CLEANUP_PLAN.md -> docs/agent/cleanup/
```

Important:

- `docs/agent/` may stay ignored if generated reports should not be committed.
- If any agent report must be committed, add an explicit `.gitignore` exception for that file or subfolder.
- Do not store permanent context here; permanent context belongs in `docs/context/`.

## 7. `docs/project/`

Stable backend documentation.

Move these files here:

```text
docs/00-backend-overview.md -> docs/project/00-backend-overview.md
docs/01-architecture.md -> docs/project/01-architecture.md
docs/02-domain-map.md -> docs/project/02-domain-map.md
docs/03-api-contracts.md -> docs/project/03-api-contracts.md
docs/04-auth-flow.md -> docs/project/04-auth-flow.md
docs/05-database-integration.md -> docs/project/05-database-integration.md
docs/06-conversations-and-ai.md -> docs/project/06-conversations-and-ai.md
docs/07-observability.md -> docs/project/07-observability.md
docs/08-deployment.md -> docs/project/08-deployment.md
docs/09-testing.md -> docs/project/09-testing.md
docs/10-known-issues.md -> docs/project/10-known-issues.md
docs/11-frontend-handoff.md -> docs/project/11-frontend-handoff.md
docs/12-backend-changelog.md -> docs/project/12-backend-changelog.md
```

Purpose:

- Product/backend documentation.
- API contracts.
- Domain map.
- Auth flow.
- Testing docs.
- Deployment docs.
- Frontend handoff.

## 8. `docs/architecture/`

Rename Spanish folder for consistency:

```text
docs/arquitectura/ -> docs/architecture/
```

Initial mapping:

```text
docs/arquitectura/database-schema.sql -> docs/architecture/database-schema.sql
docs/arquitectura/agent-context.md -> REVIEW_REQUIRED or merge into docs/context/
docs/arquitectura/financial-engine-archive.md -> docs/archive/architecture/financial-engine-archive.md after review
```

Rules:

- Do not execute SQL files.
- Do not delete archive-like architecture docs yet.
- Merge context documents into `docs/context/` only after review.

## 9. `docs/sql_snapshots/`

Keep for now.

Purpose:

- Historical SQL snapshots.
- Rollback/reference material.

Rules:

- Do not execute.
- Do not delete.
- Do not move to archive until migration history is reviewed.

Future candidate:

```text
docs/sql_snapshots/ -> docs/archive/sql_snapshots/
```

## 10. `scripts/`

Scripts may be reorganized by purpose if they are not application code.

Target structure:

```text
scripts/db/
scripts/dev/
scripts/migration/
scripts/smoke/
scripts/review-required/
```

Move candidates:

```text
scripts/check_cols.py -> scripts/db/check_cols.py
scripts/check_cols_2.py -> scripts/db/check_cols_2.py
scripts/check_db.py -> scripts/db/check_db.py
scripts/check_db2.py -> scripts/db/check_db2.py
scripts/check_fe.py -> scripts/db/check_fe.py
scripts/check_view.py -> scripts/db/check_view.py
scripts/cleanup_dev.py -> scripts/dev/cleanup_dev.py
scripts/seed_dev.py -> scripts/dev/seed_dev.py
scripts/apply_migration.py -> scripts/migration/apply_migration.py
scripts/migrate_c4.py -> scripts/migration/migrate_c4.py
scripts/migrate_v11.py -> scripts/migration/migrate_v11.py
scripts/migrate_v11_sprint2.py -> scripts/migration/migrate_v11_sprint2.py
scripts/migrate_v11_sprint4.py -> scripts/migration/migrate_v11_sprint4.py
scripts/migrate_v11_sprint5.py -> scripts/migration/migrate_v11_sprint5.py
scripts/smoke_*.py -> scripts/smoke/
```

Rules:

- Do not execute scripts during organization.
- Do not delete scripts during this phase.
- If imports, docs, or commands reference old paths, update only documentation or command references after review.
- If a script is required for backend operation, keep it under the correct `scripts/` subfolder.
- If a script is one-off or unclear, move it to `scripts/review-required/` only after approval.

## 11. Root Files

Allowed root files:

```text
AGENTS.md
README.md
pyproject.toml
uv.lock
Dockerfile
docker-compose.yml
.env.example
.gitignore
openapi.json
```

Allowed root folders:

```text
app/
tests/
scripts/
docs/
```

Root files that should not remain in root if present:

```text
append_test.py
fix.py
fix_clarification.py
fix_smokes.py
recover.py
scratch_check_db.py
scratch_*.py
recover*.py
fix_*.py
```

Classification for non-root files if present:

```text
append_test.py -> REVIEW_REQUIRED; likely old test mutation helper
fix.py -> REVIEW_REQUIRED; likely one-off repair helper
fix_clarification.py -> REVIEW_REQUIRED; likely one-off smoke repair helper
fix_smokes.py -> REVIEW_REQUIRED; likely one-off smoke repair helper
recover.py -> REVIEW_REQUIRED; likely local recovery helper, do not commit
scratch_check_db.py -> REVIEW_REQUIRED; likely local DB scratch helper, do not commit
scratch_*.py -> REVIEW_REQUIRED; do not commit by default
recover*.py -> REVIEW_REQUIRED; do not commit by default
fix_*.py -> REVIEW_REQUIRED unless promoted into scripts/review-required/
```

Decision rule:

- If used and safe, move to the right `scripts/` subfolder.
- If one-off, obsolete, or unsafe, keep ignored and propose deletion in the later cleanup phase.
- Do not delete in this organization phase.

## 12. `app/`

Application backend code.

Rules:

- Do not move.
- Do not rename.
- Do not modify during workspace organization.
- No documentation files here.
- No agent reports here.
- No scratch scripts here.

## 13. `tests/`

Automated test suite.

Rules:

- Do not move tests during this phase.
- Do not delete tests.
- Do not mix smoke scripts into `tests/` without review.
- Keep manual smoke scripts under `scripts/smoke/`.

## 14. Git And Upload Policy

Before any future commit:

```bash
git status --short
git diff
git ls-files docs/agent
git ls-files docs/codex
git ls-files "scratch_*.py"
git ls-files "recover*.py"
git ls-files "fix*.py"
```

Files that should generally not be uploaded:

```text
.env
.venv/
.pytest_cache/
.ruff_cache/
__pycache__/
docs/codex/
local .agents credentials/configs
scratch_*.py
recover*.py
one-off fix*.py unless reviewed
generated logs/artifacts
```

Files that should be uploaded if approved:

```text
AGENTS.md
backend source under app/
tests/
reviewed scripts/
README.md
pyproject.toml
uv.lock
Dockerfile
docker-compose.yml
.env.example
.gitignore
```

## 15. Execution Order

Phase A: rules and plan.

```text
Create AGENTS.md.
Update workspace organization plan.
Do not move or delete application files.
```

Phase B: create folders.

```text
docs/context/
docs/agent/audits/
docs/agent/cleanup/
docs/agent/reports/
docs/agent/sprint-reports/
docs/project/
docs/architecture/
docs/archive/
scripts/db/
scripts/dev/
scripts/migration/
scripts/smoke/
scripts/review-required/
```

Phase C: move documentation by purpose.

Phase D: move scripts by purpose.

Phase E: create context docs.

Phase F: review old/unclear files.

Phase G: propose deletions.

Phase H: commit only approved backend and documentation files.

## 16. Do Not Do Yet

```text
Do not delete files.
Do not run migrations.
Do not deploy.
Do not git add.
Do not commit.
Do not push.
Do not modify app/.
Do not modify tests unless explicitly requested.
Do not leave backend folder.
```

## 17. Current Notes

- `AGENTS.md` did not exist before this update.
- `GEMINI.md` did not exist before this update.
- Current `.gitignore` ignores `docs/agent/`, so generated agent reports remain local unless explicit exceptions are added.
- Current `.gitignore` ignores temporary root scripts such as `append_test.py`, `fix.py`, `fix_*.py`, `recover*.py`, and `scratch_*.py`.
- There were pre-existing working tree changes not made by this plan update: `.gitignore` modified and `docs/SPRINT_1_FINANCIAL_TRUTH_AUDIT.md` deleted.
