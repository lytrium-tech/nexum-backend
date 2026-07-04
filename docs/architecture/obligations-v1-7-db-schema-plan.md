# Nexum Core Obligations V1.7 — Phase 2.0 DB Schema Plan

## 1. Objective
Diseñar el esquema de base de datos exacto, determinando qué columnas reutilizar, cuáles crear y bajo qué restricciones, sin ejecutar migraciones aún. Se garantiza la continuidad operativa de V1.5 y la adaptabilidad sin conflicto hacia V1.7.

## 2. Mandatory Principles & Safety Rules
1. **No DROP TABLE:** Las tablas legacy permanecen.
2. **No DROP COLUMN:** Todo residuo se mantiene intacto o se reutiliza.
3. **No NOT NULL nuevo sin default/backfill:** Cualquier columna nueva debe ser `nullable=True` inicialmente.
4. **No constraint agresivo:** Evitar `UNIQUE` ciegos hasta que los datos sean migrados.
5. **V1.5 continua operando:** Inserciones V1.5 no deben fallar por falta de campos de V1.7.
6. **V1.5 dashboard intacto:** Las vistas legacy operan sobre las columnas estructurales de V1.5.
7. **Money = NUMERIC/DECIMAL:** Todo campo monetario es obligatoriamente `Numeric` (ej. `Numeric(18,4)`), no Float.
8. **Timestamps:** Toda tabla nueva usará `created_at` y `updated_at` (timestamptz).
9. **Idempotency:** Los pagos (writes) requieren `idempotency_key` con restricciones cuidadosas.
10. **FX Auditable:** Operaciones multimoneda persistirán origen, destino, tasa y proveedor.

## 3. Current Schema Inspection Summary
- **`obligations`**: Contiene la base legacy (`amount`, `is_active`) y columnas residuales V1.6 anuladas como nulas (`base_amount`, `type`, `start_date`, `first_due_date`, `status`, etc.).
- **`obligation_payments`**: Contiene `amount` (legacy), y residuos V1.6 reutilizables (`source_amount`, `source_currency`, `fx_rate`, `obligation_period_id`, `rate_source`).
- **`obligation_periods`**: **SÍ EXISTE** en la BD actual. Sobrevivió al rollback. Contiene: `id`, `obligation_id`, `period_key`, `sequence_number`, `start_date`, `end_date`, `due_date`, `amount`, `currency`, `paid_amount`, `status`, `created_at`, `updated_at`.
- **`financial_events`**: Existe, estructurada con `amount`, `currency`, `event_type`. Sin campos cross-currency explícitos (se apoya en `obligation_payments` o `metadata`).
- **`exchange_rates`, `fx_quotes`**: **No existen** en el snapshot de producción actual.

## 4. Obligations Column Strategy
**Tabla:** `public.obligations`
**Estrategia:** Reutilización híbrida aditiva.

*Conflicto `amount` vs `base_amount`:*
- V1.5 usa `amount`. V1.7 usará la columna residual `base_amount` junto con una nueva columna `amount_type`.
- Durante la transición, `base_amount` y `amount_type` serán opcionales a nivel de esquema (nullable), pero exigidas a nivel aplicación V1.7. `amount` seguirá existiendo para compatibilidad legacy.

*Uso de campos:*
- **Legacy V1.5 Existentes (Intactos):** `id`, `user_id`, `name`, `currency`, `payment_mode`, `amount`, `is_active`, `due_day`, `created_at`, `updated_at`.
- **Residuales V1.6 Reutilizables:** `type` (se mapea a `obligation_type`), `frequency`, `base_amount`, `start_date`, `end_date`, `end_count`, `status`.
- **Nuevas Columnas V1.7 (Alembic):**
  - `amount_type` (text, nullable).
  *Justificación:* V1.7 necesita distinguir fixed vs variable de forma consultable en DB. Derivarlo de `base_amount` no es confiable. Guardarlo en `metadata` dificulta queries. Será requerido lógicamente en V1.7 (ej. para fixed, `amount_type='fixed'` y `base_amount` tiene valor; para variable, `amount_type='variable'` y `base_amount` puede ser null).

## 5. Obligation Periods Strategy
**Tabla:** `public.obligation_periods` (TABLA EXISTENTE)
**Estrategia:** Reutilizar y enriquecer la tabla que sobrevivió al rollback.

*Estructura y Mapeo:*
- La tabla ya tiene: `id`, `obligation_id`, `period_key` (tiene `UNIQUE INDEX uq_obligation_period_key`), `sequence_number`, `start_date`, `end_date`, `due_date`, `amount` (corresponde a `amount_due`), `paid_amount`, `currency`, `status`, `created_at`, `updated_at`.
- **Ajustes Aditivos (Alembic):**
  - Agregar columna `is_current` (boolean, nullable o default false).
  - Renombrar conceptualmente en ORM `amount` -> `amount_due`. En base de datos puede seguir llamándose `amount`.

