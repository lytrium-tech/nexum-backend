# Nexum Backend Structure

This document describes the backend workspace organization.

## Root

```text
backend/
  AGENTS.md
  app/
  docs/
  scripts/
  tests/
  README.md
  pyproject.toml
  uv.lock
  Dockerfile
  docker-compose.yml
  .env.example
  .gitignore
  openapi.json
```

## Application Code

`app/` contains backend application code and domain modules. Do not move or modify it during workspace organization unless explicitly requested.

## Tests

`tests/` contains automated backend tests. Manual smoke scripts belong in `scripts/smoke/`, not in `tests/`.

## Scripts

```text
scripts/db/             Database inspection and diagnostic scripts.
scripts/dev/            Local development utilities.
scripts/migration/      Migration and SQL application helpers. Do not execute without approval.
scripts/smoke/          Manual or semi-manual smoke checks.
```

Common command paths changed with the workspace organization:

```text
scripts/check_db.py -> scripts/db/check_db.py
scripts/seed_dev.py -> scripts/dev/seed_dev.py
scripts/cleanup_dev.py -> scripts/dev/cleanup_dev.py
scripts/smoke_*.py -> scripts/smoke/smoke_*.py
scripts/migrate_*.py -> scripts/migration/migrate_*.py
```

## Documentation

```text
docs/context/       Living project context for agents and maintainers.
docs/project/       Stable backend documentation.
docs/architecture/  Technical architecture references.
docs/agent/         Generated agent outputs grouped by type.
docs/archive/       Historical documentation retained for traceability.
docs/sql_snapshots/ Historical SQL snapshots. Do not execute directly.
```

## Agent Output

```text
docs/agent/audits/         Audits and investigation outputs.
docs/agent/cleanup/        Cleanup and organization plans.
docs/agent/reports/        General generated reports.
docs/agent/sprint-reports/ Sprint closure reports.
```
