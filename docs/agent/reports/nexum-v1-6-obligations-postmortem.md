# Nexum V1.6 Obligations Postmortem

## 1. Executive Summary
El release de Nexum V1.6/V1.6.2 (Core Obligations) introdujo cambios profundos y destructivos en la base de datos y la lógica de negocio sin las debidas estrategias de compatibilidad hacia atrás ni aislamiento. Tras detectarse fallos en tiempo de ejecución, el intento de rollback reveló una dependencia estricta entre el frontend, el backend y el esquema de base de datos, lo que obligó a realizar intervenciones manuales de alto riesgo en producción para estabilizar el sistema de vuelta a la versión V1.5.

## 2. What V1.6 Tried to Achieve
V1.6 buscaba reestructurar por completo el módulo de obligaciones ("Core Obligations") mediante la introducción de entidades como `obligation_periods` y la refactorización profunda de las tablas `obligations` y `obligation_payments`. El objetivo era mejorar la trazabilidad de los pagos por periodo, soportar FX en pagos de obligaciones, y mejorar la precisión de los reportes de deuda y progreso mensual.

## 3. What Went Wrong
- Se ejecutaron migraciones de base de datos destructivas (incluyendo `DROP TABLE` o eliminación de columnas estructurales).
- La aplicación falló en producción (errores 500, dependencias en vistas SQL eliminadas, errores de validación de contrato).
- El dashboard y otras métricas de inteligencia se rompieron por completo al depender indirectamente de objetos de base de datos que fueron modificados o eliminados.
- El rollback de código dejó la base de datos en un estado inutilizable para la versión V1.5, bloqueando las operaciones del usuario final.

## 4. Technical Root Causes
- **Migraciones irreversibles:** La migración a V1.6 reemplazó y eliminó columnas clave de V1.5 (`amount`, `is_active`, `period`) y dependencias como la vista `v_pending_obligations_current_month`.
- **Desacople insuficiente:** El módulo de Inteligencia dependía estructuralmente de la implementación interna de Obligations mediante vistas SQL. Al cambiar Obligations, Inteligencia se rompió.
- **Constraints estrictos:** La adición de restricciones `NOT NULL` en nuevas columnas V1.6 bloqueó la posibilidad de que el código V1.5 siguiera escribiendo datos tras un rollback.

## 5. Product/Process Root Causes
- **Big Bang Release:** Se intentó actualizar la base de datos, el backend y el frontend simultáneamente bajo la presunción de éxito total, sin tolerancia a fallos.
- **Falta de Feature Flags:** Los cambios se expusieron inmediatamente a todos los flujos de ejecución, imposibilitando un despliegue gradual o pruebas sombra.

## 6. Data/DB Migration Failure Points
- La migración incluyó operaciones destructivas (`ALTER TABLE DROP COLUMN`, reemplazo directo de datos) sin respaldos automatizados o scripts `down` confiables probados en producción.
- No se preservó compatibilidad hacia atrás en la escritura ni lectura (Backwards Incompatible Schema Changes).

## 7. Frontend/Backend Contract Failure Points
- El contrato Pydantic/OpenAPI cambió radicalmente y se rompió la compatibilidad de lectura y escritura.
- El rollback del frontend (release/v1.5-rollback) enviaba payloads que la base de datos residual de V1.6 rechazaba silenciosamente debido a restricciones de nivel de motor (constraints).

## 8. Runtime QA Gaps
- Las pruebas no cubrieron los casos límite de interacción entre diferentes módulos funcionales (Inteligencia vs. Obligaciones).
- Las pruebas unitarias/de integración no validaron el funcionamiento sobre bases de datos persistentes con datos preexistentes; se validó un modelo ideal.

## 9. Rollback Failure Points
- El código se revirtió a la versión V1.5, pero el motor de base de datos retuvo el esquema parcial de V1.6, provocando una disociación fatal entre la lógica de negocio y las restricciones de almacenamiento.
- No había un mecanismo seguro de "Rollback de Base de Datos", obligando a operar SQL interactivo directamente en producción.

## 10. Recovery Actions Taken
- Diagnóstico en caliente y auditoría estructural del estado V1.5 vs V1.6.
- Restauración manual de columnas legacy V1.5 (`amount`, `is_active`, `period`).
- Recreación directa de vistas legacy (`v_pending_obligations_current_month`).
- Eliminación explícita de restricciones `NOT NULL` en columnas V1.6 residuales (`base_amount`, `type`, `start_date`, etc.) que bloqueaban las inserciones de V1.5.

## 11. Lessons Learned
- **El esquema de base de datos debe ser independiente del despliegue de código.** Las migraciones estructurales no pueden romper el runtime anterior hasta que el nuevo runtime esté 100% validado.
- **Los rollbacks deben ser operaciones triviales.** Cualquier release debe incluir la garantía de que revertir el commit restablece la operatividad del sistema de manera inmediata.

## 12. Non-Negotiable Rules for V1.7
1. No DROP TABLE en producción.
2. No rollback imposible.
3. No Big Bang release.
4. No cambiar DB/backend/frontend al mismo tiempo sin compatibilidad.
5. Feature flag obligatorio.
6. Backup obligatorio antes de migración.
7. Tests de contrato frontend/backend obligatorios.
8. Tests de usuarios nuevos y usuarios con datos obligatorios.
9. Intelligence/dashboard deben auditarse como consumidores indirectos.
10. Legacy V1.5 debe seguir funcionando hasta que V1.7 pase QA real.
