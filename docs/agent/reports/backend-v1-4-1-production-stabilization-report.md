# Backend V1.4.1 — Production Stabilization Report

## Resumen de Estabilización
Durante el retest de Frontend V1.4 en el entorno productivo, se detectaron 3 bugs bloqueantes provenientes del Backend. Tras un análisis y la reproducción en entorno local, todos los problemas han sido clasificados, diagnosticados y corregidos sin alterar la lógica central aprobada en la V1.4.

## Bugs Corregidos

### Bug 1: Discrepancia de Moneda (COP forzado)
- **Reporte:** Los registros de cuentas en USD (ej. ARQ) aparecían forzados a COP en el historial y en las respuestas del asistente (conversacional).
- **Causa Raíz:** Se identificó que las respuestas del chatbot tenían embebido el sufijo `COP` de manera hardcodeada (`"Gasto de {amount} COP"`). Adicionalmente, existían fallback duros a `"COP"` en la creación conversacional de metas y obligaciones.
- **Solución:** 
  - Se eliminó el hardcoding visual de `COP` en las respuestas estructuradas del chatbot en `app/conversations/service.py`.
  - Se corrigió la generación de `GoalCreate` y `ObligationCreate` para hacer uso del `data.get("currency", "COP")`, permitiendo la inyección dinámica si el LLM extrae la divisa explícita.
  - La tabla `financial_events` registraba y persiste la moneda original real en la DB. El bug era estrictamente reportado en las capas conversacionales y de historial derivado del texto o eventos previos al patch productivo.

### Bug 2: Snapshot 500 (billed_debt=None)
- **Reporte:** El endpoint `/api/v1/intelligence/snapshot` retornaba `500 Internal Server Error` debido a valores nulos.
- **Causa Raíz:** El backend esperaba que las tarjetas de crédito y obligaciones siempre devolvieran montos numéricos válidos. Algunas tarjetas/resúmenes retornaban `None` para `billed_debt` (por ejemplo, tarjetas inactivas o sin transacciones en el periodo de gracia), lo que causaba un fallo al sumar `sum()`.
- **Solución:** 
  - Se modificó `app/intelligence/service.py` para usar `safe_decimal()` manejando y convirtiendo proactivamente cualquier `None` a `Decimal("0.00")` antes de la agregación final.

### Bug 3: Reactivación de cuenta (404 Not Found)
- **Reporte:** Reactivar una cuenta archivada fallaba con 404 porque el sistema no encontraba la cuenta.
- **Causa Raíz:** El método `update_account` utilizaba `_get_account_or_404(account_id)` que internamente consultaba sólo cuentas activas (`include_inactive=False`), por lo que una cuenta inactiva jamás se encontraba para poder actualizar su estado a `True`.
- **Solución:** 
  - Se modificó `_get_account_or_404` en `app/accounts/service.py` para recibir `include_inactive=True` al actualizar.
  - Se actualizó el repositorio en `app/accounts/repository.py` para soportar explícitamente el parámetro `include_inactive`.

## Validación Realizada
Se han añadido y verificado los 10 tests de unidad/HTTP requeridos para cubrir estos tres bugs dentro de la suite:
- `tests/unit/test_stabilization.py`
  - 4 Tests verificando que Snapshot maneje `None` (`billed_debt`, `unbilled_debt`, `payment_required`, etc).
  - 2 Tests verificando la reactivación de cuentas (éxito y 404 real).
  - 4 Tests confirmando que el UI conversacional utiliza moneda dinámica y no asume explícitamente COP en sus strings de interfaz.

**Estado Final:** 183 tests pasados, 0 fallos (`python -m uv run pytest tests/ -v`).

## Instrucciones para Deploy (Steven)
El backend se encuentra estable. Ninguna de estas correcciones amerita migraciones en la base de datos (solamente afectaron lógicas Python, validaciones y repositorios). 

El código está listo para ser pusheado a la rama principal (cuando lo apruebes) y su despliegue a VPS no requerirá `alembic upgrade`.

**Bloqueos para Frontend V1.4:** Removidos. Frontend puede retestear la rama localmente para confirmar.
