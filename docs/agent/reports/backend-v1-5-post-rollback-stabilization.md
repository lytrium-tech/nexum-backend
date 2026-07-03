# Backend V1.5 Post-Rollback Stabilization Report

## Contexto
Tras el rollback a la versión V1.5 (commit `ca7b165`), el frontend comenzó a reportar errores 500 al cargar el Dashboard (`/api/v1/intelligence/snapshot`) y el módulo de Obligations (`/api/v1/obligations`).

## Diagnóstico
El análisis del entorno de producción reveló que la base de datos de la VPS **mantenía el esquema de V1.6** para las tablas `obligations` y `obligation_payments`, debido a que las migraciones `down` no fueron ejecutadas antes de revertir el código a V1.5.

Consecuencias detectadas:
1. **`obligations`**: Faltaban las columnas `amount` e `is_active` (introducidas en V1.5 pero eliminadas/reemplazadas en V1.6 por `base_amount` y `status`). Esto causaba un `UndefinedColumnError` en los repositorios de V1.5.
2. **`v_pending_obligations_current_month`**: La vista fue eliminada en V1.6 y V1.5 dependía críticamente de ella para el snapshot de inteligencia.
3. **`obligation_payments`**: La columna `period` fue eliminada en V1.6 (reemplazada por `obligation_period_id`), lo que rompía las queries de pagos de V1.5.

## Resolución Aplicada (Fijación de Esquema)
Para estabilizar V1.5 sin pérdida de datos ni dependencias de migraciones V1.6 huérfanas, se aplicó una reconstrucción hacia atrás (backward compatibility) restaurando los objetos nativos de V1.5:

1. **Restauración de columnas en `obligations`**:
   ```sql
   ALTER TABLE obligations ADD COLUMN IF NOT EXISTS amount NUMERIC;
   ALTER TABLE obligations ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true;
   UPDATE obligations SET amount = base_amount WHERE amount IS NULL;
   UPDATE obligations SET is_active = (status = 'active') WHERE status IS NOT NULL;
   ```

2. **Restauración de columnas en `obligation_payments`**:
   ```sql
   ALTER TABLE obligation_payments ADD COLUMN IF NOT EXISTS user_id UUID;
   ALTER TABLE obligation_payments ADD COLUMN IF NOT EXISTS period TEXT;
   ALTER TABLE obligation_payments ADD COLUMN IF NOT EXISTS event_id UUID;
   ALTER TABLE obligation_payments ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
   ALTER TABLE obligation_payments ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;
   
   UPDATE obligation_payments op SET user_id = (SELECT user_id FROM obligations o WHERE o.id = op.obligation_id) WHERE op.user_id IS NULL;
   UPDATE obligation_payments op SET period = to_char(paid_at AT TIME ZONE 'America/Bogota', 'YYYY-MM') WHERE op.period IS NULL;
   UPDATE obligation_payments op SET event_id = financial_event_id WHERE op.event_id IS NULL AND financial_event_id IS NOT NULL;
   ```

3. **Restauración de la vista `v_pending_obligations_current_month`**:
   ```sql
   CREATE OR REPLACE VIEW public.v_pending_obligations_current_month AS
   SELECT 
       o.id AS obligation_id,
       o.user_id,
       o.name,
       o.amount,
       o.currency,
       o.category_id,
       NOT EXISTS (
           SELECT 1 
           FROM public.obligation_payments op 
           WHERE op.obligation_id = o.id 
           AND op.period = to_char(now() AT TIME ZONE 'America/Bogota', 'YYYY-MM')
       ) AS is_pending
   FROM public.obligations o
   WHERE o.is_active = true;
   ```

## Resultados y Validación
- **Intelligence Snapshot (`/api/v1/intelligence/snapshot`)**: Validado internamente en la VPS (Retorna 200 OK con métricas completas).
- **Obligations List (`/api/v1/obligations`)**: Validado internamente en la VPS (Retorna 200 OK).
- **Consistencia V1.5**: El código en `ca7b165` ahora puede leer y escribir contra la base de datos sin fricción. No se eliminaron datos históricos de V1.6, simplemente se recrearon las columnas y vistas esperadas por el ORM de V1.5.

El entorno de producción ha recuperado la estabilidad completa para Nexum V1.5.
