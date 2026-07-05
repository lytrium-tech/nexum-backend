# Nexum V1.7 Phase 3.1 — OpenAPI / Contract Test Stabilization

## 1. Executive Summary
Esta fase (3.1) tuvo como objetivo estabilizar un test preexistente (`test_openapi_contains_v16_fields`) que fallaba en los entornos de validación debido a problemas con un archivo `openapi.json` corrupto/estático. Adicionalmente, se fortaleció la verificación del contrato backend incorporando los schemas y rutas V1.7 (leídos de forma directa desde la app FastAPI en memoria). Esto garantiza un acoplamiento seguro y validado del backend con los contratos del frontend antes de pasar a implementaciones complejas (writes V1.7).

## 2. Root Cause
El test original abría un archivo físico `openapi.json`. Este archivo localmente contenía caracteres corruptos o estaba cifrado en un encoding problemático para `json.load()` en Windows (UTF-16 LE y null bytes), lo cual desencadenaba un `json.decoder.JSONDecodeError` y previas veces un `FileNotFoundError`. En cualquier caso, depender de un snapshot local desincronizado de OpenAPI para validar contratos en runtime constituye un antipatrón en tests de integración.

## 3. OpenAPI Strategy Chosen
**Opción A — Generar OpenAPI durante test.**
Se modificó el test para utilizar dinámicamente `app.openapi()` importando la app de FastAPI en el test.
*Justificación:* Esto valida el contrato en vivo con respecto al estado exacto de los Pydantic Schemas y Routers montados en código, evitando depender de un archivo que se puede desactualizar, perder o corromper. Si el contrato de código varía, el test lo detectará instantáneamente.

## 4. Files Changed
- **`tests/unit/test_frontend_contracts.py`**: Refactor del test original, cambiando nombre a `test_openapi_contract_v15_v17` y usando el schema dinámico. Añadidas aserciones para V1.7.
- **`app/obligations/router_v17.py`**: Añadido un stub explícito read-only para exponer el esquema `ObligationPaymentV17Response` en el contrato OpenAPI, respetando el límite de no implementar writes por el momento.

## 5. Contract Validation
El nuevo test valida que en OpenAPI existan:
- Rutas V1.5 (`/api/v1/obligations`) y entidades asociadas (Goals, CreditCards).
- Rutas V1.7 (`/api/v1.7/obligations`, etc).
- Modelos V1.7 específicos (`ObligationV17Response`, `EmptyStateResponse`, `ObligationPaymentV17Response`, etc).

## 6. Tests Result
- **Comando:** `.venv/Scripts/pytest tests/unit/test_frontend_contracts.py -v` (y suite completa de 235 tests).
- **Resultado:** `235 passed`. Todos los fallos preexistentes (incluyendo el error OpenAPI) fueron mitigados exitosamente. Contrato backend/frontend estabilizado con 235 tests passing.

## 7. V1.5 Compatibility
Intacta. Las validaciones originales que aseguraban la integridad de los contratos expuestos para clientes V1.5 han sido preservadas bajo un escrutinio más estricto.

## 8. V1.7 Compatibility
Confirmado. Todos los schemas y endpoints experimentales introducidos en Phase 3 están correctamente expuestos en OpenAPI bajo las firmas planeadas (ObligationV17Response, ObligationPeriodV17Response, etc).

## 9. Production Safety
- Producción **NO FUE TOCADA**.
- No se han introducido writes.
- La inserción es inerte a nivel funcional, limitándose exclusivamente a la exposición y testeabilidad de los endpoints read-only.

## 10. Recommendation
Phase 3.1 Completado y estabilizado exitosamente con 100% test pass rate.
Recomendación: avanzar a Phase 4A — Minimal Write Contract detrás de feature flag, sin pagos ni FIFO todavía.
- Phase 3.1 estabiliza contrato OpenAPI.
- No habilita producción.
- No aprueba migración productiva.
- No implementa writes.
- El siguiente paso recomendado es Phase 4A, no writes complejos completos.
