# Nexum V1.7 Phase 2.4 — VPS Disposable DB Migration Validation

## 1. Executive Summary
Esta fase ejecutó con éxito la validación viva de la migración V1.7 (Alembic) utilizando la VPS como entorno de pruebas desechable aislado (`docker-compose.db.yml`). Se confirmó empíricamente que la migración es segura, aditiva, compatible con V1.5 y reversible (downgrade no destructivo sobre legacy). Producción no fue tocada.

## 2. VPS Environment
- **Host:** `api.nexum.lytrium.tech`
- **Current HEAD:** `d539ee8` (Sincronizado)
- **Container Host:** Docker local temporal (`nexum_backend_db_test`)
- **Isolation:** Aislado en red de prueba, puerto 5433 host, DB separada (`nexum_test_db`).

## 3. Safety Confirmation
Previo a cualquier operación, se validó mediante `docker ps`, `pwd` y `git rev-parse` que estábamos interactuando únicamente con el entorno de testing y el código de la rama `main` en `/opt/nexum-backend`. Los servicios de producción en el VPS (`nexum_backend_api`, `chatwoot`, `n8n`) permanecieron intactos y corriendo sin interrupciones.

## 4. Disposable DB Setup
Se levantó exitosamente la base PostgreSQL 15 efímera mediante:
`docker compose -f docker-compose.db.yml up -d`
La base se instanció correctamente sin impactar el servidor productivo.

## 5. Seed Validation
Se aplicó el `seed_v15_minimal.sql` sobre `nexum_test_db`. Se comprobó que las tablas `obligations`, `obligation_periods`, y `obligation_payments` fueron creadas satisfactoriamente como punto de partida.

## 6. Alembic Upgrade Result
Se ejecutó:
`docker run --rm ... nexum-backend-api bash -c 'DATABASE_URL=... alembic upgrade head'`
El comando reportó el contexto correctamente y la migración `v1_7_phase2_1` fue catalogada como `head`.

## 7. Schema Verification
Una inspección a `information_schema` en `public` validó que:
- Las tablas `exchange_rates` y `fx_quotes` fueron creadas.
- `obligations.amount_type` (text, nullable) está presente.
- `obligation_periods.is_current` (boolean, nullable) está presente.
- `obligation_payments.quote_id` e `idempotency_key` están presentes.
- Los índices `ix_fx_quotes_user_idempotency` e `ix_obligation_payments_user_idempotency` fueron confirmados con la expresión WHERE (`idempotency_key IS NOT NULL`).

## 8. Tests Result
Se corrió:
`docker run --rm ... nexum-backend-api bash -c 'DATABASE_URL=... pytest tests/ -v'`
El resultado fue de **232 passed, 1 failed**, siendo el único fallo esperado y catalogado previamente (`test_openapi_contains_v16_fields`). La migración estructural es 100% compatible con la lógica V1.5 actual.

## 9. Alembic Downgrade Result
Se ejecutó:
`docker run --rm ... nexum-backend-api bash -c 'DATABASE_URL=... alembic downgrade base'`
El script revirtió el esquema. Se verificó que:
- `exchange_rates` y `fx_quotes` fueron eliminados de `information_schema.tables`.
- Las tablas legacy `obligations`, `obligation_periods` y `obligation_payments` permanecieron seguras e inalteradas estructuralmente.

## 10. Cleanup
Se ejecutó `docker compose -f docker-compose.db.yml down -v`, destruyendo limpiamente el volumen y contenedor de prueba de manera definitiva.

## 11. Production Safety
A través de este ejercicio empírico en el hardware de destino (VPS), comprobamos que el plan aditivo tiene una validación empírica satisfactoria para el schema de V1.5 en el framework Alembic, otorgando alta confianza operativa. Cualquier migración productiva futura requiere aprobación explícita, backup y feature flag OFF.

## 12. Issues / Blockers
Ninguno.

## 13. Go / No-Go for Phase 3
**GO para Phase 3 bajo feature flag**.
La Fase 2 de Base de Datos está superada. El contrato de V1.7, su base estructural, y su validación cruzada otorgan alta confianza operativa, sin aprobación automática para producción. Phase 2.4 valida migración en DB disposable. Producción NO fue tocada. Producción NO está aprobada todavía. Phase 3 puede iniciar desarrollo controlado.
