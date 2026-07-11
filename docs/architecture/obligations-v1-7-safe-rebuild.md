> **HISTORICAL NOTICE:** Este documento conserva diseos, planes o reportes histricos. No debe ser considerado la fuente de verdad actual. Para la documentacin t?cnica cannica, consulte [docs/project/](../project/00-backend-overview.md).

# Nexum Core Obligations V1.7 — Safe Rebuild Architecture

## 1. Objective
Rediseñar e implementar la versión V1.7 de Core Obligations de manera incremental y segura, garantizando compatibilidad absoluta con V1.5 y estableciendo mecanismos a prueba de fallos para el despliegue, rollback y evolución del modelo de base de datos.

## 2. Product Principles
- **Seguridad primero:** Ningún despliegue puede comprometer la operatividad de los usuarios finales en producción.
- **Evolución continua:** Las mejoras estructurales deben coexistir con el código heredado hasta garantizar su estabilidad.
- **Aislamiento funcional:** Las fallas en Obligations no deben propagarse a Intelligence u otros módulos consumidores.

## 3. Current V1.5 Baseline
- **Backend:** `ca7b165` (V1.5 runtime)
- **Frontend:** `2f6f404` (release/v1.5-rollback)
- **Database:** Compatible con V1.5, incluyendo columnas y vistas restauradas, con constraints relajados (`DROP NOT NULL`) en columnas residuales V1.6.

## 4. Residual DB Risks
- Los registros creados bajo V1.5 poseerán valores `NULL` en columnas estructurales V1.6 como `base_amount`, `type`, `start_date` y `obligation_period_id`.
- Se requiere una migración de datos explícita ("data backfill") antes de intentar establecer cualquier restricción de integridad estructural referida a los nuevos modelos de V1.7.

## 5. V1.7 Goals
- Implementar las funcionalidades requeridas (soporte FX, trazabilidad precisa de periodos, reportes detallados) mediante un diseño tolerante a fallos.
- Garantizar que el frontend y el backend legacy puedan continuar operando simultáneamente durante la transición.
- Lograr un despliegue sin ventanas de inactividad (Zero Downtime) y 100% reversible (Zero Risk Rollback).

## 6. Non-Goals
- Reescribir módulos ajenos a Obligations (como Ledger o Goals).
- Eliminar código o tablas legacy de V1.5 durante el despliegue inicial de V1.7.
- Implementar Big Bang refactors (reestructuraciones monolíticas simultáneas).

## 7. Compatibility Strategy
- **Base de datos:** Expansión del esquema (Append-Only). Las nuevas tablas o columnas no interferirán con las existentes ni con las consultas previas.
- **Endpoints:** El código nuevo estará aislado, versionado (ej. `/api/v1/obligations/v2`) o controlado mediante Feature Flags a nivel de servicio.

## 8. Data Model Direction
- Se introducirá un esquema de `obligation_periods` y entidades secundarias que convivan con las tablas base sin romper dependencias de vistas o consultas de Inteligencia de V1.5.
- La migración de estado debe efectuarse sin bloquear la tabla `obligations`.

## 9. Migration Strategy
1. **Fase de Expansión:** Crear nuevas tablas y columnas permitiendo valores nulos o estableciendo valores por defecto compatibles con V1.5.
2. **Fase de Integración (Dual-Write / Async Sync):** El sistema escribe en el nuevo y en el viejo esquema para compatibilidad retroactiva, o migra los datos en segundo plano.
3. **Fase de Contracción (Post-QA / Futuro):** Eliminación controlada del legacy una vez que V1.7 tenga adopción total y métricas estables.

## 10. Frontend/Backend Contract Strategy
- El frontend continuará consumiendo la API V1.5 por defecto.
- Los nuevos payloads de V1.7 deberán ser probados en paralelo (Shadow testing) o expuestos primero a usuarios internos mediante inyección de Feature Flag.

## 11. Feature Flag Strategy
- Implementar un switch a nivel de aplicación (Feature Flag) que permita desviar la ejecución hacia el nuevo motor V1.7. 
- En caso de error, el switch se apaga instantáneamente regresando al motor V1.5.

## 12. Testing Strategy
- Cobertura estricta para coexistencia: Pruebas unitarias garantizando que el flujo V1.5 no corrompe al V1.7, y viceversa.
- Pruebas E2E de contrato (Frontend V1.5 contra Backend V1.7-Flag-Off).
- Pruebas E2E funcionales cubriendo explícitamente escenarios de "Usuario con histórico previo" vs "Usuario nuevo".

## 13. Rollback Strategy
- El sistema será 100% reversible apagando el Feature Flag sin requerir despliegues de código (Runtime Reversibility) ni reversión de esquemas de base de datos.

## 14. Deployment Strategy
1. Backup de base de datos.
2. Despliegue de migraciones estructurales aditivas (Dark Deployment).
3. Despliegue de código backend inactivo (Feature Flag apagado).
4. Pruebas de sanidad sobre flujo legacy (Confirmar V1.5 intacto).
5. Habilitación de Feature Flag para usuarios beta / QA interno en producción.
6. Rollout progresivo al frontend y usuarios finales.

## 15. Open Questions
- ¿Qué estrategia específica de backfill se utilizará para sanear los registros creados durante V1.5 que tienen `base_amount = NULL`?
- ¿Cómo se aislará estructuralmente el módulo de Inteligencia para evitar dependencias frágiles hacia tablas internas de Obligations?

## 16. Non-Negotiable Rules for V1.7
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

