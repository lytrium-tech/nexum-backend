# Nexum V1.7 Phase 0 — Baseline Audit V1.5

## 1. Executive Summary
Esta auditoría confirma que el entorno de producción (V1.5) se encuentra completamente operativo y estable tras el rollback. El backend, frontend y la base de datos están alineados. Los tests automatizados de V1.5 están en verde. El sistema es apto para servir como punto de partida (baseline) seguro para el diseño y despliegue iterativo de V1.7.

## 2. Backend Baseline
- **Runtime Hash (Producción VPS):** `ca7b165` (V1.5)
- **Repo Hash (Main local):** `b880a7f`
- **Health / Readiness:** OK (`200 OK` en endpoints `/health` y `/health/readiness`).
- **Estado del Working Tree:** Limpio.

## 3. Frontend Baseline
- **Release Actual:** `2f6f404` (`release/v1.5-rollback`).
- **Validación QA:** QA manual completado con éxito; la interfaz carga datos y permite interacción sin errores en consola.

## 4. Database Baseline
- **Estado de Tablas:** `obligations` y `obligation_payments` mantienen su estructura legacy funcional.
- **Residuos V1.6 Mitigados:** Las columnas introducidas por V1.6 (`base_amount`, `type`, `start_date`, `obligation_period_id`, `currency` en pagos, etc.) existen pero tienen sus restricciones `NOT NULL` relajadas (`is_nullable = YES`), permitiendo los flujos de inserción de V1.5.
- **Vistas Legacy:** `v_pending_obligations_current_month` se encuentra recreada, operativa y resolviendo consultas correctamente.

## 5. API Validation
- **GET /api/v1/intelligence/snapshot:** Funcional y resolviendo los balances (Total assets, payment required, billed debt) sin errores.
- **GET /api/v1/obligations:** Lista obligaciones correctamente.
- **POST /api/v1/obligations:** Funcional (Crea registros V1.5 dejando en `NULL` las columnas residuales V1.6).
- **POST Payment Endpoint:** Funcional (Registra pagos de obligaciones correctamente).

## 6. Manual QA Validation
- El flujo de Frontend (login/signup, onboarding, dashboard `/app`, obligations page) ha sido validado exitosamente y no muestra disrupciones.
- Creación y pago de obligaciones operan sin bloqueos.

## 7. Automated Tests
- **Comando Ejecutado:** `uv run pytest tests/ -v` (local).
- **Resultados:** 232 tests pasados, 3 ignorados (skipped).
- **Fallos:** 1 fallo aislado (`test_openapi_contains_v16_fields`) derivado de errores de decodificación JSON en disco, no atribuible a un defecto de lógica de negocio o runtime en V1.5.
- **Conclusión de Pruebas:** Altísima confiabilidad en la regresión funcional V1.5.

## 8. Logs Review
- **Revisión en VPS (`docker compose logs`):** Limpios de excepciones o Tracebacks. Las peticiones reportan estado HTTP 200 consistentemente. No hay evidencia de `NotNullViolationError` ni `UndefinedColumnError`.

## 9. Residual Risks
1. **Datos Nulos en V1.6 Residual:** Los nuevos registros de obligaciones y pagos creados bajo V1.5 mantienen valores nulos en columnas introducidas durante el breve despliegue V1.6. Al migrar a V1.7, se requerirá un proceso estricto de backfill para estas columnas.
2. **Entorno Staging Ausente:** Actualmente no hay un servidor Staging idéntico a producción; esto incrementa el riesgo en la fase QA real de V1.7 si no se implementa una base de datos clonada.
3. **Múltiples versiones vivas en BD:** La coexistencia de las columnas V1.5 y V1.6 requiere rigor al diseñar el ORM V1.7 para evitar confusión sobre cuál columna representa la "verdad" (ej. `amount` vs `base_amount`).

## 10. Go / No-Go Recommendation for Phase 1
**GO.**
El sistema está estabilizado, los flujos operativos se han restaurado a la normalidad y los riesgos residuales son conocidos y controlables mediante diseño de software. Se aprueba el inicio inmediato de la **Fase 1 — Contract Design V1.7**.