*Índices Recomendados (Alembic):*
- `(obligation_id, status)`
- `(obligation_id, due_date)`
- `(user_id, status)` (Considerar agregar `user_id` a la tabla si no existe, o manejarlo con JOIN).

## 6. Obligation Payments Strategy
**Tabla:** `public.obligation_payments`
**Estrategia:** Reutilizar y enriquecer residuos.

*Uso de campos:*
- **Existentes/Residuales V1.6:** `id`, `obligation_id`, `obligation_period_id` (FK), `amount` (legacy target amount), `currency`, `source_amount`, `source_currency`, `fx_rate`, `rate_source` (fx_provider), `rate_timestamp`, `account_id`, `event_id`, `user_id`.
- **Nuevas Columnas V1.7 (Alembic):**
  - `quote_id`: uuid (nullable, FK a `fx_quotes` si se crea).
  - `idempotency_key`: text/character varying (nullable para no romper V1.5).

*Índices Recomendados (Alembic):*
- `(obligation_id)`
- `(obligation_period_id)`
- `(user_id, created_at)`
- `CREATE UNIQUE INDEX ix_obligation_payments_user_idempotency ON public.obligation_payments (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;`

## 7. Financial Events Strategy
**Tabla:** `public.financial_events`
**Estrategia:** Mantener intacta, auditar en `metadata` temporalmente.
La tabla tiene `amount`, `currency`. Para cross-currency estricto, dado que `obligation_payments` ya almacena `source_amount`, `target_amount` (`amount`), y `fx_rate`, se documentarán los ratios en el campo `metadata` (JSONB) del `financial_event` para no alterar masivamente el core del Ledger en esta fase.
Adicionalmente, se puede añadir `idempotency_key` a `financial_events` con índice único parcial.

## 8. FX Quotes Recommendation & Exchange Rates
**Nuevas Tablas (Alembic Fase 2):**

1. **`exchange_rates`**
- `id`: uuid primary key
- `base_currency`: text
- `quote_currency`: text
- `rate`: numeric(18,8)
- `provider`: text
- `fetched_at`: timestamptz
- `expires_at`: timestamptz
- `is_stale`: boolean default false
- `metadata`: jsonb
- `created_at`: timestamptz
- `updated_at`: timestamptz
- *Índices:* `(base_currency, quote_currency, provider)`, `(expires_at)`.

2. **`fx_quotes`** (Recomendado crearla desde Fase 2)
- *Justificación:* Habilita idempotencia total y garantiza UX de FX quotes persistentes.
- `id`: uuid primary key
- `user_id`: uuid
- `from_currency`: text
- `to_currency`: text
- `source_amount`: numeric(18,4)
- `target_amount`: numeric(18,4)
- `rate`: numeric(18,8)
- `provider`: text
- `rate_timestamp`: timestamptz
- `expires_at`: timestamptz
- `status`: text (active/expired/used)
- `idempotency_key`: text
- `metadata`: jsonb
- `created_at`: timestamptz
- `updated_at`: timestamptz
- *Índices:*
  - `(user_id, status)`
  - `(expires_at)`
  - `CREATE UNIQUE INDEX ix_fx_quotes_user_idempotency ON public.fx_quotes (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;`

## 9. Views / Intelligence Strategy
**Afectación a Vistas:** Fase 2 no modifica `v_pending_obligations_current_month`.
**Estrategia:** Cualquier vista V1.7 o ajuste intelligence se posterga a Fase 5. Durante la Fase 2 (Migraciones), las vistas V1.5 no se rompen porque no hay `DROP COLUMN` ni cambios restrictivos (V1.5 sigue consumiendo `amount` e `is_active`).

## 10. Migration Safety Strategy
El plan de Alembic para la Fase 2 se rige por:
- `upgrade()`: Solo sentencias `op.create_table`, `op.add_column`, `op.create_index`. Ningún `op.execute("UPDATE ...")` de datos masivos. Ningún `DROP` ni `ALTER COLUMN SET NOT NULL`.
- `downgrade()`: El downgrade estructural puede revertir tablas/columnas nuevas (`op.drop_table`, `op.drop_column`), **pero si V1.7 ya escribió datos, ejecutar downgrade puede eliminar datos V1.7**. Por eso, en producción el rollback primario será Runtime Reversibility mediante feature flag OFF. Downgrade estructural solo se usará antes de writes reales o en entornos controlados de desarrollo/staging.

## 11. Backfill Strategy (Futuro)
Una vez implementado V1.7 y validado detrás del Feature Flag:
1. `UPDATE obligations SET base_amount = amount, type = 'recurring' WHERE base_amount IS NULL;`
2. Sintetizar `obligation_periods` a partir de `obligations` legacy activas para arrancar el motor de periodos.
3. Consolidar estados en los pagos históricos si se requieren.
4. Tras un QA total, ejecutar migración final (Fase 9) para establecer `ALTER COLUMN base_amount SET NOT NULL` sobre las nuevas columnas, limpiando la deuda técnica de V1.5. Ningún `NOT NULL` se aplicará en la Fase 2 actual.
