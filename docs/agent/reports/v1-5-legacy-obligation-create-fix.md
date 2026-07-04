# V1.5 Legacy Obligation Create Fix

## 1. Executive Summary
La creación de obligaciones legacy falla con un error 500 (Internal Server Error) en el entorno de producción (V1.5). El diagnóstico revela que el esquema de la base de datos de V1.6 dejó columnas con restricciones `NOT NULL` que bloquean los `INSERT` generados por el backend V1.5.

## 2. Runtime Error
Al intentar crear una obligación mediante el endpoint `POST /api/v1/obligations` usando el esquema de V1.5, se produce el siguiente error:
```
<class 'sqlalchemy.exc.IntegrityError'> (sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class 'asyncpg.exceptions.NotNullViolationError'>: null value in column "base_amount" of relation "obligations" violates not-null constraint
```

## 3. Root Cause
El rollback a V1.5 retrocedió el código, pero la base de datos conservó restricciones `NOT NULL` de V1.6. El ORM de V1.5 no incluye los campos de V1.6 en su consulta `INSERT`, provocando que Postgres rechace la inserción al violar las restricciones `NOT NULL` de las columnas huérfanas de V1.6 (como `base_amount`, `type`, `start_date`).

## 4. DB Schema Findings
Se identificaron las siguientes columnas en V1.6 que poseen la restricción `NOT NULL` sin un valor por defecto (default `None`), bloqueando así cualquier inserción desde V1.5:

**Tabla `obligations`:**
- `base_amount`
- `type`
- `start_date`
- `first_due_date`
- `status`
- `frequency` (V1.5 la permite nula en su modelo)

**Tabla `obligation_payments`:**
- `obligation_period_id`
- `currency`
- `source_amount`
- `source_currency`

## 5. Contract Findings
- El payload del frontend envía los campos legacy esperados para V1.5 (`name`, `amount`, `currency`, `frequency`, `due_day`, `payment_mode`).
- El backend procesa correctamente este payload mediante el esquema `ObligationCreate` de V1.5 y lo mapea al modelo SQLAlchemy de V1.5.
- No hay desajuste (mismatch) de contrato entre frontend V1.5 y backend V1.5; el problema es exclusivamente el bloqueo del motor SQL por los constraints de V1.6.

## 6. Fix Applied
**Ejecutado con Aprobación.**
Previa creación de un backup de la base de datos (con `pg_dump`) en la ruta `/opt/backups/nexum/20260704-055600-pre-v15-obligation-not-null-fix`, se ejecutaron los comandos de alteración de base de datos directamente en producción usando SQLAlchemy interactivo:

```sql
-- Fix obligations
ALTER TABLE public.obligations ALTER COLUMN base_amount DROP NOT NULL;
ALTER TABLE public.obligations ALTER COLUMN type DROP NOT NULL;
ALTER TABLE public.obligations ALTER COLUMN start_date DROP NOT NULL;
ALTER TABLE public.obligations ALTER COLUMN first_due_date DROP NOT NULL;
ALTER TABLE public.obligations ALTER COLUMN status DROP NOT NULL;
ALTER TABLE public.obligations ALTER COLUMN frequency DROP NOT NULL;

-- Fix obligation_payments
ALTER TABLE public.obligation_payments ALTER COLUMN obligation_period_id DROP NOT NULL;
ALTER TABLE public.obligation_payments ALTER COLUMN currency DROP NOT NULL;
ALTER TABLE public.obligation_payments ALTER COLUMN source_amount DROP NOT NULL;
ALTER TABLE public.obligation_payments ALTER COLUMN source_currency DROP NOT NULL;
```

Estas columnas V1.6 residuales ahora son anulables, permitiendo que el ORM de V1.5 inserte registros de manera nativa sin ser bloqueado.

## 7. Validation
- **Backup DB**: Validado (dump SQL pesa 5.9MB).
- **Crear Obligación (`POST /api/v1/obligations`)**: Validado, retorna ID `c89dc5ff...` exitosamente sin error 500.
- **Obtener Obligaciones (`GET /api/v1/obligations`)**: Retorna lista de obligaciones (Status 200 OK).
- **Dashboard (`GET /api/v1/intelligence/snapshot`)**: Validado previamente, sin disrupciones tras el cambio (Status 200 OK).
- **Frontend**: Los errores 500 han desaparecido y el frontend permite crear obligaciones satisfactoriamente.
- **Logs (VPS)**: Limpios, ningún error `NotNullViolationError` ni `UndefinedColumnError`.

## 8. Remaining Risks
Los registros creados durante V1.5 tendrán estas columnas V1.6 en estado `NULL`. Cuando se intente un futuro rollout a V1.6, se deberá ejecutar una migración de datos (data migration) que rellene estos valores nulos (`base_amount = amount`, etc.) antes de poder restablecer las restricciones `NOT NULL`.

## 9. Final State
- Frontend: Rollback completado a V1.5.
- Backend: Rollback completado a V1.5 (commit ca7b165).
- Base de datos: Funcional y estabilizada para la creación y lectura de obligaciones y pagos mediante V1.5 legacy.
- Estado: **Fix ejecutado y validado con éxito. Todo operativo.**
