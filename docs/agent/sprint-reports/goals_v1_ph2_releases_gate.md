# Release Gate Metas V1 Fase 2

## Resumen Ejecutivo
El Release Gate para el dominio de Metas (Fase 2 - Releases) ha sido validado exitosamente. Todos los endpoints (`/api/v1/goals/{id}/contributions`, `/api/v1/goals/{id}/releases`, y consultas de progreso) operan correctamente y aplican las reglas financieras obligatorias de Nexum.

## Incidentes Encontrados y Resueltos

1. **Error de Entorno de Tests 404 (Auth Bypass):**
   - **Problema:** FastAPI devolvía `404 Perfil de usuario no encontrado` durante los tests funcionales porque el flag de `AUTH_BYPASS_ENABLED` no operaba contra la base de datos correcta. `uvicorn` utilizaba la base local por defecto (`nexum`) al cargar el `.env`, en vez de la base del Release Gate (`nexum_release_gate_test`).
   - **Solución:** Se forzó la sobreescritura de `DATABASE_URL` para el comando de levantamiento de `uvicorn` permitiendo que Pydantic leyera la base correcta.

2. **IntegrityError en `financial_events` (Check Violation):**
   - **Problema:** Al registrar un `goal_release`, Postgres abortaba la transacción por una violación en `financial_events_type_check`. La restricción tipo `CHECK` de la tabla no contenía `goal_release`.
   - **Solución:** Se verificó que la migración de Alembic previamente generada (`goals_v1_ph2_releases`) no había sido ejecutada explícitamente en `nexum_release_gate_test`. Tras ejecutar `alembic upgrade head`, el constraint se actualizó y validó el registro.

3. **Inconsistencia de Payload en Smoke Script:**
   - **Problema:** La aserción que buscaba la llave `goal_reserved_amount` falló durante el step 12. Dicha llave solo se retorna bajo el modelo de Aportes, no en Releases.
   - **Solución:** Se removió la extracción y aserción de `goal_reserved_amount` del payload de `/releases` dentro de `smoke_goals.py`.

## Validación Final de Constraints
- `Release` reabre una meta completada y la retorna a estado `active`.
- El balance disponible en la cuenta origen de la reserva (`available_balance`) refleja correctamente el dinero líquido devuelto.
- Los aportes y retiros excesivos se bloquean correctamente (409 Conflict).
- Idempotencia comprobada al enviar repetidas instrucciones con el mismo `Idempotency-Key`.

## Aprobación
El dominio de Metas Fase 2 (Releases y Conciliación) es apto y **listo para commit y posterior despliegue** (sujeto a la autorización y confirmación de despliegue).
