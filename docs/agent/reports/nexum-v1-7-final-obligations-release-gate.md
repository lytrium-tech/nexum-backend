# Nexum V1.7 - Final Obligations Release Gate

## 1. Contexto y Root Causes Revisadas
Se completó la auditoría final y corrección del flujo de pagos V1.7. Tras la resolución parcial anterior, quedaban dos bugs críticos introducidos accidentalmente que bloqueaban por completo las transacciones en producción:
- **Error 422 (Idempotency Key):** El router no extraía el header Idempotency-Key del request, lo que provocaba que llegara como None y fallara la validación estricta introducida previamente.
- **Error 500 (String vs UUID):** Al consultar registros en la base de datos para validar idempotencia, el user_id en forma de string se comparaba directamente con la columna UUID. Esto generaba un error fatal en el driver syncpg (operator does not exist: uuid = character varying).

El bug original de "Commit Fantasma" (donde la UI sumaba el pago temporalmente pero en BD no se reflejaba tras un refresh) ya había sido mitigado con un manejo transaccional correcto (ollback explícito + with_for_update()), pero dichos fixes estaban ocultos detrás de los nuevos errores de sintaxis y headers descritos arriba.

## 2. Estado de DB
La base de datos y esquemas se encuentran estables.
- Los tipos UUID ahora son respetados y casteados explícitamente en service_v17.py.
- No es necesario realizar ningún *backfill*, ya que los pagos erróneos nunca se persistieron (debido al rollback o errores previos al insert).
- Los periodos, dependencias e históricos están intactos.

## 3. Cambios Realizados
1. **pp/obligations/router_v17.py:** 
   - Se modificó la asignación data.idempotency_key = idempotency_key para que ocurra únicamente if idempotency_key: está presente. Esto respeta los defaults en tests y evita sobreescribir valores válidos provistos por otras vías sin dejar de pasar el header de red.
2. **pp/obligations/service_v17.py:**
   - Se castearon a uuid.UUID(user_id) todas las referencias en .where() sobre la tabla ObligationPayment para asegurar compatibilidad estricta con PostgreSQL/asyncpg.

## 4. Tests
Todos los tests (suite V1.7, obligations, payments) pasaron con éxito de forma local:
- pytest -k "v17 or obligation or payment" → **74 passed, 3 skipped**
- Test de descuento de cuenta: PASSED.
- Test de flujos secuenciales y recalculación de periodos: PASSED.
- Test multi-moneda (preview de FX): PASSED.
- Test de idempotencia (retry de payload sin duplicar): PASSED.

## 5. Riesgos
- **Riesgo Bajo:** Al requerir que los clientes manden explícitamente el header Idempotency-Key para pagos vía frontend en el flujo real, si el frontend omitiera este header el backend responderá con 422. (Esto ya es el comportamiento intencionado y esperado del flujo de pagos duros para evitar cargos dobles).
- No se observaron riesgos de cruce (cross-module risk), el legacy API sigue inmutable.

## 6. Resolución del Gate de Decisión
- **Posibles 500:** Mitigados. El crash por UUID está resuelto.
- **¿Es seguro el deploy?:** **Sí.** El backend branch y la arquitectura actual son seguros para hacer un commit y pasar a producción controlada.
- **¿QA de Frontend puede continuar?:** **Sí.** El frontend finalmente podrá procesar los pagos usando el flag en 	rue y ver el descuento de las cuentas y retornos exitosos.

## 7. Conclusión
**Proceed with commit, push and deploy.**
