# Nexum V1.7 Phase 4A — Minimal Create Obligation

## 1. Executive Summary
Esta fase implementó el primer write endpoint mínimo para Obligations V1.7: `POST /api/v1.7/obligations`. El endpoint permite crear la estructura principal de la obligación (regla/template) de manera controlada detrás del feature flag `NEXUM_OBLIGATIONS_V17_ENABLED`. Se incluyó validación estricta de esquemas y tipos, mapeo hacia los campos V1.5 para garantizar retrocompatibilidad y se evitó proactivamente cualquier generación de períodos, pagos, lógicas de FIFO o eventos financieros, manteniendo la operación al mínimo.

## 2. Files Changed
- **`app/obligations/schemas_v17.py`**: Añadido `ObligationV17CreateRequest` con validaciones Pydantic vía `@model_validator` (ej. asegurar que el currency es uppercase, base_amount sea válido según el tipo y fechas lógicas).
- **`app/obligations/service_v17.py`**: Creado `ObligationV17Service` con la lógica de instanciación del objeto `Obligation` en SQLAlchemy mapeando explícitamente desde tipos V1.7 hacia representaciones V1.5 nativas sin romper campos.
- **`app/obligations/router_v17.py`**: Implementado `POST /api/v1.7/obligations`, asegurado con dependencia del feature flag.
- **`tests/unit/test_obligations_v17_api.py`**: Agregados tests exhaustivos de comportamiento del feature flag, persistencia controlada (vía `mock_db`) y las reglas de validación funcionales para el payload.
- **`tests/unit/test_frontend_contracts.py`**: Actualizada la batería de OpenAPI para asercionar que el nuevo esquema existe en el contrato expuesto.

## 3. Endpoint Added
- **Ruta:** `POST /api/v1.7/obligations`
- **Operación:** Crea el template de obligación (registro en tabla `obligations`). No genera periodos (`obligation_periods`) ni transacciones.

## 4. Feature Flag Behavior
- **OFF (`False`)**: El endpoint de creación (junto a toda la suite V1.7) deniega la petición retornando inmediatamente un error `403 Forbidden` (`feature_flag_disabled`).
- **ON (`True`)**: Permite la ejecución de las validaciones, servicio y escritura en base de datos.

## 5. Create Contract
- Los esquemas utilizan tipados de Enum estrictos (ej. `Frequency`, `AmountType`, `ObligationType`).
- `currency` por defecto a `COP`.
- `metadata` provista de manera opcional.
- La inserción mapea atributos a las nomenclaturas de V1.5 (`type`, `payment_mode`, `due_day`, `due_month`) manteniendo compatibilidad transparente.

## 6. Validations
Agregadas dentro del esquema Pydantic en `model_validator`:
- `name` no vacío.
- Si `amount_type == fixed`, `base_amount` debe ser `> 0`.
- Si `amount_type == variable`, `base_amount` puede ser null o `>= 0`.
- `first_due_date` nunca menor que `start_date`.
- `end_date` nunca menor que `start_date`.

## 7. Persistence Behavior
La persistencia ocurre usando `AsyncSession` estándar: `session.add(new_obligation)`. Se confirmó a nivel de test que no hay dependencias u operaciones subsecuentes inyectadas.
- No se insertan periodos.
- No se emiten financial events.
- No interactúa con saldo de caja.

## 8. OpenAPI Validation
El contrato OpenAPI se actualizó implícitamente por FastAPI. El test confirma que `ObligationV17CreateRequest` es parte del diccionario generado. Adicionalmente, las rutas V1.5 (`/api/v1/obligations`) no sufrieron colisión.

## 9. Tests Result
- **Comando:** `.venv/Scripts/pytest tests/ -v`
- **Resultado:** 237 passed. Todos los tests de validación semántica (ej. amounts negativos, dates invertidas) pasaron correctamente.

## 10. V1.5 Compatibility
El código y campos legados no se tocaron. V1.5 sigue utilizando sus propios esquemas y los clientes existentes seguirán operando bajo el router V1.

## 11. Production Safety
- Producción intacta.
- El feature flag (`NEXUM_OBLIGATIONS_V17_ENABLED`) permanece default en `False` en `config.py`.
- No hay migraciones a ejecutar todavía en producción.

## 12. Issues / Blockers
Ninguno.

## 13. Recommendation
Phase 4A Completada. El write basal V1.7 está preparado y el contrato es sólido. El siguiente paso recomendado es **Phase 4B — Period Generation**, donde se implementará la creación y estandarización del primer periodo o la inicialización del motor de calendarios para este template.
