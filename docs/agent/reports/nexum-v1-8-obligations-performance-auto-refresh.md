# Nexum V1.8 — Obligations Performance Auto-Refresh

## Resumen Ejecutivo

Se implementó con éxito la optimización de rendimiento para el auto-refresh de Obligaciones en V1.8. Se eliminó el problema de N+1 consultas (queries) en endpoints de lectura como `get_summary` al reemplazar el ciclo de `refresh_overdue_periods` por una nueva función agregada y segura: `refresh_due_periods_for_user`. 

Se optó por la **Opción B (Batch refresh por usuario)** sin necesidad de introducir nuevas migraciones de base de datos en esta fase, utilizando comprobaciones de secuencias en memoria como un guardián de obsolescencia (staleness guard) funcional.

## Endpoints Afectados
- `GET /api/v1.7/obligations/summary` (`get_summary`)

*(Nota: `list_periods_for_obligation` sigue usando el refresh individual puntual, ya que O(1) obligación = O(1) consultas, por lo cual es eficiente. El problema N+1 afectaba únicamente a resúmenes y listados de usuario.)*

## Cambios Implementados Frente al Plan Original

El plan fue aprobado y ejecutado, pero con las siguientes correcciones de riesgos requeridas:

1. **Gap Handling**: En lugar de consultar `MAX(sequence_number)` (lo cual ignoraba gaps si faltaban periodos intermedios), el sistema ahora consulta **todas las secuencias existentes** para las obligaciones activas en un solo query, y construye un mapa en memoria de secuencias existentes `existing_seqs_map`. El ciclo de generación compara contra este mapa para crear cualquier periodo faltante.
2. **Duplicate / Concurrency Handling**: Se envolvió la inserción masiva (`session.add_all(new_periods)`) dentro de un bloque `async with self.session.begin_nested()` interceptando `IntegrityError`. Si un request concurrente inserta los mismos periodos (violando la restricción `uq_obligation_period_key`), el backend simplemente desecha los periodos de la sesión de manera controlada y permite que la operación general continúe sin fallar.
3. **`is_current` Handling**: Se auditó `PeriodEngine._ensure_period_exists` y los modelos V1.5/V1.7. Al generar periodos futuros/pasados mediante batch, no asignamos explícitamente `is_current=True` para prevenir que existan múltiples en estado true o que colisionen. Dejamos que los defaults y consultas por `due_date`/`status` sigan rigiendo como en V1.7.
4. **Fixed / Variable Logic**: Se reutilizó fielmente la lógica de `PeriodEngine._ensure_period_exists`. Las obligaciones variables se inicializan con `amount=None` y status `pending_amount_definition`. Las obligaciones fijas se inicializan con `base_amount` y `pending_payment` (o `overdue` si `due_date < today`).
5. **Transaction Handling**: El método `refresh_due_periods_for_user` expone un flag `commit: bool = True`. Al inyectarlo en `get_summary`, evitamos exponer transacciones rotas y documentamos explícitamente el uso de `begin_nested()` para el manejo de inserciones atómicas sobre operaciones de mayor alcance.

## Mediciones de Performance (Query Count)

La solución reduce significativamente la cantidad de peticiones a la base de datos (Query Count), transformando el problema de O(N) a un costo acotado.
**Sin embargo, es importante aclarar que el procesamiento en CPU (iteraciones) sigue siendo O(N) respecto al número de obligaciones activas y O(M) respecto a los periodos nuevos a generar.**

### Before (O(N) Queries)
Por cada obligación activa se ejecutaba `refresh_overdue_periods`, lo que implicaba una serie de consultas `SELECT`, `UPDATE` o llamadas al `PeriodEngine`.
- **1 Obligación:** ~5 queries.
- **10 Obligaciones:** ~50 queries.
- **100 Obligaciones:** ~500 queries.

### After (Consultas Acotadas)
Con la estrategia Batch, agrupamos las actualizaciones e inspecciones. El conteo máximo de queries en la ruta feliz es ~4 independientemente del número de obligaciones activas.
- **1 Obligación:** ~4 queries (1 UPDATE masivo, 1 SELECT active, 1 SELECT sequences, 1 INSERT (si hay gaps)).
- **10 Obligaciones:** ~4 queries.
- **100 Obligaciones:** ~4 queries.

## Tradeoffs y Complejidad Restante
- **CPU Loop**: Seguimos generando instancias en memoria (`calculate_period_bounds`, `generate_period_key`) en un ciclo `for`. Aunque esto es rápido en memoria, si un usuario tiene miles de secuencias y un rango de fechas muy grande, impactará el CPU.
- **Idempotencia Soportada por DB**: Nos basamos en que la base de datos rechace la concurrencia a través de `IntegrityError` y `uq_obligation_period_key`.
- **Migración Pendiente**: Eventualmente, la migración a un trabajador asíncrono en background (ej. Celery/ARQ) o un "cron" real será el enfoque definitivo O(1) si la base de usuarios crece exponencialmente. Por ahora, este "staleness guard" resuelve el bloqueo de UI sin sacrificar arquitectura.

## Recomendación de Deploy
El backend está seguro, todos los tests V1.7 y de concurrencia/batch agregados (6 tests específicos) pasaron en un 100%. No se tocaron endpoints frontend ni hubo cambios destructivos.
**Se recomienda proceder con el Commit del backend y posterior despliegue si así lo autoriza Steven.**
