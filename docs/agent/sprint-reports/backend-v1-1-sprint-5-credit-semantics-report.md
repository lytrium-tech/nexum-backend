# Reporte de Cierre - Backend V1.1 - Sprint 5: Credit Semantics

## Resumen Ejecutivo
Sprint 5 se completó funcionalmente. Credit Core queda con una semántica financiera explícita basada en transacciones: la deuda vigente se calcula desde `credit_card_transactions`, no desde `credit_cards.current_debt`.

El resultado cumple el objetivo del roadmap: tarjetas de crédito comprensibles para el usuario, separando deuda actual, crédito disponible, deuda facturada, deuda no facturada, pago requerido y estimación del próximo pago.

## Decisiones Semánticas Cerradas
- Fuente de verdad V1.1: `credit_card_transactions` enlazadas a `financial_events`.
- `credit_cards.current_debt` permanece en BD, pero no se usa como fuente de lectura ni se sincroniza manualmente.
- `current_debt` expuesto por API es calculado.
- `available_credit = credit_limit - current_debt`.
- `payment_required = billed_debt`.
- `next_payment_estimate` se expone separado y marcado como estimado.
- `statement_balance = None` porque Nexum no persiste extractos cerrados.
- `management_fee`, `monthly_interest_rate`, `annual_interest_rate`, `network` y `franchise` son metadata; no generan cargos automáticos.

## Cambios Realizados
1. Modelos y migración:
   - Se agregaron `network` y `franchise` a tarjetas.
   - Se agregó `CreditCardInstallment` y tabla `credit_card_installments`.
   - Se creó `scripts/migration/migrate_v11_sprint5.py` con migración aditiva y backfill de cuotas.

2. Contrato Credit V1.1:
   - `CreditCardRead` y `CreditCardStatusRead` exponen `current_debt`, `available_credit`, `payment_required`, `next_payment_estimate`, `statement_balance` y `data_quality`.
   - Se conservaron aliases legacy: `estimated_current_debt`, `estimated_available_credit`, `monthly_cc_payment`.
   - Se expone metadata financiera sin ejecutar intereses ni cuotas de manejo automáticas.

3. Servicio Credit:
   - Compras de tarjeta aumentan deuda y no reducen cash.
   - Pagos de tarjeta reducen cash y reducen deuda transaccional.
   - Sobrepagos se rechazan contra deuda calculada.
   - Cuotas se materializan como schedule de capital simple, sin intereses ni cargos implícitos.

4. Intelligence y conversación:
   - Snapshot usa `payment_required = billed_debt`.
   - `committed_outflows` no usa `next_payment_estimate` como obligación exigible.
   - Respuestas conversacionales de deuda distinguen deuda actual, pago requerido y estimación.

5. Operación y smokes:
   - Se agregó `scripts/smoke/smoke_credit_semantics_v11.py`.
   - Se actualizó `scripts/smoke/smoke_traceability.py` para seguir el flujo real de clarificación antes de confirmación y limpiar acciones pendientes al inicio.
   - `Dockerfile` copia `scripts/` para permitir migraciones/smokes dentro de imagen.

## Validaciones Ejecutadas
- `python -m uv run ruff check .`: All checks passed.
- `python -m uv run pytest tests/unit/test_credit.py tests/unit/test_intelligence.py -v`: 15 passed.
- `python -m uv run pytest tests/ -v`: 140 passed, 1 warning.
- `python -m uv run python scripts/smoke/smoke_credit_semantics_v11.py`: passed.
- `python -m uv run python scripts/smoke/smoke_credit_core.py`: passed.
- `python -m uv run python scripts/smoke/smoke_financial_truth_v11.py`: passed.
- `python -m uv run python scripts/smoke/smoke_obligations_v11.py`: passed.
- `python -m uv run python scripts/smoke/smoke_goals_consistency_v11.py`: passed.
- `python -m uv run python scripts/smoke/smoke_ledger_history.py`: passed.
- `python -m uv run python scripts/smoke/smoke_traceability.py`: passed.
- `python -m uv run python scripts/smoke/smoke_ownership.py`: passed.

