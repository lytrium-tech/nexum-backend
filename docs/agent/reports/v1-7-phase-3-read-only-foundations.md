# Nexum V1.7 Phase 3 — Read-Only Foundations

## 1. Executive Summary
Esta fase estableció las bases backend de lectura para Nexum Core Obligations V1.7. Se introdujo el feature flag `NEXUM_OBLIGATIONS_V17_ENABLED`, deshabilitado por defecto para proteger la operación de producción (V1.5). Adicionalmente, se crearon los enums y schemas requeridos usando Decimal, se mapeó estructuralmente la base de datos a los modelos SQLAlchemy actualizados y se habilitaron los endpoints read-only experimentales en la ruta `/api/v1.7/obligations`. Todo esto manteniendo compatibilidad absoluta con V1.5.

## 2. Files Changed
- **`app/core/config.py`**: Añadido feature flag `NEXUM_OBLIGATIONS_V17_ENABLED`.
- **`app/obligations/enums_v17.py`**: Creados los Enums de dominio V1.7 (`AmountType`, `PeriodStatus`, `FXQuoteStatus`, etc.).
- **`app/obligations/schemas_v17.py`**: Creados Pydantic Schemas `ObligationV17Response`, `ObligationPeriodV17Response` y `ObligationPaymentV17Response` asegurando el uso estricto de `Decimal`.
- **`app/obligations/models.py`**: Extendidos los modelos de V1.5 (con nullable para retrocompatibilidad) y añadidas las entidades `ExchangeRate` y `FXQuote`.
- **`app/obligations/router_v17.py`**: Creado router experimental con los stubs read-only y chequeo de feature flag incorporado como dependencia FastAPI.
- **`app/api/router.py`**: Registrado el router v1.7.
- **`tests/unit/test_obligations_v17_api.py`**: Batería de pruebas que afirma el comportamiento del feature flag y de los stubs vacíos.

## 3. Feature Flag
- **Constante**: `NEXUM_OBLIGATIONS_V17_ENABLED: bool = False`
- **Comportamiento**: Si es `False`, cualquier request a `/api/v1.7/obligations*` responde con HTTP 403 Forbidden y el código de error `feature_flag_disabled`.

## 4. Enums/Schemas
Implementados usando tipados fuertes y estandarizados (importados y definidos). Todos los montos en los modelos de respuesta (`schemas_v17.py`) utilizan `Decimal` explícitamente para cumplir la invariante financiera de precisión.

## 5. SQLAlchemy Models
Se extendieron los modelos existentes sin romper compatibilidad V1.5 y en paralelo a la migración Alembic (Phase 2):
- Se añadió `amount_type` a `Obligation`.
- Se añadió `is_current` a `ObligationPeriod`.
- Se añadieron `quote_id` e `idempotency_key` y `user_id` a `ObligationPayment`.
- Se crearon los modelos `ExchangeRate` y `FXQuote` para mapear las tablas homónimas insertadas.

## 6. Read-Only Endpoints
Implementados como GET endpoints stub bajo `/api/v1.7/obligations` (listado, id y periodos). Por ahora, bajo feature flag activado retornan empty state `EmptyStateResponse(message="No obligations found", items=[])` o HTTP 404 para entidades inexistentes, ya que la lógica de escritura y población de la base no forma parte de Phase 3.

## 7. OpenAPI Validation
OpenAPI exporta ahora tanto los esquemas/rutas `/api/v1/obligations` (preservados sin alteración alguna) como las nuevas adiciones experimentales `/api/v1.7/obligations`. La coexistencia está validada y documentada vía Swagger UI mediante los decorators.

## 8. Tests Result
- **Comando:** `.venv/Scripts/pytest tests/ -v`
- **Resumen:** `234 passed, 1 failed`
- **Fallo Preexistente Documentado:** `test_openapi_contains_v16_fields` falló con `FileNotFoundError`. Esto es un issue heredado del setup del repositorio relacionado a la generación offline estática del openapi.json. Los tests V1.7 propios (`test_v17_endpoints_disabled_by_default` y `test_v17_endpoints_enabled`) pasaron exitosamente.

## 9. V1.5 Compatibility
Las entidades y endpoints V1.5 no fueron perturbados. La app arranca sin percance y la estructura centralizada sigue operando independientemente de las capacidades aditivas dormidas por defecto.

## 10. Production Safety
- Producción **NO FUE TOCADA**. No se ejecutaron comandos remotos contra entornos live.
- La inserción de lógica es inerte sin la variable de entorno correspondiente, limitando la superficie de riesgo a cero en producción.

## 11. Issues / Blockers
El test de openapi `test_openapi_contains_v16_fields` permanece roto localmente. No es un stopper para Phase 4 debido a que se identificó como problema estático desvinculado a V1.7.

## 12. Recommendation
Phase 3 Read-Only Foundations is ready to commit. The recommended next step is Phase 3.1 — Read-Only Integration Hardening / OpenAPI Test Stabilization before implementing V1.7 writes.
- Phase 3 no habilita writes.
- Phase 3 no aprueba deploy automático.
- Phase 3 no aprueba migración productiva.
- Antes de Phase 4 writes, se recomienda estabilizar el fallo OpenAPI preexistente.
