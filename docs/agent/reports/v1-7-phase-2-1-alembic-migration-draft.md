# Nexum V1.7 Phase 2.1 — Alembic Migration Draft

## 1. Executive Summary
Se revisó exhaustivamente el borrador de la migración V1.7 Phase 2.1. El archivo contiene la estructura sintáctica válida para una migración Alembic (revision, upgrade, downgrade), sin embargo, el proyecto carece de una inicialización nativa de Alembic (`alembic.ini`) o entorno local disponible configurado para ejecutar upgrades desechables de manera directa con CLI estándar. Los tests pasaron sin regresiones en V1.5.

## 2. Files Created/Changed
- `scripts/migration/alembic_v17_phase2_draft.py` (Script de migración).

## 3. Migration Contents
- **`obligations`**: Agregada columna `amount_type` (text, nullable).
- **`obligation_periods`**: Agregada columna `is_current` (boolean, nullable con default falso), e índices `(obligation_id, status)` y `(obligation_id, due_date)`.
- **`obligation_payments`**: Agregadas columnas `quote_id` e `idempotency_key` (text, nullable). Agregados índices únicos parciales `WHERE idempotency_key IS NOT NULL`.
- **`exchange_rates` / `fx_quotes`**: Creadas nuevas tablas aditivas con money types precisos (`Numeric(18,4)` / `Numeric(18,8)`).

## 4. Safety Review
- **No DROP TABLE / DROP COLUMN / ALTER SET NOT NULL** en el upgrade.
- Todas las columnas nuevas son nullable o tienen defaults seguros.
- Compatibilidad absoluta con `V1.5`.
- El downgrade revierte adecuadamente pero documenta pérdida de datos V1.7 si se ejecuta posterior a escrituras reales.

## 5. Local/Test Validation
- **Alembic Path:** El archivo no está en un entorno configurado (`alembic/versions`), y no hay `alembic.ini`. Es un draft estructurado.
- **Local DB:** No se encontró un comando o base local desechable para aplicar directamente `alembic upgrade`.
- **Tests ejecutados (`.venv\Scripts\pytest tests/ -v`):** 
  - Resultado: `232 passed, 1 failed`
  - Fallo: `test_openapi_contains_v16_fields` (preexistente, no relacionado: FileNotFound/DecodeError de `openapi.json`).
  - Bloquea Fase 2: **No**.

## 6. Recommendation
- **Commit:** Sí. Es un draft perfectamente válido estructuralmente que el equipo podrá integrar a su workflow real.
- **Siguiente paso imperativo:** Antes de avanzar con código funcional, el equipo de plataforma o el arquitecto debe configurar el pipeline de base de datos desechable / staging (`docker-compose` setup local con DB seed) y proveer las herramientas nativas de Alembic.