## Auditoría Productiva Previa
La auditoría previa aprobada no encontró bloqueantes para la migración:
- `installments_total_invalid`: 0.
- `debt_negative_candidates`: 0.
- `payments_exceeding_purchases`: 0.
- `limit_lower_than_debt`: 0.
- `missing_credit_event_link`: 0.
- `credit_events_without_tx`: 0.
- `existing_installment_table`: 0.
- `network_column`: 0.
- `franchise_column`: 0.

La contradicción principal observada fue confirmada y resuelta semánticamente:
- `credit_cards.current_debt <> 0`: 0 tarjetas.
- `v_credit_card_debt.credit_card_debt <> 0`: 29 tarjetas, total `6030000.00`.

## Riesgos y Límites Explícitos
- No existe todavía statement persistido; por eso `statement_balance` se reporta como no disponible.
- El schedule de cuotas es una distribución de capital, no un motor de facturación.
- No se implementaron intereses, mora, pago mínimo bancario ni cuota de manejo automática.
- `credit_cards.current_debt` sigue existiendo por compatibilidad de esquema, pero queda obsoleto de facto.

## Estado Final
Sprint 5 queda funcionalmente cerrado a nivel local con migración, contrato, lógica, tests y regresión smoke aprobados.

Pendiente operativo antes de cierre productivo:
- Revisar diff final y preparar commit.
- Push a `main`.
- Ejecutar migración Sprint 5 en VPS.
- Redeploy productivo.
- Verificar `/health`, `/health/readiness` y estado Docker.

No se avanzó a Sprint 6.

## 24. Git State Review
Revisión previa al staging ejecutada en `main`:

```bash
git status --short
git branch --show-current
git log --oneline -10
git diff --stat
git diff
git diff --check
```

Clasificación de archivos versionados:
- `app/credit/models.py`: Sprint 5 legítimo.
- `app/credit/repository.py`: Sprint 5 legítimo.
- `app/credit/router.py`: Sprint 5 legítimo.
- `app/credit/schemas.py`: Sprint 5 legítimo.
- `app/credit/service.py`: Sprint 5 legítimo.
- `app/intelligence/schemas.py`: Sprint 5 legítimo.
- `app/intelligence/service.py`: Sprint 5 legítimo.
- `app/conversations/prompts.py`: Sprint 5 legítimo.
- `tests/unit/test_credit.py`: Sprint 5 legítimo.
- `tests/unit/test_intelligence.py`: Sprint 5 legítimo.
- `scripts/migration/migrate_v11_sprint5.py`: Sprint 5 legítimo.
- `scripts/smoke/smoke_credit_semantics_v11.py`: Sprint 5 legítimo.
- `scripts/smoke/smoke_traceability.py`: smoke/regresión válida.
- `Dockerfile`: cambio operativo válido para incluir `scripts/` en la imagen.
- `docs/agent/BACKEND_V1_1_SPRINT_5_CREDIT_SEMANTICS_AUDIT.md`: documentación válida.
- `docs/agent/BACKEND_V1_1_SPRINT_5_CREDIT_SEMANTICS_REPORT.md`: documentación válida.

No se identificaron residuos de debugging, cambios ajenos ni archivos sensibles.

## 25. Data Audit
Auditoría productiva previa a migración:
- `cards`: 67.
- `purchases`: 65, total `11250000.00`.
- `payments`: 32, total `3420000.00`.
- `installments_total_invalid`: 0.
- `debt_negative_candidates`: 0.
- `payments_exceeding_purchases`: 0.
- `limit_lower_than_debt`: 0.
- `missing_credit_event_link`: 0.
- `credit_events_without_tx`: 0.

Hallazgo: `credit_card_installments`, `network` y `franchise` ya existían en producción antes de ejecutar la migración Sprint 5.

Validación de esa estructura preexistente:
- `installment_rows`: 118.
- `installment_duplicate_purchase_number`: 0.
- `purchase_without_installments`: 0.
- `installment_without_purchase`: 0.
- `installment_principal_mismatch`: 0.
- FKs presentes: 3.
- Índices esperados presentes: 3.

No se detectaron inconsistencias bloqueantes.

## 26. Migration Execution
Secuencia productiva:

```bash
ssh lytrium-vps
cd /opt/nexum-backend
git pull origin main
git rev-parse --short HEAD
docker compose build --no-cache
docker compose run --rm api sh -lc 'cd /app && PYTHONPATH=/app .venv/bin/python scripts/migration/migrate_v11_sprint5.py'
```

