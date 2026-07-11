# 05 — Database and Migrations

## Base de Datos
- **Motor:** PostgreSQL, alojado en Supabase.
- **Acceso:** Asíncrono mediante `SQLAlchemy (asyncio)`.

## Alembic
El control de esquemas y versiones de la base de datos está gestionado por **Alembic**. Todo cambio en la estructura (`models.py`) debe acompañarse de su respectiva migración en la carpeta `alembic/versions/`.

## Estado Actual de Migraciones Relevantes
En la versión V1.7 / iteración V1.8, el estado del head de migraciones incluye cambios acumulativos:
- Modificaciones a Obligation (ej. enum updates `v1_7_phase2_1`).
- FX Rate Snapshot schema updates (`v1_7_phase_fx_rate_snapshot`).
- Las migraciones han adaptado el tipo UUID e introducido campos críticos como `rate_snapshot_id`.

## Política de Migraciones
- **Migraciones Aditivas:** Se prefieren cambios de esquema que no rompan código productivo antiguo ("Additive Migrations"), por ejemplo, añadir columnas nullable o campos enum adicionales.
- **Autogeneración:** Se recomienda el workflow con base en scripts (ej. `scripts/migration/alembic.py revision --autogenerate -m "..."`).
- **Seguridad:** *NUNCA* se debe mutar el esquema de producción de Supabase directamente desde el panel de control manual (Dashboard SQL). Toda alteración pasa por un proceso revisado y el comando `alembic upgrade head`.
- **Backups:** Es obligatorio ejecutar un backup pre-migración antes de aplicar comandos de base de datos contra producción.

---
*Last verified against `367ebdc`*