Resultado de migración:

```text
INFO: Migrating credit semantics to Sprint 5...
INFO: Backfilling installment schedule for existing purchases...
INFO: Sprint 5 migration completed.
```

Validación post-migración:
- `installment_rows`: 118.
- `installment_duplicate_purchase_number`: 0.
- `purchase_without_installments`: 0.
- `installment_without_purchase`: 0.
- `installment_principal_mismatch`: 0.
- `network_column`: 1.
- `franchise_column`: 1.
- `indexes`: 3.
- `fk_count`: 3.
- `purchases`: 65, total `11250000.00`.
- `payments`: 32, total `3420000.00`.

La migración fue idempotente y no creó deuda adicional ni duplicó cuotas.

## 27. Production Smoke
Primer intento con bypass de test fue rechazado por producción con `401 Unauthorized`, comportamiento esperado porque `AUTH_BYPASS_ENABLED=true` está prohibido en producción.

Smoke productivo final ejecutado contra la API desplegada con JWT real de Supabase generado para usuarios efímeros controlados.

Cobertura validada:
- Crear usuario/bootstrap real.
- Crear cuenta con saldo inicial.
- Crear tarjeta con límite y metadata.
- Crear compra de una cuota.
- Validar idempotencia de compra.
- Crear compra de varias cuotas.
- Consultar installment schedule.
- Confirmar que compras con tarjeta no reducen cash.
- Consultar `current_debt`.
- Consultar `available_credit`.
- Consultar `billed_debt` y `unbilled_debt`.
- Consultar `payment_required`.
- Consultar `next_payment_estimate`.
- Confirmar `statement_balance = null` y `data_quality.next_payment_estimate = estimated`.
- Confirmar que `committed_outflows` usa `payment_required`.
- Pagar tarjeta.
- Confirmar que cash y deuda disminuyen.
- Confirmar idempotencia de pago.
- Confirmar bloqueo de sobrepago.
- Confirmar ownership.

Resultado:

```text
PROD_CREDIT_SEMANTICS_API_SMOKE_PASSED
```

No se limpió data productiva creada por el smoke porque no existe mecanismo de cleanup seguro aprobado; los datos usan emails con prefijo `prod_credit_semantics_` y dominio `nexum-smoke.local`.

## 28. Commit and Deploy
Commit de implementación:

```text
afb336f feat: implement credit semantics v1.1
```

Push:

```text
origin/main = afb336f
```

Commit desplegado tras `git pull origin main` en VPS:

```text
afb336f
```

Commit documental final: este reporte se versiona en commit documental separado posterior al deploy.

## 29. Docker Health
Deploy ejecutado:

```bash
docker compose up -d
docker compose ps
curl -s https://api.nexum.lytrium.tech/health
curl -s https://api.nexum.lytrium.tech/health/readiness
git rev-parse --short HEAD
```

Resultado `docker compose ps`:

```text
NAME                IMAGE               COMMAND                  SERVICE   CREATED         STATUS                   PORTS
nexum_backend_api   nexum-backend-api   ".venv/bin/uvicorn a…"   api       3 minutes ago   Up 3 minutes (healthy)   127.0.0.1:8010->8000/tcp
```

Health/readiness:

```json
{"status":"ok","service":"nexum-backend"}
{"status":"ok","service":"nexum-backend"}
```

Commit desplegado:

```text
afb336f
```

## 30. Formal Closure
Sprint 5 puede cerrarse formalmente a nivel técnico y productivo.

Reglas finales cerradas:
- Source of truth: `credit_card_transactions` enlazadas a `financial_events`.
- `payment_required = billed_debt`.
- `next_payment_estimate` se mantiene separado y marcado `estimated`.
- `statement_balance = null` y `data_quality.statement_balance = not_available`.
- Installment schedule distribuye capital; no crea deuda, intereses ni cargos automáticos.

Limitaciones conocidas:
- No existe statement persistido.
- No existe motor de intereses, mora, pago mínimo bancario ni cuota de manejo automática.
- `credit_cards.current_debt` sigue presente por compatibilidad de esquema, pero queda obsoleto de facto y no participa en cálculos.
- Datos de smoke productivo quedan persistidos como data controlada por falta de cleanup seguro aprobado.

No se avanzó a Sprint 6.
